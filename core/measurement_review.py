"""Staged, image-preserving corrections of saved microscopy object records.

Edited areas are counts of mask pixels. Split partitions the same pixels and
merge takes their union; neither operation manufactures a segmentation result.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite, pi, sqrt

import cv2
import numpy as np
from scipy import ndimage
from skimage.draw import polygon
from skimage.measure import find_contours

from core.measurement_scale import apply_area_scale, apply_length_scale


def object_rows(record):
    """Return the native object-list location; never synthesize aggregate objects."""
    for key in ('object_table', 'detected_spots'):
        if isinstance(record.get(key), list):
            return (key,), record[key]
    if isinstance(record.get('results'), list):
        return ('results',), record['results']
    results = record.get('results', {})
    if isinstance(results, dict):
        for key in ('object_table', 'objects', 'nuclei'):
            if isinstance(results.get(key), list):
                return ('results', key), results[key]
    raise ValueError('This is an aggregate-only measurement; no per-object geometry or nuclei were saved.')


def _points(values):
    if values is None:
        return []
    return [[float(p['x']), float(p['y'])] if isinstance(p, dict)
            else [float(p[0]), float(p[1])] for p in values]


def _saved_calibration(record):
    review = record.get('object_review', {})
    if 'calibration_snapshot' in review:
        return deepcopy(review['calibration_snapshot'])
    metadata = record.get('analysis_metadata', {})
    for candidate in (record.get('measurement_scale_bar'),
                      metadata.get('measurement_scale_bar'), metadata.get('calibration_snapshot')):
        if isinstance(candidate, dict) and any(k in candidate for k in ('um_per_px', 'um_per_px_x', 'um_per_px_y')):
            return deepcopy(candidate)
    return {}


def _finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and isfinite(value)


def _max_feret(points):
    hull = cv2.convexHull(np.asarray(points, np.float32)).reshape(-1, 2)
    best = (-1., hull[0], hull[0])
    # The hull is small; one vector at a time avoids an N x N allocation.
    for point in hull:
        distances = np.sum((hull - point) ** 2, axis=1)
        index = int(np.argmax(distances))
        if distances[index] > best[0]:
            best = (float(distances[index]), point, hull[index])
    return sqrt(best[0]), best[1].tolist(), best[2].tolist()


class ObjectReviewSession:
    """An isolated working copy with stable object IDs and reversible commands."""

    def __init__(self, record, image_shape, *, intensity_image=None):
        self.original = deepcopy(record)
        self.row_path, rows = object_rows(record)
        self.image_shape = tuple(int(value) for value in image_shape)
        if len(self.image_shape) != 2 or min(self.image_shape) < 1:
            raise ValueError('A valid image shape is required for object review.')
        self.calibration_snapshot = _saved_calibration(record)
        self.intensity_image = None if intensity_image is None else np.asarray(intensity_image)
        if self.intensity_image is not None and self.intensity_image.shape != self.image_shape:
            raise ValueError('Intensity image must match the reviewed image dimensions.')
        numeric_ids = [int(row.get('object_id', row.get('id', 0))) for row in rows
                       if str(row.get('object_id', row.get('id', ''))).isdigit()]
        self._next_id = max([0, *numeric_ids, int(record.get('object_review', {}).get('next_object_id', 1)) - 1]) + 1
        self._objects = []
        seen = set()
        for source in rows:
            row = deepcopy(source)
            identifier = row.get('object_id', row.get('id'))
            if identifier is None:
                identifier = self._new_id()
            if str(identifier) in seen:
                raise ValueError(f'duplicate object ID: {identifier}')
            seen.add(str(identifier))
            row['object_id'] = identifier
            paths = row.get('review_contours_px')
            if paths is None:
                path = _points(row.get('contour_points_px', row.get('contour_px')))
                paths = [path] if len(path) >= 3 else []
            row['review_contours_px'] = paths
            row['review_geometry_available'] = bool(paths or row.get('review_mask_runs'))
            row.setdefault('review_provenance', {'status': 'automatic', 'action': 'original'})
            self._objects.append(row)
        self._audit = deepcopy(record.get('object_review', {}).get('audit', []))
        self._undo = []
        self._redo = []
        self._allowed = self._allowed_pixels(record)

    @property
    def objects(self):
        return deepcopy(self._objects)

    def native_geometry(self):
        """Reuse reviewed pixel runs/contours for downstream object relationships."""
        labels = np.zeros(self.image_shape, np.int32)
        rows = self.objects
        overlapping = set()
        for index, row in enumerate(rows, 1):
            mask = self._row_mask(row)
            owners = set(int(v) for v in np.unique(labels[mask]) if v)
            if owners:
                overlapping.update(owners); overlapping.add(index)
            labels[mask & (labels == 0)] = index
            row['label_id'] = index
        return dict(labels=labels, objects=rows, overlapping_ids=sorted(overlapping),
                    roi_mask=self._allowed.copy())

    @property
    def measurement_scope(self):
        return self._allowed.copy()

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    @property
    def changed(self):
        return bool(self._undo)

    def object(self, identifier):
        for row in self._objects:
            if str(row['object_id']) == str(identifier):
                return deepcopy(row)
        raise ValueError(f'Object ID not found: {identifier}')

    def _new_id(self):
        identifier = self._next_id
        self._next_id += 1
        return identifier

    def _allowed_pixels(self, record):
        roi = record.get('roi_or_geometry') or record.get('roi') or {}
        if record.get('type') == 'spot_count' and record.get('method') == 'sample_like':
            roi = {}  # This ROI describes the sample reference, not the counting scope.
        if roi.get('items') and roi.get('mode') != 'whole_image':
            roi = next((item for item in roi['items'] if item.get('roi_uuid') == roi.get('active_item_uuid')), roi['items'][-1])
        kind = roi.get('type')
        coords = roi.get('coordinates_px', {})
        allowed = np.ones(self.image_shape, dtype=bool)
        if kind == 'mask' and not roi.get('reconstructable', False):
            # Deletion remains possible, but spatial edits cannot escape an unknown ROI.
            allowed[:] = False
        elif kind == 'rectangle' and coords:
            allowed[:] = False
            x, y = int(coords['x']), int(coords['y'])
            allowed[max(0, y):max(0, y + int(coords['h'])), max(0, x):max(0, x + int(coords['w']))] = True
        elif kind in ('polygon', 'ellipse', 'circle') and coords:
            allowed[:] = False
            if kind == 'polygon':
                pts = _points(coords.get('points', []))
                if len(pts) >= 3:
                    rr, cc = polygon(np.asarray(pts)[:, 1], np.asarray(pts)[:, 0], self.image_shape)
                    allowed[rr, cc] = True
            else:
                yy, xx = np.ogrid[:self.image_shape[0], :self.image_shape[1]]
                x, y, w, h = (float(coords[key]) for key in ('x', 'y', 'w', 'h'))
                if w > 0 and h > 0:
                    allowed = ((xx - x - w / 2) / (w / 2)) ** 2 + ((yy - y - h / 2) / (h / 2)) ** 2 <= 1
        for region in record.get('analysis', {}).get('excluded_regions', []):
            x, y, w, h = (int(region[key]) for key in ('x', 'y', 'w', 'h'))
            allowed[max(0, y):max(0, y + h), max(0, x):max(0, x + w)] = False
        return allowed

    def _polygon_mask(self, points):
        pts = np.asarray(_points(points), dtype=float)
        if len(pts) < 3 or not np.isfinite(pts).all():
            raise ValueError('A polygon needs at least three finite vertices.')
        if (pts < 0).any() or (pts[:, 0] > self.image_shape[1] - 1).any() or (pts[:, 1] > self.image_shape[0] - 1).any():
            raise ValueError('Polygon vertices must be inside the source image.')
        if cv2.contourArea(pts.astype(np.float32)) <= 0:
            raise ValueError('Polygon must enclose a non-zero area without crossing itself.')
        # Reject crossing non-adjacent edges instead of silently accepting bow ties.
        def side(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            for j in range(i + 2, len(pts)):
                if i == 0 and j == len(pts) - 1:
                    continue
                c, d = pts[j], pts[(j + 1) % len(pts)]
                if side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0:
                    raise ValueError('Polygon edges must not cross.')
        mask = np.zeros(self.image_shape, dtype=bool)
        rr, cc = polygon(pts[:, 1], pts[:, 0], self.image_shape)
        mask[rr, cc] = True
        if not mask.any():
            raise ValueError('Polygon contains no image pixels.')
        if np.any(mask & ~self._allowed):
            raise ValueError('Polygon must stay inside the saved measurement ROI and outside excluded regions.')
        return mask

    def _row_mask(self, row):
        if not row.get('review_geometry_available'):
            raise ValueError('Saved object geometry is unavailable; draw a replacement polygon first.')
        mask = np.zeros(self.image_shape, dtype=bool)
        if 'review_mask_runs' in row:
            for y, x0, x1 in row['review_mask_runs']:
                mask[int(y), int(x0):int(x1)] = True
        else:
            for points in row['review_contours_px']:
                pts = np.asarray(_points(points), float)
                rr, cc = polygon(pts[:, 1], pts[:, 0], self.image_shape)
                mask[rr, cc] ^= True
        if not mask.any():
            raise ValueError('Saved object geometry has no image pixels.')
        return mask

    def _measured_row(self, mask, identifier, action, parents, source=None):
        row = deepcopy(source or {})
        yy, xx = np.nonzero(mask)
        y0, y1, x0, x1 = int(yy.min()), int(yy.max() + 1), int(xx.min()), int(xx.max() + 1)
        local = mask[y0:y1, x0:x1]
        paths = [np.column_stack((path[:, 1] + x0 - 1, path[:, 0] + y0 - 1)).tolist()
                 for path in find_contours(np.pad(local.astype(np.uint8), 1), .5)]
        all_points = np.concatenate([np.asarray(path) for path in paths])
        perimeter = sum(cv2.arcLength(np.asarray(path, np.float32), True) for path in paths)
        feret, start, end = _max_feret(all_points)
        _, dimensions, _ = cv2.minAreaRect(all_points.astype(np.float32))
        runs = []
        for y in range(y0, y1):
            boundaries = np.flatnonzero(np.diff(np.r_[False, mask[y], False]))
            runs.extend([[y, int(begin), int(stop)] for begin, stop in zip(boundaries[::2], boundaries[1::2])])
        area = int(mask.sum())
        sx = apply_length_scale(1, 0, self.calibration_snapshot)
        sy = apply_length_scale(0, 1, self.calibration_snapshot)
        calibrated = sx > 0 and sy > 0
        physical = all_points * np.array([sx, sy]) if calibrated else None
        physical_area = apply_area_scale(area, self.calibration_snapshot) if calibrated else None
        margin = max(0., float(self.original.get('params', {}).get('border_margin_px', 0) or 0))
        border = bool(x0 <= margin or y0 <= margin or x1 >= self.image_shape[1] - margin or y1 >= self.image_shape[0] - margin)
        row.update(
            object_id=identifier, id=identifier, area_px=area, area_um2=physical_area,
            centroid_x=float(xx.mean()), centroid_y=float(yy.mean()),
            center_x_px=float(xx.mean()), center_y_px=float(yy.mean()),
            bbox_x_min=x0, bbox_x_max=x1, bbox_y_min=y0, bbox_y_max=y1,
            x=x0, y=y0, w=x1 - x0, h=y1 - y0,
            width_um=(x1 - x0) * sx if sx else None, height_um=(y1 - y0) * sy if sy else None,
            equivalent_diameter_px=sqrt(4 * area / pi),
            equivalent_diameter_um=sqrt(4 * physical_area / pi) if calibrated else None,
            max_feret_px=feret, min_feret_px=float(min(dimensions)),
            max_feret_um=_max_feret(physical)[0] if calibrated else None,
            min_feret_um=float(min(cv2.minAreaRect(physical.astype(np.float32))[1])) if calibrated else None,
            feret_start_px=start, feret_end_px=end,
            perimeter_px=float(perimeter), circularity=min(1., 4 * pi * area / perimeter ** 2) if perimeter else 0.,
            aspect_ratio=float(max(dimensions) / max(min(dimensions), 1e-9)),
            shape_class='reviewed', detection_source='manual_review',
            border_truncated=border, include_in_statistics=not border and (
                (source or {}).get('include_in_statistics', True) or (source or {}).get('geometry_review_required', False)),
            contour_px=max(paths, key=len),
            contour_points_px=[{'x': x, 'y': y} for x, y in max(paths, key=len)],
            review_contours_px=paths, review_mask_runs=runs, review_geometry_available=True,
            area_semantics='manual_review_mask_pixels',
            review_provenance={'status': 'edited', 'action': action, 'parent_object_ids': list(parents)},
            channel_index=self.original.get('channel_index', (source or {}).get('channel_index', 0)),
        )
        # Old detector confidence/contrast no longer describes a corrected contour.
        if self.original.get('type') in ('brightfield_bright_round_cell_diameter','myotube_measurement') or 'max_width_px' in row:
            from core.image_analysis.cell_width import mask_width_metrics
            try:
                row.update(mask_width_metrics(local,sx if calibrated else 1.,sy if calibrated else 1.,offset=(x0,y0),
                    include_length=self.original.get('type')=='myotube_measurement' or 'centerline_length_px' in row))
                if not calibrated:row['max_width_um']=None
                if not calibrated and 'centerline_length_um' in row:row['centerline_length_um']=None
                if row.get('geometry_review_required'):row['include_in_statistics']=False
            except ValueError:
                row.update(max_width_px=None,max_width_um=None,width_method='requires_contour_review')
                if self.original.get('type')=='myotube_measurement' or 'centerline_length_px' in row:
                    row.update(centerline_length_px=None,centerline_length_um=None,centerline_points_px=[],length_method='requires_contour_review')
        for key in ('solidity', 'contrast_mean', 'threshold_relative_spread', 'detection_radius_px'):
            row.pop(key, None)
        if self.intensity_image is not None:
            values = self.intensity_image[mask]
            row['mean_intensity'] = float(np.mean(values))
            row['integrated_intensity'] = float(np.sum(values, dtype=np.float64))
        else:
            row['mean_intensity'] = row['integrated_intensity'] = None
        return row

    def _change(self, action, before, after):
        old = (deepcopy(self._objects), deepcopy(self._audit))
        removed = {str(row['object_id']) for row in before}
        self._objects = [row for row in self._objects if str(row['object_id']) not in removed]
        self._objects.extend(after)
        self._audit.append({'action': action, 'at': datetime.now(timezone.utc).isoformat(),
                            'before': deepcopy(before), 'after': deepcopy(after),
                            'count_before': len(old[0]), 'count_after': len(self._objects)})
        self._undo.append(old)
        self._redo.clear()

    def add_polygon(self, points):
        mask = self._polygon_mask(points)
        identifier = self._new_id()
        row = self._measured_row(mask, identifier, 'add', [])
        self._change('add', [], [row])
        return identifier

    def replace_polygon(self, identifier, points):
        source = self.object(identifier)
        row = self._measured_row(self._polygon_mask(points), source['object_id'], 'replace', [source['object_id']], source)
        self._change('replace', [source], [row])
        return source['object_id']

    def delete_objects(self, identifiers):
        before = [self.object(value) for value in dict.fromkeys(identifiers)]
        if not before:
            raise ValueError('Select at least one object to delete.')
        self._change('delete', before, [])

    def split_object(self, identifier, start, end):
        source = self.object(identifier)
        mask = self._row_mask(source)
        a, b = np.asarray(start, float), np.asarray(end, float)
        if a.shape != (2,) or b.shape != (2,) or not np.isfinite([a, b]).all() or np.array_equal(a, b):
            raise ValueError('Split requires two different finite line points.')
        yy, xx = np.nonzero(mask)
        side = (xx - a[0]) * (b[1] - a[1]) - (yy - a[1]) * (b[0] - a[0])
        if not np.any(side < 0) or not np.any(side >= 0):
            raise ValueError('The split line must cross the selected object.')
        pieces = []
        for chosen in (side < 0, side >= 0):
            part = np.zeros(self.image_shape, dtype=bool)
            part[yy[chosen], xx[chosen]] = True
            labels, count = ndimage.label(part)
            for label in range(1, count + 1):
                pieces.append(labels == label)
        after = [self._measured_row(part, self._new_id(), 'split', [source['object_id']], source) for part in pieces]
        self._change('split', [source], after)
        return [row['object_id'] for row in after]

    def merge_objects(self, identifiers):
        before = [self.object(value) for value in dict.fromkeys(identifiers)]
        if len(before) < 2:
            raise ValueError('Select at least two objects to merge.')
        union = np.zeros(self.image_shape, dtype=bool)
        for row in before:
            union |= self._row_mask(row)
        identifier = self._new_id()
        row = self._measured_row(union, identifier, 'merge', [item['object_id'] for item in before])
        self._change('merge', before, [row])
        return identifier

    def undo(self):
        if not self._undo:
            return False
        self._redo.append((deepcopy(self._objects), deepcopy(self._audit)))
        self._objects, self._audit = self._undo.pop()
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append((deepcopy(self._objects), deepcopy(self._audit)))
        self._objects, self._audit = self._redo.pop()
        return True

    def updated_record(self):
        result = deepcopy(self.original)
        target = result
        for key in self.row_path[:-1]:
            target = target[key]
        target[self.row_path[-1]] = self.objects
        result['object_review'] = {'version': 1, 'calibration_snapshot': deepcopy(self.calibration_snapshot),
                                   'audit': deepcopy(self._audit), 'next_object_id': self._next_id,
                                   'area_semantics': 'edited_objects_use_mask_pixel_count'}
        result['review_status'] = 'edited' if self._audit else 'automatic'
        self._refresh_summary(result)
        return result

    def _refresh_summary(self, result):
        rows = self._objects
        count = len(rows)
        areas = [row.get('area_px') for row in rows]
        valid_area = all(_finite(value) for value in areas)
        total = sum(areas) if valid_area else None
        mean = float(np.mean(areas)) if areas and valid_area else (0. if not rows else None)
        median = float(np.median(areas)) if areas and valid_area else (0. if not rows else None)
        sx = apply_length_scale(1, 0, self.calibration_snapshot)
        sy = apply_length_scale(0, 1, self.calibration_snapshot)
        scaled = lambda value: value * sx * sy if value is not None and sx > 0 and sy > 0 else None
        intensities = [row.get('mean_intensity') for row in rows]
        integrated = [row.get('integrated_intensity') for row in rows]
        metrics = {'mean_object_area_px': mean, 'median_object_area_px': median,
                   'mean_object_area_um2': scaled(mean), 'median_object_area_um2': scaled(median),
                   'mean_intensity': float(np.mean(intensities)) if rows and all(_finite(v) for v in intensities) else (0. if not rows else None),
                   'integrated_intensity': sum(integrated) if all(_finite(v) for v in integrated) else None}
        if self.row_path[0] == 'object_table':
            key = 'ai_object_count' if 'ai_object_count' in result else 'smart_object_count'
            result.update(metrics, **{key: count}, total_object_area_px=total, total_object_area_um2=scaled(total),
                          object_area_distribution_px=areas, object_area_distribution_um2=[scaled(value) for value in areas])
        elif self.row_path[0] == 'detected_spots':
            result.update(metrics, count=count, total_positive_area_px=total, total_positive_area_um2=scaled(total))
        elif self.row_path == ('results',):
            summary = deepcopy(result.get('summary', {}))
            included = [row for row in rows if row.get('include_in_statistics', True)]
            diameters = [row.get('equivalent_diameter_um') for row in included]
            valid_diameters = all(_finite(value) for value in diameters)
            summary.update(count=count, round_count=sum(row.get('shape_class') == 'round' for row in rows),
                           elongated_count=sum(row.get('shape_class') == 'elongated' for row in rows),
                           reviewed_count=sum(row.get('shape_class') == 'reviewed' for row in rows),
                           border_truncated_count=sum(bool(row.get('border_truncated')) for row in rows),
                           complete_elongated_count=sum(row.get('shape_class') == 'elongated' for row in included),
                           dark_elongated_count=sum(row.get('detection_source') == 'dark_background' for row in rows))
            for name, function in (('mean', np.mean), ('median', np.median), ('min', min), ('max', max)):
                summary[name + '_equivalent_diameter_um'] = float(function(diameters)) if diameters and valid_diameters else (0. if not diameters else None)
            from core.image_analysis.cell_width import update_cell_size_summary
            update_cell_size_summary(summary,rows)
            result['summary'] = summary
            result.setdefault('analysis', {})['results'] = self.objects
            result['analysis']['summary'] = deepcopy(summary)
        else:
            result['results'].update(object_count=count, reviewed_object_count=count)
