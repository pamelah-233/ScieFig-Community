"""Presentation-only tools shared by source layouts and Composer pages."""
from copy import deepcopy
import json
from uuid import uuid4
from PyQt6.QtCore import QObject, QSettings, QPointF, QRectF, pyqtSignal
from PyQt6.QtWidgets import QMenu
from PyQt6 import sip
from core.utils.i18n import get_language
from core.models.layout_editor import LayoutObject
from ui.widgets.canvas_object_interaction import locked
from ui.widgets.shared_layout_controls import _text_formats
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_shapes import SharedShapeItem
from ui.widgets.layout_editing import EditableLayoutRecord, align_records, distribute_records, make_records_same_size


def label(zh, en):
    return zh if get_language() == 'zh' else en


class LayoutToolsController(QObject):
    message = pyqtSignal(str)
    properties_requested = pyqtSignal()
    insert_requested = pyqtSignal(str)
    _copied_style = None

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.settings = QSettings('ScieFig', 'AnnotationStyles')
        self.bar = None
        self._bound_scene = None
        self._bound_zoom = None

    def bind(self):
        if self._bound_scene is not None and not sip.isdeleted(self._bound_scene):
            try:self._bound_scene.selectionChanged.disconnect(self.sync_summary)
            except (TypeError,RuntimeError):pass
        self._bound_scene=None
        if self._bound_zoom is not None and not sip.isdeleted(self._bound_zoom):
            self._bound_zoom.layout_tools_bar=None
        self._bound_zoom=None
        if self.canvas is None:
            if self.bar is not None and not sip.isdeleted(self.bar):
                self.bar.setParent(self.owner.window);self.bar.hide()
            return
        from ui.widgets.layout_tools_bar import LayoutToolsBar
        if self.bar is None or sip.isdeleted(self.bar):self.bar = LayoutToolsBar(self)
        self.canvas.zoom_controls.layout().insertWidget(1,self.bar)
        self._bound_zoom=self.canvas.zoom_controls
        self._bound_zoom.layout_tools_bar=self.bar
        self.bar.show()
        self._bound_scene=self.canvas.scene
        self._bound_scene.selectionChanged.connect(self.sync_summary)
        self.bar.retranslate()

    def sync_summary(self):
        if (self.bar is None or sip.isdeleted(self.bar) or sip.isdeleted(self.bar.properties)
                or self.canvas is None or sip.isdeleted(self.canvas)):return
        items=self.selected()
        text=label('属性','Properties')
        if len(items)==1 and self.style_kind(items[0])=='text':
            formats=_text_formats(items[0]);sizes={f['font_size_pt'] for f in formats}
            text+=' · '+(f'{next(iter(sizes)):g} pt' if len(sizes)==1 else label('混合','Mixed'))
        elif len(items)>1:text+=f' · {len(items)}'
        self.bar.properties.setText(text)
        self.bar.properties.setToolTip(text)
        self.bar.properties.setAccessibleName(text)
        if hasattr(self.bar,'inline_panel') and not sip.isdeleted(self.bar.inline_panel):self.bar.inline_panel.refresh()

    @property
    def canvas(self):
        return self.owner.canvas

    def selected(self):
        return self.canvas.view.object_interaction.selected() if self.canvas is not None else []

    @staticmethod
    def item_key(item):
        ref = getattr(item, 'ref', None)
        if ref is not None: return ref.ref_uid
        obj = getattr(item, 'layout_object', None)
        if obj is not None:
            return str(obj.metadata.get('text_uuid') or obj.metadata.get('shape_uuid') or obj.object_id)
        if item.data(0) in {'fluoro_image_channel','fluoro_image_merge'}:
            return f'{item.data(3)}:{item.data(1)}'
        return str(item.data(1) or '')

    def _layout_state(self, create=False):
        if hasattr(self.owner, 'undo_stack'):
            return self.canvas.current_figure().canvas if self.canvas.project is not None else {}
        panel = self.owner.panel
        if panel is None: return {}
        key = f'{self.owner.module}_layout'
        return panel.data.setdefault(key, {}) if create else panel.data.get(key, {})

    def groups(self):
        return self._layout_state().get('layout_groups', {})

    def _set_editor_state(self,key,value):
        self._layout_state(create=True)[key]=deepcopy(value)
        if getattr(self.owner,'module',None) in {'ihc','brightfield'}:
            module=self.owner.module
            for item in self.canvas.layout_items():
                obj=item.layout_object
                if obj.metadata.get(f'{module}_kind')=='layout_root':
                    payload=obj.to_dict()
                    payload['metadata'][f'{module}_layout'][key]=deepcopy(value)
                    item.layout_object=LayoutObject.from_dict(payload)

    def image_aspect_locked(self,item):
        if hasattr(item,'ref'): return bool(item.ref.aspect_locked)
        obj=getattr(item,'layout_object',None)
        return bool(self._layout_state().get('image_aspect_locks',{}).get(self.item_key(item),
                    obj.metadata.get('aspect_locked',True) if obj is not None else True))

    @staticmethod
    def is_image(item):
        return (hasattr(item,'ref') or getattr(getattr(item,'layout_object',None),'object_type',None) in {'image','plot'}
                or str(item.data(0) or '').startswith(('fluoro_image_','fluoro_stats_plot','fluoro_profile_plot','fluoro_coloc_plot')))

    def set_image_aspect(self,value):
        items=self.selected()
        def change():
            for item in items:
                if hasattr(item,'ref'):item.ref.aspect_locked=bool(value)
                elif self.is_image(item):
                    locks=deepcopy(self._layout_state().get('image_aspect_locks',{}))
                    locks[self.item_key(item)]=bool(value)
                    self._set_editor_state('image_aspect_locks',locks)
            if hasattr(self.owner,'undo_stack'):self.canvas.reference_changed()
        self.transaction(label('图片宽高比','Image aspect ratio'),change)

    def set_geometry(self, delta):
        items=self.selected()
        if len(items)!=1 or locked(items[0]):return False
        item=items[0]
        def change():
            obj=getattr(item,'layout_object',None)
            rect=item.rect() if hasattr(item,'rect') else item.boundingRect()
            width=delta.get('width',obj.width if obj is not None else rect.width())
            height=delta.get('height',obj.height if obj is not None else rect.height())
            if self.is_image(item) and self.image_aspect_locked(item):
                old_width=obj.width if obj is not None else rect.width()
                old_height=obj.height if obj is not None else rect.height()
                if 'width' in delta and 'height' not in delta:height=width*old_height/max(.001,old_width)
                elif 'height' in delta and 'width' not in delta:width=height*old_width/max(.001,old_height)
            x,y=delta.get('x',item.x()),delta.get('y',item.y())
            if hasattr(item,'update_geometry'):item.update_geometry(x,y,width,height)
            elif hasattr(item,'apply_geometry'):item.apply_geometry(x,y,width,height)
            else:item.setPos(x,y)
            if 'rotation' in delta:item.setRotation(delta['rotation'])
        self.transaction(label('位置 / 尺寸','Position / Size'),change)
        return True

    def _set_groups(self, groups):
        self._set_editor_state('layout_groups',groups)
        if hasattr(self.owner, 'undo_stack'): self.canvas.reference_changed()

    def group_members(self, items):
        available = {self.item_key(i): i for i in self.canvas.view.object_interaction.items()}
        result = set(items)
        for group in self.groups().values():
            if not any(self.item_key(i) in group for i in items): continue
            members = [available[key] for key in group if key in available]
            if len(members) != len(group) or any(locked(i) for i in members):
                result.difference_update(i for i in items if self.item_key(i) in group)
            else:
                result.update(members)
        return list(result)

    def group_selection(self):
        items = self.selected()
        if len(items) < 2 or any(locked(i) or not self.item_key(i) for i in items): return False
        keys = {self.item_key(i) for i in items}
        def change():
            groups = {uid: list(group) for uid, group in self.groups().items() if not keys.intersection(group)}
            groups[uuid4().hex] = sorted(keys)
            self._set_groups(groups)
        self.transaction(label('组合', 'Group'), change)
        return True

    def ungroup_selection(self):
        keys = {self.item_key(i) for i in self.selected()}
        groups = {uid: list(group) for uid, group in self.groups().items() if not keys.intersection(group)}
        if groups == self.groups(): return False
        self.transaction(label('取消组合', 'Ungroup'), lambda: self._set_groups(groups))
        return True

    def clipboard_groups(self):
        selected = {self.item_key(i) for i in self.selected()}
        return {uid: list(members) for uid, members in self.groups().items() if set(members).issubset(selected)}

    def paste_groups(self, copied, identifiers):
        groups = deepcopy(self.groups())
        for members in copied.values():
            if len(members) > 1 and all(key in identifiers for key in members):
                groups[uuid4().hex] = [identifiers[key] for key in members]
        if groups != self.groups(): self._set_groups(groups)

    def transaction(self, title, operation):
        if hasattr(self.owner, 'perform'):
            self.owner.flush_edits()
            return self.owner.perform(title, operation)
        self.owner.flush()
        self.owner._begin_interaction()
        try:
            result=operation()
            if self.owner.module=='wb':self.owner._persist()
            return result
        finally:
            self.owner._end_interaction()
            self.owner.sync_selection()

    @staticmethod
    def style_kind(item):
        obj = getattr(item, 'layout_object', None)
        if isinstance(item, SharedTextItem): return 'text'
        if isinstance(item, SharedShapeItem) and obj.object_type in {'shape', 'line'}: return 'shape'
        return None

    def style_payload(self):
        items = self.selected()
        if len(items) != 1 or self.style_kind(items[0]) is None:
            self.message.emit(label('请选择一个文字或图形作为格式来源。', 'Select one text or shape as the style source.'))
            return None
        item = items[0]
        kind = self.style_kind(item)
        if kind == 'text':
            formats = _text_formats(item)
            delta = {key: values.pop() for key in formats[0]
                     if len(values := {fmt[key] for fmt in formats}) == 1}
            meta = item.layout_object.metadata.get('text', {})
            delta.update(alignment=meta.get('alignment', 'left'), line_spacing=float(meta.get('line_spacing', 1)))
        else:
            obj = item.layout_object
            delta = dict(stroke_color=obj.style.stroke_color, fill_color=obj.style.fill_color,
                         stroke_width=obj.style.stroke_width, stroke_dash=obj.metadata.get('stroke_dash', 'solid'),
                         fill_alpha=float(obj.metadata.get('fill_alpha', 1)))
        return {'kind': kind, 'delta': delta}

    def copy_style(self):
        value = self.style_payload()
        if value is None: return False
        LayoutToolsController._copied_style = deepcopy(value)
        self.message.emit(label('格式已复制；选择目标后应用格式。', 'Style copied. Select targets and apply style.'))
        return True

    def apply_payload(self, value):
        items = self.selected()
        targets = [item for item in items if not locked(item) and self.style_kind(item) == value.get('kind')]
        if not targets or len(targets) != len(items):
            self.message.emit(label('请选择同类且未锁定的文字或图形；未应用格式。',
                                    'Select matching unlocked text or shapes. No style was applied.'))
            return False
        # Existing owners retain their module-specific style callbacks and history.
        self.owner.apply_style(deepcopy(value['delta']))
        return True

    def paste_style(self):
        if self._copied_style is None:
            self.message.emit(label('请先复制格式。', 'Copy a style first.'))
            return False
        return self.apply_payload(self._copied_style)

    def presets(self):
        try:
            value = json.loads(str(self.settings.value('annotation_styles', '{}')))
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}

    def save_preset(self, name):
        name = name.strip()
        value = self.style_payload()
        if not name or value is None: return False
        records = self.presets()
        records[name] = value
        self.settings.setValue('annotation_styles', json.dumps(records, ensure_ascii=False))
        self.settings.sync()
        return self.settings.status() == QSettings.Status.NoError

    def apply_preset(self, name):
        value = self.presets().get(name)
        return self.apply_payload(value) if value else False

    def remove_preset(self, name):
        records = self.presets()
        if name not in records: return False
        del records[name]
        self.settings.setValue('annotation_styles', json.dumps(records, ensure_ascii=False))
        self.settings.sync()
        return self.settings.status() == QSettings.Status.NoError

    def available(self, command):
        items = self.selected()
        editable = [i for i in items if not locked(i)]
        reason = label('请选择未锁定的对象。', 'Select unlocked objects.')
        if command.startswith('distribute_'):
            return len(editable) >= 3, label('至少选择三个未锁定对象。', 'Select at least three unlocked objects.')
        if command.startswith('align_') or command in {'same_size', 'group'}:
            return len(editable) >= 2 and len(editable) == len(items), label('至少选择两个未锁定对象。', 'Select at least two unlocked objects.')
        if command == 'ungroup':
            keys = {self.item_key(i) for i in items}
            return any(keys.intersection(group) for group in self.groups().values()), label('请选择已组合的对象。', 'Select a grouped object.')
        if command == 'copy_style':
            return len(items) == 1 and self.style_kind(items[0]) is not None, label('请选择一个文字或图形。', 'Select one text or shape.')
        if command == 'paste_style':
            value = self._copied_style
            return bool(value and items and all(not locked(i) and self.style_kind(i) == value['kind'] for i in items)), label('先复制格式，再选择同类未锁定对象。', 'Copy style, then select matching unlocked objects.')
        if command == 'paste': return True, ''
        if command in {'locate', 'properties', 'copy'}: return bool(items), label('请先选择对象。', 'Select an object first.')
        if command == 'lock':
            return bool(items) and all(hasattr(i, 'set_flags_for_locked') or hasattr(i, 'ref') for i in items), reason
        return bool(editable), reason

    def arrange(self, command):
        enabled, reason = self.available(command)
        if not enabled:
            self.message.emit(reason); return False
        items = self.selected()
        items = [i for i in items if not any(p in items for p in self.canvas.view.object_interaction._parents(i))]
        records = []
        for item in items:
            bounds = item.sceneBoundingRect()
            records.append(EditableLayoutRecord('object', self.item_key(item), item, bounds.x(), bounds.y(),
                                                 bounds.width(), bounds.height(), locked=locked(item)))
        before = {r.uid: (r.x, r.y) for r in records}
        if command.startswith('align_'): changed = align_records(records, command.removeprefix('align_'))
        elif command.startswith('distribute_'): changed = distribute_records(records, command.removeprefix('distribute_'))
        elif command == 'same_size': changed = make_records_same_size(records)
        else: return False
        if not changed: return False
        def apply():
            for record in records:
                item = record.model
                if command == 'same_size':
                    if hasattr(item, 'ref'):
                        height = record.width*item.ref.height_mm/max(.01,item.ref.width_mm) if item.ref.aspect_locked else record.height
                        item.apply_geometry(item.ref.x_mm,item.ref.y_mm,record.width/4,height/4,repaint=False)
                    else:
                        height = record.height
                        if self.is_image(item) and self.image_aspect_locked(item):
                            obj = getattr(item,'layout_object',None)
                            rect = item.rect() if hasattr(item,'rect') else item.boundingRect()
                            width = obj.width if obj is not None else rect.width()
                            old_height = obj.height if obj is not None else rect.height()
                            height = record.width*old_height/max(.001,width)
                        if hasattr(item, 'update_geometry'):
                            item.update_geometry(item.x(),item.y(),record.width,height)
                        elif hasattr(item,'apply_geometry'):
                            item.apply_geometry(item.x(),item.y(),record.width,height)
                else:
                    old_x, old_y = before[record.uid]
                    delta = QPointF(record.x-old_x, record.y-old_y)
                    parent = item.parentItem()
                    if parent is not None: delta = parent.mapFromScene(delta)-parent.mapFromScene(QPointF())
                    item.setPos(item.pos()+delta)
        self.transaction(label('排列对象', 'Arrange objects'), apply)
        return True

    def frame_view(self, mode):
        if self.canvas is None: return False
        controller = self.canvas._view_controller
        if mode == 'page':
            controller.fit(); return True
        items = self.selected() if mode == 'selection' else self.canvas.view.object_interaction.items()
        if not items: return False
        bounds = self.canvas.view.object_interaction.bounds(items)
        if bounds.isEmpty(): return False
        from ui.widgets.canvas_zoom import fit_graphics_view_to_rect
        # Expand view navigation room only; the scene/page rectangle is unchanged.
        view = self.canvas.view
        controller._fit_timer.stop(); controller._fit_timer_late.stop()
        view.setSceneRect(view.sceneRect().united(bounds.adjusted(-100,-100,100,100)))
        controller.fit_mode = False
        controller.zoom_factor = fit_graphics_view_to_rect(view,bounds.adjusted(-20,-20,20,20))
        controller._sync_controls()
        controller.view_changed.emit(controller.zoom_factor)
        return True

    def change_layer(self, command):
        if hasattr(self.owner,'undo_stack'):
            self.transaction(label('图层', 'Layer'), lambda:self.canvas.layer_selected(command))
            return
        items = self.selected()
        all_items = self.canvas.view.object_interaction.items()
        z = (max(i.zValue() for i in all_items)+1) if command == 'front' else min(i.zValue() for i in all_items)-len(items)
        self.transaction(label('图层', 'Layer'),lambda:[i.setZValue(z+n) for n,i in enumerate(items) if not locked(i)])

    def toggle_lock(self):
        if not self.available('lock')[0]: return False
        if hasattr(self.owner,'undo_stack'):
            self.transaction(label('锁定 / 解锁', 'Lock / Unlock'),self.canvas.toggle_lock_selected)
        else:
            items = self.selected()
            state = any(not locked(i) for i in items)
            self.transaction(label('锁定 / 解锁', 'Lock / Unlock'),lambda:[i.set_flags_for_locked(state) for i in items])
        return True

    def execute(self, command):
        enabled, reason = self.available(command)
        if not enabled:
            self.message.emit(reason); return False
        if command.startswith(('align_','distribute_')) or command == 'same_size': return self.arrange(command)
        if command in {'group','ungroup'}: return getattr(self,command+'_selection')()
        if command in {'copy_style','paste_style'}: return getattr(self,command)()
        if command == 'properties': self.properties_requested.emit(); return True
        if command == 'locate': return self.frame_view('selection')
        if command in {'front','back'}: self.change_layer(command); return True
        if command == 'lock': return self.toggle_lock()
        return self.owner.handle_command(command)

    def context_menu(self):
        menu = QMenu(self.canvas)
        menu.setToolTipsVisible(True)
        commands = [('copy','复制','Copy'),('cut','剪切','Cut'),('paste','粘贴','Paste'),('delete','删除','Delete'),
                    ('group','组合','Group'),('ungroup','取消组合','Ungroup'),
                    ('copy_style','复制格式','Copy style'),('paste_style','应用格式','Apply style'),
                    ('front','置顶','Bring to front'),('back','置底','Send to back'),
                    ('lock','锁定 / 解锁','Lock / Unlock'),('locate','定位选中对象','Locate selection'),
                    ('properties','属性','Properties')]
        for command,zh,en in commands:
            action = menu.addAction(label(zh,en)); action.setData(command)
            enabled, reason = self.available(command)
            action.setEnabled(enabled); action.setToolTip('' if enabled else reason)
            action.triggered.connect(lambda checked=False,c=command:self.execute(c))
        menu.addSeparator()
        menu.addAction(label('模块专用操作…','Module-specific actions…')).setData('module_advanced')
        return menu
