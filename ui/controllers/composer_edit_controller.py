"""Composer document commands and history; no asset pixels in undo snapshots."""
from copy import deepcopy
import re
from uuid import uuid4
from PyQt6 import sip
from PyQt6.QtCore import QObject,QEvent,QPointF,Qt,pyqtSignal
from PyQt6.QtGui import QUndoStack,QUndoCommand,QUndoGroup
from core.models.composer import PanelReference,AnnotationObject,ComposerLayout
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_controls import apply_selection_style
from ui.widgets.layout_clipboard import read_layout_clipboard,write_layout_clipboard
from core.models.layout_editor import LayoutObject
from core.utils.i18n import tr
from ui.layout_adapters.composer_layout_adapter import update_annotation_from_layout
from ui.controllers.layout_tools_controller import LayoutToolsController

PHYSICAL_UNITS='physical_mm'
LEGACY_FACTOR=4.0*25.4/72.0


class _FigureChange(QUndoCommand):
    def __init__(self,controller,title,before,after):
        super().__init__(title)
        self.controller=controller
        self.before,self.after=before,after
        self.before_selection=list(controller._last_selection)
        self.after_selection=controller._selection()
        self.first=True
    def undo(self):self.controller._restore(self.before,self.before_selection)
    def redo(self):
        if self.first:self.first=False
        else:self.controller._restore(self.after,self.after_selection)


