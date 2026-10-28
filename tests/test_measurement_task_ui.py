"""Approved A workbench: persistent tasks, isolated pages, full parameter access."""
from pathlib import Path
from copy import deepcopy
import numpy as np
import pytest
from PIL import Image
from PyQt6.QtWidgets import QComboBox,QLabel,QAbstractSpinBox,QWidget


@pytest.fixture
def window(qt_application,desktop_screen):
    from ui.main_window import MainWindow
    w=MainWindow(Path(__file__).resolve().parents[1]/'resources');w.project_recovery.timer.stop()
    yield w
    w._capture_clean_project_baseline();w.close();qt_application.processEvents()


def test_brightfield_import_stain_field_is_not_compressed(window,qt_application,tmp_path,request):
    from PyQt6.QtGui import QFont
    w=window;old_style=qt_application.styleSheet();old_font=qt_application.font()
    request.addfinalizer(lambda:qt_application.setStyleSheet(old_style))
    request.addfinalizer(lambda:qt_application.setFont(old_font))
    qt_application.setFont(QFont('Microsoft YaHei',10))
    qt_application.setStyleSheet((Path(__file__).resolve().parents[1]/'resources/sciefig.qss').read_text(encoding='utf-8'))
    path=tmp_path/'he.tif';Image.new('RGB',(100,80),(210,90,160)).save(path)
    w._active_brightfield_panel().import_image(path)
    w.resize(1280,800);w.switch_language('zh');w.set_module('brightfield');w.set_brightfield_workflow_step(0)
    w.v9_shell.set_right_panel_width(280);w.show()
    for _ in range(20):qt_application.processEvents()
    combo=w.data_panel.brightfield_stain_type_combo
    assert combo.height()>=combo.minimumSizeHint().height(),(combo.size(),combo.minimumSizeHint())
    button=w.data_panel.brightfield_apply_metadata_button
    assert not combo.geometry().intersects(button.geometry()),(combo.geometry(),button.geometry())


def test_he_count_and_central_classification_are_separate_controllers(window):
    w=window
    assert hasattr(w,'central_nuclei_controller'), 'Central nuclei must have an independent feature'
    he=w.nuclei_controllers['brightfield'];joint=w.central_nuclei_controller
    assert he is not joint and he.controls is not joint.controls
    assert he.job_key!=joint.job_key
    assert he.current_task()=='he' and joint.current_task()=='muscle'
    assert not hasattr(he.controls,'task'), 'Remove the old nested task selector'
    he.controls.diameter.setValue(38)
    assert joint.controls.band.value()==20


@pytest.mark.parametrize('module,expected',[('brightfield',{'cells','area','fiber','he','central','myotube'}),
    ('fluoro',{'roi_intensity','spot_count','dapi','myotube','ai_assisted','line_profile','colocalization'})])
def test_task_buttons_persist_one_page_and_footer_follows_selection(window,qt_application,module,expected):
    w=window;w.set_module(module)
    if module=='brightfield':w.set_brightfield_workflow_step(2)
    else:w.fluoro_workspace.set_step(2)
    assert hasattr(w,'measurement_tasks'), 'Approved persistent task workbench is missing'
    deck=w.measurement_tasks[module];w.show();qt_application.processEvents()
    assert set(deck.selector.buttons)==expected
    for task in expected:
        deck.selector.buttons[task].click();qt_application.processEvents();w.workflow_ui.sync()
        assert deck.current_task()==task
        assert deck.selector.isVisible()
        assert all(button.isVisible() for button in deck.selector.buttons.values())
        assert deck.stack.currentWidget() is deck.pages[task]
        assert sum(not deck.stack.widget(i).isHidden() for i in range(deck.stack.count()))==1
        assert w.workflow_ui.footers[module].source is deck.primary_action()
    if module=='brightfield':
        deck.selector.buttons['central'].click();qt_application.processEvents()
        assert w.central_nuclei_controller.controls.muscle_options.isVisible()
        assert not w.nuclei_controllers['brightfield'].controls.nuclear_options.isVisible()


