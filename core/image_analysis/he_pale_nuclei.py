"""Accepted cyto3 HE supplement: preserve base nuclei, filter only additions."""
from types import MappingProxyType
import numpy as np
from scipy import ndimage as ndi
from skimage.measure import regionprops
from .he_round_nuclei import merge_nuclear_candidates


HE_PALE_NUCLEI_V1 = MappingProxyType(dict(
    baseline_tile_px=128, supplement_tile_px=32, supplement_diameter_cap=8.,
    supplement_min_area_px=5, supplement_max_area_px=160,
    supplement_cellprob_threshold=-.5, min_solidity=.6, min_core_to_rim=1.25))


def resolve_pale_settings(overrides=None):
    if overrides is not None and set(overrides)-HE_PALE_NUCLEI_V1.keys():
        raise ValueError('Unknown H&E pale-nucleus setting.')
    settings = {**HE_PALE_NUCLEI_V1, **(overrides or {})}
    if not np.isfinite(list(settings.values())).all():
        raise ValueError('H&E pale-nucleus settings must be finite.')
    for key in ('baseline_tile_px','supplement_tile_px','supplement_min_area_px','supplement_max_area_px'):
        if settings[key] < 1 or int(settings[key]) != settings[key]:
            raise ValueError('H&E pale-nucleus pixel settings must be positive integers.')
        settings[key] = int(settings[key])
    if (settings['supplement_diameter_cap'] < 2 or settings['min_core_to_rim'] <= 0
            or not 0 <= settings['min_solidity'] <= 1
            or settings['supplement_max_area_px'] < settings['supplement_min_area_px']):
        raise ValueError('Invalid H&E pale-nucleus size or shape setting.')
    return settings


def filter_added_nuclei(baseline,supplemented,hematoxylin,*,min_solidity=.6,min_core_to_rim=1.25):
    result = supplemented.copy(); decisions = []; last_original = baseline.max()
    for region in regionprops(supplemented):
        if region.label <= last_original:
            continue
        mask = region.image
        core = ndi.binary_erosion(mask,border_value=0)
        rim = mask & ~core
        signal = hematoxylin[region.slice]
        core_mean = float(signal[core].mean()) if core.any() else 0.
        rim_mean = float(signal[rim].mean()) if rim.any() else 0.
        if region.solidity < min_solidity or not core.any():
            reason = 'fragmented_or_no_interior'
        elif core_mean <= min_core_to_rim * rim_mean:
            reason = 'no_concentrated_nuclear_core'
        else:
            reason = 'retained'
        decisions.append(dict(label=int(region.label),area=int(region.area),solidity=float(region.solidity),
            core_mean=core_mean,rim_mean=rim_mean,decision=reason))
        if reason != 'retained':
            result[region.slice][mask] = 0
    return result,decisions


def pale_nuclear_labels(signal,diameter_px,min_area_px,settings):
    from .cellpose_backend import run_cellpose_labels
    base, baseline_provenance = run_cellpose_labels(signal,model='cyto3',diameter=diameter_px,
        min_size=int(min_area_px),normalization_tile_px=settings['baseline_tile_px'])
    proposal, supplement_provenance = run_cellpose_labels(signal,model='cyto3',
        diameter=min(diameter_px,settings['supplement_diameter_cap']),
        min_size=settings['supplement_min_area_px'],normalization_tile_px=settings['supplement_tile_px'],
        cellprob_threshold=settings['supplement_cellprob_threshold'])
    sizes = np.bincount(proposal.ravel())
    allowed = np.flatnonzero((sizes >= settings['supplement_min_area_px']) & (sizes <= settings['supplement_max_area_px']))
    allowed = allowed[allowed > 0]
    merged = merge_nuclear_candidates(base,np.where(np.isin(proposal,allowed),proposal,0).astype(np.int32))
    filtered, decisions = filter_added_nuclei(base,merged,signal,
        min_solidity=settings['min_solidity'],min_core_to_rim=settings['min_core_to_rim'])
    return filtered,dict(backend='cellpose',model='cyto3',version='he_cyto3_pale_v1',
        baseline=baseline_provenance,supplement=supplement_provenance,
        supplement_filter=decisions,baseline_pixels_preserved=True,
        merge_overlap_fraction=.15,merge_radius_fraction=.9)

