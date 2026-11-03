"""Paper-data and AI-raster checks for the spatial demo (not a clinical benchmark).

Run from the project root with dependencies available. Local frozen-runtime use:
exec(Path('.local-runtime/bundled-dev-imports.py').read_text(encoding='utf-8'))
exec(compile(Path('packaging/validate_spatial_demo.py').read_text(encoding='utf-8'),
             'packaging/validate_spatial_demo.py', 'exec'))
"""
import csv
import io
import json
import time
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
import tifffile
from scipy import ndimage as ndi
from scipy.optimize import linear_sum_assignment

from core.spatial_demo import analyze, spatial_metrics


BASE = Path.cwd()/'.local-runtime/spatial-validation'
OUT = BASE/'results'


def decode_mask(mask):
    # Author's decoder labels equal-valued components in channel 0, using
    # full connectivity, rather than merging all nonzero touching colors.
    gray = mask[:, :, 0]
    labels = np.zeros(gray.shape, np.int32)
    offset = 0
    for value in np.unique(gray):
        if value == 0:
            continue
        components, count = ndi.label(gray == value, structure=np.ones((3, 3)))
        foreground = components > 0
        labels[foreground] = components[foreground]+offset
        offset += count
    return labels


def segmentation_scores(pred, truth):
    n, m = int(pred.max()), int(truth.max())
    overlap = np.bincount((pred*(m+1)+truth).ravel(), minlength=(n+1)*(m+1)).reshape(n+1, m+1)
    intersections = overlap[1:, 1:]
    unions = overlap.sum(axis=1)[1:, None]+overlap.sum(axis=0)[None, 1:]-intersections
    iou = np.divide(intersections, unions, out=np.zeros_like(intersections, float), where=unions > 0)
    tp = 0
    if n and m:
        eligible = iou >= .5
        rows, cols = linear_sum_assignment(-(eligible.astype(float)+iou*1e-6))
        tp = int(eligible[rows, cols].sum())
    denominator = int((pred > 0).sum()+(truth > 0).sum())
    return dict(predicted=n, ground_truth=m, tp=tp, fp=n-tp, fn=m-tp,
                f1=2*tp/(n+m) if n+m else 1.,
                binary_dice=2*int(((pred > 0)&(truth > 0)).sum())/denominator if denominator else 1.)


def arithmetic_checks(image, result, scale):
    # Independent pixel indexing and Python point-to-point distances.
    for i in np.linspace(0, len(result.ids)-1, min(20, len(result.ids)), dtype=int):
        cell = result.cells == result.ids[i]
        reference = np.array([image[ch][cell].astype(float).sum()/cell.sum()/65535 for ch in (1, 2, 3)])
        np.testing.assert_allclose(result.intensities[i], reference, atol=1e-12)
        yy, xx = np.nonzero(result.nuclei == result.ids[i])
        np.testing.assert_allclose(result.xy[i], [xx.mean()*scale, yy.mean()*scale], atol=1e-10)
    np.testing.assert_array_equal(result.cells[result.nuclei > 0], result.nuclei[result.nuclei > 0])
    present = np.unique(result.phenotype)
    if len(present):
        a, b = int(present[0]), int(present[-1])
        metrics = spatial_metrics(result, a, b, radius_um=20, permutations=0)
        targets = np.flatnonzero(result.selected & (result.phenotype == b))
        for k, i in enumerate(metrics['source_indices'][:20]):
            distances = [float(np.hypot(*(result.xy[i]-result.xy[j]))) for j in targets if i != j]
            nearest = min(distances, default=np.inf)
            np.testing.assert_allclose(metrics['nearest'][k], nearest, atol=1e-10)
            assert metrics['neighbors'][k] == sum(d <= 20 for d in distances)
    return 'PASS: up to 20 cells; means, centroids, ownership, nearest distances and radius counts'


