"""Channel-agnostic fluorescent spot separation, without biological labels."""
import numpy as np
from scipy import ndimage as ndi
from skimage.filters import threshold_otsu, threshold_li, threshold_triangle
from skimage.measure import regionprops
from skimage.morphology import h_maxima
from skimage.segmentation import watershed


def segment_spots(channel, threshold, min_area=5, *, automatic=False, threshold_method='otsu'):
    source = np.asarray(channel, np.float32)
    if automatic:
        # Estimate denoising scale from dominant foreground objects, rather than
        # treating weak within-object granules as separate point-sized signals.
        initial, _ = ndi.label(source > threshold_otsu(source))
        sizes = np.bincount(initial.ravel())[1:]
        sizes = np.sort(sizes[sizes >= max(1,int(min_area))])
        typical = sizes[np.searchsorted(np.cumsum(sizes), sizes.sum()*.5)] if sizes.size else min_area
        sigma = float(np.clip(.25*np.sqrt(typical/np.pi), .7, 2.5))
        smooth = ndi.gaussian_filter(source, sigma)
        signal = np.maximum(smooth - ndi.gaussian_filter(smooth, 40), 0)
        median = float(np.median(signal))
        noise = 1.4826 * float(np.median(np.abs(signal - median)))
        estimator = {'otsu':threshold_otsu, 'li':threshold_li, 'triangle':threshold_triangle}[threshold_method]
        threshold = max(float(estimator(signal))*.15, median+5*noise, float(signal.max())*.015)
    else:
        # Manual thresholds and sampled intensities stay in original channel units.
        signal = source
    mask = signal > threshold
    closing_size = 2*int(np.ceil(sigma))+1 if automatic else 3
    mask = ndi.binary_fill_holes(ndi.binary_closing(mask, structure=np.ones((closing_size,closing_size))))
    components, _ = ndi.label(mask)
    areas = np.bincount(components.ravel())
    mask &= areas[components] >= max(1, int(min_area))
    distance = ndi.gaussian_filter(ndi.distance_transform_edt(mask), 1)
    peaks = h_maxima(distance, 1.5) & mask
    # A small but valid isolated spot still needs a seed.
    components, _ = ndi.label(mask)
    for index, slices in enumerate(ndi.find_objects(components), 1):
        if slices is None:
            continue
        local = components[slices] == index
        if not np.any(peaks[slices] & local):
            y,x = np.unravel_index(np.argmax(np.where(local, distance[slices], -1)),local.shape)
            peaks[slices[0].start+y,slices[1].start+x] = True
    markers, _ = ndi.label(peaks)
    labels = watershed(-distance, markers, mask=mask)
    return labels, float(threshold)


def sample_spot_profile(channel, sample_mask):
    values = np.asarray(channel)[sample_mask]
    if values.size == 0 or float(values.max()) <= float(np.percentile(channel,25)):
        raise ValueError('Please draw the sample box around one visible fluorescent spot.')
    cut = float(threshold_otsu(values)) if float(values.max()) > float(values.min()) else float(values.min())*.5
    labels, _ = ndi.label((channel > cut) & sample_mask)
    objects = regionprops(labels, intensity_image=channel)
    chosen = max(objects, key=lambda r:r.area)
    return int(chosen.area), float(chosen.intensity_mean)
