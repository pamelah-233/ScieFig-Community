"""Per-channel display processing; explicit detection may reuse it, raw quantification must not."""
from collections.abc import Mapping
import math
from threading import Lock
import numpy as np

_FILTER_CACHE=[]
_CACHE_LOCK=Lock()
_CACHE_BYTES=32*1024*1024


def display_settings(payload=None):
    values=payload if isinstance(payload,Mapping) else {}
    def bounded(name,default,low,high):
        try: value=float(values.get(name,default))
        except (TypeError,ValueError): value=default
        return min(high,max(low,value)) if math.isfinite(value) else default
    black=bounded('black_point',0,-math.inf,math.inf)
    white=bounded('white_point',1,-math.inf,math.inf)
    if white <= black:
        black,white=0.0,1.0
    return {'enabled':bool(values.get('enabled',True)),
            'black_point':black,'white_point':white,
            'denoise':bounded('denoise',0,0,100),
            'strength':bounded('strength',0,-100,100),
            'background_radius':bounded('background_radius',0,0,500)}


def display_processing_active(payload=None):
    s=display_settings(payload)
    return s['enabled'] and (s['black_point'] != 0 or s['white_point'] != 1 or any(s[key] for key in ('denoise','strength','background_radius')))


def display_points_active(payload=None):
    s=display_settings(payload)
    return s['enabled'] and (s['black_point'] != 0 or s['white_point'] != 1)


def sample_display_point(plane, geometry, channel, point):
    """Sample raw pixels, returning display settings only; never a measurement ROI."""
    if point not in {'black','white'} or plane.ndim != 2:
        raise ValueError('Invalid display point')
    try:
        x,y,w,h=(float(geometry[key]) for key in ('x','y','w','h'))
        if not all(math.isfinite(value) for value in (x,y,w,h)) or w <= 0 or h <= 0:
            raise ValueError('Empty display region')
        x0,x1=max(0,math.floor(x)),min(plane.shape[1],math.ceil(x+w))
        y0,y1=max(0,math.floor(y)),min(plane.shape[0],math.ceil(y+h))
        if x1 <= x0 or y1 <= y0:
            raise ValueError('Region outside image')
        region=np.asarray(plane[y0:y1,x0:x1])
        values=region[np.isfinite(region)]
        low,high=float(channel.get('display_min',0)),float(channel.get('display_max',1))
        if not values.size or not math.isfinite(low) or not math.isfinite(high) or high <= low:
            raise ValueError('No finite display reference')
    except (KeyError,TypeError,OverflowError) as exc:
        raise ValueError('Invalid display region') from exc
    reference=float(np.median(values) if point == 'black' else np.percentile(values,99))
    settings=display_settings(channel.get('display_processing'))
    settings[point+'_point']=(reference-low)/(high-low)
    if settings['white_point'] <= settings['black_point']:
        raise ValueError('Black point must be below white point')
    settings['enabled']=True
    return settings


def apply_display_optimization(normalized,settings=None):
    s=display_settings(settings)
    if not display_processing_active(s):
        return normalized
    result=np.nan_to_num(normalized.astype(np.float32,copy=True),nan=0,posinf=1,neginf=0)
    if s['black_point'] != 0 or s['white_point'] != 1:
        result-=s['black_point']
        result/=s['white_point']-s['black_point']
    np.clip(result,0,1,out=result)
    if s['denoise'] or s['background_radius']:
        result=_filtered_channel(result,s)
    if s['strength']:
        gain=np.float32(2**(s['strength']/50))
        result=gain*result/(1+(gain-1)*result)
    return np.clip(result,0,1)


def _filtered_channel(source,s):
    key=(s['denoise'],s['background_radius'],source.shape)
    with _CACHE_LOCK:
        for index,(saved_key,saved_source,saved_result) in enumerate(_FILTER_CACHE):
            # Equality guards against edits, array-id reuse and unrelated channels.
            if saved_key==key and np.array_equal(saved_source,source):
                _FILTER_CACHE.insert(0,_FILTER_CACHE.pop(index))
                return saved_result
    result=source
    if s['denoise'] and min(result.shape)>=8:
        from skimage.restoration import denoise_nl_means
        # Orthonormal 2x2 Haar diagonal details + MAD estimate; no optional
        # wavelet runtime, and isolated bright structures do not dominate it.
        even=result[:result.shape[0]//2*2,:result.shape[1]//2*2]
        details=(even[::2,::2]-even[1::2,::2]-even[::2,1::2]+even[1::2,1::2])*.5
        sigma=float(np.median(np.abs(details-np.median(details)))/.67448975)
        if math.isfinite(sigma) and sigma>1e-6:
            # Compare enough neighbouring texture to reject isolated noise,
            # without variance subtraction making distinct fibres look alike.
            # Bound the bandwidth even at 100% to avoid broad, waxy patches.
            amount=s['denoise']/100
            filtered=denoise_nl_means(result,h=(.55+.25*amount)*sigma,
                sigma=0,patch_size=5,patch_distance=5,fast_mode=True,
                preserve_range=True,channel_axis=None).astype(np.float32,copy=False)
            # Fade in only at low settings; retaining raw noise at the default
            # and strong settings defeated the purpose of the denoiser.
            result=result+min(1.,amount/.4)*(filtered-result)
    if s['background_radius'] and min(result.shape)>=8:
        from scipy.ndimage import gaussian_filter,zoom
        from skimage.restoration import rolling_ball
        from skimage.transform import resize_local_mean
        # Estimate only the slowly varying background on a small grid. Full
        # resolution signal pixels are retained for subtraction and rendering.
        ratio=min(1.,320/max(result.shape),24/s['background_radius'])
        shape=tuple(max(2,round(size*ratio)) for size in result.shape)
        # Interpolating sparse noisy pixels biased the rolling-ball minimum
        # downward. Area-average and smooth ONLY the background estimator;
        # subtraction still uses the full-resolution signal image.
        coarse=resize_local_mean(result,shape,preserve_range=True) if ratio<1 else result
        coarse=gaussian_filter(coarse,1)
        background=rolling_ball(coarse*255.,radius=max(2,s['background_radius']*ratio))/255.
        if ratio<1:
            background=zoom(background,(result.shape[0]/background.shape[0],result.shape[1]/background.shape[1]),order=1)
        result=np.maximum(result-background,0).astype(np.float32,copy=False)
    size=source.nbytes+result.nbytes
    if size<=_CACHE_BYTES:
        with _CACHE_LOCK:
            while _FILTER_CACHE and sum(a.nbytes+b.nbytes for _,a,b in _FILTER_CACHE)+size>_CACHE_BYTES:
                _FILTER_CACHE.pop()
            _FILTER_CACHE.insert(0,(key,source.copy(),result.copy()))
    return result
