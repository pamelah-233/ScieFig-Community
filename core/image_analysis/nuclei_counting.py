"""Native-resolution DAPI/hematoxylin nuclei, independent of display adjustments."""
import cv2
import numpy as np
from types import MappingProxyType
from scipy import ndimage as ndi
from skimage.color import rgb2hed
from skimage.feature import peak_local_max
from skimage.filters import threshold_otsu
from skimage.segmentation import watershed
from skimage.morphology import local_maxima
from .he_round_nuclei import HE_ROUND_NUCLEI_V1, HE_REFINED_NUCLEI_V1, round_nuclear_labels, merge_nuclear_candidates


# Retained baseline: new images may override a copy, never this named recipe.
HE_LOCAL_CONTRAST_V1 = MappingProxyType(dict(
    clip_percentile=99., smooth_sigma=.65, background_scale=1.,
    contrast_threshold_fraction=.25, signal_threshold_fraction=.4,
    curvature_scale=.2, curvature_ratio=.04, minimum_width_fraction=.22,
    seed_distance_fraction=.4, minimum_circularity=.25))


def _resolve_he_settings(overrides, method='watershed'):
    recipe = (HE_REFINED_NUCLEI_V1 if method == 'he_refined' else
              HE_ROUND_NUCLEI_V1 if method == 'he_round' else HE_LOCAL_CONTRAST_V1)
    if overrides is not None and set(overrides)-recipe.keys():
        raise ValueError('Unknown H&E nuclear optimization setting.')
    settings = {**recipe, **(overrides or {})}
    settings = {key: float(value) for key, value in settings.items()}
    if any(not np.isfinite(value) or value < 0 for value in settings.values()):
        raise ValueError('H&E nuclear optimization settings must be finite and nonnegative.')
    if any(settings[key] <= 0 for key in settings if key not in ('minimum_circularity', 'curvature_ratio')):
        raise ValueError('H&E nuclear scales and thresholds must be positive.')
    if settings['clip_percentile'] > 100 or settings['minimum_circularity'] > 1 or settings.get('curvature_ratio', 0) >= .25 or settings.get('core_blue_fraction', 0) > 1 or settings.get('maximum_punctum_fraction', 0) > 1:
        raise ValueError('Invalid H&E nuclear percentile or shape threshold.')
    return settings


def nuclear_signal(image, stain):
    source = np.asarray(image)
    if not np.isfinite(source).all():
        raise ValueError('Nuclear input contains non-finite pixels.')
    if stain == 'dapi' and source.ndim == 2:
        return source.astype(np.float32)
    if stain == 'he' and source.ndim == 3 and source.shape[2] == 3:
        return np.maximum(rgb2hed(source.astype(np.float32) / 255)[..., 0], 0).astype(np.float32)
    raise ValueError('Choose one native DAPI channel, or an RGB H&E image.')


def object_rows(labels, signal):
    rows = []
    height, width = labels.shape
    for identifier, slices in enumerate(ndi.find_objects(labels), 1):
        if slices is None:
            continue
        ys, xs = slices
        mask = labels[slices] == identifier
        yy, xx = np.nonzero(mask)
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contour = max(contours, key=cv2.contourArea)[:, 0] + [xs.start, ys.start]
        edge = ys.start == 0 or xs.start == 0 or ys.stop == height or xs.stop == width
        rows.append(dict(object_id=identifier, center_x_px=float(xx.mean()+xs.start),
                         center_y_px=float(yy.mean()+ys.start), area_px=int(mask.sum()),
                         mean_raw_signal=float(signal[slices][mask].mean()),
                         contour=contour.tolist(), border_truncated=bool(edge),
                         include_in_statistics=not edge))
    return rows


