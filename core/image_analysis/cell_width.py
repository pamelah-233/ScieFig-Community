"""Local centerline-normal width, kept distinct from Feret length and box size."""
import cv2
import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra


def _centerline_paths(centerline, sx, sy):
    """Ordered geodesic trunks, so nearby side branches cannot rotate tangents."""
    yy,xx=np.nonzero(centerline)
    indices=np.full(centerline.shape,-1,dtype=np.int32)
    indices[yy,xx]=np.arange(len(xx))
    sources=[];targets=[];weights=[]
    for dx,dy in ((1,0),(0,1),(1,1),(-1,1)):
        nx,ny=xx+dx,yy+dy
        inside=(nx>=0)&(nx<centerline.shape[1])&(ny<centerline.shape[0])
        start=np.flatnonzero(inside)
        end=indices[ny[inside],nx[inside]]
        valid=end>=0
        start,end=start[valid],end[valid]
        sources.extend((start,end));targets.extend((end,start))
        weights.extend((np.full(len(start),np.hypot(dx*sx,dy*sy)),)*2)
    graph=coo_matrix((np.concatenate(weights),(np.concatenate(sources),np.concatenate(targets))),
                     shape=(len(xx),len(xx))).tocsr()
    count,components=connected_components(graph,directed=False)
    for component in range(count):
        nodes=np.flatnonzero(components==component)
        if len(nodes)<3:continue
        distances=dijkstra(graph,directed=False,indices=int(nodes[0]))
        first=int(nodes[np.argmax(distances[nodes])])
        distances,previous=dijkstra(graph,directed=False,indices=first,return_predecessors=True)
        last=int(nodes[np.argmax(distances[nodes])])
        path=[last]
        while path[-1]!=first:path.append(int(previous[path[-1]]))
        path=np.asarray(path[::-1],dtype=int)
        yield path,distances[path]


def _length_metrics(path, physical, arclength, radii, a, edges, scale, shift):
    """Smooth pixel stair-steps, then extend the principal trunk to its end caps."""
    step=min(scale)
    samples=np.linspace(0,arclength[-1],max(3,int(np.ceil(arclength[-1]/step))+1))
    points=np.column_stack([np.interp(samples,arclength,physical[path,axis]) for axis in (0,1)])
    radius=float(np.median(radii[path]))
    points=ndimage.gaussian_filter1d(points,max(1.,.2*radius/step),axis=0,mode='nearest')
    trim=min(max(2*step,radius),.15*arclength[-1])
    keep=(samples>=trim)&(samples<=samples[-1]-trim)
    points=points[keep]
    if len(points)<3:return dict(centerline_length_px=None,centerline_length_um=None,centerline_points_px=[],length_method='requires_contour_review')
    ends=[]
    span=max(2,min(len(points)-1,int(2*radius/step)))
    for point,inside in ((points[0],points[span]),(points[-1],points[-1-span])):
        direction=point-inside
        direction/=max(float(np.linalg.norm(direction)),1e-12)
        delta=a-point
        den=direction[0]*edges[:,1]-direction[1]*edges[:,0]
        valid=np.abs(den)>1e-9
        t=np.zeros(len(a));u=t.copy()
        t[valid]=(delta[valid,0]*edges[valid,1]-delta[valid,1]*edges[valid,0])/den[valid]
        u[valid]=(delta[valid,0]*direction[1]-delta[valid,1]*direction[0])/den[valid]
        hits=t[valid&(t>=0)&(u>=0)&(u<=1)]
        ends.append(point+direction*float(hits.min()) if len(hits) else point)
    points=np.vstack([ends[0],points,ends[1]])
    pixels=points/scale+shift
    return dict(centerline_length_px=float(np.linalg.norm(np.diff(pixels,axis=0),axis=1).sum()),
                centerline_length_um=float(np.linalg.norm(np.diff(points,axis=0),axis=1).sum()),
                centerline_points_px=pixels.tolist(),length_method='principal_centerline_v1')


