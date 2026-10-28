"""Translate clipboard annotations to existing source-module layout records."""
from copy import deepcopy
from uuid import uuid4

from core.models.layout_editor import LayoutObject,LayoutSourceIdentity
from core.utils.i18n import tr
from ui.layout_adapters.annotation_records import write_shared_fields
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_shapes import SharedShapeItem
from ui.widgets.layout_clipboard import read_layout_clipboard,write_layout_clipboard
from core.models.composer import AnnotationObject
from ui.layout_adapters.composer_layout_adapter import annotation_layout_object


def annotation_record(obj):
    metadata=obj.to_dict()['metadata']
    record=deepcopy(metadata.get('text_record' if obj.object_type=='text' else 'shape',{}))
    record.update(x=obj.x,y=obj.y,width=obj.width,height=obj.height,rotation=obj.rotation_deg,
                  z_order=obj.z_order,locked=obj.locked,hidden=obj.hidden)
    if obj.object_type=='text':
        record.update(text=obj.text,font_family=obj.style.font_family,font_size=obj.style.font_size_pt,
                      color=obj.style.text_color,bold=obj.style.bold,italic=obj.style.italic)
    else:
        record.update(shape_type=metadata.get('shape_kind',metadata.get('shape_type','rectangle')),
                      stroke_color=obj.style.stroke_color,stroke_width=obj.style.stroke_width,fill_color=obj.style.fill_color)
    write_shared_fields(record,obj)
    return record


def copy_selection(controller):
    entries=[]
    panel=controller.panel
    module=controller.module
    selected=controller.canvas.scene.selectedItems()
    roots=[]
    for item in selected:
        parent=item.parentItem()
        while parent is not None and parent not in selected:parent=parent.parentItem()
        if parent is None:roots.append(item)
    for item in roots:
        if isinstance(item,SharedTextItem):
            entries.append({'kind':'annotation','object':item.to_layout_object().to_dict()})
        elif isinstance(item,SharedShapeItem) and item.layout_object.object_type in {'shape','line'}:
            entries.append({'kind':'annotation','object':item.layout_object.to_dict()})
        elif module=='qpcr' and item.data(0)=='qpcr_layout_plot':
            record=next((r for r in panel.data.get('qpcr_layout',{}).get('plots',[]) if r.get('plot_uuid')==item.data(1)),None)
            if record:entries.append({'kind':'plot','record':deepcopy(record)})
        elif module in {'ihc','brightfield'} and getattr(item,'layout_object',None) is not None and item.layout_object.metadata.get(f'{module}_kind')=='layout_output':
            record=deepcopy(item.layout_object.to_dict()['metadata']['output'])
            record.update(x=item.pos().x(),y=item.pos().y(),width=item.rect().width(),height=item.rect().height())
            entries.append({'kind':'output','record':record})
        elif module=='fluoro' and item.data(0) in {'fluoro_image_channel','fluoro_image_merge'}:
            source=next((image for image in panel.images() if image.get('uid')==item.data(3)),None)
            if source is not None:
                geometry=deepcopy(source.get('layout_item_geometries',{}).get(str(item.data(1)),{}))
                geometry.update(x=item.pos().x(),y=item.pos().y(),width=item.rect().width(),height=item.rect().height(),
                    source_layout_key=geometry.get('source_layout_key',str(item.data(1))),source_channel_index=item.data(2),enabled=True)
                entries.append({'kind':'fluoro_image','image_uid':item.data(3),'record':geometry})
        elif module=='fluoro' and item.data(0) in {'fluoro_stats_plot','fluoro_profile_plot','fluoro_coloc_plot'}:
            record=next((r for r in controller.canvas._layout_plot_records() if r.get('plot_uuid')==item.data(1)),None)
            if record:entries.append({'kind':'plot','record':deepcopy(record)})
    if len(entries)!=len(roots):
        controller.window.statusBar().showMessage(tr('canvas_clipboard_unsupported'),5000)
        return False
    if entries:
        for entry,item in zip(entries,roots):entry['editor_id']=controller.tools.item_key(item)
        entries.sort(key=lambda entry:entry.get('object',{}).get('z_order',0))
        write_layout_clipboard({'kind':'source','module':module,'panel_uuid':panel.panel_uuid,'entries':entries,
                               'groups':controller.tools.clipboard_groups()})
    return bool(entries)