def _he_watershed_labels(signal, diameter_px, min_area_px, sensitivity, settings):
    """Local H contrast for faint nuclei; compact peaks, not fiber-rim seeds."""
    positive = signal[signal > 1e-6]
    if positive.size:
        # Near-zero RGB samples create extreme optical-density spikes. Limit
        # their influence on seeds only; retain the original signal for rows.
        signal = np.minimum(signal, np.percentile(positive, settings['clip_percentile']))
    smooth = ndi.gaussian_filter(signal, settings['smooth_sigma'])
    threshold = float(threshold_otsu(smooth)) / sensitivity
    background = ndi.gaussian_filter(smooth, max(2., diameter_px*settings['background_scale']))
    contrast = np.maximum(smooth-background, 0)
    foreground = ((contrast > threshold*settings['contrast_threshold_fraction'])
                  & (smooth > threshold*settings['signal_threshold_fraction']))
    components, _ = ndi.label(foreground)
    sizes = np.bincount(components.ravel())
    foreground &= sizes[components] >= min_area_px

    scale = max(.8, diameter_px*settings['curvature_scale'])
    xx = ndi.gaussian_filter(signal, scale, order=(0, 2))
    yy = ndi.gaussian_filter(signal, scale, order=(2, 0))
    xy = ndi.gaussian_filter(signal, scale, order=(1, 1))
    trace = xx+yy
    determinant = xx*yy-xy*xy
    compact = (trace < 0) & (determinant > settings['curvature_ratio']*trace*trace)
    response = ndi.gaussian_filter(contrast, scale)
    minor_curvature = (trace-np.sqrt((xx-yy)**2+4*xy*xy))/2
    # A ragged thin rim can have an intensity maximum, but lacks nuclear width.
    width_supported = response >= np.maximum(-minor_curvature, 0)*max(.8, diameter_px*settings['minimum_width_fraction'])**2
    peaks = peak_local_max(smooth, labels=foreground,
                           min_distance=max(2, round(diameter_px*settings['seed_distance_fraction'])), exclude_border=False)
    # Find actual H maxima before applying curvature. Masking by curvature or
    # taking maxima of a high-pass image creates phantom rim/corner seeds.
    if len(peaks):
        peaks = peaks[(compact & width_supported)[tuple(peaks.T)]]
        plateaus, _ = ndi.label(local_maxima(smooth) & foreground, structure=np.ones((3, 3)))
        seen = set()
        selected = []
        for y, x in peaks:
            plateau = int(plateaus[y, x])
            if plateau and plateau in seen:
                continue
            seen.add(plateau)
            selected.append((y, x))
        peaks = np.asarray(selected, dtype=int).reshape(-1, 2)
    markers = np.zeros(signal.shape, np.int32)
    if len(peaks):
        markers[tuple(peaks.T)] = np.arange(1, len(peaks)+1)
    return watershed(-smooth, markers, mask=foreground).astype(np.int32)


