"""Offline Cellpose worker bridge and calibrated instance measurements."""
import cv2
import numpy as np
from scipy import ndimage

from .bright_round_cells import _detect_scale_bar_region, _maximum_feret, _serialise_row
from .cellpose_backend import run_cellpose_labels, worker_command

VERSION = 'cellpose-cyto3-3.1.1.3'


def measurements_from_labels(rgb, labels, um_per_px, settings, roi_mask=None, excluded_regions=()):
    if um_per_px <= 0:
        raise ValueError('Cellpose measurement requires a positive um/px calibration.')
    height, width = labels.shape
    allowed = np.ones(labels.shape, bool) if roi_mask is None else np.asarray(roi_mask, bool).copy()
    for r in excluded_regions:
        allowed[r['y']:r['y']+r['h'], r['x']:r['x']+r['w']] = False
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    rows = []
    for identifier, slices in enumerate(ndimage.find_objects(labels), 1):
        if slices is None:
            continue
        ys, xs = slices
        full_mask = labels[slices] == identifier
        local = full_mask & allowed[slices]
        if not local.any():
            continue
        contours, _ = cv2.findContours(local.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)
        if area <= 0:
            continue
        contour = contour + np.array([[[xs.start, ys.start]]], np.int32)
        perimeter = cv2.arcLength(contour, True)
        hull = cv2.convexHull(contour)
        (cx, cy), sides, _ = cv2.minAreaRect(contour)
        aspect = min(sides) / max(sides) if max(sides) else 1.
        equivalent = float(np.sqrt(4 * area / np.pi))
        shape = 'round' if aspect >= .70 else 'elongated'
        # The existing controls describe bright/round equivalent diameters, not long-cell lengths.
        if shape == 'round' and not (float(settings.get('min_diameter_um', 0)) <= equivalent * um_per_px <= float(settings.get('max_diameter_um', float('inf')))):
            continue
        start, end, feret = _maximum_feret(hull)
        row = _serialise_row(dict(
            contour=contour, center_x_px=cx, center_y_px=cy, area_px=area,
            equivalent_diameter_px=equivalent, max_feret_px=feret,
            min_feret_px=min(sides), feret_start_px=start, feret_end_px=end,
            circularity=4*np.pi*area/perimeter**2 if perimeter else 0,
            aspect_ratio=aspect, solidity=area/max(cv2.contourArea(hull), 1),
            mean_intensity=float(gray[slices][local].mean()),
            shape_class=shape, detection_source='cellpose_cyto3'), len(rows)+1, um_per_px)
        clipped = bool(xs.start <= 1 or ys.start <= 1 or xs.stop >= width-1 or ys.stop >= height-1
                       or np.any(full_mask & ~allowed[slices]))
        row.update(border_truncated=clipped, include_in_statistics=not clipped, source_label_id=int(identifier))
        rows.append(row)
    diameters = [r['equivalent_diameter_um'] for r in rows if r['include_in_statistics']]
    summary = dict(count=len(rows), round_count=sum(r['shape_class']=='round' for r in rows),
                   elongated_count=sum(r['shape_class']=='elongated' for r in rows),
                   border_truncated_count=sum(r['border_truncated'] for r in rows),
                   calibration_um_per_px=float(um_per_px),
                   median_equivalent_diameter_um=float(np.median(diameters)) if diameters else 0,
                   mean_equivalent_diameter_um=float(np.mean(diameters)) if diameters else 0)
    from .cell_width import update_cell_size_summary
    update_cell_size_summary(summary,rows)
    return dict(analysis_version=VERSION, um_per_px=float(um_per_px),
                settings={**settings, 'backend':'cellpose', 'model':'cyto3',
                          'model_diameter_px':float(settings.get('model_diameter_px',30)),
                          'model_min_size_px':int(settings.get('model_min_size_px',60))},
                results=rows, summary=summary, excluded_regions=list(excluded_regions),
                image_size_px=[width, height])


def analyze_cellpose_cells(rgb, um_per_px, settings=None, roi_mask=None):
    settings = dict(settings or {})
    strategy = settings.setdefault('cellpose_strategy', 'general')
    if strategy not in ('general', 'local_contrast'):
        raise ValueError('Unknown brightfield Cellpose strategy: ' + str(strategy))
    inference_options = {'normalization_tile_px':128} if strategy == 'local_contrast' else {}
    image = np.asarray(rgb, np.uint8)
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    region = _detect_scale_bar_region(gray) if settings.get('exclude_scale_bar', True) else None
    excluded = [region] if region else []
    for r in excluded:
        x,y,w,h = (r[k] for k in ('x','y','w','h'))
        sample = gray[max(0,y-40):y, x:x+w]
        gray[y:y+h,x:x+w] = np.median(sample) if sample.size else np.median(gray)
    labels,provenance = run_cellpose_labels(gray,diameter=float(settings.get('model_diameter_px',30)),
                                          min_size=int(settings.get('model_min_size_px',60)), **inference_options)
    result=measurements_from_labels(image, labels, um_per_px, settings, roi_mask, excluded)
    result['cellpose']=provenance
    return result
