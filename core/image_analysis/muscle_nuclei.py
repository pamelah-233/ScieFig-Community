"""Per-fiber central-nucleus distribution with explicit review/exclusion classes."""
from collections import Counter
from copy import deepcopy
import cv2
import numpy as np
from scipy import ndimage as ndi
from .nuclei_counting import object_rows


def summarize_fiber_nuclei(fibers, nuclei):
    lookup = {row['fiber_id']: row for row in fibers}
    for row in fibers:
        row.update(central_nuclei=0, peripheral_nuclei=0, uncertain_nuclei=0, uncertain_central_nuclei=0)
    classes = Counter()
    for nucleus in nuclei:
        category = nucleus['classification'] if nucleus.get('include_in_statistics', True) else 'excluded'
        classes[category] += 1
        fiber = lookup.get(nucleus.get('fiber_id'))
        if category in ('central', 'peripheral') and fiber is not None:
            fiber[category+'_nuclei'] += 1
        elif category == 'uncertain':
            for identifier in nucleus.get('candidate_fiber_ids', []):
                if identifier in lookup:
                    lookup[identifier]['uncertain_nuclei'] += 1
            for identifier in nucleus.get('central_candidate_fiber_ids', nucleus.get('candidate_fiber_ids', [])):
                if identifier in lookup:
                    lookup[identifier]['uncertain_central_nuclei'] += 1
    reliable = [row for row in fibers if row.get('include_in_statistics', True) and not row['uncertain_central_nuclei']]
    bins = Counter(row['central_nuclei'] for row in reliable)
    distribution = [dict(central_nuclei=n, fiber_count=bins[n], percentage=100*bins[n]/max(1, len(reliable)))
                    for n in range(max(bins, default=0)+1)]
    return dict(total_fibers=len(fibers), included_fibers=len(reliable),
                excluded_edge_fibers=sum(row.get('border_truncated', False) and not row.get('include_in_statistics', True) for row in fibers),
                excluded_manual_fibers=sum(not row.get('border_truncated', False) and not row.get('include_in_statistics', True) for row in fibers),
                review_fibers=sum(row['uncertain_central_nuclei'] > 0 and row.get('include_in_statistics', True) for row in fibers),
                peripheral_review_fibers=sum(row['uncertain_nuclei'] > 0 and not row['uncertain_central_nuclei'] and row.get('include_in_statistics', True) for row in fibers),
                central_nuclei=sum(row['central_nuclei'] for row in reliable),
                peripheral_nuclei=sum(row['peripheral_nuclei'] for row in reliable),
                observed_central_nuclei=classes['central'], observed_peripheral_nuclei=classes['peripheral'],
                outside_nuclei=classes['outside'], uncertain_nuclei=classes['uncertain'],
                excluded_nuclei=classes['excluded'],
                centrally_nucleated_fibers=sum(row['central_nuclei'] > 0 for row in reliable),
                distribution=distribution)