def test_task_switch_keeps_parameters_and_does_not_mix_result_records(window,qt_application,tmp_path):
    w=window
    assert hasattr(w,'measurement_tasks')
    panel=w._active_brightfield_panel();path=tmp_path/'he.png';Image.fromarray(np.full((80,100,3),180,np.uint8)).save(path)
    image=panel.import_image(path);w.set_module('brightfield');w.set_brightfield_workflow_step(2)
    he=w.nuclei_controllers['brightfield'];central=w.central_nuclei_controller;deck=w.measurement_tasks['brightfield']
    deck.select('he');he.controls.diameter.setValue(19)
    he.accept_result(dict(task='he',image_uid=image['uid'],nuclei=[],parameters={'diameter_px':19},provenance={}))
    saved=deepcopy(he.current_result())
    deck.select('central');central.controls.band.setValue(31)
    deck.select('myotube');w.myotube_controllers['brightfield'].controls.diameter.setValue(73)
    deck.select('he');assert he.controls.diameter.value()==19 and he.current_result()==saved
    deck.select('central');assert central.controls.band.value()==31 and central.current_result() is None
    deck.select('myotube');assert w.myotube_controllers['brightfield'].controls.diameter.value()==73


@pytest.mark.parametrize('language',['zh','en'])
@pytest.mark.parametrize('module',['brightfield','fluoro'])
def test_narrow_task_forms_do_not_overlap(window,qt_application,language,module):
    w=window;w.switch_language(language);w.set_module(module)
    assert hasattr(w,'measurement_tasks')
    deck=w.measurement_tasks[module];deck.panel.setFixedWidth(308)
    if module=='brightfield':w.set_brightfield_workflow_step(2)
    else:w.fluoro_workspace.set_step(2)
    w.show()
    for task in ('central','he','myotube') if module=='brightfield' else ('dapi','myotube'):
        deck.select(task)
        for _ in range(4):qt_application.processEvents()
        page=deck.pages[task]
        for widget in page.findChildren(QAbstractSpinBox)+page.findChildren(QComboBox):
            if widget.isVisible():assert widget.width()>=60 and widget.height()>=widget.minimumSizeHint().height()
        for label in page.findChildren(QLabel):
            if label.isVisible():
                assert label.height()>=label.fontMetrics().height()
                if label.wordWrap():assert label.height()>=label.heightForWidth(label.width()), (task,label.text(),label.height(),label.heightForWidth(label.width()))


def test_task_page_does_not_reappear_after_module_switch(window,qt_application):
    w=window;w.set_module('brightfield');w.set_brightfield_workflow_step(2)
    deck=w.measurement_tasks['brightfield'];deck.select('central')
    w.set_module('fluoro');w.set_module('brightfield');qt_application.processEvents();w.workflow_ui.sync()
    assert deck.stack.currentWidget() is deck.pages['central']
    assert all(page.isHidden() for key,page in deck.pages.items() if key!='central')


def test_completed_background_nuclear_job_does_not_steal_selected_task(window,tmp_path):
    w=window;panel=w._active_brightfield_panel();path=tmp_path/'he.png'
    Image.fromarray(np.full((40,60,3),180,np.uint8)).save(path);image=panel.import_image(path)
    deck=w.measurement_tasks['brightfield'];deck.select('myotube')
    c=w.nuclei_controllers['brightfield']
    c.accept_result(dict(task='he',image_uid=image['uid'],nuclei=[],parameters={},provenance={}))
    assert deck.current_task()=='myotube'
    assert c.current_result() is not None


