"""Pale-core HE nuclei: reconstruct small nuclear bodies, not white tissue voids."""
from types import MappingProxyType
import cv2
import numpy as np
from scipy import ndimage as ndi
from skimage.filters import threshold_otsu
from skimage.feature import peak_local_max
from skimage.morphology import local_maxima, h_maxima
from skimage.segmentation import watershed


HE_ROUND_NUCLEI_V1 = MappingProxyType(dict(
    clip_percentile=99., smooth_sigma=.65, threshold_fraction=.45,
    closing_radius_fraction=.08, opening_radius_fraction=.13, max_hole_diameter_fraction=1.5,
    core_blue_fraction=.12, core_blue_margin=2., maximum_punctum_fraction=.25,
    seed_distance_fraction=.45, minimum_seed_radius_fraction=.2,
    minimum_circularity=.35))

HE_REFINED_NUCLEI_V1 = MappingProxyType({**HE_ROUND_NUCLEI_V1,
    'peak_prominence_fraction': .125, 'supplement_min_area_fraction': .25})


def merge_nuclear_candidates(primary, candidates):
    """Add independent weak-body detections without counting intranuclear fragments twice."""
    from skimage.measure import regionprops
    merged = np.asarray(primary, np.int32).copy()
    regions = regionprops(merged)
    centers = [region.centroid for region in regions]
    radii = [np.sqrt(region.area/np.pi) for region in regions]
    next_id = int(merged.max())+1
    for region in regionprops(np.asarray(candidates, np.int32)):
        y,x = np.round(region.centroid).astype(int)
        slices = region.slice
        mask = candidates[slices] == region.label
        if merged[y,x] or np.count_nonzero(merged[slices][mask])/region.area >= .15:
            continue
        radius = np.sqrt(region.area/np.pi)
        if centers:
            distance = np.linalg.norm(np.asarray(centers)-region.centroid, axis=1)
            if np.any(distance < .9*(np.asarray(radii)+radius)):
                continue
        local = merged[slices]
        local[mask & (local == 0)] = next_id
        centers.append(region.centroid);radii.append(radius);next_id += 1
    return merged


def round_nuclear_labels(rgb, signal, diameter_px, min_area_px, max_area_px, sensitivity, settings):
    positive = signal[signal > 1e-6]
    working = np.minimum(signal, np.percentile(positive, settings['clip_percentile'])) if positive.size else signal
    smooth = ndi.gaussian_filter(working, settings['smooth_sigma'])
    foreground = smooth > float(threshold_otsu(smooth))*settings['threshold_fraction']/sensitivity
    radius = max(1, round(diameter_px*settings['closing_radius_fraction']))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius*2+1, radius*2+1)).astype(bool)
    # Pad so a genuinely edge-truncated nucleus stays open instead of acquiring a false hole.
    padded = np.pad(foreground, radius)
    foreground = ndi.binary_closing(padded, structure=kernel)[radius:-radius, radius:-radius]
    bodies, _ = ndi.label(foreground)
    body_sizes = np.bincount(bodies.ravel())
    holes, _ = ndi.label(ndi.binary_fill_holes(foreground) & ~foreground)
    max_hole_area = min(max_area_px, np.pi*(diameter_px*settings['max_hole_diameter_fraction']/2)**2)
    channels = np.asarray(rgb, np.float32)
    margin = settings['core_blue_margin']
    blue_core = ((channels[...,2] > channels[...,0]+margin)
                 & (channels[...,2] > channels[...,1]+margin))
    voids = np.zeros(signal.shape, bool)
    nuclear_cores = np.zeros(signal.shape, bool)
    for identifier, slices in enumerate(ndi.find_objects(holes), 1):
        if slices is None:
            continue
        hole = holes[slices] == identifier
        if hole.sum() > max_hole_area:
            continue
        expanded = tuple(slice(max(0,s.start-1), min(signal.shape[axis],s.stop+1)) for axis,s in enumerate(slices))
        local_hole = holes[expanded] == identifier
        neighbors = bodies[expanded][ndi.binary_dilation(local_hole) & ~local_hole]
        neighbors = neighbors[neighbors > 0]
        enclosing = int(np.bincount(neighbors).argmax()) if len(neighbors) else 0
        body_area = body_sizes[enclosing]+hole.sum() if enclosing else np.inf
        punctum = body_area <= max_area_px and hole.sum()/body_area <= settings['maximum_punctum_fraction']
        if float(blue_core[slices][hole].mean()) >= settings['core_blue_fraction'] or punctum:
            foreground[slices] |= hole
            nuclear_cores[slices] |= hole
        else:
            # A blue outline around white background is not a nuclear body.
            voids[slices] |= hole
    if voids.any():
        protected = ndi.binary_dilation(nuclear_cores, structure=kernel, iterations=2)
        foreground &= ~ndi.binary_dilation(voids, structure=kernel, iterations=2) | protected
    opening_radius = max(1, round(diameter_px*settings['opening_radius_fraction']))
    opening_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (opening_radius*2+1, opening_radius*2+1)).astype(bool)
    foreground = ndi.binary_opening(foreground, structure=opening_kernel)
    distance = ndi.distance_transform_edt(foreground)
    threshold = max(1.1, diameter_px*settings['minimum_seed_radius_fraction'])
    prominence = diameter_px*settings.get('peak_prominence_fraction', 0.)
    if prominence > 0:
        maxima = h_maxima(distance, prominence) & foreground & (distance >= threshold)
        components, count = ndi.label(maxima)
        peaks = np.asarray(ndi.maximum_position(distance, components, list(range(1,count+1))), dtype=int).reshape(-1,2)
    else:
        peaks = peak_local_max(distance, labels=foreground,
                               min_distance=max(2, round(diameter_px*settings['seed_distance_fraction'])),
                               threshold_abs=threshold, exclude_border=False)
    markers = np.zeros(signal.shape, np.int32)
    plateaus, _ = ndi.label(local_maxima(distance) & foreground, structure=np.ones((3,3)))
    seen = set()
    for y, x in peaks:
        plateau = int(plateaus[y,x])
        if plateau and plateau in seen:
            continue
        seen.add(plateau)
        markers[y,x] = len(seen)
    return watershed(-distance, markers, mask=foreground).astype(np.int32)