def run_case(image, name, scale=1.):
    start = time.perf_counter()
    result = analyze(image, pixel_um=scale)
    seconds = time.perf_counter()-start
    checks = arithmetic_checks(image, result, scale)
    row = dict(sample=name, width=image.shape[2], height=image.shape[1], dtype=str(image.dtype),
               nuclei=len(result.ids), segmentation_seconds=round(seconds, 4), arithmetic=checks,
               nuclear_threshold=.16, normalization_divisor=65535, expansion=3, scale=scale)
    return result, row


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bbbc, tissue, ai = [], [], []
    with zipfile.ZipFile(BASE/'sources/bbbc039-images.zip') as images, \
            zipfile.ZipFile(BASE/'sources/bbbc039-masks.zip') as masks, \
            zipfile.ZipFile(BASE/'sources/bbbc039-metadata.zip') as metadata:
        names = metadata.read('metadata/test.txt').decode().splitlines()
        for name in names:
            raw = tifffile.imread(io.BytesIO(images.read('images/'+Path(name).with_suffix('.tif').name)))
            truth = decode_mask(np.array(Image.open(io.BytesIO(masks.read('masks/'+name)))))
            image = np.zeros((4, *raw.shape), np.uint16)
            image[0] = raw
            result, row = run_case(image, name)
            row.update(segmentation_scores(result.nuclei, truth))
            row['nuclear_max_raw'] = int(raw.max())
            bbbc.append(row)
            if len(bbbc) == 1:
                np.savez_compressed(OUT/'bbbc039-first-test.npz', raw=raw, truth=truth, prediction=result.nuclei)
        print('BBBC039 held-out images:', len(bbbc), flush=True)

    with zipfile.ZipFile(BASE/'sources/mcmicro-exemplar001.zip') as archive:
        marker_rows = list(csv.DictReader(io.StringIO(archive.read('exemplar-001/markers.csv').decode())))
        for cycle in (6, 7, 8):
            name = f'exemplar-001/raw/exemplar-001-cycle-{cycle:02d}.ome.tiff'
            path = Path(archive.extract(name, BASE/'sources/mcmicro'))
            markers = [r['marker_name'] for r in marker_rows if r['cycle_number'] == str(cycle)]
            with tifffile.TiffFile(path) as stack:
                pixels = next(e for e in ET.fromstring(stack.ome_metadata).iter() if e.tag.endswith('Pixels'))
                sx, sy = float(pixels.attrib['PhysicalSizeX']), float(pixels.attrib['PhysicalSizeY'])
                assert np.isclose(sx, sy)
                for tile, series in enumerate(stack.series):
                    image = series.asarray()
                    result, row = run_case(image, f'cycle-{cycle:02d}-tile-{tile:02d}', sx)
                    row.update(markers=markers, calibration_unit='um', has_ground_truth=False,
                               positive_cells=int((result.phenotype > 0).sum()))
                    tissue.append(row)
                    if cycle == 7 and tile == 0:
                        np.savez_compressed(OUT/'mcmicro-cycle07-tile00.npz', raw=image, prediction=result.nuclei)
            print('MCMICRO cycle', cycle, 'finished', flush=True)

    for path in sorted((BASE/'ai').glob('*.png')):
        # A generated RGB composite is not independent raw fluorescence channels.
        # This mapping ONLY tests numeric robustness. No marker identity or µm.
        if path.stem in ('ai04-ihc', 'ai05-he'):
            continue
        rgb = np.asarray(Image.open(path).convert('RGB'))
        image = np.zeros((4, *rgb.shape[:2]), np.uint16)
        for ch, rgb_index in enumerate((2, 1, 0)):
            image[ch] = rgb[:, :, rgb_index].astype(np.uint16)*257
        result, row = run_case(image, path.name)
        row.update(source='AI_GENERATED_RGB', proxy_channels=['B', 'G', 'R', 'absent'],
                   calibration_unit='pixel (no physical calibration)', has_ground_truth=False,
                   warning='Not independent markers; counts are algorithm output, not true cell counts')
        ai.append(row)
        print('AI', path.name, row['width'], row['height'], 'detected', row['nuclei'], flush=True)

    # Large native-array execution check, with analytically known object count.
    # Not an ImageGen image and not a biological segmentation-accuracy test.
    large = np.zeros((4, 2160, 3840), np.uint16)
    expected = 0
    for y in range(20, 2140, 28):
        for x in range(20, 3820, 28):
            large[0, y-4:y+5, x-4:x+5] = 40000
            large[1, y-4:y+5, x-4:x+5] = 30000
            expected += 1
    start = time.perf_counter()
    large_result = analyze(large, pixel_um=1, expansion_um=0)
    assert len(large_result.ids) == expected
    large_neighbors = spatial_metrics(large_result, 1, 1, radius_um=20, permutations=0)
    np.testing.assert_allclose(large_neighbors['nearest'], 28)
    assert not large_neighbors['neighbors'].any()
    large_check = dict(width=3840, height=2160, objects=expected, detected=len(large_result.ids),
                       seconds=round(time.perf_counter()-start, 4), source='DETERMINISTIC_ARRAY_NOT_IMAGEGEN',
                       caveat='Sparse rectangular objects; does not validate dense tissue separation or whole-slide memory')
    output = dict(bbbc039=bbbc, mcmicro=tissue, imagegen=ai, native_large_array=large_check,
                  limitations=['Threshold connected components cannot separate touching nuclei reliably',
                               '16-bit storage does not imply full 16-bit effective signal range',
                               'No ground truth phenotype or cell boundaries for MCMICRO/AI inputs',
                               'No cross-cycle registration, spectral unmixing or whole-slide streaming is tested',
                               'Demo supports four uint16 TIFF channels at native dimensions; arbitrary marker counts are not tested'])
    (OUT/'validation.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    for name, rows in [('bbbc039', bbbc), ('mcmicro', tissue), ('imagegen', ai)]:
        if rows:
            with (OUT/(name+'.csv')).open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    print('Saved', OUT/'validation.json', flush=True)


if __name__ == '__main__':
    main()