def classify_fiber_nuclei(fiber_labels, nuclear_labels=None, *, nuclei=None, peripheral_fraction=.2):
    fibers = np.asarray(fiber_labels, np.int32).copy()
    labels = None if nuclear_labels is None else np.asarray(nuclear_labels, np.int32)
    if fibers.ndim != 2 or (labels is not None and fibers.shape != labels.shape) or not 0 < peripheral_fraction < 1:
        raise ValueError('Fiber/nuclear shapes or peripheral band are invalid.')
    if (labels is None) == (nuclei is None):
        raise ValueError('Provide either nuclear labels or saved nuclear rows.')
    fiber_rows = object_rows(fibers, fibers)
    distances = np.zeros(fibers.shape, np.float32)
    radii = {}
    for row, slices in zip(fiber_rows, [s for s in ndi.find_objects(fibers) if s is not None]):
        identifier = row.pop('object_id')
        row['fiber_id'] = identifier
        mask = ndi.binary_fill_holes(fibers[slices] == identifier)
        # Restore nuclei-shaped holes, but never overwrite a neighboring fiber.
        mask &= (fibers[slices] == 0) | (fibers[slices] == identifier)
        fibers[slices][mask] = identifier
        distance = ndi.distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1]
        distances[slices][mask] = distance[mask]
        radii[identifier] = float(distance.max())
    outside_distance, nearest_indices = ndi.distance_transform_edt(fibers == 0, return_indices=True)
    nearest_fiber = fibers[tuple(nearest_indices)] if radii else np.zeros_like(fibers)
    nuclei = object_rows(labels, labels) if nuclei is None else deepcopy(nuclei)
    for row in nuclei:
        contour = np.asarray(row['contour'], np.int32).reshape(-1, 2)
        runs = row.get('review_mask_runs') if labels is None else None
        left, top = np.maximum(contour.min(axis=0), 0)
        right, bottom = np.minimum(contour.max(axis=0)+1, fibers.shape[::-1])
        if runs:
            top = max(0, min(int(r[0]) for r in runs))
            bottom = min(fibers.shape[0], max(int(r[0]) for r in runs)+1)
            left = max(0, min(int(r[1]) for r in runs))
            right = min(fibers.shape[1], max(int(r[2]) for r in runs))
        slices = np.s_[top:bottom, left:right]
        if labels is not None:
            pixels = labels[slices] == row['object_id']
        elif runs:
            pixels = np.zeros((bottom-top, right-left), bool)
            for y, start, stop in runs:
                if top <= y < bottom:
                    pixels[int(y)-top, max(0,int(start)-left):min(right-left,int(stop)-left)] = True
        else:
            pixels = np.zeros((bottom-top, right-left), np.uint8)
            cv2.fillPoly(pixels, [contour-[left, top]], 1)
            pixels = pixels.astype(bool)
        pixel_count = max(1, int(pixels.sum()))
        identifiers, counts = np.unique(fibers[slices][pixels], return_counts=True)
        overlap = sorted([(int(i), int(n)) for i, n in zip(identifiers, counts) if i], key=lambda pair: -pair[1])
        candidates = [identifier for identifier, n in overlap if n/pixel_count >= .15]
        identifier = overlap[0][0] if overlap else None
        fraction = overlap[0][1]/pixel_count if overlap else 0.
        # A nucleus partly outside one clear edge may still be peripheral. Pure
        # proximity cannot establish myonuclear identity: keep those cases for review.
        if fraction < .5:
            radius = max(2., np.sqrt(pixel_count/np.pi)*.5)
            nearby = pixels & (outside_distance[slices] <= radius)
            candidates = sorted(set(candidates) | (set(np.unique(nearest_fiber[slices][nearby])) - {0}))
        ambiguous = len(candidates) > 1 or (identifier is not None and fraction < .15)
        y = int(np.clip(round(row['center_y_px']), 0, fibers.shape[0]-1))
        x = int(np.clip(round(row['center_x_px']), 0, fibers.shape[1]-1))
        inside = distances[y, x]/max(radii.get(identifier, 1), 1)
        body_distances = np.where(fibers[slices][pixels] == identifier, distances[slices][pixels], 0.)
        edge_distance = float(np.percentile(body_distances, 10)) if body_distances.size else 0.
        edge_ratio = edge_distance/max(radii.get(identifier, 1), 1)
        if ambiguous or (identifier is None and candidates):
            category, reason = 'uncertain', 'multiple_fibers' if len(candidates) > 1 else 'near_boundary_no_owner'
            identifier = None
        elif identifier is None:
            category, reason = 'outside', 'outside_fibers'
        elif fibers[y, x] == identifier and edge_ratio > peripheral_fraction:
            category, reason = 'central', 'nuclear_body_inside'
        else:
            category, reason = 'peripheral', 'nuclear_body_at_boundary'
        if (row['border_truncated'] and not row.get('reviewed', False)) or not row.get('include_in_statistics', True):
            category, reason = 'excluded', 'source_excluded'
        central_candidates = []
        if category == 'uncertain':
            for candidate in candidates:
                owned = pixels & (fibers[slices] == candidate)
                values = distances[slices][owned]
                if values.size and np.percentile(values, 90) > peripheral_fraction*max(radii[candidate], 1):
                    central_candidates.append(int(candidate))
        row.update(fiber_id=identifier, candidate_fiber_ids=[int(i) for i in candidates],
                   central_candidate_fiber_ids=central_candidates,
                   classification=category, automatic_classification=category,
                   overlap_fraction=fraction, normalized_border_distance=float(inside),
                   nuclear_edge_distance_px=edge_distance, normalized_nuclear_edge_distance=edge_ratio,
                   assignment_reason=reason, automatic_assignment_reason=reason)
    summary = summarize_fiber_nuclei(fiber_rows, nuclei)
    return dict(fibers=fiber_rows, nuclei=nuclei, summary=summary,
                classification_version='muscle_body_distance_v2',
                parameters=dict(peripheral_fraction=peripheral_fraction, nuclear_edge_percentile=10,
                    minimum_overlap_fraction=.15, border_candidate_radius_fraction=.5,
                    minimum_border_candidate_distance_px=2.))


def nuclear_overlay(rgb, result):
    output = np.asarray(rgb, np.uint8).copy()
    for row in result.get('fibers', []):
        contour = np.asarray(row['contour'], np.int32).reshape(-1, 1, 2)
        color = (90, 220, 160) if row.get('include_in_statistics', True) and not row.get('uncertain_central_nuclei', row.get('uncertain_nuclei')) else (150, 150, 150)
        cv2.drawContours(output, [contour], -1, color, 1)
        origin = (round(row['center_x_px']), round(row['center_y_px']))
        cv2.putText(output, f"{row['fiber_id']}:{row['central_nuclei']}", origin, cv2.FONT_HERSHEY_SIMPLEX, .4, (20, 20, 20), 2)
        cv2.putText(output, f"{row['fiber_id']}:{row['central_nuclei']}", origin, cv2.FONT_HERSHEY_SIMPLEX, .4, (255, 255, 255), 1)
    colors = dict(central=(20, 240, 80), peripheral=(255, 190, 20), outside=(70, 180, 255), uncertain=(255, 60, 200), excluded=(150, 150, 150))
    for row in result.get('nuclei', []):
        color = colors.get(row.get('classification'), (40, 255, 80))
        if row.get('classification')!='excluded' and not result.get('signal_classification',{}).get('stale'):
            color=dict(negative=(160,160,160),weak_positive=(255,190,20),strong_positive=(40,255,80)).get(row.get('signal_class'),color)
        cv2.drawContours(output, [np.asarray(row['contour'], np.int32).reshape(-1, 1, 2)], -1, color, 1)
    return output