def test_native_fluoro_task_switch_keeps_container_and_table_heights(window,qt_application,tmp_path):
    from PyQt6.QtGui import QFont,QFontDatabase
    from PyQt6.QtCore import QPoint
    w=window;old_style=qt_application.styleSheet();old_font=qt_application.font()
    try:
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
        qt_application.setFont(QFont('Microsoft YaHei',10))
        qt_application.setStyleSheet((Path(__file__).resolve().parents[1]/'resources/sciefig.qss').read_text(encoding='utf-8'))
        w.resize(1600,1040);w.switch_language('zh');w.set_module('fluoro')
        import tifffile
        path=tmp_path/'channels.tif'
        tifffile.imwrite(path,np.full((2,64,80),100,np.uint16),metadata={'axes':'CYX'},photometric='minisblack')
        w._active_fluoro_panel().import_image(path)
        f=w.fluoro_workspace;f.set_step(2);w.show();deck=w.measurement_tasks['fluoro']
        for task in ('roi_intensity','spot_count','myotube','dapi','line_profile','colocalization','ai_assisted'):
            deck.selector.buttons[task].click()
            for _ in range(8):qt_application.processEvents()
        f.ai_backend_combo.setCurrentIndex(f.ai_backend_combo.findData('cellpose_cyto3'))
        for _ in range(12):qt_application.processEvents()
        for stack in (f.measure_params_stack,f.measure_summary_stack):
            page=stack.currentWidget()
            assert stack.height()>=page.minimumSizeHint().height(), (stack.height(),page.minimumSizeHint().height())
            assert stack.height()>=page.heightForWidth(stack.width()), (stack.height(),page.heightForWidth(stack.width()))
        params_bottom=f.measure_params_stack.mapTo(deck.stack.currentWidget(),QPoint(0,f.measure_params_stack.height())).y()
        assert not f.calibration_controls.isVisibleTo(f.workflow_panel)
        assert f.measure_summary_stack.y()>=params_bottom
        table_bottom=f.ai_results_table.y()+f.ai_results_table.height()
        assert f.ai_summary_stats_table.y()>=table_bottom
        native=deck.stack.currentWidget();layout=native.layout();last_bottom=0
        for index in range(layout.count()):
            item=layout.itemAt(index);widget=item.widget()
            if widget is None or widget.isHidden():continue
            assert widget.y()>=last_bottom,(index,last_bottom,widget.y())
            assert widget.y()-last_bottom<=24,(index,last_bottom,widget.y())
            last_bottom=widget.y()+widget.height()
    finally:
        qt_application.setStyleSheet(old_style);qt_application.setFont(old_font)


def test_task_disclosure_reflows_without_reserving_expanded_space(window,qt_application):
    from ui.widgets.workflow_presentation import WorkflowDisclosure
    w=window;w.set_module('fluoro');f=w.fluoro_workspace;f.set_step(2);w.show()
    deck=w.measurement_tasks['fluoro'];deck.select('ai_assisted')
    section=next(s for s in f.ai_assisted_group.findChildren(WorkflowDisclosure) if s.title_key=='task_parameters')
    for _ in range(10):qt_application.processEvents()
    before=f.measure_params_stack.height()
    section.setExpanded(True)
    for _ in range(10):qt_application.processEvents()
    expanded=f.measure_params_stack.height()
    assert expanded>before+60
    section.setExpanded(False)
    for _ in range(10):qt_application.processEvents()
    assert f.measure_params_stack.height()==before
    assert section.height()<=section.toggle.sizeHint().height()+12


def test_preview_switches_remain_directly_accessible_and_tables_scroll(window,qt_application):
    from PyQt6.QtWidgets import QHeaderView,QTableWidget
    w=window;w.show()
    for module in ('brightfield','fluoro'):
        w.set_module(module)
        if module=='brightfield':w.set_brightfield_workflow_step(2)
        else:w.fluoro_workspace.set_step(2)
        deck=w.measurement_tasks[module];deck.select('fiber' if module=='brightfield' else 'ai_assisted')
        for _ in range(10):qt_application.processEvents()
        owner=w.brightfield_workspace if module=='brightfield' else w.fluoro_workspace
        for name in (('original_preview_button','overlay_preview_button') if module=='brightfield' else ('ai_original_preview_button','ai_overlay_preview_button')):
            assert deck.selector_wrap.isAncestorOf(getattr(owner,name)), name
        for table in deck.stack.findChildren(QTableWidget):
            header=table.horizontalHeader()
            assert header.minimumSectionSize()>=80
            assert all(header.sectionResizeMode(i)!=QHeaderView.ResizeMode.Stretch for i in range(table.columnCount()))