def count_nuclei(image, *, stain, method='watershed', diameter_px=17., min_area_px=8,
                 max_area_px=2000, roi_mask=None, sensitivity=1., he_settings=None, local_he_settings=None,
                 cellpose_input='raw', measurement_source=None, he_pale_settings=None):
    if not np.isfinite([diameter_px, min_area_px, max_area_px, sensitivity]).all() or diameter_px < 2 or min_area_px < 1 or max_area_px < min_area_px or sensitivity <= 0:
        raise ValueError('Invalid nuclear diameter, area or sensitivity.')
    signal = nuclear_signal(image, stain)
    measured_signal = signal if measurement_source is None else nuclear_signal(measurement_source, stain)
    if measured_signal.shape != signal.shape:
        raise ValueError('Measurement signal must match the segmentation image.')
    if roi_mask is not None and np.shape(roi_mask) != signal.shape:
        raise ValueError('Nuclear ROI must match the source image.')
    settings = _resolve_he_settings(he_settings, method) if stain == 'he' and method in ('watershed', 'he_round', 'he_refined') else None
    local_settings = _resolve_he_settings(local_he_settings) if stain == 'he' and method == 'he_refined' else None
    pale_settings = None
    if method == 'cellpose_cyto3_he_pale_v1':
        if stain != 'he':
            raise ValueError('The pale-nucleus method requires an RGB H&E image.')
        from .he_pale_nuclei import resolve_pale_settings
        pale_settings = resolve_pale_settings(he_pale_settings)
    provenance = dict(backend='skimage', method=method, model=None)
    if np.ptp(signal) <= 1e-8:
        labels = np.zeros(signal.shape, np.int32)
    elif method == 'cellpose_cyto3_he_pale_v1':
        from .he_pale_nuclei import pale_nuclear_labels
        labels, provenance = pale_nuclear_labels(signal,diameter_px,min_area_px,pale_settings)
    elif method in ('cellpose_nuclei', 'cellpose_cyto3', 'cellpose_cyto3_dapi_reviewed_v1'):
        from .cellpose_backend import run_cellpose_labels,prepare_cellpose_channel
        tile = 128 if cellpose_input == 'tile_normalization' else 0
        prepared,input_settings=prepare_cellpose_channel(signal,'raw' if tile else cellpose_input)
        if tile:input_settings=dict(mode='tile_normalization',tile_norm_blocksize=tile,percentiles=[1.,99.])
        extra = dict(normalization_tile_px=tile) if tile else {}
        labels, provenance = run_cellpose_labels(prepared, model=method.removeprefix('cellpose_'),
                                                diameter=diameter_px, min_size=int(min_area_px), **extra)
        provenance={**provenance,'input_preparation':input_settings}
        labels = np.asarray(labels, np.int32)
        if labels.shape != signal.shape:
            raise ValueError('Cellpose label dimensions do not match the source image.')
    elif method in ('he_round', 'he_refined'):
        if stain != 'he':
            raise ValueError('The round H&E nuclear method requires an RGB H&E image.')
        labels = round_nuclear_labels(image, signal, diameter_px, min_area_px, max_area_px, sensitivity, settings)
        provenance['version'] = 'he_round_nuclei_v1'
        if method == 'he_refined':
            labels, _ = _filter_nuclear_objects(labels, signal, None, settings, min_area_px, max_area_px)
            supplement_min = max(min_area_px, round(np.pi*(diameter_px/2)**2*settings['supplement_min_area_fraction']))
            candidates = _he_watershed_labels(signal, diameter_px, supplement_min, sensitivity, local_settings)
            candidates, _ = _filter_nuclear_objects(candidates, signal, None, local_settings, supplement_min, max_area_px)
            labels = merge_nuclear_candidates(labels, candidates)
            provenance.update(version='he_refined_nuclei_v1', merge_overlap_fraction=.15,
                merge_radius_fraction=.9, supplement_min_area_px=supplement_min)
    elif method == 'watershed':
        if stain == 'he':
            labels = _he_watershed_labels(signal, diameter_px, min_area_px, sensitivity, settings)
            provenance['version'] = 'he_local_contrast_v1'
        else:
            smooth = ndi.gaussian_filter(signal, .65)
            threshold = float(threshold_otsu(smooth)) / sensitivity
            foreground = smooth > threshold
            components, _ = ndi.label(foreground)
            sizes = np.bincount(components.ravel())
            foreground &= sizes[components] >= min_area_px
            distance = ndi.distance_transform_edt(foreground)
            peaks = peak_local_max(ndi.gaussian_filter(distance, .7), labels=foreground,
                                   min_distance=max(2, round(diameter_px*.45)), exclude_border=False)
            markers = np.zeros(signal.shape, np.int32)
            if len(peaks):
                markers[tuple(peaks.T)] = np.arange(1, len(peaks)+1)
            labels = watershed(-distance, markers, mask=foreground).astype(np.int32)
    else:
        raise ValueError('Unknown nuclear counting method.')
    kept, rows = _filter_nuclear_objects(labels, measured_signal, roi_mask, settings, min_area_px, max_area_px)
    parameters = dict(stain=stain, method=method, diameter_px=diameter_px,
                      min_area_px=min_area_px, max_area_px=max_area_px, sensitivity=sensitivity)
    if method in ('cellpose_nuclei','cellpose_cyto3','cellpose_cyto3_dapi_reviewed_v1'):parameters['cellpose_input']=cellpose_input
    if settings is not None:
        parameters['he_settings'] = settings
    if local_settings is not None:
        parameters['local_he_settings'] = local_settings
    if pale_settings is not None:
        parameters['he_pale_settings'] = pale_settings
    return dict(labels=kept, nuclei=rows, provenance=provenance, parameters=parameters)


def _filter_nuclear_objects(labels, signal, roi_mask, settings, min_area_px, max_area_px):
    # Select whole objects by centroid; flag truncation rather than counting clipped fragments.
    kept = np.zeros(labels.shape, np.int32)
    rows = object_rows(labels, signal)
    for row in rows:
        if not min_area_px <= row['area_px'] <= max_area_px:
            continue
        if settings is not None:
            contour = np.asarray(row['contour'], np.int32).reshape(-1, 1, 2)
            perimeter = cv2.arcLength(contour, True)
            if perimeter == 0 or 4*np.pi*cv2.contourArea(contour) < settings['minimum_circularity']*perimeter**2:
                continue
        x, y = round(row['center_x_px']), round(row['center_y_px'])
        if roi_mask is not None and not roi_mask[y, x]:
            continue
        kept[labels == row['object_id']] = row['object_id']
        if roi_mask is not None and np.any((labels == row['object_id']) & ~roi_mask):
            row['border_truncated'] = True
            row['include_in_statistics'] = False
    accepted = set(np.unique(kept)) - {0}
    rows = [row for row in rows if row['object_id'] in accepted]
    return kept, rows
