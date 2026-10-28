"""Shared annotation record fields used by source-module layout adapters."""
from copy import deepcopy
from core.models.layout_editor import LayoutObject,LayoutObjectStyle


def text_from_record(record,object_id,metadata=None):
    return LayoutObject(object_id=object_id,object_type='text',
        x=float(record.get('x',24)),y=float(record.get('y',24)),
        width=float(record.get('width',160)),height=float(record.get('height',45)),
        text=str(record.get('text','Text')),rotation_deg=float(record.get('rotation',0)),
        z_order=int(record.get('z_order',200)),locked=bool(record.get('locked',False)),
        hidden=bool(record.get('hidden',False)),
        style=LayoutObjectStyle(font_family=str(record.get('font_family','Arial')),
            font_size_pt=float(record.get('font_size',record.get('font_size_pt',10))),
            bold=bool(record.get('bold',False)),italic=bool(record.get('italic',False)),
            text_color=str(record.get('color','#111111'))),
        metadata={**(metadata or {}),'text':deepcopy(record.get('text_payload',{}))})


def shape_from_record(record,object_id,metadata=None):
    kind=str(record.get('shape_type',record.get('type','rectangle')))
    return LayoutObject(object_id=object_id,object_type='line' if kind=='line' else 'shape',
        x=float(record.get('x',40)),y=float(record.get('y',40)),
        width=float(record.get('width',120)),height=float(record.get('height',70)),
        rotation_deg=float(record.get('rotation',0)),z_order=int(record.get('z_order',160)),
        locked=bool(record.get('locked',False)),hidden=bool(record.get('hidden',False)),
        style=LayoutObjectStyle(stroke_color=str(record.get('stroke_color','#000000')),
            fill_color=str(record.get('fill_color','transparent')),
            stroke_width=float(record.get('stroke_width',2)),opacity=float(record.get('opacity',1))),
        metadata={**(metadata or {}),'shape_kind':kind,'shape_type':kind,
            'stroke_dash':record.get('stroke_dash','solid'),'fill_alpha':record.get('fill_alpha',1),
            **{key:record[key] for key in ('x1','y1','x2','y2') if key in record}})


def write_shared_fields(record,obj):
    """Keep existing and unknown fields; add only shared editable fields."""
    meta=obj.to_dict()['metadata']
    if not ('text' in meta if obj.object_type=='text' else any(k in meta for k in ('shape_kind','stroke_dash','fill_alpha','x1','y1','x2','y2'))):
        return record
    record.update(width=float(obj.width),height=float(obj.height),rotation=float(obj.rotation_deg),
                  locked=bool(obj.locked),hidden=bool(obj.hidden),z_order=int(obj.z_order))
    if obj.object_type=='text':
        record['text_payload']=deepcopy(meta.get('text',{}))
    else:
        record['opacity']=obj.style.opacity
        for key in ('stroke_dash','fill_alpha','x1','y1','x2','y2'):
            if key in meta:record[key]=deepcopy(meta[key])
    return record


def restore_shared_fields(obj):
    meta=obj.to_dict()['metadata']
    record=meta.get('text_record') if obj.object_type=='text' else meta.get('shape',meta.get('shape_record'))
    if not isinstance(record,dict):return obj
    payload=obj.to_dict()
    for old,new in [('width','width'),('height','height'),('rotation','rotation_deg'),('z_order','z_order'),('locked','locked'),('hidden','hidden')]:
        if old in record:payload[new]=record[old]
    if 'text_payload' in record:payload['metadata']['text']=deepcopy(record['text_payload'])
    if 'opacity' in record:payload['style']['opacity']=record['opacity']
    for key in ('stroke_dash','fill_alpha','x1','y1','x2','y2'):
        if key in record:
            payload['metadata'][key]=record[key]
            payload['metadata']['shape_kind']=record.get('shape_type',meta.get('shape_type','rectangle'))
    return LayoutObject.from_dict(payload)


def update_annotation_extras(layout,obj):
    key='text_uuid' if obj.object_type=='text' else 'shape_uuid'
    uid=obj.metadata.get(key)
    if uid is None:return
    records=layout.get('texts' if obj.object_type=='text' else 'shapes',[])
    for index,record in enumerate(records):
        if record.get(key)==uid or (not record.get(key) and index==obj.metadata.get('index')):
            write_shared_fields(record,obj)
            return


def prune_removed_annotations(layout,objects):
    for collection,key,prefix in [('texts','text_uuid','text'),('shapes','shape_uuid','shape')]:
        present={str(obj.metadata[key]) for obj in objects if key in obj.metadata}
        if collection in layout:
            layout[collection]=[record for index,record in enumerate(layout[collection])
                if not isinstance(record,dict) or str(record.get(key) or f'{prefix}:{index}') in present]
