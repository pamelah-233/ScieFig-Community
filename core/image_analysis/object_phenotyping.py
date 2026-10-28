"""Object-level signal classes and count ratios, independent of segmentation models."""
from collections import Counter
import numpy as np
from scipy import ndimage as ndi


def validated_labels(labels):
    array = np.asarray(labels)
    if array.ndim != 2 or not array.size or array.dtype.kind not in 'iu' or np.any(array < 0):
        raise ValueError('Object labels must be a nonnegative integer 2D image.')
    return array


def classify_object_signal(labels, signal, *, positive_threshold, strong_threshold,
                           background=0., roi_mask=None, excluded_ids=(),
                           region='object', ring_width_px=5., include_border=False):
    """Fixed native-intensity cutoffs; a nuclear ring excludes every nucleus.

    Unknown/empty measurement regions are reported for review. Thresholds are
    supplied by the caller and never estimated separately for each image.
    """
    labels = validated_labels(labels)
    signal = np.asarray(signal)
    limits = np.asarray([positive_threshold, strong_threshold, background, ring_width_px], float)
    if (signal.shape != labels.shape or signal.dtype.kind not in 'uif' or
            not np.isfinite(signal).all() or not np.isfinite(limits).all()):
        raise ValueError('Signal and finite thresholds must match the label image.')
    if positive_threshold < 0 or strong_threshold <= positive_threshold:
        raise ValueError('Strong signal cutoff must exceed the nonnegative positive cutoff.')
    if region not in ('object', 'perinuclear_ring') or ring_width_px <= 0:
        raise ValueError('Choose an object or a positive-width perinuclear ring.')
    roi = np.ones(labels.shape, bool) if roi_mask is None else np.asarray(roi_mask, bool)
    if roi.shape != labels.shape:
        raise ValueError('ROI must match the label image.')
    identities = np.unique(labels[labels > 0])
    excluded = set(excluded_ids)
    regions = labels.copy()
    if region == 'perinuclear_ring' and len(identities):
        distance, nearest = ndi.distance_transform_edt(labels == 0, return_indices=True)
        regions = labels[tuple(nearest)].copy()
        regions[(labels > 0) | (distance > ring_width_px)] = 0
    regions[~roi] = 0
    rows = []
    for identifier in identities:
        identifier = int(identifier)
        yy, xx = np.nonzero(labels == identifier)
        edge = (yy.min() == 0 or xx.min() == 0 or yy.max() == labels.shape[0]-1 or
                xx.max() == labels.shape[1]-1 or not roi[yy, xx].all())
        values = signal[regions == identifier]
        included = identifier not in excluded and (include_border or not edge) and bool(roi[yy, xx].any())
        mean = float(values.mean()) if values.size else None
        corrected = mean-float(background) if mean is not None else None
        category = ('unresolved' if corrected is None else
                    'strong_positive' if corrected >= strong_threshold else
                    'weak_positive' if corrected > positive_threshold else 'negative')
        rows.append(dict(object_id=identifier, area_px=int(len(xx)), signal_area_px=int(values.size),
                         mean_signal=mean, background_corrected_mean=corrected,
                         signal_class=category, border_truncated=bool(edge),
                         include_in_statistics=bool(included), review_required=corrected is None))
    eligible = [r for r in rows if r['include_in_statistics']]
    counts = Counter(r['signal_class'] for r in eligible)
    positives = counts['weak_positive']+counts['strong_positive']
    unresolved = counts['unresolved']
    ratio = count_ratio(positives, len(eligible), subset=True)
    return dict(objects=rows, summary=dict(total_objects=len(rows), eligible_objects=len(eligible),
                negative=counts['negative'], weak_positive=counts['weak_positive'],
                strong_positive=counts['strong_positive'], unresolved=unresolved,
                positive_objects=positives, positive_percent=ratio['percent'] if not unresolved else None,
                provisional_positive_percent=ratio['percent'], excluded_objects=len(rows)-len(eligible)),
                parameters=dict(positive_threshold=float(positive_threshold), strong_threshold=float(strong_threshold),
                    background=float(background), statistic='mean', region=region, ring_width_px=float(ring_width_px),
                    include_border=bool(include_border), threshold_scope='fixed_batch_values'),
                classification_version='object_signal_v1')


def count_ratio(numerator, denominator, *, subset=False):
    """A zero denominator is undefined; independent counts may exceed 100%."""
    values = (numerator, denominator)
    if any(isinstance(v, (bool, np.bool_)) or not np.isfinite(v) or v < 0 or int(v) != v for v in values):
        raise ValueError('Counts must be finite nonnegative integers.')
    if subset and numerator > denominator:
        raise ValueError('A positive-object subset cannot exceed its denominator.')
    return dict(numerator=int(numerator), denominator=int(denominator),
                fraction=float(numerator/denominator) if denominator else None,
                percent=float(100*numerator/denominator) if denominator else None,
                interpretation='positive_subset' if subset else 'independent_counts',
                status='ok' if denominator else 'empty_denominator')

