"""Selectable fluorescence counters and per-run provenance."""
import numpy as np


def count_provenance(result):
    algorithm = result.get('algorithm')
    names = {'log':'LoG', 'cellpose_cyto3':'Cellpose cyto3', 'cellpose_nuclei':'Cellpose nuclei'}
    if algorithm in names:
        return names[algorithm], dict(result.get('algorithm_parameters', {}))
    method = str(result.get('method') or 'unspecified')
    title = 'Sample spot' if method == 'sample_like' else 'Traditional / '+method
    if result.get('spot_enhancement'):
        title += ' (legacy enhanced)'
    params = result.get('algorithm_parameters')
    if params is None:
        params = {k:result[k] for k in ('threshold','min_area_px','max_area_px','sample_area_px','sample_mean') if k in result}
        if method == 'sample_like':params['sample_roi'] = result.get('roi_or_geometry',{})
    return title, dict(params)


def count_with_algorithm(channel, algorithm, diameter_px=40., sensitivity=1., min_area_px=5, max_area_px=None,*,intensity_image=None):
    image = np.asarray(channel, np.float32)
    if diameter_px <= 0 or sensitivity <= 0:
        raise ValueError('Object diameter and sensitivity must be positive.')
    if algorithm == 'log':
        from scipy import ndimage
        from skimage.feature import blob_log
        signal = np.maximum(image-ndimage.gaussian_filter(image,40),0)
        normalization = max(float(np.percentile(signal,99.8)),1.)
        signal /= normalization
        threshold = .04 / float(sensitivity)
        min_sigma, max_sigma = diameter_px/(4*np.sqrt(2)), diameter_px/np.sqrt(2)
        blobs = blob_log(signal,min_sigma=min_sigma,max_sigma=max_sigma,num_sigma=19,
                         threshold=None,threshold_rel=threshold,overlap=.5,exclude_border=False)
        spots=[]
        for y,x,sigma in blobs:
            radius=float(sigma*np.sqrt(2))
            spots.append(dict(x=int(max(0,x-radius)),y=int(max(0,y-radius)),w=int(2*radius+1),h=int(2*radius+1),
                              centroid_x=float(x),centroid_y=float(y),detection_radius_px=radius))
        return dict(count=len(spots), detected_spots=spots, total_positive_area_px=None,
                    mean_object_area_px=None, mean_intensity=None, area_semantics='not_measured_by_blob_detector',
                    algorithm=algorithm, algorithm_parameters=dict(diameter_px=float(diameter_px),sensitivity=float(sensitivity),
                    threshold_rel=threshold,min_sigma_px=float(min_sigma),max_sigma_px=float(max_sigma),num_sigma=19,
                    overlap=.5,background_sigma_px=40,normalization_percentile=99.8,normalization_value=normalization))
    if algorithm not in ('cellpose_cyto3','cellpose_nuclei'):
        raise ValueError('Unknown fluorescence counting algorithm: '+str(algorithm))
    from core.image_analysis.cellpose_backend import run_cellpose_labels
    from skimage.measure import regionprops
    model = algorithm.removeprefix('cellpose_')
    labels, metadata = run_cellpose_labels(image,model=model,diameter=diameter_px,min_size=min_area_px)
    regions=[r for r in regionprops(labels,intensity_image=image if intensity_image is None else intensity_image)
             if r.area>=min_area_px and (max_area_px is None or r.area<=max_area_px)]
    spots=[]
    import cv2
    for region in regions:
        y0,x0,y1,x1=region.bbox
        contours,_=cv2.findContours(np.asarray(region.image,dtype=np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        contour=max(contours,key=cv2.contourArea) if contours else np.empty((0,1,2))
        spots.append(dict(x=int(x0),y=int(y0),w=int(x1-x0),h=int(y1-y0),
                          centroid_x=float(region.centroid[1]),centroid_y=float(region.centroid[0]),area_px=int(region.area),
                          object_id=int(region.label),contour_points_px=[[float(p[0][0]+x0),float(p[0][1]+y0)] for p in contour]))
    return dict(count=len(spots),detected_spots=spots,total_positive_area_px=int(sum(r.area for r in regions)),
                mean_object_area_px=float(np.mean([r.area for r in regions])) if regions else 0.,
                mean_intensity=float(np.mean([r.intensity_mean for r in regions])) if regions else 0.,
                algorithm=algorithm,algorithm_parameters={**metadata,'max_area_px':max_area_px},
                area_semantics='cellpose_instance_pixels')
