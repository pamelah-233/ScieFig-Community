import cv2
import numpy as np
from core.models import FluorescencePanel


def panel_for(channel):
    panel = FluorescencePanel(panel_id='spot-test')
    panel.data['fluorescence_images'] = [dict(uid='x', channels=[dict(index=0,name='signal')])]
    panel.black_balanced_image_data = lambda _: channel[None].astype(np.float32)
    return panel


def test_enhanced_count_separates_two_touching_spots():
    image = np.zeros((100,120),np.float32)
    cv2.circle(image,(45,50),15,100,-1)
    cv2.circle(image,(70,50),15,100,-1)
    panel=panel_for(image)
    old=panel.count_channel_objects('x',[0],method='manual',manual_value=40)[0]
    new=panel.count_channel_objects('x',[0],method='manual',manual_value=40,enhance=True)[0]
    assert old['count']==1
    assert new['count']==2
    assert new['threshold']==40


def test_sample_count_uses_spot_area_not_box_area():
    image=np.zeros((200,240),np.float32)
    for x in (45,115,185):
        cv2.circle(image,(x,85),9,100,-1)
    panel=panel_for(image)
    rows=[panel.count_like_sample('x',[0],dict(x=45-w//2,y=85-w//2,w=w,h=w),enhance=True)[0] for w in (26,50)]
    assert [r['count'] for r in rows]==[3,3]
    assert rows[0]['sample_object_area_px']==rows[1]['sample_object_area_px']
    assert rows[0]['min_area_px']==rows[1]['min_area_px']


def test_enhanced_auto_count_keeps_dim_isolated_spot():
    image=np.zeros((180,220),np.float32)
    cv2.circle(image,(50,80),12,200,-1)
    cv2.circle(image,(150,80),12,25,-1)
    panel=panel_for(image)
    assert panel.count_channel_objects('x',[0],enhance=True)[0]['count']==2


def test_local_count_does_not_overwrite_raw_channel_threshold():
    image=np.zeros((80,80),np.float32)
    cv2.circle(image,(40,40),10,100,-1)
    panel=panel_for(image)
    panel.count_channel_objects('x',[0],method='manual',manual_value=35)
    before=list(panel.images()[0]['thresholds'])
    panel.count_channel_objects('x',[0],enhance=True)
    assert panel.images()[0]['thresholds']==before


def test_auto_count_does_not_split_granular_signal_inside_one_spot():
    image=np.zeros((180,240),np.float32)
    cv2.circle(image,(55,85),20,200,-1)
    yy,xx=np.indices(image.shape)
    weak=(xx-170)**2+(yy-85)**2<=20**2
    rng=np.random.default_rng(7)
    image[weak]=rng.binomial(1,.12,size=int(weak.sum()))*200
    assert panel_for(image).count_channel_objects('x',[0],enhance=True)[0]['count']==2


def test_auto_count_retains_small_independent_points():
    image=np.zeros((70,90),np.float32)
    for x in (20,40,60):
        cv2.circle(image,(x,35),2,100,-1)
    assert panel_for(image).count_channel_objects('x',[0],enhance=True)[0]['count']==3