@pytest.mark.parametrize('language',['zh','en'])
@pytest.mark.parametrize('module',['brightfield','fluoro'])
def test_all_task_controls_do_not_overlap_when_sections_expand(window,qt_application,module,language,request):
    from PyQt6.QtWidgets import QAbstractButton,QTableWidget,QListWidget
    from PyQt6.QtGui import QFont,QFontDatabase
    from ui.widgets.workflow_presentation import WorkflowDisclosure
    old_style=qt_application.styleSheet();old_font=qt_application.font()
    request.addfinalizer(lambda:qt_application.setStyleSheet(old_style))
    request.addfinalizer(lambda:qt_application.setFont(old_font))
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc');qt_application.setFont(QFont('Microsoft YaHei',10))
    qt_application.setStyleSheet((Path(__file__).resolve().parents[1]/'resources/sciefig.qss').read_text(encoding='utf-8'))
    w=window;w.switch_language(language);w.set_module(module)
    deck=w.measurement_tasks[module];deck.panel.setFixedWidth(332)
    if module=='brightfield':w.set_brightfield_workflow_step(2)
    else:w.fluoro_workspace.set_step(2)
    w.show()
    for task in deck.selector.buttons:
        deck.select(task)
        if task=='ai_assisted':
            f=w.fluoro_workspace;f.ai_backend_combo.setCurrentIndex(f.ai_backend_combo.findData('cellpose_cyto3'))
        for expanded in (False,True,False):
            for section in deck.pages[task].findChildren(WorkflowDisclosure):section.setExpanded(expanded)
            for _ in range(12):qt_application.processEvents()
            controls=[x for x in deck.pages[task].findChildren(QWidget) if isinstance(x,(QLabel,QAbstractButton,QAbstractSpinBox,QComboBox,QTableWidget,QListWidget)) and x.isVisible()]
            for i,first in enumerate(controls):
                for second in controls[i+1:]:
                    if first.parentWidget() is second.parentWidget():
                        assert not first.geometry().intersects(second.geometry()),(module,language,task,type(first).__name__,getattr(first,'text',lambda:'')(),type(second).__name__,getattr(second,'text',lambda:'')())


@pytest.mark.parametrize('language',['zh','en'])
@pytest.mark.parametrize('module',['fluoro','brightfield','ihc'])
def test_scale_editor_is_primary_only_at_import_and_layout(window,qt_application,module,language):
    from ui.widgets.workflow_presentation import WorkflowDisclosure
    w=window;w.switch_language(language);w.set_module(module);workspace=getattr(w,module+'_workspace');workspace.workflow_panel.setFixedWidth(300);w.show()
    w.workflow_ui.buttons[module][0].click()
    for _ in range(10):qt_application.processEvents()
    for control in (workspace.scale_bar_length_spin,workspace.scale_bar_apply_current_button,workspace.scale_bar_remove_current_button):
        assert control.isVisibleTo(workspace.workflow_panel)
        assert not any(section.isAncestorOf(control) and section.toggle.isVisible() for section in workspace.workflow_panel.findChildren(WorkflowDisclosure))
        assert control.geometry().right()<control.parentWidget().width(),(language,module,control.geometry().getRect(),control.parentWidget().width())
    calibration=workspace.calibration_controls if module!='brightfield' else w.data_panel.brightfield_calibration_controls
    assert calibration.isVisibleTo(workspace.workflow_panel)
    for button in (calibration.save_button,calibration.rename_button,calibration.apply_preset_button):
        assert button.isVisibleTo(workspace.workflow_panel)
    layout_step=3 if module=='ihc' else 4
    for step in range(1,layout_step):
        w.workflow_ui.buttons[module][step].click()
        for _ in range(5):qt_application.processEvents()
        assert not calibration.isVisibleTo(workspace.workflow_panel)
        assert not workspace.scale_bar_apply_current_button.isVisibleTo(workspace.workflow_panel)
        assert not workspace.scale_bar_length_spin.isVisibleTo(workspace.workflow_panel)
        if module=='fluoro':
            assert not workspace.canvas.view.scale_editing_enabled
        else:
            assert not workspace.canvas.scale_editing_enabled
    w.workflow_ui.buttons[module][layout_step].click()
    for _ in range(10):qt_application.processEvents()
    assert workspace.scale_display_section.expanded
    assert not workspace.scale_display_section.toggle.isVisible()


