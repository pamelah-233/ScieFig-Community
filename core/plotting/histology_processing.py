"""Image-local illumination correction and explicit histology analysis inputs."""
from copy import deepcopy

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, gaussian_filter1d

from core.plotting.histology_display import apply_white_balance, qimage_rgb
from PyQt6.QtGui import QImage


ILLUMINATION_METHOD = 'background_and_veil_v2'


def _estimate_central_veil(dark, width, height, rows, columns):
    """Estimate the excess dark level only where the rise spans most sectors."""
    short_side = min(width, height)
    sample_height, sample_width = dark.shape
    y, x = np.ogrid[:sample_height, :sample_width]
    dx = ((x + .5) / sample_width - .5) * width / short_side
    dy = ((y + .5) / sample_height - .5) * height / short_side
    band_width = .033
    bands = (np.hypot(dx, dy) / band_width).astype(int)
    sectors = ((np.arctan2(dy, dx) + np.pi) * 4 / np.pi).astype(int) % 8
    low = np.full((11, 8), np.nan)
    high = low.copy()
    for band in range(11):
        for sector in range(8):
            values = dark[(bands == band) & (sectors == sector)]
            if values.size >= 12:
                dark_level, bright_level = np.percentile(values, (8, 90))
                if bright_level - dark_level >= 35:
                    low[band, sector] = dark_level
                    high[band, sector] = bright_level

    # Compare each direction to its own outer reference to limit tissue bias.
    supported = np.count_nonzero(np.isfinite(low[7:]), axis=0) >= 2
    if np.count_nonzero(supported) < 6:
        return np.zeros((rows, columns), dtype=np.float32)
    low, high = low[:, supported], high[:, supported]
    outer_low = np.nanmedian(low[7:], axis=0)
    outer_high = np.nanmedian(high[7:], axis=0)
    excess = low - outer_low * np.clip(high / outer_high, .85, 1.35)
    profile = np.zeros(len(low))
    for band, ring in enumerate(excess[:7]):
        if np.count_nonzero(ring > 3) >= 6:
            profile[band] = np.clip(np.nanpercentile(ring, 30), 0, 70)
    if profile[:4].max() < 8:
        return np.zeros((rows, columns), dtype=np.float32)
    if np.count_nonzero(np.isfinite(excess[0])) < 6:
        profile[0] = profile[1]
    profile = gaussian_filter1d(profile, .8, mode='nearest')
    y, x = np.ogrid[:rows, :columns]
    radius = np.hypot(((x + .5) / columns - .5) * width,
                      ((y + .5) / rows - .5) * height) / short_side
    return np.interp(radius, (np.arange(len(profile)) + .5) * band_width, profile, right=0).astype(np.float32)


