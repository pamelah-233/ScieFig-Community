from pathlib import Path
import numpy as np
import pytest

from core.image_analysis import cellpose_cells


@pytest.mark.parametrize('strategy,tile', [('general',0),('local_contrast',128)])
def test_strategy_uses_same_model_native_signal_and_geometry(monkeypatch,strategy,tile):
    calls=[]
    def infer(image,**options):
        calls.append((image.copy(),options))
        labels=np.zeros(image.shape,np.int32);labels[5:17,6:19]=1
        return labels,dict(model='cyto3',inference_options={'normalization_tile_px':options.get('normalization_tile_px',0)})
    monkeypatch.setattr(cellpose_cells,'run_cellpose_labels',infer)
    image=np.full((30,40,3),110,np.uint8);before=image.copy()
    result=cellpose_cells.analyze_cellpose_cells(image,0.5,settings={'cellpose_strategy':strategy,'model_diameter_px':45,'exclude_scale_bar':False})
    assert calls[0][1].get('normalization_tile_px',0)==tile
    assert calls[0][1]['diameter']==45 and calls[0][1].get('model','cyto3')=='cyto3'
    assert result['settings']['cellpose_strategy']==strategy
    assert result['results'][0]['mean_intensity']==110
    assert result['results'][0]['area_um2']==result['results'][0]['area_px']*0.25
    np.testing.assert_array_equal(image,before)


def test_unknown_strategy_fails_before_inference(monkeypatch):
    monkeypatch.setattr(cellpose_cells,'run_cellpose_labels',lambda *a,**k:pytest.fail('Invalid strategy must not start inference'))
    with pytest.raises(ValueError,match='strategy'):
        cellpose_cells.analyze_cellpose_cells(np.zeros((20,30,3),np.uint8),1.,settings={'cellpose_strategy':'unknown'})


def test_ui_two_strategies_editable_size_and_no_automatic_preset(qt_application):
    from ui.main_window import MainWindow
    from core.utils.i18n import get_language,set_language,tr
    previous=get_language()
    window=MainWindow(Path(__file__).resolve().parents[1]/'resources');window.project_recovery.timer.stop()
    try:
        ui=window.data_panel
        assert not hasattr(ui,'brightfield_cellpose_checkbox')
        assert ui.bright_round_cell_analysis_settings()['backend']=='traditional'
        assert ui.brightfield_cellpose_strategy_combo.currentData()=='traditional'
        ui.set_cell_analysis_settings({'backend':'cellpose','model_diameter_px':37})
        combo=ui.brightfield_cellpose_strategy_combo
        assert [combo.itemData(i) for i in range(combo.count())]==['traditional','general','local_contrast']
        combo.setCurrentIndex(combo.findData('local_contrast'))
        assert ui.brightfield_cellpose_diameter_spin.value()==37
        settings=ui.bright_round_cell_analysis_settings()
        assert settings['cellpose_strategy']=='local_contrast'
        ui.brightfield_cellpose_reference_combo.setCurrentIndex(ui.brightfield_cellpose_reference_combo.findData(45))
        ui.brightfield_cellpose_reference_combo.activated.emit(ui.brightfield_cellpose_reference_combo.currentIndex())
        assert ui.brightfield_cellpose_diameter_spin.value()==45
        ui.brightfield_cellpose_diameter_spin.setValue(52)
        for language in ('zh','en'):
            window.switch_language(language)
            assert combo.currentText()==tr('cellpose_cell_strategy_local_contrast')
            assert ui.brightfield_cellpose_diameter_spin.value()==52
        ui.set_cell_analysis_settings(settings)
        assert ui.bright_round_cell_analysis_settings()==settings
        combo.setCurrentIndex(combo.findData('traditional'))
        traditional=ui.bright_round_cell_analysis_settings()
        assert traditional['backend']=='traditional'
        assert ui.brightfield_bright_round_cell_threshold_spin.isEnabled()
        assert not ui.brightfield_cellpose_diameter_spin.isEnabled()
        assert combo.currentText() in ui.brightfield_cellpose_model_hint.text()
        ui.set_cell_analysis_settings(traditional)
        assert ui.bright_round_cell_analysis_settings()==traditional
        combo.setCurrentIndex(combo.findData('general'))
        assert ui.bright_round_cell_analysis_settings()['backend']=='cellpose'
        assert ui.brightfield_cellpose_diameter_spin.isEnabled()
        assert not ui.brightfield_bright_round_cell_threshold_spin.isEnabled()
        assert ui.brightfield_cellpose_diameter_spin.value()==settings['model_diameter_px']
        ui.set_cell_analysis_settings({'backend':'cellpose'})
        assert combo.currentData()=='general' and ui.brightfield_cellpose_diameter_spin.value()==30
        assert not window._analysis_job_service.is_busy('brightfield_bright_round_cells')
    finally:
        window._capture_clean_project_baseline();window.close();window.deleteLater();set_language(previous);qt_application.processEvents()