class ComposerEditController(QObject):
    paste_rejected=pyqtSignal(str)

    def __init__(self,canvas):
        super().__init__(canvas)
        self.canvas=canvas
        self.tools=LayoutToolsController(self)
        self.undo_group=QUndoGroup(self)
        self._page_stacks={}
        self._activate_history(None)
        self._depth=0
        self._restoring=False
        self._last=None
        self._title='编辑'
        self._last_selection=[]
        canvas.document_changed.connect(self.record_change)
        canvas.pages_changed.connect(self._page_changed)
        interaction=canvas.view.object_interaction
        interaction.editing_tools=self.tools
        interaction.command_handler=self.handle_command
        interaction.gesture_started.connect(self.begin)
        interaction.gesture_finished.connect(self._finish_gesture)
        self.tools.bind()

    @property
    def undo_stack(self):
        return self.undo_group.activeStack()

    def _activate_history(self,uid):
        if uid not in self._page_stacks:
            stack=QUndoStack(self)
            stack.setUndoLimit(50)
            self.undo_group.addStack(stack)
            self._page_stacks[uid]=stack
        self.undo_group.setActiveStack(self._page_stacks[uid])

    def _selection(self):
        return [str(item.data(1)) for item in self.canvas.scene.selectedItems() if item.data(0) in {'panel_ref','annotation'}]

    def _select(self,ids):
        self.canvas.scene.clearSelection()
        for item in self.canvas.scene.items():
            if item.data(0) in {'panel_ref','annotation'}:item.setSelected(str(item.data(1)) in ids)

    def _finish_gesture(self,cancelled):
        if cancelled and self._last is not None:
            self._restore(self._last,self._last_selection)
        self.end()

    def _page_changed(self):
        if self._restoring:return
        current=self._snapshot()
        if current and (not self._last or self._last['figure_uid']!=current['figure_uid']):
            self._activate_history(current['figure_uid'])
            self._last=current
            self._last_selection=self._selection()

    def _snapshot(self):
        if self.canvas.project is None:return None
        figure=self.canvas.current_figure()
        return {'figure_uid':figure.figure_uid,'canvas':deepcopy(figure.canvas),
                'panel_refs':deepcopy([ref.to_dict() for ref in figure.panel_refs]),
                'annotations':deepcopy([a.to_dict() for a in figure.annotations])}

    def reset(self):
        for stack in self._page_stacks.values():
            self.undo_group.removeStack(stack)
            stack.deleteLater()
        self._page_stacks={}
        self._activate_history(self.canvas.current_figure().figure_uid if self.canvas.project is not None else None)
        if self.canvas.project is not None:
            figure=self.canvas.current_figure()
            if not figure.panel_refs and not figure.annotations:
                figure.canvas['unit_contract']=PHYSICAL_UNITS
        self._last=self._snapshot()
        self._last_selection=self._selection()

    def record_change(self):
        if self._restoring or self._depth:return
        after=self._snapshot()
        if self._last is not None and after!=self._last:
            self.undo_stack.push(_FigureChange(self,self._title,self._last,after))
        self._last=after
        self._last_selection=self._selection()

    def begin(self,title='移动 / 缩放'):
        if not self._depth:
            self._title=title
            self._last=self._snapshot()
            self._last_selection=self._selection()
        self._depth+=1

    def end(self):
        self._depth=max(0,self._depth-1)
        if not self._depth:self.record_change()

    def perform(self,title,operation):
        self.begin(title)
        try:return operation()
        finally:self.end()

    def eventFilter(self,watched,event):
        if sip.isdeleted(self.canvas) or sip.isdeleted(self.canvas.view):return False
        if event.type() in {QEvent.Type.KeyPress,QEvent.Type.ShortcutOverride}:
            return self.canvas.view.object_interaction.eventFilter(watched,event)
        return False

    def _restore(self,state,selection=None):
        if not state or self.canvas.project is None:return
        self._restoring=True
        try:
            project=self.canvas.project
            figure=next((f for f in project.composer_figures if f.figure_uid==state['figure_uid']),None)
            if figure is None:return
            project.active_figure_uid=figure.figure_uid
            self._activate_history(figure.figure_uid)
            figure.canvas=deepcopy(state['canvas'])
            figure.panel_refs=[PanelReference.from_dict(v) for v in state['panel_refs']]
            figure.annotations=[AnnotationObject.from_dict(v) for v in state['annotations']]
            self.canvas.layout_model=figure.to_layout(figure.canvas.get('grid_preset','2x2'))
            project.composer_layout=self.canvas.layout_model.to_dict()
            project.touch()
            self.canvas.render()
            if selection is not None:self._select(selection)
            self.canvas.pages_changed.emit()
            self._last=self._snapshot()
        finally:self._restoring=False

    def handle_command(self,command):
        if self.canvas.project is None:return False
        self.flush_edits()
        if command in {'undo','redo'}:
            getattr(self,command)();return True
        if command=='delete':
            self.perform('删除',self.canvas.delete_selected);return True
        if command in {'copy','cut'}:
            ids=set(self._selection())
            figure=self.canvas.current_figure()
            entries=[]
            refs=[ref for ref in figure.panel_refs if ref.ref_uid in ids]
            ref_ids={ref.ref_uid for ref in refs}
            for ref in refs:entries.append({'kind':'panel_ref','object':deepcopy(ref.to_dict())})
            for ann in figure.annotations:
                if ann.annotation_uid in ids or ann.properties.get('role')=='panel_label' and ann.properties.get('panel_ref_uid') in ref_ids:
                    entries.append({'kind':'annotation','object':deepcopy(ann.to_dict())})
            if not entries:return True
            for entry in entries:
                entry['editor_id']=entry['object'].get('ref_uid',entry['object'].get('annotation_uid'))
            write_layout_clipboard({'kind':'composer','project_uuid':self.canvas.project.project_uuid,'entries':entries,
                                    'groups':self.tools.clipboard_groups()})
            if command=='cut':self.perform('剪切',self.canvas.delete_selected)
            return True
        if command!='paste':return False
        payload=read_layout_clipboard()
        if payload and payload.get('kind')=='text':
            def paste_text():
                center=self.canvas.view.mapToScene(self.canvas.view.viewport().rect().center())
                annotation=self.canvas.add_text_annotation(payload['text'],center)
                self._select([annotation.annotation_uid])
            self.perform('粘贴文字',paste_text)
            return True
        if payload and payload.get('kind')=='source':
            try:
                annotations=[]
                for entry in payload['entries']:
                    if entry.get('kind')!='annotation':raise ValueError('Source-backed assets require explicit transfer')
                    obj=LayoutObject.from_dict(entry['object'])
                    if obj.object_type not in {'text','shape','line'}:raise ValueError('Not an annotation')
                    annotation=AnnotationObject(type='text' if obj.object_type=='text' else 'shape')
                    update_annotation_from_layout(annotation,obj)
                    annotations.append({'kind':'annotation','object':annotation.to_dict(),
                                        'editor_id':entry.get('editor_id',obj.object_id)})
                payload={'kind':'composer','project_uuid':self.canvas.project.project_uuid,'entries':annotations,
                         'groups':payload.get('groups',{})}
            except (KeyError,TypeError,ValueError):
                self.paste_rejected.emit(tr('canvas_paste_unsupported'))
                return True
        if not payload or payload.get('kind')!='composer':return True
        if payload.get('project_uuid')!=self.canvas.project.project_uuid:return True
        try:
            refs=[PanelReference.from_dict(e['object']) for e in payload['entries'] if e.get('kind')=='panel_ref']
            annotations=[AnnotationObject.from_dict(e['object']) for e in payload['entries'] if e.get('kind')=='annotation']
        except (TypeError,ValueError,KeyError):return True
        existing_panels={panel.panel_uuid for panel in self.canvas.project.panels}
        if any(ref.panel_uid not in existing_panels for ref in refs):return True
        def paste():
            figure=self.canvas.current_figure()
            models=refs+annotations
            if not models:return
            occupied={(round(o.x_mm,3),round(o.y_mm,3)) for o in figure.panel_refs+figure.annotations}
            offset=6.0
            while (round(models[0].x_mm+offset,3),round(models[0].y_mm+offset,3)) in occupied:offset+=6.0
            ids={ref.ref_uid:str(uuid4()) for ref in refs}
            annotation_ids={ann.annotation_uid:str(uuid4()) for ann in annotations}
            identifiers={entry.get('editor_id'):({**ids,**annotation_ids}).get(
                entry['object'].get('ref_uid',entry['object'].get('annotation_uid'))) for entry in payload['entries']}
            top=self.canvas._next_layer_index()
            bottom=min(o.layer_index for o in models)
            pasted=[]
            for ref in refs:
                ref.ref_uid=ids[ref.ref_uid]
                pasted.append(ref.ref_uid)
            for ann in annotations:
                ann.annotation_uid=annotation_ids[ann.annotation_uid];pasted.append(ann.annotation_uid)
                props=ann.properties
                if props.get('role')=='panel_label':
                    old=props.get('panel_ref_uid')
                    if old in ids:props['panel_ref_uid']=ids[old]
                    else:
                        for key in ('role','panel_ref_uid','follow_panel','label_auto'):props.pop(key,None)
            for obj in models:
                obj.x_mm+=offset;obj.y_mm+=offset
                obj.layer_index=top+obj.layer_index-bottom
            figure.panel_refs.extend(refs);figure.annotations.extend(annotations)
            self.tools.paste_groups(payload.get('groups',{}),{key:value for key,value in identifiers.items() if key and value})
            self.canvas.reference_changed();self.canvas.render();self._select(pasted)
        self.perform('粘贴',paste)
        return True

    def undo(self):
        focused=self.canvas.scene.focusItem()
        if isinstance(focused,SharedTextItem) and focused.is_editing:focused.document().undo()
        else:self.undo_stack.undo()

    def redo(self):
        focused=self.canvas.scene.focusItem()
        if isinstance(focused,SharedTextItem) and focused.is_editing:focused.document().redo()
        else:self.undo_stack.redo()

    def apply_style(self,delta):
        items=list(self.canvas.scene.selectedItems())
        self.perform('文字 / 图形格式',lambda:apply_selection_style(items,delta))
        self.tools.sync_summary()

    def add_annotation(self,kind,rect=None,endpoints=None):
        def create():
            pos=rect.topLeft() if rect is not None else None
            if kind=='text':annotation=self.canvas.add_text_annotation('Text',pos)
            else:
                annotation=self.canvas.add_shape_annotation(kind,pos,
                    width_mm=rect.width()/4 if rect else 24,
                    height_mm=rect.height()/4 if rect else 16)
                annotation.properties['fill_alpha']=0
                if endpoints:
                    annotation.properties.update({key:float(value)/4 for key,value in endpoints.items()})
            if rect is not None and kind=='text':
                annotation.properties.update(width_mm=max(10,rect.width()/4),height_mm=max(6,rect.height()/4))
            self.canvas.render()
            self.canvas.scene.clearSelection()
            item=next((i for i in self.canvas.scene.items() if i.data(0)=='annotation' and i.data(1)==annotation.annotation_uid),None)
            if item is not None:item.setSelected(True)
            return annotation
        return self.perform('添加标注',create)

    def set_page(self,width_mm,height_mm,preset='custom'):
        if width_mm<=0 or height_mm<=0:raise ValueError('页面尺寸必须大于零')
        def change():
            figure=self.canvas.current_figure()
            if figure.canvas.get('unit_contract')!=PHYSICAL_UNITS:
                self._convert_legacy(figure)
            figure.canvas.update(width_mm=float(width_mm),height_mm=float(height_mm),
                                 page_preset=preset,unit_contract=PHYSICAL_UNITS)
            self.canvas.layout_model=figure.to_layout(figure.canvas.get('grid_preset','2x2'))
            self.canvas.reference_changed()
            self.canvas.render()
            self.canvas.fit_to_canvas()
            self.canvas.pages_changed.emit()
        self.perform('页面尺寸',change)

    @staticmethod
    def _convert_legacy(figure):
        # Keep the legacy page's actual exported object sizes when adopting real mm.
        factor=LEGACY_FACTOR
        for ref in figure.panel_refs:
            for key in ('x_mm','y_mm','width_mm','height_mm'):
                setattr(ref,key,getattr(ref,key)*factor)
        for ann in figure.annotations:
            ann.x_mm*=factor
            ann.y_mm*=factor
            props=ann.properties
            for key in ('width_mm','height_mm','offset_x_mm','offset_y_mm','font_size_pt','stroke_width_pt','x1','y1','x2','y2'):
                if key in props:props[key]=float(props[key])*factor
            rich=props.get('text',{})
            for run in rich.get('runs',[]):
                if 'font_size_pt' in run:run['font_size_pt']*=factor
            if rich.get('html'):
                rich['html']=re.sub(r'(font-size\s*:\s*)([\d.]+)(pt|px)',
                    lambda m:f'{m[1]}{float(m[2])*factor:g}{m[3]}',rich['html'])

    def set_grid(self,preset):
        self.perform('排版网格',lambda:self.canvas.set_layout_preset(preset))

    def arrange_to_grid(self):
        def arrange():
            figure=self.canvas.current_figure()
            layout=ComposerLayout.create(self.canvas.layout_model.preset)
            margin=float(figure.canvas.get('margin_mm',2))
            layout.width_mm=max(10,float(figure.canvas['width_mm'])-2*margin)
            layout.height_mm=max(10,float(figure.canvas['height_mm'])-2*margin)
            layout.rebuild_empty_cells()
            refs=[r for r in figure.panel_refs if not r.locked and r.visible]
            for ref,cell in zip(refs,layout.cells):
                ratio=ref.width_mm/max(.01,ref.height_mm)
                ref.width_mm=min(cell.width_mm,cell.height_mm*ratio)
                ref.height_mm=ref.width_mm/ratio
                ref.x_mm,ref.y_mm=cell.x_mm+margin,cell.y_mm+margin
            self.canvas.reference_changed()
            self.canvas.render()
        self.perform('按网格整理',arrange)

    def move_resize(self,delta):
        items=self.canvas.scene.selectedItems()
        if len(items)!=1:return
        item=items[0]
        def change():
            ref=getattr(item,'ref',None)
            ann=getattr(item,'annotation',None)
            if ref is not None:
                if ref.locked:return
                values={k:getattr(ref,k) for k in ('x_mm','y_mm','width_mm','height_mm')}
                values.update(delta)
                if ref.aspect_locked:
                    if 'width_mm' in delta and 'height_mm' not in delta:
                        values['height_mm']=values['width_mm']*ref.height_mm/max(.01,ref.width_mm)
                    elif 'height_mm' in delta and 'width_mm' not in delta:
                        values['width_mm']=values['height_mm']*ref.width_mm/max(.01,ref.height_mm)
                item.apply_geometry(**values,repaint=False)
            elif ann is not None and not ann.locked:
                values={'x_mm':ann.x_mm,'y_mm':ann.y_mm,
                    'width_mm':ann.properties.get('width_mm',55),'height_mm':ann.properties.get('height_mm',20)}
                values.update(delta)
                item.update_geometry(values['x_mm']*4,values['y_mm']*4,values['width_mm']*4,values['height_mm']*4)
        self.perform('位置 / 尺寸',change)

    def flush_edits(self):
        for item in list(self.canvas.scene.items()):
            if isinstance(item,SharedTextItem) and item.is_editing:
                item.finish_edit(commit=True)
                break
