"""Per-myotube nuclear occupancy and fusion index from independently reviewed masks."""
from collections import Counter
import numpy as np
from scipy import ndimage as ndi
from .nuclei_counting import object_rows
from .object_phenotyping import validated_labels, count_ratio


def calculate_fusion_index(fiber_labels, nuclear_labels, *, min_nuclei=2,
                           minimum_overlap=.5, competing_overlap=.15, roi_mask=None,
                           excluded_fiber_ids=(), excluded_nuclear_ids=(),
                           review_fiber_ids=(), edge_policy='exclude'):
    """One nucleus has at most one owner. Unresolved ownership remains explicit.

    With edge_policy='exclude', nuclei touching an excluded edge fiber are
    removed from BOTH numerator and denominator. 'visible' includes visible
    in-field counts and flags incomplete fibers in the output table.
    """
    fibers = validated_labels(fiber_labels)
    nuclei = validated_labels(nuclear_labels)
    if fibers.shape != nuclei.shape:
        raise ValueError('Nuclear and fiber labels must share native coordinates.')
    if isinstance(min_nuclei, bool) or int(min_nuclei) != min_nuclei or min_nuclei < 2:
        raise ValueError('A multinucleated myotube requires at least two nuclei.')
    if not np.isfinite([minimum_overlap, competing_overlap]).all() or not 0 < minimum_overlap <= 1 or not 0 < competing_overlap < minimum_overlap:
        raise ValueError('Invalid nuclear overlap thresholds.')
    if edge_policy not in ('exclude', 'visible'):
        raise ValueError('Unknown edge policy.')
    roi = np.ones(fibers.shape, bool) if roi_mask is None else np.asarray(roi_mask, bool)
    if roi.shape != fibers.shape:
        raise ValueError('ROI must match the native image.')
    fiber_rows = object_rows(fibers, np.zeros(fibers.shape, np.uint8))
    nuclear_rows = object_rows(nuclei, np.zeros(nuclei.shape, np.uint8))
    excluded_fibers = set(excluded_fiber_ids)
    review_fibers = set(review_fiber_ids)
    lookup = {}
    for row in fiber_rows:
        identifier = row.pop('object_id'); pixels = fibers == identifier
        row.pop('mean_raw_signal', None)
        truncated = bool(row['border_truncated'] or (pixels & ~roi).any())
        excluded = identifier in excluded_fibers or not (pixels & roi).any() or (truncated and edge_policy == 'exclude')
        row.update(fiber_id=identifier, border_truncated=truncated, include_in_statistics=not excluded,
                   nuclei_ids=[], nuclei_count=0, review_required=identifier in review_fibers,
                   uncertain_nuclear_ids=[])
        lookup[identifier] = row
    excluded_nuclei = set(excluded_nuclear_ids)
    slices = ndi.find_objects(nuclei)
    for row in nuclear_rows:
        identifier = row['object_id']; crop = slices[identifier-1]
        mask = nuclei[crop] == identifier
        row.pop('mean_raw_signal', None)
        ids, hits = np.unique(fibers[crop][mask], return_counts=True)
        overlaps = sorted([(int(i), int(n)/row['area_px']) for i, n in zip(ids, hits) if i], key=lambda p:p[1], reverse=True)
        candidates = [i for i, _ in overlaps]
        owner, fraction = overlaps[0] if overlaps else (None, 0.)
        competing = len(overlaps) > 1 and overlaps[1][1] >= competing_overlap
        source_excluded = (identifier in excluded_nuclei or not roi[crop][mask].all() or
                           (row['border_truncated'] and edge_policy == 'exclude'))
        touches_excluded = any(not lookup[i]['include_in_statistics'] for i in candidates)
        ambiguous = (bool(candidates) and (fraction < minimum_overlap or competing or
                     any(i in review_fibers for i in candidates)))
        if source_excluded or touches_excluded:
            category, owner = 'excluded', None
        elif ambiguous:
            category, owner = 'uncertain', None
            for candidate in candidates:
                lookup[candidate]['uncertain_nuclear_ids'].append(identifier)
                lookup[candidate]['review_required'] = True
        elif owner is None:
            category = 'outside'
        else:
            category = 'inside'
            lookup[owner]['nuclei_ids'].append(identifier)
        row.update(fiber_id=owner, candidate_fiber_ids=candidates, overlap_fraction=float(fraction),
                   assignment=category, include_in_statistics=category != 'excluded')
    eligible_nuclei = sum(r['include_in_statistics'] for r in nuclear_rows)
    fused_nuclei = 0
    for row in fiber_rows:
        row['nuclei_count'] = len(row['nuclei_ids'])
        row['multinucleated'] = bool(row['nuclei_count'] >= min_nuclei)
        row['included_in_fusion_numerator'] = bool(row['include_in_statistics'] and
                                                  not row['review_required'] and row['multinucleated'])
        if row['included_in_fusion_numerator']:
            fused_nuclei += row['nuclei_count']
    reliable = [r for r in fiber_rows if r['include_in_statistics'] and not r['review_required']]
    bins = Counter(r['nuclei_count'] for r in reliable)
    uncertain = sum(r['assignment'] == 'uncertain' for r in nuclear_rows)
    ratio = count_ratio(fused_nuclei, eligible_nuclei, subset=True)
    review = any(r['include_in_statistics'] and r['review_required'] for r in fiber_rows) or bool(uncertain)
    summary = dict(total_nuclei=len(nuclear_rows), eligible_nuclei=eligible_nuclei,
                   excluded_nuclei=len(nuclear_rows)-eligible_nuclei, fused_nuclei=fused_nuclei,
                   outside_nuclei=sum(r['assignment']=='outside' for r in nuclear_rows), uncertain_nuclei=uncertain,
                   fusion_index_percent=None if review else ratio['percent'], provisional_percent=ratio['percent'],
                   review_required=review, status='review_required' if review else ratio['status'],
                   total_fibers=len(fiber_rows), eligible_fibers=len(reliable),
                   multinucleated_fibers=sum(r['included_in_fusion_numerator'] for r in fiber_rows),
                   nuclei_per_fiber=[dict(nuclei_count=n, fiber_count=bins[n]) for n in range(max(bins, default=0)+1)])
    return dict(fibers=fiber_rows, nuclei=nuclear_rows, summary=summary,
                parameters=dict(min_nuclei=int(min_nuclei), minimum_overlap=float(minimum_overlap),
                                competing_overlap=float(competing_overlap), edge_policy=edge_policy),
                analysis_version='fusion_index_v1')

