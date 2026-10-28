from copy import deepcopy
import importlib.util
import numpy as np
import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication
from core.plotting.fluoro_render import render_merge_qimage, render_single_qimage

APP=QApplication.instance() or QApplication([])

def api():
    assert importlib.util.find_spec('core.plotting.fluoro_display'), 'Display processing not implemented'
    from core.plotting.fluoro_display import apply_display_optimization
    return apply_display_optimization

def rgb(image):
    q=image.convertToFormat(QImage.Format.Format_RGB888)
    ptr=q.bits();ptr.setsize(q.sizeInBytes())
    return np.frombuffer(ptr,np.uint8).reshape(q.height(),q.bytesPerLine())[:,:q.width()*3].reshape(q.height(),q.width(),3).copy()

def test_default_and_bypass_are_exact_identity():
    f=api();x=np.random.default_rng(1).random((40,40),dtype=np.float32)
    assert np.array_equal(f(x,{}),x)
    assert np.array_equal(f(x,{'enabled':False,'denoise':90,'strength':70,'background_radius':30}),x)

def test_smooth_strength_monotonic_bounded_and_no_clipped_highlights():
    f=api();x=np.linspace(0,1,256,dtype=np.float32)[None,:]
    high=f(x,{'strength':75});low=f(x,{'strength':-75})
    assert np.all(high[:,1:-1]>x[:,1:-1]) and np.all(low[:,1:-1]<x[:,1:-1])
    assert np.all(np.diff(high)>0) and high[0,0]==0 and high[0,-1]==1
    assert np.count_nonzero(high==1)==1

def test_denoise_reduces_background_variance_preserving_bright_object():
    f=api();truth=np.full((96,96),.15,np.float32);truth[32:64,32:64]=.7
    noisy=np.clip(truth+np.random.default_rng(4).normal(0,.035,truth.shape),0,1).astype(np.float32)
    before=noisy.copy();filtered=f(noisy,{'denoise':60})
    assert filtered[:24,:].std()<noisy[:24,:].std()*.8
    assert filtered[40:56,40:56].mean()>.6
    assert np.array_equal(noisy,before)

@pytest.mark.parametrize('level,min_contrast,max_noise',[(40,.90,.35),(100,.85,.23)])
def test_denoise_preserves_fine_fibres_while_reducing_noise(level,min_contrast,max_noise):
    y,x=np.mgrid[:192,:192]
    truth=np.full((192,192),.15,np.float32)
    for k in range(5):
        centre=42+k*19+3*np.sin(y/15+k)
        truth+=.13*np.exp(-.5*((x-centre)/.75)**2)*(y>32)*(y<158)
    for row,col in [(48,48),(65,82),(108,62),(142,130)]:
        truth+=.16*np.exp(-((x-col)**2+(y-row)**2)/1.5)
    noisy=np.clip(truth+np.random.default_rng(145).normal(0,.035,truth.shape),0,1).astype(np.float32)
    filtered=api()(noisy,{'denoise':level})
    signal=truth-.15
    contrast=np.sum((filtered-.15)*signal)/np.sum(signal**2)
    assert contrast>min_contrast, 'Denoising erased thin structures, despite retaining broad objects'
    assert filtered[:24].std()<max_noise*noisy[:24].std()
    assert np.mean((filtered-truth)**2)<np.mean((noisy-truth)**2)


def test_small_denoise_setting_stays_close_to_unprocessed_image():
    x=np.random.default_rng(302).uniform(.1,.5,(64,64)).astype(np.float32)
    low=api()(x,{'denoise':1});normal=api()(x,{'denoise':40})
    assert np.mean(np.abs(low-x))<.1*np.mean(np.abs(normal-x))


def test_background_correction_retains_signal_and_reduces_gradient():
    f=api();x=np.tile(np.linspace(.1,.35,96,dtype=np.float32),(96,1));x[40:48,40:48]+=.5
    out=f(x,{'background_radius':20})
    assert out[:20].mean()<x[:20].mean()*.5
    assert out[41:47,41:47].mean()>.25