@pytest.mark.parametrize('module',['brightfield','ihc','fluoro'])
def test_import_scale_apply_stays_at_import_and_uses_embedded_metadata(window,qt_application,tmp_path,module):
    import tifffile
    w=window;path=tmp_path/'calibrated.ome.tif'
    tifffile.imwrite(path,np.full((256,512),90,np.uint8),ome=True,
        metadata={'axes':'YX','PhysicalSizeX':.5,'PhysicalSizeXUnit':'µm','PhysicalSizeY':.5,'PhysicalSizeYUnit':'µm'})
    panel=getattr(w,'_active_'+module+'_panel')();image=panel.import_image(path)
    w.set_module(module);workspace=getattr(w,module+'_workspace');workspace.set_panel(panel)
    w.workflow_ui.buttons[module][0].click();w.show()
    if module!='fluoro':getattr(w,'_refresh_'+module+'_preview')(panel)
    for _ in range(8):qt_application.processEvents()
    assert panel.measurement_scale_bar(image['uid'])['calibration_source']=='embedded'
    # A previously used Layout-only override must not supersede native
    # calibration through a field that is hidden at intake.
    workspace.scale_bar_manual_um_per_px_spin.setValue(.125)
    workspace.scale_bar_length_spin.setValue(25)
    workspace.scale_bar_apply_current_button.click()
    for _ in range(8):qt_application.processEvents()
    assert w.workflow_ui._step(module)==0, 'Adding a scale at intake must not navigate to Layout'
    assert workspace.canvas.current_step==0 if module=='fluoro' else workspace.canvas_stack.currentWidget() is workspace.canvas
    states=panel.active_image()['layout_scale_bars'] if module=='fluoro' else panel.data[module+'_layout']['scale_bars']
    assert all('manual_um_per_px' not in state for state in states)
    if module=='fluoro':
        assert any(item.data(0)=='fluoro_scale_bar' for item in workspace.canvas.scene.items())
    else:
        canvas=workspace.canvas
        assert canvas.image_label.pixmap().toImage()!=canvas._scaled_base_pixmap(canvas._image_display_size()).toImage()
        workspace.scale_bar_remove_current_button.click()
        assert canvas.image_label.pixmap().toImage()==canvas._scaled_base_pixmap(canvas._image_display_size()).toImage()


@pytest.mark.parametrize('module',['brightfield','ihc','fluoro'])
def test_import_scale_requires_calibration_and_drawing_does_not_change_task(window,qt_application,tmp_path,module):
    from PyQt6.QtWidgets import QMessageBox
    w=window;path=tmp_path/'uncalibrated.png';Image.new('RGB',(400,200),(90,90,90)).save(path)
    panel=getattr(w,'_active_'+module+'_panel')();image=panel.import_image(path)
    w.set_module(module);workspace=getattr(w,module+'_workspace');workspace.set_panel(panel)
    w.workflow_ui.buttons[module][0].click()
    assert not panel.measurement_scale_bar(image['uid'])['calibrated']
    messages=[]
    # Nonmodal capture of the same validation warning shown to users.
    from unittest.mock import patch
    with patch.object(QMessageBox,'information',side_effect=lambda *args:messages.append(args)):
        before=deepcopy(panel.data)
        workspace.scale_bar_length_spin.setValue(40)
        assert not messages and panel.data==before, 'Editing settings must wait for explicit Apply'
        workspace.scale_bar_apply_current_button.click()
    assert messages, 'Unknown calibration must not silently create an arbitrary physical scale'
    assert w.workflow_ui._step(module)==0


    editor=w.workflow_ui.scales.editors[module];editor.length_spin.setValue(50)
    if module=='fluoro':
        before=workspace.measure_mode;workspace.ai_draw_scale_line_button.click()
        assert workspace.measure_mode==before
        workspace._add_drawn_roi('scale_line',dict(x1=10,y1=100,x2=110,y2=100))
    elif module=='ihc':
        workspace.draw_scale_button.click();workspace._set_measurement_scale_line(dict(x1=10,y1=100,x2=110,y2=100))
    else:
        w.data_panel.brightfield_he_fiber_draw_scale_line_button.click()
        w.set_brightfield_measurement_scale_line(dict(x1=10,y1=100,x2=110,y2=100))
    assert panel.measurement_scale_bar(image['uid'])['um_per_px']==pytest.approx(.5)
    workspace.scale_bar_apply_current_button.click()
    assert w.workflow_ui._step(module)==0


