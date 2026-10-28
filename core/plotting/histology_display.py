"""Image-local RGB white balance for presentation only, never analytical input."""
import numpy as np
from PyQt6.QtGui import QImage


def qimage_rgb(image):
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    bits = image.constBits(); bits.setsize(image.sizeInBytes())
    return np.frombuffer(bits, np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()*3].reshape(image.height(),image.width(),3).copy()


def sample_white_balance(rgb, geometry=None):
    image=np.asarray(rgb)
    if image.ndim!=3 or image.shape[2]!=3:
        raise ValueError('White balance requires an RGB image.')
    if geometry is not None:
        x,y,w,h=(int(geometry[k]) for k in ('x','y','w','h'))
        if x<0 or y<0 or w<2 or h<2 or x+w>image.shape[1] or y+h>image.shape[0]:
            raise ValueError('Choose a valid background rectangle inside the image.')
        pixels=image[y:y+h,x:x+w].reshape(-1,3)
    else:
        pixels=image.reshape(-1,3).astype(np.float32)
        bright=pixels.mean(axis=1)
        chroma=np.ptp(pixels,axis=1)/np.maximum(pixels.max(axis=1),1)
        pixels=pixels[(bright>=max(100,float(np.percentile(bright,90)))) & (chroma<.25)]
    if len(pixels)<4:
        raise ValueError('No neutral bright background found. Select an unstained background patch.')
    reference=np.median(pixels,axis=0).astype(float)
    if reference.min()<30:
        raise ValueError('The selected region is too dark; choose unstained background.')
    gains=float(reference.mean())/reference
    if not np.isfinite(gains).all() or gains.min()<.25 or gains.max()>4:
        raise ValueError('Selected region is not suitable for white balance.')
    return dict(gains=gains.tolist(),reference_rgb=reference.tolist(),
                sampling='rectangle' if geometry is not None else 'bright_background',
                region=dict(geometry) if geometry is not None else None,display_only=True)


def apply_white_balance(rgb, settings=None):
    image=np.asarray(rgb)
    if not settings:return image.copy()
    gains=np.asarray(settings['gains'],float)
    if gains.shape!=(3,) or not np.isfinite(gains).all() or (gains<=0).any():
        raise ValueError('Invalid white balance gains.')
    return np.clip(np.rint(image.astype(float)*gains),0,255).astype(np.uint8)


def white_balanced_qimage(image, settings=None):
    if not settings:return image.copy()
    rgb=np.ascontiguousarray(apply_white_balance(qimage_rgb(image),settings))
    return QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format.Format_RGB888).copy()
