import tracemalloc
import numpy as np
import pytest
from PyQt6.QtGui import QImage
from core.plotting import fluoro_render as render


def _reference(data, channels):
    rgb = np.zeros((data.shape[1], data.shape[2], 3), dtype=np.float32)
    for channel in channels:
        if not channel.get('visible',True):
            continue
        index=int(channel.get('index',0))
        if not 0<=index<data.shape[0]:
            continue
        low,high=float(channel.get('display_min',0)),float(channel.get('display_max',1))
        if high<=low: high=low+1
        normalized=np.clip((data[index].astype(np.float32)-low)/(high-low),0,1)
        color=render._hex_to_rgb(channel.get('pseudocolor','#FFFFFF'))
        rgb+=normalized[:,:,None]*np.array(color,dtype=np.float32)[None,None,:]
    return np.clip(rgb,0,255).astype(np.uint8)


def _pixels(image):
    converted=image.convertToFormat(QImage.Format.Format_RGB888)
    p=converted.constBits()
    p.setsize(converted.sizeInBytes())
    return np.frombuffer(p,np.uint8).reshape(converted.height(),converted.bytesPerLine())[:,:converted.width()*3].reshape(converted.height(),converted.width(),3).copy()


@pytest.mark.parametrize('dtype',[np.uint8,np.uint16,np.float32])
def test_render_matches_reference_pixels_without_changing_source(dtype):
    data=np.random.default_rng(6).integers(0,25000,(4,131,193)).astype(dtype)
    before=data.copy()
    channels=[dict(index=0,display_min=27.2,display_max=17001.7,pseudocolor='#0066FF'),
              dict(index=1,display_min=100,display_max=20000,pseudocolor='#00D26A'),
              dict(index=2,display_min=20000,display_max=20000,pseudocolor='#FF3333'),
              dict(index=3,visible=False)]
    image=render.render_merge_qimage(data,channels)
    assert np.array_equal(_pixels(image),_reference(data,channels))
    assert np.array_equal(data,before)
    assert (image.width(),image.height()) == (193,131)


def test_normalization_uses_one_working_float_buffer():
    data=np.arange(1024*1024,dtype=np.uint16).reshape(1024,1024)
    tracemalloc.start()
    try:
        result=render._normalize_channel(data,10.5,50000.25)
        _,peak=tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < result.nbytes*1.3
    assert not np.shares_memory(data,result)


def test_merge_uses_bounded_planar_workspaces():
    data=np.ones((3,1024,1024),np.uint16)
    channels=[dict(index=i,display_max=65535,pseudocolor=c) for i,c in enumerate(('#2345FF','#88DD00','#FFAA22'))]
    tracemalloc.start()
    try:
        image=render.render_merge_qimage(data,channels)
        _,peak=tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < data.shape[1]*data.shape[2]*24
    assert image.width()==1024 and image.height()==1024


def test_merge_matches_reference_for_noncontiguous_and_extreme_display_values():
    data=np.random.default_rng(8).normal(100,100,(5,92,70)).astype(np.float32)[:,::2,::-1]
    channels=[dict(index=i,display_min=low,display_max=high,pseudocolor=color) for i,low,high,color in (
        (0,-12.25,333.9,'#113355'),(1,20,20,'#335577'),(2,90,-1,'#FF10BB'),(3,-800,190.8,'#000000'),(4,0,20,'#FFFFFF'))]
    actual=render.render_merge_qimage(data,channels)
    assert np.array_equal(_pixels(actual),_reference(data,channels))


def test_qimage_owns_unchanged_pixels_after_source_buffer_reuse():
    data=np.arange(7*11*3,dtype=np.uint8).reshape(7,11,3)[:,::-1]
    expected=data.copy()
    image=render._rgb_array_to_qimage(data)
    data[:]=0
    assert np.array_equal(_pixels(image),expected)