def estimate_illumination(rgb):
    image = np.asarray(rgb)
    if image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 32:
        raise ValueError('Choose a brightfield RGB image at least 32 pixels wide and high.')
    height, width = image.shape[:2]
    rows, columns = min(32, height // 16), min(40, width // 16)
    step = max(1, min(height, width) // 360)
    sampled = image[::step, ::step]
    dark = sampled.min(axis=2)
    sample_height, sample_width = dark.shape
    minimum = max(70, np.percentile(dark, 85) - 48)
    background = np.zeros((rows, columns, 3), dtype=np.float32)
    weights = np.zeros((rows, columns), dtype=np.float32)
    for row in range(rows):
        y = slice(row * sample_height // rows, (row + 1) * sample_height // rows)
        for column in range(columns):
            x = slice(column * sample_width // columns, (column + 1) * sample_width // columns)
            brightness = dark[y, x]
            mask = brightness >= max(minimum, np.percentile(brightness, 90))
            if np.count_nonzero(mask) >= 3:
                background[row, column] = np.median(sampled[y, x][mask], axis=0)
                weights[row, column] = min(1, mask.mean() / .08)
    valid = weights > 0
    if valid.mean() < .25:
        raise ValueError('Too little bright background to estimate illumination. Choose a field with unstained gaps.')
    reference = np.median(background[valid], axis=0)
    weights *= np.clip((background.min(axis=2) - minimum) / 18, .15, 1)
    support = gaussian_filter(weights, 1.35)[:, :, None]
    field = gaussian_filter(background * weights[:, :, None], (1.35, 1.35, 0))
    field /= np.maximum(support, 1e-6)
    # Fade poorly supported areas towards a neutral gain instead of copying a distant tile.
    field = reference + (field - reference) * np.clip(support / .45, 0, 1)
    veil = _estimate_central_veil(dark, width, height, rows, columns)
    gains = np.clip(reference / np.maximum(field - veil[:, :, None], 20), .65, 1.55)
    return {'method': ILLUMINATION_METHOD, 'enabled': True, 'strength': 100,
            'gains': gains.tolist(), 'veil': veil.tolist(), 'source_size': [width, height]}


def illumination_parameters(correction):
    """Return validated processing data, including for saved flat-field-only corrections."""
    if not isinstance(correction, dict) or 'gains' not in correction:
        return None
    try:
        gains = np.asarray(correction['gains'], dtype=np.float32)
        strength = float(correction.get('strength', 100)) / 100
        veil_data = correction.get('veil')
        veil = np.asarray(veil_data, dtype=np.float32) if veil_data is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
    if (gains.ndim != 3 or gains.shape[2] != 3 or not gains.size
            or not np.isfinite(gains).all() or np.any(gains <= 0) or not 0 <= strength <= 1):
        return None
    if correction.get('method') == ILLUMINATION_METHOD and veil is None:
        return None
    if veil is not None and (veil.shape != gains.shape[:2] or not np.isfinite(veil).all()
                             or np.any(veil < 0) or np.any(veil >= 255)):
        return None
    return gains, veil, strength


def apply_histology_base(rgb, record):
    image = np.asarray(rgb)
    correction = record.get('illumination_correction')
    parameters = illumination_parameters(correction) if isinstance(correction, dict) and correction.get('enabled') else None
    if parameters and parameters[2] > 0:
        gains, veil, strength = parameters
        height, width = image.shape[:2]
        size = (width, height)
        offset = 0
        if veil is not None and veil.any():
            offset = np.asarray(Image.fromarray(veil).resize(size, Image.Resampling.BILINEAR))
        output = np.empty_like(image)
        for channel in range(3):
            field = np.asarray(Image.fromarray(gains[:, :, channel]).resize(
                size, Image.Resampling.BILINEAR))
            original = image[:, :, channel].astype(np.float32)
            adjusted = original * (1 + strength * (field - 1)) - strength * offset * field
            output[:, :, channel] = np.clip(np.rint(adjusted), 0, 255)
        image = output
    return apply_white_balance(image, record.get('display_white_balance'))


def apply_histology_processing(rgb, record):
    image = apply_histology_base(rgb, record)
    if record.get('rgb_display'):
        from core.plotting.fluoro_display import apply_display_optimization, display_points_active
        settings = record['rgb_display']
        low, high = float(settings.get('display_min',0)), float(settings.get('display_max',255))
        processing = settings.get('display_processing')
        output = np.empty_like(image)
        for channel in range(3):
            normalized = (image[:,:,channel].astype(np.float32) - low) / max(1,high-low)
            if not display_points_active(processing):
                normalized = np.clip(normalized,0,1)
            adjusted = apply_display_optimization(normalized,processing)
            adjusted = (adjusted - .5) * (1 + settings.get('contrast',0)/100) + .5 + settings.get('brightness',0)/100
            output[:,:,channel] = np.rint(np.clip(adjusted,0,1)*255).astype(np.uint8)
        image = output
    return image


def processed_histology_qimage(image, record):
    correction = record.get('illumination_correction')
    if (not record.get('display_white_balance') and not record.get('rgb_display')
            and not (isinstance(correction, dict) and correction.get('enabled'))):
        return image.copy()
    rgb = np.ascontiguousarray(apply_histology_processing(qimage_rgb(image), record))
    return QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()


def histology_input_context(record):
    if record.get('segmentation_input', 'raw') != 'adjusted':
        return {}
    correction = record.get('illumination_correction')
    illumination = (deepcopy(correction) if illumination_parameters(correction) and correction.get('enabled') else None)
    return {'mode': 'adjusted', 'white_balance': deepcopy(record.get('display_white_balance')),
            'illumination': illumination,
            'rgb_display': deepcopy(record.get('rgb_display'))}
