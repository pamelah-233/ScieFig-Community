"""Image-local multi-marker measurements from saved, reviewable nuclear masks."""
import numpy as np
from scipy import ndimage as ndi

from core.measurement_relations import geometry_record, measurement_id
from core.spatial_demo import DemoResult, spatial_metrics


def measure_multiplex(image, source, *, markers, expansion=3., radius=20.,
                      source_phenotype=None, target_phenotype=None,
                      pixel_size_xy=None, roi_mask=None):
    image = np.asarray(image)
    if image.ndim != 3 or not markers or not np.isfinite([expansion, radius]).all() or expansion < 0 or radius <= 0:
        raise ValueError('Choose marker channels and positive spatial parameters.')
    channels = [int(m['channel_index']) for m in markers]
    if len(set(channels)) != len(channels) or any(c < 0 or c >= image.shape[0] for c in channels):
        raise ValueError('Marker channels must be distinct channels of this image.')
    cutoffs = np.asarray([m['threshold'] for m in markers], float)
    if not np.isfinite(cutoffs).all():
        raise ValueError('Marker cutoffs must be finite raw intensities.')
    scale = np.asarray(pixel_size_xy if pixel_size_xy is not None else (1., 1.), float)
    if scale.shape != (2,) or not np.isfinite(scale).all() or np.any(scale <= 0):
        raise ValueError('Invalid X/Y calibration.')
    geometry = geometry_record(source, image.shape[1:])
    nuclei = geometry['labels']
    roi = geometry['roi_mask'].copy()
    if roi_mask is not None:
        if np.shape(roi_mask) != nuclei.shape:
            raise ValueError('ROI must match native image dimensions.')
        roi &= roi_mask
    ids = np.unique(nuclei[nuclei > 0])
    if len(ids):
        distance, nearest = ndi.distance_transform_edt(nuclei == 0, sampling=scale[::-1], return_indices=True)
        cells = np.where(distance <= expansion, nuclei[tuple(nearest)], 0)
        means = np.column_stack([ndi.mean(image[c], cells, ids) for c in channels])
        xy_px = np.asarray(ndi.center_of_mass(nuclei > 0, nuclei, ids))[:, ::-1]
        flags = means >= cutoffs
        combinations, codes = np.unique(flags, axis=0, return_inverse=True)
    else:
        cells = nuclei.copy()
        means, xy_px = np.empty((0, len(markers))), np.empty((0, 2))
        combinations, codes = np.empty((0, len(markers)), bool), np.empty(0, int)
    titles = [' / '.join(str(m['name'])+('+' if on else '−') for m, on in zip(markers, combination))
              for combination in combinations]
    selected = np.zeros(len(ids), bool)
    if len(ids):
        centers = np.rint(xy_px).astype(int)
        centers[:, 0] = np.clip(centers[:, 0], 0, nuclei.shape[1]-1)
        centers[:, 1] = np.clip(centers[:, 1], 0, nuclei.shape[0]-1)
        selected = roi[centers[:, 1], centers[:, 0]]
    objects_by_id = {r['label_id']: r for r in geometry['objects']}
    excluded = set(geometry['overlapping_ids'])
    for i, identifier in enumerate(ids):
        selected[i] &= objects_by_id[int(identifier)].get('include_in_statistics', True) and identifier not in excluded
    area = float(roi.sum()*np.prod(scale))
    if area <= 0:
        raise ValueError('ROI has no measurable area.')
    r = DemoResult(nuclei, cells, ids, xy_px*scale, means, codes, area, selected, (0, 0, nuclei.shape[1], nuclei.shape[0]))
    present = sorted(set(codes[selected].tolist()))
    choose = lambda title, default: titles.index(title) if title in titles else default if title is None else -1
    a = choose(source_phenotype, present[0] if present else -1)
    b = choose(target_phenotype, present[-1] if present else -1)
    metrics = spatial_metrics(r, a, b, radius_um=radius)
    neighbors = {int(i): (float(d) if np.isfinite(d) else None, int(n))
                 for i, d, n in zip(metrics['source_ids'], metrics['nearest'], metrics['neighbors'])}
    colors = ('#A0A0A0', '#3ED995', '#F5A65B', '#BE91EA', '#40C7DF', '#EE7B96', '#DCCF61', '#89A7F7')
    rows = []
    for i, identifier in enumerate(ids):
        original = objects_by_id[int(identifier)]
        d, n = neighbors.get(int(identifier), (None, None))
        rows.append(dict(object_id=int(identifier), source_object_id=original['object_id'],
                         center_x_px=float(xy_px[i, 0]), center_y_px=float(xy_px[i, 1]),
                         x=float(r.xy[i, 0]), y=float(r.xy[i, 1]), phenotype=titles[codes[i]],
                         marker_means=[float(v) for v in means[i]],
                         nearest_target=d, target_count_in_radius=n,
                         include_in_statistics=bool(selected[i]),
                         contour_points_px=original.get('contour_points_px', original.get('contour_px', [])),
                         overlay_color=colors[int(codes[i]) % len(colors)]))
    finite = metrics['nearest'][np.isfinite(metrics['nearest'])]
    total = int(selected.sum())
    summary = dict(cells=total, excluded=len(ids)-total, area=area,
                   area_unit='um2' if pixel_size_xy is not None else 'px2',
                   length_unit='um' if pixel_size_xy is not None else 'px',
                   density=total/area, source_cells=metrics['source_n'], target_cells=metrics['target_n'],
                   nearest_mean=float(finite.mean()) if len(finite) else None,
                   nearest_median=float(np.median(finite)) if len(finite) else None,
                   fraction_with_neighbors=float(np.mean(metrics['neighbors'] > 0)) if len(metrics['neighbors']) else None,
                   permutation_p=metrics['p'], null_mean=metrics['null_mean'])
    phenotypes = [dict(phenotype=titles[c], cells=int(np.sum(selected & (codes == c))),
                       fraction=float(np.sum(selected & (codes == c))/total),
                       density=float(np.sum(selected & (codes == c))/area)) for c in present] if total else []
    return dict(type='multiplex_spatial', image_uid=source['image_uid'], image_name=source.get('image_name', ''),
                channel_index=source.get('channel_index'), object_table=rows, summary=summary, phenotypes=phenotypes,
                source_measurements={'nuclei': measurement_id(source)},
                parameters=dict(markers=markers, expansion=expansion, radius=radius,
                                source_phenotype=source_phenotype if source_phenotype is not None else titles[a] if a >= 0 else None,
                                target_phenotype=target_phenotype if target_phenotype is not None else titles[b] if b >= 0 else None, pixel_size_xy=pixel_size_xy),
                measurement_source='raw', scope='centroid_in_roi; neighbors_in_roi; self_excluded',
                spatial_test='199 conditional phenotype-label permutations; no edge correction')