def mask_width_metrics(mask, sx=1., sy=1., offset=(0.,0.), *, include_length=False):
    from skimage.morphology import skeletonize
    binary=np.asarray(mask,bool)
    if binary.ndim!=2 or not binary.any() or not np.isfinite([sx,sy]).all() or min(sx,sy)<=0:
        raise ValueError('Width requires a nonempty 2D mask and positive finite pixel sizes.')
    yy,xx=np.nonzero(binary)
    x0,y0=int(xx.min()),int(yy.min())
    binary=binary[y0:int(yy.max())+1,x0:int(xx.max())+1]
    padded=np.pad(binary,1)
    centerline=skeletonize(padded)
    points=np.column_stack(np.nonzero(centerline)[::-1]).astype(float)-1
    physical=points*np.array([sx,sy])
    distance=ndimage.distance_transform_edt(padded,sampling=(sy,sx))
    radii=distance[centerline]
    contours,_=cv2.findContours(binary.astype(np.uint8),cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    paths=[contour[:,0].astype(float)*np.array([sx,sy]) for contour in contours if len(contour)>=3]
    if not paths:raise ValueError('The object is too thin to measure transverse width.')
    a=np.concatenate(paths);b=np.concatenate([np.roll(path,-1,axis=0) for path in paths]);edges=b-a
    sections=[]
    trunks=list(_centerline_paths(centerline,sx,sy))
    for path,arclength in trunks:
        candidates=np.flatnonzero(radii[path]>=max(min(sx,sy),.5*float(radii[path].max())))
        if len(candidates)>256:
            candidates=np.unique(np.r_[candidates[np.linspace(0,len(candidates)-1,256).astype(int)],np.argmax(radii[path])])
        for position in candidates:
            index=path[position];radius=radii[index]
            if min(arclength[position],arclength[-1]-arclength[position])<max(radius*1.2,min(sx,sy)*2):continue
            span=max(min(sx,sy)*3,radius*2)
            left,right=np.searchsorted(arclength,[arclength[position]-span,arclength[position]+span])
            sections.append((index,physical[path[left:right]]))
    best=None
    for index,nearby in sections:
        point=physical[index]
        if len(nearby)<3:continue
        eigenvalues,eigenvectors=np.linalg.eigh(np.cov(nearby.T))
        if eigenvalues[-1]<=0 or eigenvalues[0]>.5*eigenvalues[-1]:continue
        normal=eigenvectors[:,0]
        delta=a-point
        denominator=normal[0]*edges[:,1]-normal[1]*edges[:,0]
        valid=np.abs(denominator)>1e-9
        t=np.zeros(len(a));u=t.copy()
        t[valid]=(delta[valid,0]*edges[valid,1]-delta[valid,1]*edges[valid,0])/denominator[valid]
        u[valid]=(delta[valid,0]*normal[1]-delta[valid,1]*normal[0])/denominator[valid]
        crossings=t[valid & (u>=0) & (u<=1)]
        before=crossings[crossings<=0];after=crossings[crossings>=0]
        if not len(before) or not len(after):continue
        low,high=float(before.max()),float(after.min())
        width=high-low
        # A transverse diameter must also pass through the local medial body.
        # At a spur or end cap a skeleton neighbourhood can tilt the normal,
        # yielding a long chord whose midpoint lies next to a side boundary.
        # Pixel-centre EDT includes half a boundary pixel; remove that margin.
        midpoint=point+normal*((low+high)/2)
        midpoint_px=midpoint/[sx,sy]
        clearance=float(ndimage.map_coordinates(distance,
            [[midpoint_px[1]+1],[midpoint_px[0]+1]],order=1,mode='constant')[0])-.5*min(sx,sy)
        if clearance<.49*width:continue
        if width>0 and (best is None or width>best[0]):
            best=(width,(point+normal*low)/[sx,sy],(point+normal*high)/[sx,sy])
    shift=np.array([offset[0]+x0,offset[1]+y0])
    if best is None:
        if not include_length:raise ValueError('No unambiguous centerline cross-section; review the object contour.')
        result=dict(max_width_px=None,max_width_um=None,width_start_px=None,width_end_px=None,width_method='requires_contour_review')
    else:
        start,end=best[1]+shift,best[2]+shift
        result=dict(max_width_px=float(np.linalg.norm(end-start)),max_width_um=float(best[0]),
                width_start_px=start.tolist(),width_end_px=end.tolist(),
                width_method='centerline_normal_v2')
    if include_length:
        if trunks:
            path,arclength=max(trunks,key=lambda item:item[1][-1])
            result.update(_length_metrics(path,physical,arclength,radii,a,edges,np.array([sx,sy]),shift))
            trunk_mask=np.zeros(centerline.shape,bool)
            trunk_points=np.rint(points[path]+1).astype(int)
            trunk_mask[trunk_points[:,1],trunk_points[:,0]]=True
            away=ndimage.distance_transform_edt(~trunk_mask,sampling=(sy,sx))[centerline]
            body_radius=float(np.median(radii[path]))
            branched=bool(np.any((away>2*body_radius)&(radii>=.5*body_radius)))
            result.update(geometry_review_required=branched,
                          geometry_review_reason='branched_or_joined_contour' if branched else '')
        else:result.update(centerline_length_px=None,centerline_length_um=None,centerline_points_px=[],length_method='requires_contour_review')
    return result


def contour_width_metrics(points, sx=1., sy=1., *, include_length=False):
    contour=np.asarray(points,float).reshape(-1,2)
    x0,y0=np.floor(contour.min(axis=0)).astype(int)
    width,height=np.ceil(contour.max(axis=0)-[x0,y0]).astype(int)+1
    mask=np.zeros((height,width),np.uint8)
    cv2.fillPoly(mask,[np.rint(contour-[x0,y0]).astype(np.int32)],1)
    return mask_width_metrics(mask,sx,sy,offset=(x0,y0),include_length=include_length)


def update_cell_size_summary(summary, rows):
    included=[row for row in rows if row.get('include_in_statistics',True)]
    summary['complete_count']=len(included)
    if 'geometry_review_count' in summary or any('geometry_review_required' in row for row in rows):
        summary['geometry_review_count']=sum(bool(row.get('geometry_review_required')) for row in rows)
    for field in ('max_width_um','area_um2','centerline_length_um'):
        values=[row[field] for row in included if isinstance(row.get(field),(float,int)) and np.isfinite(row[field])]
        summary['mean_'+field]=float(np.mean(values)) if values else None