@pytest.mark.parametrize('module',['brightfield','ihc','fluoro'])
def test_scale_source_and_color_labels_follow_chinese_language(window,qt_application,tmp_path,module):
    w=window;path=tmp_path/'source.png';Image.new('RGB',(400,200)).save(path)
    panel=getattr(w,'_active_'+module+'_panel')();image=panel.import_image(path)
    panel.update_measurement_scale_bar(image['uid'],um_per_px=.5,calibration_source='manual')
    w.set_module(module);workspace=getattr(w,module+'_workspace');workspace.set_panel(panel)
    w.switch_language('zh');w.workflow_ui.buttons[module][0].click()
    editor=w.workflow_ui.scales.editors[module]
    assert '参考线实际长度' in editor.length_label.text()
    assert all(word not in editor.status.text() for word in ('manual','embedded','assumed'))
    assert workspace.scale_bar_line_color_combo.itemText(workspace.scale_bar_line_color_combo.findData('#FFFFFF'))=='白色'


@pytest.mark.parametrize('module',['brightfield','ihc','fluoro'])
def test_layout_shared_format_editor_is_primary_without_changing_selected_text(window,qt_application,tmp_path,module):
    from PyQt6.QtCore import QRectF
    from ui.widgets.workflow_presentation import WorkflowDisclosure
    w=window;path=tmp_path/'source.png';Image.fromarray(np.full((80,120,3),120,np.uint8)).save(path)
    panel=getattr(w,'_active_'+module+'_panel')();panel.import_image(path);w.set_module(module);workspace=getattr(w,module+'_workspace')
    workspace.set_panel(panel)
    if module=='fluoro':workspace.set_step(4)
    else:getattr(w,'show_'+module+'_layout')()
    w._sync_shared_layout_editor();controller=w.layout_edit_controller
    assert controller.canvas is not None
    controller.add('text',QRectF(20,20,100,40));w.show()
    for _ in range(12):qt_application.processEvents()
    controls=controller.controls
    assert workspace.workflow_panel.isAncestorOf(controls)
    for widget in (controls.font_family,controls.font_size,controls.text_color):
        assert widget.isVisible()
        viewport=controller._docked_scroll.viewport()
        assert viewport.rect().contains(widget.mapTo(viewport,widget.rect().center()))
        assert not any(s.isAncestorOf(widget) for s in workspace.workflow_panel.findChildren(WorkflowDisclosure))
    before=deepcopy(panel.data);controller.sync_selection()
    assert panel.data==before
    controller.show();assert not controller.dialog.isVisible()
    geometry_form=controller.geometry_panel.layout()
    w.switch_language('zh')
    assert geometry_form.labelForField(controller.geometry_fields['width']).text()=='宽度'
    w.switch_language('en')
    assert geometry_form.labelForField(controller.geometry_fields['width']).text()=='Width'
    assert hasattr(controller.tools.bar,'inline_panel')
    assert workspace.workflow_panel.isAncestorOf(controller.tools.bar.inline_panel)
    assert not any(button.isVisible() for button in controller.tools.bar.buttons.values())


@pytest.mark.parametrize('module,step',[('qpcr',2),('wb',5),('fluoro',4),('ihc',3),('brightfield',4)])
def test_layout_parameters_have_no_disclosure_level(window,qt_application,module,step):
    from ui.widgets.workflow_presentation import WorkflowDisclosure
    w=window;w.set_module(module);w.show()
    w.workflow_ui.buttons[module][step].click()
    for _ in range(10):qt_application.processEvents()
    panel=w.workflow_ui.panels[module]
    for section in panel.findChildren(WorkflowDisclosure):
        if section.isVisibleTo(panel):
            assert section.expanded and not section.toggle.isVisible(),(module,section.title_key)


def test_composer_uses_the_same_primary_format_and_flat_commands(window,qt_application):
    from PyQt6.QtCore import QRectF
    w=window;w.set_module('composer');workspace=w.composer_workspace;w.show()
    workspace.controller.add_annotation('text',QRectF(20,20,100,40))
    for _ in range(10):qt_application.processEvents()
    assert workspace.sidebar.isAncestorOf(workspace.format_controls.font_family)
    assert workspace.format_controls.font_family.isVisible()
    bar=workspace.controller.tools.bar
    assert workspace.sidebar.isAncestorOf(bar.inline_panel)
    assert not any(button.isVisible() for button in bar.buttons.values())
