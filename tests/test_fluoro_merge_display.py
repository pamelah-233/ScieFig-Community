"""Merging channels retains source display settings and raw signal values."""
from copy import deepcopy
import numpy as np
import tifffile
from core.models import FluorescencePanel
from core.plotting.fluoro_render import render_fluorescence_qimage
import pytest


@pytest.mark.parametrize('alpha',[False,True])
def test_merge_retains_pixel_varying_rgb_after_source_removal_and_reopen(tmp_path,alpha):
    from core.models import Project
    panel=FluorescencePanel(panel_id='true-colors');sources=[];expected=[]
    for number in range(2):
        rgb=np.zeros((24,32,4 if alpha else 3),np.uint8)
        rgb[:12,:16,:3]=[185,21,7];rgb[12:,:16,:3]=[26,160,75]
        rgb[:,16:,:3]=[40+number*30,65,170]
        if alpha:rgb[:,:,3]=np.arange(32,dtype=np.uint8)[None,:]*8
        path=tmp_path/f'color{number}.tif'
        tifffile.imwrite(path,rgb,photometric='rgb',**({'extrasamples':'unassalpha'} if alpha else {}))
        image=panel.import_image(path);sources.append(image)
        expected.append(render_fluorescence_qimage(panel.image_data(image['uid']),image,image['channels'],view_mode='merge'))
    merged=panel.merge_images_as_channels([image['uid'] for image in sources])
    for i,before in enumerate(expected):
        assert render_fluorescence_qimage(panel.image_data(merged['uid']),merged,merged['channels'],channel=merged['channels'][i])==before
    # A merged image is self-contained, not a render-time dependency on sources.
    panel.data['fluorescence_images']=[merged]
    for image in sources:panel.remove_cached_image_data(image['uid'])
    project=Project(title='color');project.add_panel(panel);target=tmp_path/'color.sfig';project.save_sfig(target)
    loaded=Project.load_sfig(target);restored=loaded.panels[0];record=restored.active_image()
    try:
        for i,before in enumerate(expected):
            assert render_fluorescence_qimage(restored.image_data(record['uid']),record,record['channels'],channel=record['channels'][i])==before
        raw_path=restored.save_image_data_as_ome_tiff(record['uid'],tmp_path/'raw.ome.tif')
        raw=tifffile.imread(raw_path)
        assert raw.shape==(2,24,32),'Display components must not become scientific TIFF channels'
        assert record['metadata']['n_channels']==2
        np.testing.assert_array_equal(raw,restored.image_data(record['uid'])[:2])
    finally:loaded.cleanup_archive_extract_root()


def test_merge_edited_rgb_components_preserves_display_without_double_processing(tmp_path):
    from ui.panels.fluorescence_image_export import render_fluorescence_export_payloads
    panel=FluorescencePanel(panel_id='edited-rgb');sources=[];expected=[]
    for number in range(2):
        data=np.arange(24*32*3,dtype=np.uint16).reshape(24,32,3)%240
        path=tmp_path/f'rgb{number}.tif';tifffile.imwrite(path,data.astype(np.uint8),photometric='rgb')
        record=panel.import_image(path);panel.force_split_rgb_samples(record['uid'])
        panel.update_channel(record['uid'],1,display_min=30,display_max=210,display_processing={'strength':20})
        sources.append(record)
        expected.append(render_fluorescence_qimage(panel.image_data(record['uid']),record,panel._styled_channels_for_render(record['channels'],record),view_mode='merge'))
    merged=panel.merge_images_as_channels([r['uid'] for r in sources])
    exports=render_fluorescence_export_payloads(panel,merged,source='original',composition='channels')
    assert [image for _,image in exports]==expected
    panel.update_channel(merged['uid'],0,pseudocolor='#00FF00')
    changed=render_fluorescence_export_payloads(panel,merged,source='original',composition='channels')[0][1]
    assert changed!=expected[0]
    initial=render_fluorescence_export_payloads(panel,merged,source='unadjusted',composition='channels')[0][1]
    assert initial==expected[0]