def test_background_estimation_is_not_pulled_down_by_dark_noise():
    y,x=np.mgrid[:256,:256]
    background=.12+.13*(x/255)+.10*np.exp(-((x-80)**2+(y-130)**2)/(90**2))
    truth=background.copy();truth[70:160,100:104]+=.3
    noisy=np.clip(truth+np.random.default_rng(71).normal(0,.07,truth.shape),0,1).astype(np.float32)
    corrected=api()(noisy,{'background_radius':79})
    background_mask=(x<60)|(x>150)
    assert corrected[background_mask].mean()<.055
    assert corrected[80:150,100:104].mean()>.25

def test_renderer_changes_only_selected_channel():
    x=np.stack([np.full((32,32),80,np.uint16),np.full((32,32),100,np.uint16)])
    channels=[{'index':0,'display_min':0,'display_max':255,'pseudocolor':'#FF0000'},
              {'index':1,'display_min':0,'display_max':255,'pseudocolor':'#00FF00'}]
    before=rgb(render_merge_qimage(x,channels));raw=x.copy()
    channels[0]['display_processing']={'strength':60}
    after=rgb(render_merge_qimage(x,channels))
    assert np.all(after[:,:,0]>before[:,:,0])
    assert np.array_equal(after[:,:,1:],before[:,:,1:])
    assert np.array_equal(rgb(render_single_qimage(x,channels[0]))[:,:,0],after[:,:,0])
    assert np.array_equal(raw,x)

def test_channel_settings_persist_without_changing_measurements(tmp_path):
    api()
    from core.models import FluorescencePanel,Project
    image=QImage(64,64,QImage.Format.Format_RGB32);image.fill(0xff604020)
    path=tmp_path/'rgb.png';image.save(str(path))
    panel=FluorescencePanel(panel_id='F');record=panel.import_image(path)
    panel.force_split_rgb_samples(record['uid'])
    raw=panel.image_data(record['uid']).copy();other=deepcopy(record['channels'][1])
    before=panel.create_roi_intensity_measurement(record['uid'],'rectangle',{'x':4,'y':4,'w':20,'h':20},[0])
    panel.update_channel(record['uid'],0,display_processing={'denoise':40,'strength':35})
    assert record['channels'][0]['display_processing']['strength']==35
    assert record['channels'][1]==other
    after=panel.create_roi_intensity_measurement(record['uid'],'rectangle',{'x':4,'y':4,'w':20,'h':20},[0])
    assert before['results_table']==after['results_table']
    assert np.array_equal(raw,panel.image_data(record['uid']))
    project=Project();project.add_panel(panel);target=tmp_path/'display.sfig';project.save_sfig(target)
    loaded=Project.load_sfig(target)
    assert loaded.panels[0].active_image()['channels'][0]['display_processing']['strength']==35
    loaded.dispose();project.dispose()

def test_channel_controls_are_independent_resettable_and_locked(tmp_path):
    from core.models import FluorescencePanel
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace
    image=QImage(40,40,QImage.Format.Format_RGB32);image.fill(0xff804020)
    path=tmp_path/'test.png';image.save(str(path))
    panel=FluorescencePanel(panel_id='F');record=panel.import_image(path)
    panel.force_split_rgb_samples(record['uid'])
    w=FluorescenceWorkspace();w.set_panel(panel);w.set_step(1)
    try:
        assert w._channel_widgets[0][3].currentData().upper()==record['channels'][0]['pseudocolor'].upper()
        assert hasattr(w,'_display_processing_controls'),'Per-channel display controls required'
        control=w._display_processing_controls[0]
        other=deepcopy(record['channels'][1])
        control.denoise_button.click()
        control.strength.setValue(45)
        assert record['channels'][0]['display_processing']['denoise']==40
        assert record['channels'][0]['display_processing']['strength']==45
        assert record['channels'][1]==other
        control.compare.setChecked(True)
        assert not record['channels'][0]['display_processing']['enabled']
        assert control.strength.value()==45
        w.lock_display_checkbox.setChecked(True)
        assert not control.isEnabled()
        w.lock_display_checkbox.setChecked(False)
        control.reset_button.click()
        assert record['channels'][0]['display_processing']['strength']==0
        assert record['channels'][0]['display_processing']['denoise']==0
    finally:
        w.close();w.deleteLater();APP.processEvents()

