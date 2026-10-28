"""One annotation tool window for source Layouts; analysis/ROI logic stays in modules."""
from copy import deepcopy
import sys
from uuid import uuid4
from PyQt6 import sip
from PyQt6.QtCore import QObject,QEvent,QPoint,Qt,QSignalBlocker,QTimer
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QScrollArea,QWidget,QFormLayout,QDoubleSpinBox
from core.models.layout_editor import LayoutObject,LayoutSourceIdentity
from core.utils.i18n import get_language,tr,bind_i18n
from ui.widgets.shared_layout_controls import SharedLayoutControls,apply_selection_style
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_shapes import SharedShapeItem
from ui.widgets.shared_layout_drawing import LayoutDrawTool
from ui.layout_adapters.annotation_records import text_from_record,shape_from_record
from ui.controllers.layout_tools_controller import LayoutToolsController


def _qt_alive(obj):
    return obj is not None and not sip.isdeleted(obj)


class LayoutEditController(QObject):
    def __init__(self,window):
        super().__init__(window)
        self.window=window
        self.module=None;self.panel=None;self.canvas=None;self.workspace=None
        self.draw=None
        self.undo_states=[];self.redo_states=[]
        self._before=None
        self._last=None
        self._studio_object_controls = None
        self.tools=LayoutToolsController(self)
        self.tools.message.connect(lambda text:self.window.statusBar().showMessage(text,5000))
        self.tools.properties_requested.connect(self.show)
        self.tools.insert_requested.connect(self.arm)
        self.dialog=QDialog(window,Qt.WindowType.Tool)
        self.dialog.setProperty('parameterScrollSurface',True)
        self.dialog.setWindowTitle('文字与图形标注')
        self.dialog.resize(310,570)
        root=QVBoxLayout(self.dialog)
        self.hint=QLabel('只编辑排版标注，不改变测量 ROI。')
        self.hint.setWordWrap(True);root.addWidget(self.hint)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        self._controls_scroll=scroll
        self.controls=SharedLayoutControls();scroll.setWidget(self.controls);root.addWidget(scroll)
        self.geometry_panel=QWidget()
        geometry_form=QFormLayout(self.geometry_panel)
        self.geometry_fields={}
        for key,title in [('x','X'),('y','Y'),('width','layout_geometry_width'),('height','layout_geometry_height'),('rotation','rotation')]:
            spin=QDoubleSpinBox();spin.setDecimals(2)
            spin.setRange(.1 if key in {'width','height'} else -100000,100000)
            if key=='rotation':spin.setSuffix('°')
            spin.valueChanged.connect(lambda value,k=key:self.tools.set_geometry({k:value}))
            geometry_form.addRow(bind_i18n(QLabel(),title),spin);self.geometry_fields[key]=spin
        self.controls.layout().addWidget(self.geometry_panel)
        actions=QHBoxLayout()
        self._buttons=[]
        for title,callback in [('撤销',self.undo),('重做',self.redo),('删除',self.delete)]:
            button=QPushButton(title);button.clicked.connect(callback);actions.addWidget(button)
            self._buttons.append(button)
        root.addLayout(actions)
        self.controls.style_requested.connect(self.apply_style)
        self.controls.add_requested.connect(self.arm)
        self.window.installEventFilter(self)
        self.dialog.installEventFilter(self)

    def set_target(self,module=None,panel=None,canvas=None,workspace=None):
        identity=(module,id(panel),getattr(panel,'panel_uuid',None),getattr(panel,'data',{}).get('active_image_uid'),id(canvas))
        if identity==getattr(self,'_identity',None):return
        self._release_target()
        if self.panel is not None and self.module in {'qpcr','ihc','brightfield'}:
            self._record(self._last)
            histories=getattr(self.panel,'_layout_edit_histories',{})
            histories[self.module]=(self.undo_states,self.redo_states,self._snapshot())
            self.panel._layout_edit_histories=histories
        self._identity=identity
        self.module,self.panel,self.canvas,self.workspace=module,panel,canvas,workspace
        self.undo_states=[];self.redo_states=[];self._before=None
        self._last=self._snapshot()
        history=getattr(panel,'_layout_edit_histories',{}).get(module)
        if history is not None and history[2]==self._last:
            self.undo_states,self.redo_states=history[:2]
        self.tools.bind()
        self._dock_controls()
        if canvas is None:self.dialog.hide();return
        self.draw=LayoutDrawTool(canvas.view)
        canvas.view.object_interaction.editing_tools=self.tools
        self.draw.created.connect(self.add)
        canvas.scene.selectionChanged.connect(self.sync_selection)
        canvas.scene.changed.connect(self._scene_changed)
        interaction=getattr(canvas.view,'object_interaction',None)
        if interaction is not None:
            interaction.command_handler=self.handle_command
            interaction.gesture_started.connect(self._begin_interaction)
            interaction.gesture_finished.connect(self._end_interaction)
        self.sync_selection()

    def _dock_controls(self):
        """Reuse the same editor inline; never revive module-specific formats."""
        if self._controls_scroll.widget() is self.controls:self._controls_scroll.takeWidget()
        parent=self.controls.parentWidget()
        if parent is not None and parent.layout() is not None:parent.layout().removeWidget(self.controls)
        panels=getattr(getattr(self.window,'workflow_ui',None),'panels',{})
        panel=panels.get(self.module) if self.canvas is not None else None
        scroll=panel.findChild(QScrollArea) if panel is not None else None
        self._docked_scroll=scroll
        bar=self.tools.bar
        if bar is not None and hasattr(bar,'inline_panel'):
            flat=bar.inline_panel;flat.hide()
            old=flat.parentWidget()
            if old is not None and old.layout() is not None:old.layout().removeWidget(flat)
            flat.setParent(self.dialog)
        if scroll is None:self._controls_scroll.setWidget(self.controls)
        else:
            if sys.platform == 'darwin':
                from PyQt6.QtWidgets import QBoxLayout
                options = getattr(self.canvas.zoom_controls, 'object_controls', None)
                if options is not None:
                    home = options.parentWidget()
                    home.layout().removeWidget(options)
                    options.layout().setDirection(QBoxLayout.Direction.TopToBottom)
                    options.setProperty('workflowPlain', True)
                    scroll.widget().layout().insertWidget(0, options)
                    self._studio_object_controls = (options, home)
            scroll.widget().layout().insertWidget(0,self.controls)
            self.controls.retranslate()
            flat=bar.create_inline_panel();scroll.widget().layout().insertWidget(1,flat);flat.show()
            self.controls.setProperty('workflowPlain',True)
            from ui.controllers.measurement_workbench_controller import readable_forms
            from PyQt6.QtWidgets import QSizePolicy
            self.controls.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
            for widget in (self.controls,self.controls.text_group,self.controls.shape_group,self.geometry_panel):
                widget.setProperty('workflowPlain',True);widget.style().unpolish(widget);widget.style().polish(widget)
            readable_forms(self.controls)
            self.controls.show();self.dialog.hide()
            QTimer.singleShot(0,self._show_primary_controls)

    def _show_primary_controls(self):
        if _qt_alive(getattr(self,'_docked_scroll',None)):
            self._docked_scroll.verticalScrollBar().setValue(0)

    def _release_target(self):
        if self._studio_object_controls is not None:
            from PyQt6.QtWidgets import QBoxLayout
            options, home = self._studio_object_controls
            if _qt_alive(options) and _qt_alive(home):
                options.parentWidget().layout().removeWidget(options)
                options.layout().setDirection(QBoxLayout.Direction.LeftToRight)
                home.layout().insertWidget(0, options)
            self._studio_object_controls = None
        canvas=self.canvas
        if _qt_alive(canvas):
            view=getattr(canvas,'view',None)
            if _qt_alive(view):
                interaction=getattr(view,'object_interaction',None)
                if _qt_alive(interaction):
                    interaction.cancel()
                    interaction.command_handler=None
                    interaction.editing_tools=None
                    try:interaction.gesture_started.disconnect(self._begin_interaction)
                    except (TypeError,RuntimeError):pass
                    try:interaction.gesture_finished.disconnect(self._end_interaction)
                    except (TypeError,RuntimeError):pass
            scene=getattr(canvas,'scene',None)
            if _qt_alive(scene):
                try:scene.selectionChanged.disconnect(self.sync_selection)
                except (TypeError,RuntimeError):pass
                try:scene.changed.disconnect(self._scene_changed)
                except (TypeError,RuntimeError):pass
        draw=self.draw
        self.draw=None
        if _qt_alive(draw):
            draw.cancel()
            draw.deleteLater()

    def _scene_changed(self,*_args):
        if self._before is None:self._record(self._last)

    def show(self):
        if self.canvas is None:return
        zh=get_language()=='zh'
        self.dialog.setWindowTitle('文字与图形标注' if zh else 'Text and shapes')
        self.hint.setText('只编辑排版标注，不改变测量 ROI。' if zh else 'Edit layout annotations without changing measurement ROIs.')
        for button,label in zip(self._buttons,['撤销','重做','删除'] if zh else ['Undo','Redo','Delete']):button.setText(label)
        self.controls.retranslate()
        self.sync_selection()
        if self._docked_scroll is not None:
            self.window.v9_shell.restore_right_panel()
            self._show_primary_controls()
            return
        origin=self.window.mapToGlobal(QPoint(max(0,self.window.width()-340),110))
        self.dialog.move(origin);self.dialog.show();self.dialog.raise_()

    def sync_selection(self):
        if self.canvas is None:return
        items=self.tools.selected()
        was_text=self.controls.text_group.isVisible()
        self.controls.set_selection(items)
        if not was_text and self.controls.text_group.isVisible():QTimer.singleShot(0,self._show_primary_controls)
        self.geometry_panel.setEnabled(len(items)==1 and not getattr(getattr(items[0],'layout_object',None),'locked',False))
        if len(items)==1:
            item=items[0];obj=getattr(item,'layout_object',None)
            rect=item.rect() if hasattr(item,'rect') else item.boundingRect()
            values=dict(x=item.x(),y=item.y(),width=obj.width if obj is not None else rect.width(),
                        height=obj.height if obj is not None else rect.height(),rotation=item.rotation())
            blockers=[QSignalBlocker(spin) for spin in self.geometry_fields.values()]
            for key,value in values.items():self.geometry_fields[key].setValue(value)
            self.geometry_fields['rotation'].setEnabled(isinstance(item,(SharedTextItem,SharedShapeItem)) and not self.tools.is_image(item))
            del blockers

    def _snapshot(self):
        if self.panel is None:return None
        if self.module=='fluoro':return self.canvas._capture_layout_snapshot()
        if self.module=='wb':return self.window._capture_wb_state()
        if self.module in {'qpcr','ihc','brightfield'}:
            layout=self.panel.data.get(f'{self.module}_layout',{})
            return deepcopy(layout)
        return None

    def _record(self,before):
        if self._before is not None:return
        if self.module in {'wb','fluoro'}:
            self._last=self._snapshot();return
        after=self._snapshot()
        if before is not None and before!=after and after!=self._last:
            self.undo_states.append(before);self.undo_states=self.undo_states[-50:]
            self.redo_states.clear()
        self._last=after

    def eventFilter(self,watched,event):
        if watched in (self.window,self.dialog) and _qt_alive(self.canvas) and _qt_alive(self.canvas.view):
            interaction=getattr(self.canvas.view,'object_interaction',None)
            if interaction is not None and event.type() in {QEvent.Type.KeyPress,QEvent.Type.ShortcutOverride}:
                return interaction.eventFilter(watched,event)
        return False

    def _begin_interaction(self):
        self._before=self._snapshot()
        if self.module=='fluoro':self.canvas._layout_edit_transaction=True
        elif self.module=='wb':self.window.wb_history.begin_transaction()

    def _end_interaction(self,cancelled=False):
        before,self._before=self._before,None
        if self.module=='wb':
            try:
                if cancelled and before is not None:self.window._restore_wb_state(before)
            finally:self.window.wb_history.end_transaction()
            if not cancelled and before is not None:self.window._record_wb_change('排版编辑',before)
            self._last=self._snapshot()
            return
        if self.module=='fluoro':
            try:
                if cancelled:
                    if before is not None:self.canvas._restore_layout_snapshot(before)
                else:
                    self._persist()
                    after=self._snapshot()
                    if before is not None and before!=after:
                        self.canvas._layout_undo_stack.append(before)
                        self.canvas._layout_undo_stack=self.canvas._layout_undo_stack[-50:]
                        self.canvas._layout_redo_stack.clear()
                self._last=self._snapshot()
            finally:self.canvas._layout_edit_transaction=False
            return
        if cancelled:
            # Shared movement has already restored positions before this signal.
            self._persist()
            self._last=self._snapshot()
        else:
            self._persist()
            self._record(before)

    def handle_command(self,command):
        if self.canvas is None:return False
        if command in {'undo','redo'}:
            getattr(self,command)();return True
        if command=='copy':
            from ui.controllers.source_layout_clipboard import copy_selection
            self._persist();copy_selection(self);return True
        if command not in {'cut','paste','delete'}:return False
        from ui.controllers.source_layout_clipboard import copy_selection,paste_selection
        self._persist()
        self._begin_interaction()
        try:
            if command=='delete':self.delete()
            elif command=='cut':
                if copy_selection(self):self.delete()
            elif not paste_selection(self):
                self.window.statusBar().showMessage(tr('canvas_paste_unsupported'),5000)
        finally:self._end_interaction()
        return True

    def _persist(self):
        if self.module=='qpcr':self.canvas.persist_layout_edits()
        elif self.module=='wb':
            for item in self.canvas.scene.items():
                if item.data(0)=='wb_free_annotation':self.canvas._persist_free_annotation(item)
        elif self.module in {'ihc','brightfield'}:
            if self.canvas.layout_objects():self.workspace.persist_layout_edits()
        elif self.module=='fluoro':
            for item in self.canvas.scene.items():
                if item.data(0) in {'fluoro_layout_text','fluoro_layout_shape'}:
                    self.canvas._persist_shared_annotation(item)
            objects=self.canvas.layout_objects()
            if objects:self.canvas.apply_layout_objects(objects,refresh=False,append_audit=False)

    def _refresh(self):
        if self.module=='qpcr':self.canvas.refresh()
        elif self.module in {'ihc','brightfield'}:self.workspace._refresh_layout_after_scale_bar_change()
        elif self.module=='fluoro':self.canvas.refresh()
        elif self.module=='wb':self.canvas.render()

    def _restore(self,state):
        if state is None:return
        self.panel.data[f'{self.module}_layout']=deepcopy(state)
        self._refresh()
        self._last=self._snapshot()

    def undo(self):
        if self.canvas is None:return
        focused=self.canvas.scene.focusItem()
        if isinstance(focused,SharedTextItem) and focused.is_editing:focused.document().undo();return
        if self.module=='wb':self.window.undo_wb();return
        if self.module=='fluoro':self.canvas.undo_layout_edit();return
        if self.undo_states:
            self.redo_states.append(self._snapshot());self._restore(self.undo_states.pop())

    def redo(self):
        if self.canvas is None:return
        focused=self.canvas.scene.focusItem()
        if isinstance(focused,SharedTextItem) and focused.is_editing:focused.document().redo();return
        if self.module=='wb':self.window.redo_wb();return
        if self.module=='fluoro':self.canvas.redo_layout_edit();return
        if self.redo_states:
            self.undo_states.append(self._snapshot());self._restore(self.redo_states.pop())

    def arm(self,kind):
        if self.canvas is None or not _qt_alive(self.draw):return
        self.dialog.hide()
        self.draw.arm(kind)
        self.window.statusBar().showMessage('在画布上拖动创建标注；Shift 约束，Esc 取消。',5000)

    def add(self,kind,rect=None,endpoints=None):
        if self.canvas is None:return
        before=self._snapshot()
        wb_before=self.window._capture_wb_state() if self.module=='wb' else None
        if self.module=='fluoro':self.canvas._record_layout_snapshot()
        if self.module=='qpcr':item=self.canvas.add_text() if kind=='text' else self.canvas.add_shape(kind)
        elif self.module=='fluoro':
            item=self.canvas.add_layout_text() if kind=='text' else self.canvas.add_layout_shape(kind,record_snapshot=False)
        elif self.module=='wb':item=self.canvas.add_free_annotation(kind)
        else:
            uid=uuid4().hex;typ='text' if kind=='text' else 'shape'
            record={f'{typ}_uuid':uid,'shape_type':kind,'text':'Text','x':24,'y':24,'width':140,'height':45}
            meta={f'{self.module}_kind':f'layout_{typ}',f'{typ}_uuid':uid,
                  'text_record' if typ=='text' else 'shape':record,'shape_type':kind}
            factory=text_from_record if typ=='text' else shape_from_record
            obj=factory(record,f'{self.panel.panel_uuid}:{self.module}_{typ}:{uid}',meta)
            objects=self.canvas.layout_objects()+[obj]
            self.canvas.set_layout_objects(self.canvas.canvas_spec,objects,self.workspace._layout_pixmap_provider,
                                           svg_text_changed_callback=self.workspace.persist_layout_edits)
            item=self.canvas.item_for_object_id(obj.object_id)
        if rect is not None:item.update_geometry(rect.x(),rect.y(),rect.width(),rect.height())
        if endpoints and isinstance(item,SharedShapeItem):
            payload=item.layout_object.to_dict();payload['metadata'].update(endpoints)
            item.layout_object=LayoutObject.from_dict(payload);item.update_handles();item.update()
            if self.module=='wb':self.canvas._persist_free_annotation(item)
            elif self.module=='fluoro':self.canvas._persist_shared_annotation(item)
        self._persist()
        self.canvas.scene.clearSelection();item.setSelected(True)
        if isinstance(item,SharedTextItem):item.begin_edit()
        self._record(before)
        if wb_before is not None:self.window._record_wb_change('添加排版标注',wb_before)

    def apply_style(self,delta):
        if self.canvas is None:return
        before=self._snapshot()
        if self.module=='fluoro':self.canvas._record_layout_snapshot()
        items=self.canvas.scene.selectedItems()
        if self.module=='wb' and items and all(hasattr(i,'style_payload') for i in items):
            self.window.apply_wb_selected_text_style(delta)
        else:
            wb_before=self.window._capture_wb_state() if self.module=='wb' else None
            apply_selection_style(items,delta)
            if wb_before is not None:self.window._record_wb_change('排版格式',wb_before)
        self._persist();self._record(before)
        self.tools.sync_summary()

    def delete(self):
        if self.canvas is None:return
        before=self._snapshot()
        if self.module=='qpcr':self.canvas.delete_selected()
        elif self.module=='fluoro':self.canvas.delete_selected_layout_items()
        elif self.module in {'ihc','brightfield'}:self.workspace.delete_selected_layout_items()
        else:
            wb_before=self.window._capture_wb_state()
            ids={i.data(1) for i in self.canvas.scene.selectedItems() if i.data(0)=='wb_free_annotation'}
            layout=self.panel.data.setdefault('wb_layout',{})
            layout['free_annotations']=[r for r in layout.get('free_annotations',[]) if r['object_id'] not in ids]
            self.canvas.render();self.window._record_wb_change('删除标注',wb_before)
        self._record(before)

    def flush(self):
        if self.canvas is None:return
        if _qt_alive(self.draw):self.draw.cancel()
        for item in list(self.canvas.scene.items()):
            if isinstance(item,SharedTextItem) and item.is_editing:
                item.finish_edit(commit=True);break
        self._persist()

