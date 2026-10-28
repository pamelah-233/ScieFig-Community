"""Long-myotube phase-rim adaptation, preserving independent overlapping masks."""
import json
from pathlib import Path
import subprocess
import tempfile
import cv2
import numpy as np
from scipy import ndimage as ndi
from skimage.measure import regionprops
from skimage.morphology import skeletonize
from .cell_width import _centerline_paths,update_cell_size_summary
from . import cellpose_backend

METHOD='truefad_rim_path'
PARAMETERS=dict(center_threshold=.4,minimum_seed_area_px=100,minimum_seed_aspect=3,
    minimum_center_length_px=100,step_px=3,offset_range_px=[4,40],maximum_offset_delta_px=2,
    slope_penalty=.035,near_center_penalty=.0015,minimum_rim_support=.6)


def run_centers(gray):
    """Use the shared offline worker; never substitute cyto3 on failure."""
    device=cellpose_backend.cellpose_device_preference()
    with tempfile.TemporaryDirectory(prefix='ScieFig-myotube-') as folder:
        source,target=Path(folder)/'source.npy',Path(folder)/'centers.npy'
        np.save(source,gray,allow_pickle=False)
        result=subprocess.run(cellpose_backend.worker_command()+[str(source),str(target),'--model','truefad_myotube','--device',device],
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=600,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            raise RuntimeError('Long-myotube boundary runtime failed: '+(result.stderr or result.stdout)[-1800:])
        metadata=json.loads(target.with_suffix('.json').read_text(encoding='utf-8'))
        probability=np.load(target,allow_pickle=False)
        if metadata.get('model')!='truefad_myotube' or metadata.get('output')!='native_center_response':
            raise RuntimeError('Myotube runtime returned a different method.')
        if metadata.get('device')!=device:
            raise RuntimeError('Myotube runtime did not use the selected CPU/GPU device. Update the bundled runtime.')
        if probability.shape!=gray.shape or not np.isfinite(probability).all():
            raise RuntimeError('Myotube runtime returned an invalid native center map.')
        return probability,metadata


def _sample(image,xy):
    p=np.asarray(xy);q=p.reshape(-1,2)
    return ndi.map_coordinates(image,[q[:,1],q[:,0]],order=1,mode='nearest').reshape(p.shape[:-1])


def _rim_path(values):
    n,m=values.shape
    emission=-values+.0015*np.arange(m)[None,:]
    cost=emission[0].copy();back=np.zeros((n,m),np.int16)
    for i in range(1,n):
        choices=np.full((5,m),np.inf)
        for k,d in enumerate(range(-2,3)):
            js=np.arange(m);previous=js+d;ok=(previous>=0)&(previous<m)
            choices[k,ok]=cost[previous[ok]]+.035*abs(d)
        best=choices.argmin(axis=0)
        back[i]=np.arange(m)+best-2
        cost=choices[best,np.arange(m)]+emission[i]
    path=[int(cost.argmin())]
    for i in range(n-1,0,-1):path.append(int(back[i,path[-1]]))
    return np.array(path[::-1])+4


def boundary_masks(gray,probability):
    """Frozen reviewed experiment parameters; seed ends remain unverified."""
    gray=np.asarray(gray);probability=np.asarray(probability)
    if gray.ndim!=2 or probability.shape!=gray.shape or not np.isfinite(probability).all():
        raise ValueError('Myotube center response must match the source image.')
    low,high=np.percentile(gray,[1,99])
    g=np.clip((gray.astype(np.float32)-low)/max(1.,high-low),0,1).astype(np.float32)
    g=cv2.GaussianBlur(g,(0,0),1.5)
    components,_=ndi.label(probability<.4)
    masks=[];rows=[]
    for r in regionprops(components):
        if r.area<100 or r.axis_minor_length<1 or r.axis_major_length/r.axis_minor_length<3:continue
        y0,x0,y1,x1=r.bbox
        sk=skeletonize(np.pad(components[y0:y1,x0:x1]==r.label,1))
        paths=list(_centerline_paths(sk,1.,1.))
        if not paths:continue
        path,arclength=max(paths,key=lambda p:p[1][-1])
        if arclength[-1]<100:continue
        xy=np.column_stack(np.nonzero(sk)[::-1]).astype(float)-1+[x0,y0]
        positions=np.arange(0,arclength[-1],3.)
        centers=np.column_stack([np.interp(positions,arclength,xy[path,axis]) for axis in (0,1)])
        centers=ndi.gaussian_filter1d(centers,2,axis=0,mode='nearest')
        tangent=np.gradient(centers,axis=0)
        tangent/=np.maximum(1e-6,np.linalg.norm(tangent,axis=1))[:,None]
        normals=np.column_stack([-tangent[:,1],tangent[:,0]])
        offsets=np.arange(4.,41.)
        left_values=_sample(g,centers[:,None,:]-offsets[None,:,None]*normals[:,None,:])
        right_values=_sample(g,centers[:,None,:]+offsets[None,:,None]*normals[:,None,:])
        left=centers-normals*_rim_path(left_values)[:,None]
        right=centers+normals*_rim_path(right_values)[:,None]
        widths=np.linalg.norm(right-left,axis=1)
        support=float(np.mean(np.minimum(_sample(g,left),_sample(g,right))-_sample(g,centers)>.035))
        if support<.6 or arclength[-1]/np.median(widths)<4:continue
        mask=np.zeros(gray.shape,np.uint8)
        cv2.fillPoly(mask,[np.rint(np.vstack([left,right[::-1]])).astype(np.int32)],1)
        if ndi.label(mask)[1]!=1:continue
        masks.append(mask)
        rows.append(dict(source_seed_label=int(r.label),rim_supported_fraction=support,
            endpoint_status='model_center_ends_unverified'))
    overlap=np.zeros(gray.shape,np.uint16)
    for mask in masks:overlap+=mask
    for mask,row in zip(masks,rows):row['overlap_fraction']=float(np.mean(overlap[mask>0]>1))
    return masks,rows


def analyze_boundary_myotubes(image,params,calibration,roi_mask,excluded_regions):
    from .myotube_cells import myotube_measurements_from_labels
    source=np.asarray(image,np.uint8)
    # Original TRUEFAD inference used ImageJ's equal RGB weights. Boundary fitting
    # uses OpenCV grayscale, exactly as the reviewed experiment did.
    inference=np.rint(source.astype(float).mean(axis=2)).astype(np.uint8)
    gray=cv2.cvtColor(source,cv2.COLOR_RGB2GRAY)
    if np.ptp(inference)==0:
        masks,metadata=[],[];provenance=dict(model='truefad_myotube',blank_input=True)
    else:
        probability,provenance=run_centers(inference)
        masks,metadata=boundary_masks(gray,probability)
    result=myotube_measurements_from_labels(source,np.zeros(gray.shape,np.int32),modality='brightfield',
        settings=params,calibration=calibration,roi_mask=roi_mask,excluded_regions=excluded_regions)
    allowed=np.ones(gray.shape,bool) if roi_mask is None else np.asarray(roi_mask,bool).copy()
    for r in excluded_regions:allowed[r['y']:r['y']+r['h'],r['x']:r['x']+r['w']]=False
    rows=[];saved=[]
    for mask,info in zip(masks,metadata):
        one=myotube_measurements_from_labels(source,mask.astype(np.int32),modality='brightfield',
            settings=params,calibration=calibration,roi_mask=roi_mask,excluded_regions=excluded_regions)
        if not one['results']:continue
        row=one['results'][0];identifier=len(rows)+1
        local=mask.astype(bool)&allowed
        contour_mask=np.zeros(gray.shape,np.uint8)
        cv2.fillPoly(contour_mask,[np.asarray(row['contour_px'],np.int32)],1)
        local&=contour_mask.astype(bool)
        # Preserve original candidate pixel area; the common legacy method uses
        # polygon area and stays unchanged. Recompute physical area accordingly.
        row.update(info,id=identifier,source_label_id=identifier,detection_source=METHOD,
            area_px=int(local.sum()),geometry_review_required=True,
            geometry_review_reason='myotube_overlap_or_unverified_ends',include_in_statistics=False)
        if one['calibrated']:row['area_um2']=row['area_px']*one['um_per_px_x']*one['um_per_px_y']
        rows.append(row)
        yy,xx=np.nonzero(local);x0,y0=int(xx.min()),int(yy.min());x1,y1=int(xx.max()+1),int(yy.max()+1)
        saved.append(dict(id=identifier,bbox_xywh=[x0,y0,x1-x0,y1-y0],
            mask=cellpose_backend.pack_instance_labels(local[y0:y1,x0:x1].astype(np.int32))))
    result.update(results=rows,instance_masks=saved,method=METHOD,analysis_version='myotube-rim-path-v1',
        provenance={**provenance,'boundary_method':METHOD,'boundary_parameters':PARAMETERS.copy()})
    result['summary'].update(count=len(rows),round_count=0,elongated_count=len(rows),
        border_truncated_count=sum(r['border_truncated'] for r in rows),geometry_review_count=len(rows))
    update_cell_size_summary(result['summary'],rows)
    return result