def test_strength_changes_reuse_filter_but_changed_pixels_do_not(monkeypatch):
    f=api()
    from skimage import restoration
    original=restoration.denoise_nl_means
    calls=[]
    def observed(*args,**kwargs):
        calls.append(True)
        return original(*args,**kwargs)
    monkeypatch.setattr(restoration,'denoise_nl_means',observed)
    x=np.random.default_rng(78).uniform(.1,.5,(48,48)).astype(np.float32)
    a=f(x,{'denoise':43})
    b=f(x.copy(),{'denoise':43,'strength':20})
    assert len(calls)==1
    assert not np.array_equal(a,b)
    x[20:30,20:30]=1
    f(x,{'denoise':43})
    assert len(calls)==2

def test_native_export_obeys_optimization_and_bypass(tmp_path):
    from core.models import FluorescencePanel
    from ui.panels.fluorescence_image_export import render_fluorescence_export_payloads
    from core.plotting.fluoro_render import render_fluorescence_qimage
    q=QImage(40,40,QImage.Format.Format_RGB32);q.fill(0xff603020)
    path=tmp_path/'rgba.png';q.save(str(path))
    panel=FluorescencePanel(panel_id='export');record=panel.import_image(path)
    panel.force_split_rgb_samples(record['uid'])
    data=panel.image_data(record['uid'])
    baseline=rgb(render_fluorescence_qimage(data,record,record['channels']))
    panel.update_channel(record['uid'],0,display_processing={'strength':55})
    expected=rgb(render_fluorescence_qimage(data,record,record['channels']))
    assert np.all(expected[:,:,0]>baseline[:,:,0]) # forced-RGB fast path must not skip settings
    payload=render_fluorescence_export_payloads(panel,record,source='original',composition='merge')
    assert np.array_equal(rgb(payload[0][1]),expected)
    panel.update_channel(record['uid'],0,display_processing={'enabled':False,'strength':55})
    assert np.array_equal(rgb(render_fluorescence_qimage(data,record,record['channels'])),baseline)
    panel.dispose()


@pytest.mark.parametrize('view_mode',['merge','single','split'])
def test_parameter_buttons_keep_panned_canvas_stationary(tmp_path,view_mode):
    from PyQt6.QtCore import QPointF
    from PyQt6.QtTest import QTest
    from core.models import FluorescencePanel
    from ui.panels.fluorescence_workspace import FluorescenceWorkspace
    image=QImage(64,64,QImage.Format.Format_RGB32);image.fill(0xff804020)
    path=tmp_path/'stationary.png';image.save(str(path))
    panel=FluorescencePanel(panel_id='stationary');record=panel.import_image(path)
    panel.force_split_rgb_samples(record['uid']);record['view_mode']=view_mode
    w=FluorescenceWorkspace();w.set_panel(panel);w.set_step(1)
    c=w.canvas;c.resize(900,700);c.show();QTest.qWait(80)
    try:
        c.zoom_by(1.6);c._view_controller.pan_by(93,-71)
        item=c._image_items[0];item.moveBy(37,21);item.setSelected(True)
        item_position=item.pos();original_pixels=item.pixmap().toImage()
        before=c.view.mapFromScene(QPointF(0,0));transform=c.view.transform()
        paper=c._view_controller.backend.content_rect()
        controls=w._display_processing_controls[0]
        for control in (controls.strength,controls.denoise,controls.radius):
            if control is controls.radius:controls.background.setChecked(True)
            for _ in range(3):
                control.stepUp();QTest.qWait(w.CHANNEL_REFRESH_DEBOUNCE_MS+40)
                assert (c.view.mapFromScene(QPointF(0,0))-before).manhattanLength()<=1
                assert c.view.transform()==transform
                assert c._view_controller.backend.content_rect()==paper
                assert c._image_items[0] is item
                assert item.pos()==item_position and item.isSelected()
        assert item.pixmap().toImage()!=original_pixels
        for action in (controls.compare.toggle,controls.compare.toggle,
                       controls.denoise_button.click,controls.reset_button.click):
            action();QTest.qWait(w.CHANNEL_REFRESH_DEBOUNCE_MS+40)
            assert c._image_items[0] is item and item.pos()==item_position
            assert c.view.mapFromScene(QPointF(0,0))==before
        if view_mode=='split':
            w._channel_widgets[0][0].setChecked(False)
            QTest.qWait(w.CHANNEL_REFRESH_DEBOUNCE_MS+40)
            assert [pixmap.data(1) for pixmap in c._image_items]==[1,2,None]
    finally:
        c.close();w.close();panel.dispose();APP.processEvents()