def paste_selection(controller):
    payload=read_layout_clipboard()
    if payload and payload.get('kind')=='composer':
        try:
            entries=[]
            for entry in payload['entries']:
                if entry.get('kind')!='annotation':return False
                annotation=AnnotationObject.from_dict(entry['object'])
                if annotation.type not in {'text','shape'}:return False
                entries.append({'kind':'annotation','object':annotation_layout_object(annotation).to_dict(),
                                'editor_id':entry.get('editor_id',annotation.annotation_uid)})
            payload={'kind':'source','entries':entries,'groups':payload.get('groups',{})}
        except (KeyError,TypeError,ValueError):return False
    if payload and payload.get('kind')=='text':
        center=controller.canvas.view.mapToScene(controller.canvas.view.viewport().rect().center())
        obj=LayoutObject(uuid4().hex,'text',center.x()-80,center.y(),160,45,text=payload['text'])
        payload={'kind':'source','entries':[{'kind':'annotation','object':obj.to_dict()}]}
    if not payload or payload.get('kind')!='source':return False
    panel,module=controller.panel,controller.module
    entries=deepcopy(payload['entries'])
    if any(e.get('kind')!='annotation' for e in entries) and (payload.get('module')!=module or payload.get('panel_uuid')!=panel.panel_uuid):
        return False
    try:
        for entry in entries:
            if entry.get('kind')=='annotation':LayoutObject.from_dict(entry['object'])
            elif entry.get('kind')=='plot' and module in {'qpcr','fluoro'}:
                if not isinstance(entry.get('record'),dict):return False
            elif entry.get('kind')=='output' and module in {'ihc','brightfield'}:
                if not isinstance(entry.get('record'),dict) or not entry['record'].get('export_item_key'):return False
            elif entry.get('kind')=='fluoro_image' and module=='fluoro':
                if not isinstance(entry.get('record'),dict) or not any(i.get('uid')==entry.get('image_uid') for i in panel.images()):return False
            else:return False
    except (ValueError,TypeError,KeyError):return False
    canvas=controller.canvas
    occupied={(round(item.pos().x(),3),round(item.pos().y(),3)) for item in canvas.scene.items() if item.isVisible()}
    first=entries[0].get('object',entries[0].get('record',{}))
    offset=24.0
    while (round(float(first.get('x',0))+offset,3),round(float(first.get('y',0))+offset,3)) in occupied:offset+=24
    ids=[]
    for entry in entries:
        uid=uuid4().hex;ids.append(uid)
        if entry['kind']=='annotation':
            data=entry['object'];data.update(object_id=uid,x=float(data['x'])+offset,y=float(data['y'])+offset,hidden=False)
            data['source']=LayoutSourceIdentity(panel.source_module,panel.panel_uuid,uid,'layout_annotation').to_dict()
            if module=='wb':data.setdefault('metadata',{})['wb_kind']='free_annotation'
            obj=LayoutObject.from_dict(data)
            record=annotation_record(obj)
            key='text_uuid' if obj.object_type=='text' else 'shape_uuid'
            record[key]=uid
            if module=='wb':
                panel.data.setdefault('wb_layout',{}).setdefault('free_annotations',[]).append(obj.to_dict())
            elif module=='fluoro':
                image=panel.active_image()
                if image is None:return False
                image.setdefault('layout_texts' if obj.object_type=='text' else 'layout_shapes',[]).append(record)
            else:
                panel.data.setdefault(f'{module}_layout',{}).setdefault('texts' if obj.object_type=='text' else 'shapes',[]).append(record)
        elif entry['kind']=='output':
            record=entry['record'];record.update(instance_uid=uid,x=float(record.get('x',0))+offset,y=float(record.get('y',0))+offset,enabled=True)
            panel.data.setdefault(f'{module}_layout',{}).setdefault('outputs',[]).append(record)
        elif entry['kind']=='fluoro_image':
            image=next(i for i in panel.images() if i.get('uid')==entry['image_uid'])
            record=entry['record'];record.update(x=float(record.get('x',0))+offset,y=float(record.get('y',0))+offset,enabled=True)
            image.setdefault('layout_item_geometries',{})[uid]=record
        else:
            record=entry['record'];record.update(plot_uuid=uid,x=float(record.get('x',0))+offset,y=float(record.get('y',0))+offset,enabled=True)
            if module=='qpcr':panel.data.setdefault('qpcr_layout',{}).setdefault('plots',[]).append(record)
            else:panel.analysis.setdefault('fluoro_layout_plots',[]).append(record)
    controller._refresh()
    canvas.scene.clearSelection()
    for item in canvas.scene.items():
        if item.data(1) in ids:item.setSelected(True)
        obj=getattr(item,'layout_object',None)
        if obj is not None and (any(obj.metadata.get(key) in ids for key in ('text_uuid','shape_uuid')) or obj.metadata.get('output',{}).get('instance_uid') in ids):item.setSelected(True)
    new_keys={str(item.data(1)):controller.tools.item_key(item) for item in controller.tools.selected()}
    for item in controller.tools.selected():
        obj=getattr(item,'layout_object',None)
        if obj is not None:
            for key in ('text_uuid','shape_uuid'):
                if obj.metadata.get(key):new_keys[str(obj.metadata[key])]=controller.tools.item_key(item)
    identifiers={entry['editor_id']:new_keys[uid] for entry,uid in zip(entries,ids)
                 if entry.get('editor_id') and uid in new_keys}
    controller.tools.paste_groups(payload.get('groups',{}),identifiers)
    return True
