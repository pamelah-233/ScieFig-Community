"""Deterministic synthetic multiplex imaging demo; never a clinical reference."""
from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree


@dataclass
class DemoResult:
    nuclei: np.ndarray
    cells: np.ndarray
    ids: np.ndarray
    xy: np.ndarray
    intensities: np.ndarray
    phenotype: np.ndarray
    area_um2: float
    selected: np.ndarray
    roi: tuple


def synthetic_image(seed=23):
    """4-channel uint16 field, 0.5 µm/px, with known non-overlapping nuclei."""
    rng = np.random.default_rng(seed)
    size = 512
    y, x = np.mgrid[:size, :size]
    image = rng.uniform(250, 650, (4, size, size))
    centers = []
    for _ in range(12000):
        point = rng.uniform(20, size-20, 2)
        if all(np.linalg.norm(point-p) > 27 for p in centers):
            centers.append(point)
        if len(centers) == 130:
            break
    for cy, cx in centers:
        r2 = (x-cx)**2+(y-cy)**2
        image[0] += rng.uniform(26000, 52000)*np.exp(-r2/(2*3.4**2))
        # Deliberately mixed phenotypes; regional composition varies.
        category = rng.choice(4, p=(.45, .25, .15, .15) if cx < 256 else (.15, .20, .50, .15))
        positive = ((1, 1, 0), (1, 0, 0), (0, 0, 1), (0, 0, 0))[category]
        profile = np.exp(-r2/(2*8.8**2))
        for channel, on in enumerate(positive, 1):
            image[channel] += rng.uniform(38000, 55000) * (1 if on else .035) * profile
    return np.clip(image, 0, 65535).astype(np.uint16)


def analyze(image, pixel_um=.5, nuclear_threshold=.16, expansion_um=3., thresholds=(.2, .2, .2), roi=None):
    """Threshold nuclei; nearest-nucleus expansion without overlapping ownership."""
    if image.ndim != 3 or image.shape[0] != 4 or min(image.shape[1:]) < 1 or pixel_um <= 0 or expansion_um < 0:
        raise ValueError('Invalid demo image or scale')
    height, width = image.shape[1:]
    if roi is None:
        roi = (0, 0, width, height)
    x0, y0, x1, y1 = roi
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError('ROI must be a nonempty rectangle inside the image')
    raw = image.astype(float)/65535
    binary = ndi.binary_fill_holes(raw[0] >= nuclear_threshold)
    labels, count = ndi.label(binary)
    sizes = np.bincount(labels.ravel())
    keep = np.flatnonzero(sizes >= 12)
    keep = keep[keep != 0]
    lookup = np.zeros(count+1, dtype=np.int32)
    lookup[keep] = np.arange(1, len(keep)+1)
    nuclei = lookup[labels]
    ids = np.arange(1, len(keep)+1)
    if not len(ids):
        cells = nuclei.copy()
        xy, means = np.empty((0, 2)), np.empty((0, 3))
    else:
        distance, nearest = ndi.distance_transform_edt(nuclei == 0, return_indices=True)
        cells = np.where(distance <= expansion_um/pixel_um, nuclei[tuple(nearest)], 0)
        # Array coordinates are y,x; reported centroids are x,y in µm.
        xy = np.asarray(ndi.center_of_mass(nuclei > 0, nuclei, ids))[:, ::-1]*pixel_um
        means = np.column_stack([ndi.mean(raw[ch], cells, ids) for ch in (1, 2, 3)])
    flags = means >= np.asarray(thresholds)
    phenotype = (flags*np.array([1, 2, 4])).sum(axis=1).astype(int)
    x0, y0, x1, y1 = roi
    selected = ((xy[:, 0] >= x0*pixel_um) & (xy[:, 0] < x1*pixel_um)
                & (xy[:, 1] >= y0*pixel_um) & (xy[:, 1] < y1*pixel_um))
    return DemoResult(nuclei, cells, ids, xy, means, phenotype,
                      (x1-x0)*(y1-y0)*pixel_um**2, selected, roi)


def phenotype_name(code, names=('CD3', 'CD8', 'CD68')):
    return ' / '.join(name+('+' if code & (1 << i) else '−') for i, name in enumerate(names))


def spatial_metrics(result, source_code, target_code, radius_um=20., permutations=199):
    """Directed A→B statistics with self exclusion and conditional label permutation."""
    mask = result.selected
    ids, xy, codes = result.ids[mask], result.xy[mask], result.phenotype[mask]
    source, target = np.flatnonzero(codes == source_code), np.flatnonzero(codes == target_code)
    if not len(source) or not len(target):
        return {'source_n': len(source), 'target_n': len(target), 'nearest': np.empty(0),
                'neighbors': np.empty(0, dtype=int), 'source_ids': ids[source], 'p': None,
                'null_mean': None, 'source_indices': np.flatnonzero(mask)[source]}
    same_type = source_code == target_code
    tree = cKDTree(xy[target])
    # A full N×N distance array exhausts memory on dense, large tissue fields.
    # For same-type queries the first point is self; the second is the nearest
    # other cell (also correct when two distinct centroids coincide).
    distances = tree.query(xy[source], k=2 if same_type else 1)[0]
    nearest = distances[:, 1] if same_type else distances
    neighbors = np.asarray(tree.query_ball_point(xy[source], radius_um, return_length=True))-int(same_type)
    finite = nearest[np.isfinite(nearest)]
    null_mean, p = None, None
    if len(finite) and permutations and len(np.unique(codes)) > 1:
        observed = finite.mean()
        rng = np.random.default_rng(773)
        null = []
        for _ in range(permutations):
            shuffled = rng.permutation(codes)
            a, b = np.flatnonzero(shuffled == source_code), np.flatnonzero(shuffled == target_code)
            distances = cKDTree(xy[b]).query(xy[a], k=2 if same_type else 1)[0]
            values = distances[:, 1] if same_type else distances
            values = values[np.isfinite(values)]
            if len(values):
                null.append(values.mean())
        if null:
            null = np.asarray(null)
            # Two-sided Monte Carlo tail probability, with +1 correction.
            p = min(1., 2*min((1+np.sum(null <= observed))/(len(null)+1),
                              (1+np.sum(null >= observed))/(len(null)+1)))
            null_mean = float(null.mean())
    return {'source_n': len(source), 'target_n': len(target), 'source_ids': ids[source],
            'source_indices': np.flatnonzero(mask)[source], 'nearest': nearest,
            'neighbors': neighbors, 'p': p, 'null_mean': null_mean}