def test_merge_preserves_each_channels_color_contrast_and_processing(tmp_path):
    panel=FluorescencePanel(panel_id='colors');sources=[];settings=[];pixels=[]
    for index,color in enumerate(('#FF0000','#0000FF')):
        path=tmp_path/f'channel{index}.tif'
        tifffile.imwrite(path,np.arange(400,dtype=np.uint16).reshape(20,20),metadata={'axes':'YX'})
        image=panel.import_image(path)
        image['channels'][0].update(pseudocolor=color,color_source='file_metadata',display_min=20,display_max=500,
            display_processing={'strength':20+index*10,'denoise':0})
        sources.append(image);settings.append(deepcopy(image['channels'][0]));pixels.append(panel.image_data(image['uid']).copy())
    original=deepcopy(sources)
    merged=panel.merge_images_as_channels([image['uid'] for image in sources])
    for index,old in enumerate(settings):
        new=merged['channels'][index]
        for key in ('pseudocolor','display_min','display_max','display_processing'):
            assert new[key]==old[key]
        before=render_fluorescence_qimage(pixels[index],sources[index],sources[index]['channels'],channel=old)
        after=render_fluorescence_qimage(panel.image_data(merged['uid']),merged,merged['channels'],channel=new)
        assert before==after
        np.testing.assert_array_equal(panel.image_data(merged['uid'])[index],pixels[index][0])
    assert sources==original


def test_merge_rgb_single_signal_retains_red_and_blue_not_palette_reassignment(tmp_path):
    panel=FluorescencePanel(panel_id='rgb');sources=[]
    for index in (0,2):
        rgb=np.zeros((24,32,3),np.uint8);rgb[4:20,5:25,index]=200
        path=tmp_path/f'signal_{index}.tif';tifffile.imwrite(path,rgb,photometric='rgb')
        sources.append(panel.import_image(path))
    merged=panel.merge_images_as_channels([image['uid'] for image in sources])
    channels=panel._styled_channels_for_render(merged['channels'],merged)
    for index,source in enumerate(sources):
        original=render_fluorescence_qimage(panel.image_data(source['uid']),source,source['channels'],view_mode='merge')
        exported=render_fluorescence_qimage(panel.image_data(merged['uid']),merged,channels,channel=channels[index])
        assert original==exported


def test_merge_and_explicit_color_edits_are_not_overridden_by_global_palette(tmp_path,monkeypatch):
    panel=FluorescencePanel(panel_id='palette');uids=[]
    for index in range(2):
        path=tmp_path/f'{index}.tif';tifffile.imwrite(path,np.arange(64,dtype=np.uint16).reshape(8,8))
        image=panel.import_image(path);uids.append(image['uid'])
    monkeypatch.setattr(panel,'_render_image_plate_style',lambda: {'channel_colors':['#00FFFF','#FF00FF']})
    panel.update_channel(uids[0],0,pseudocolor='#FF0000')
    assert panel._styled_channels_for_render(panel.images()[0]['channels'],panel.images()[0])[0]['pseudocolor']=='#FF0000'
    merged=panel.merge_images_as_channels(uids)
    assert merged['channels'][0]['pseudocolor']=='#FF0000'
    monkeypatch.setattr(panel,'_render_image_plate_style',lambda: {'channel_colors':['#00FF00','#FFFFFF']})
    assert panel._styled_channels_for_render(merged['channels'],merged)[0]['pseudocolor']=='#FF0000'


def test_merge_different_bit_depths_preserves_raw_values_and_display(tmp_path):
    panel=FluorescencePanel(panel_id='depth');uids=[]
    for dtype,value in ((np.uint8,180),(np.uint16,4000)):
        path=tmp_path/f'{value}.tif';tifffile.imwrite(path,np.full((16,20),value,dtype=dtype))
        image=panel.import_image(path);uids.append(image['uid'])
    merged=panel.merge_images_as_channels(uids)
    data=panel.image_data(merged['uid'])
    assert data.dtype==np.uint16 and int(data[1].max())==4000
    assert merged['metadata']['bit_depth']==16


def test_canvas_and_export_share_color_rules_after_merge(tmp_path,monkeypatch):
    from ui.widgets.fluoro_canvas import FluorescenceCanvas
    import ui.widgets.fluoro_canvas as canvas_module
    panel=FluorescencePanel(panel_id='canvas');uids=[]
    for index in range(2):
        path=tmp_path/f'{index}.tif';tifffile.imwrite(path,np.arange(400,dtype=np.uint16).reshape(20,20))
        image=panel.import_image(path);image['channels'][0]['pseudocolor']='#FF0000' if index==0 else '#0000FF'
        uids.append(image['uid'])
    merged=panel.merge_images_as_channels(uids)
    monkeypatch.setattr(panel,'_render_image_plate_style',lambda: {'channel_colors':['#00FF00','#00FFFF']})
    monkeypatch.setattr(canvas_module,'resolve_runtime_image_plate_style_if_active',lambda *a:{'channel_colors':['#00FF00','#00FFFF']})
    canvas=FluorescenceCanvas();canvas.set_panel(panel)
    assert canvas._styled_channels_for_image(merged)==panel._styled_channels_for_render(merged['channels'],merged)
    canvas.close()
