"""Independent myotube measurements on the shared offline segmentation base."""
from copy import deepcopy
import cv2
import numpy as np
from . import cellpose_backend
from .cellpose_cells import measurements_from_labels
from .cell_calibration import calibrate_cell_analysis
from .cell_width import update_cell_size_summary,contour_width_metrics
from core.measurement_scale import apply_length_scale

DEFAULTS=dict(diameter_px=60.,min_area_px=60,min_aspect_ratio=2.,exclude_scale_bar=True)
METHODS=('cyto3','truefad_rim_path')


def _inputs(source,modality,settings,roi_mask):
    image=np.asarray(source)
    valid=(modality=='fluoro' and image.ndim==2) or (modality=='brightfield' and image.ndim==3 and image.shape[-1]==3)
    if not valid or not image.size or image.dtype.kind not in 'uif' or not np.isfinite(image).all():
        raise ValueError('Myotubes require a native fluorescence channel or brightfield RGB image.')
    if roi_mask is not None and np.shape(roi_mask)!=image.shape[:2]:raise ValueError('ROI must match the source image.')
    params={**DEFAULTS,**dict(settings or {})}
    method=params.get('method','cyto3')
    if method not in METHODS or (modality!='brightfield' and method!='cyto3'):
        raise ValueError('Invalid myotube method for this modality.')
    if not np.isfinite([params['diameter_px'],params['min_area_px'],params['min_aspect_ratio']]).all() or params['diameter_px']<=0 or params['min_area_px']<1 or params['min_aspect_ratio']<1.5:
        raise ValueError('Invalid myotube diameter, minimum area or aspect ratio.')
    signal=cv2.cvtColor(image.astype(np.uint8),cv2.COLOR_RGB2GRAY) if modality=='brightfield' else image
    return image,signal,params


def myotube_measurements_from_labels(source,labels,*,modality,settings=None,calibration=None,roi_mask=None,excluded_regions=()):
    image,signal,params=_inputs(source,modality,settings,roi_mask)
    labels=np.asarray(labels)
    if labels.shape!=signal.shape or labels.dtype.kind not in 'ui' or np.any(labels<0):
        raise ValueError('Myotube labels must match the native source image.')
    if modality=='fluoro':
        low,high=np.percentile(signal,[1,99.8])
        display=np.uint8(np.clip((signal.astype(float)-low)/max(float(high-low),1e-9),0,1)*255)
        rgb=np.repeat(display[...,None],3,axis=-1)
    else:rgb=image.astype(np.uint8)
    analysis=measurements_from_labels(rgb,labels,1.,params,roi_mask,excluded_regions)
    rows=[]
    # Cellpose geometry reports short/long aspect; muscle targets retain only
    # elongated candidates, leaving the ordinary cell-size workflow unchanged.
    for row in analysis['results']:
        if row['area_px']<params['min_area_px'] or row['aspect_ratio']>1/params['min_aspect_ratio']:continue
        points=np.asarray(row['contour_px'],np.int32)
        native=np.zeros(labels.shape,np.uint8);cv2.fillPoly(native,[points],1)
        allowed=(labels>0)&native.astype(bool)
        if roi_mask is not None:allowed&=np.asarray(roi_mask,bool)
        if allowed.any():
            counts=np.bincount(labels[allowed].astype(np.int64));counts[0]=0
            allowed&=labels==int(np.argmax(counts))
        row.update(id=len(rows)+1,shape_class='elongated',detection_source='cellpose_cyto3',
                   mean_intensity=float(np.mean(signal[allowed])) if allowed.any() else None)
        row.update(contour_width_metrics(points,include_length=True))
        rows.append(row)
    analysis['results']=rows
    analysis['summary'].update(count=len(rows),round_count=0,elongated_count=len(rows),
        border_truncated_count=sum(bool(r['border_truncated']) for r in rows))
    scale=deepcopy(calibration or {})
    sx=apply_length_scale(1,0,scale);sy=apply_length_scale(0,1,scale)
    calibrated=sx>0 and sy>0 and not scale.get('assumed',False)
    if calibrated:analysis=calibrate_cell_analysis(analysis,scale)
    else:
        for row in rows:
            for key in ('area_um2','max_width_um','max_feret_um','min_feret_um','equivalent_diameter_um','centerline_length_um'):row[key]=None
        analysis['um_per_px']=0.
        for key in ('median_equivalent_diameter_um','mean_equivalent_diameter_um','calibration_um_per_px'):analysis['summary'][key]=None
    for row in analysis['results']:
        if row.get('geometry_review_required'):row['include_in_statistics']=False
    analysis['summary']['geometry_review_count']=sum(bool(row.get('geometry_review_required')) for row in analysis['results'])
    update_cell_size_summary(analysis['summary'],analysis['results'])
    analysis.update(type='myotube_measurement',modality=modality,settings=params,
                    measurement_scale_bar=scale,analysis_version='myotube-cyto3-width-length-v2',calibrated=calibrated)
    return analysis


def analyze_myotubes(source,*,modality,settings=None,calibration=None,roi_mask=None,measurement_source=None):
    image,signal,params=_inputs(source,modality,settings,roi_mask)
    prepared=signal.copy();excluded=[]
    if modality=='brightfield' and params['exclude_scale_bar']:
        from .bright_round_cells import _detect_scale_bar_region
        region=_detect_scale_bar_region(prepared)
        if region:
            excluded.append(region);x,y,w,h=(region[k] for k in ('x','y','w','h'))
            prepared[y:y+h,x:x+w]=np.median(prepared)
    if params.get('method','cyto3')=='truefad_rim_path':
        from .myotube_boundary import analyze_boundary_myotubes
        return analyze_boundary_myotubes(image,params,calibration,roi_mask,excluded)
    if np.ptp(prepared)==0:
        labels=np.zeros(signal.shape,np.int32);provenance=dict(model='cyto3',blank_input=True)
    else:
        labels,provenance=cellpose_backend.run_cellpose_labels(prepared,model='cyto3',
            diameter=float(params['diameter_px']),min_size=int(params['min_area_px']))
    result=myotube_measurements_from_labels(image if measurement_source is None else measurement_source,labels,modality=modality,settings=params,
        calibration=calibration,roi_mask=roi_mask,excluded_regions=excluded)
    result['provenance']=provenance
    saved=np.zeros(labels.shape,np.int32)
    for row in result['results']:
        saved[labels==row['source_label_id']]=row['id']
    result['instance_labels']=cellpose_backend.pack_instance_labels(saved)
    return result
