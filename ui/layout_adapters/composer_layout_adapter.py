"""Composer mm records ↔ shared scene items; scientific assets are not rewritten."""
from copy import deepcopy
from core.models.layout_editor import LayoutObject, LayoutObjectStyle


def annotation_layout_object(annotation, units_per_mm=4.0):
    props = annotation.properties
    metadata = {"text": deepcopy(props.get("text", {}))}
    if annotation.type == "shape":
        metadata.update(shape_kind=props.get("shape_type", "rectangle"),
                        stroke_dash=props.get("stroke_dash", "solid"),
                        fill_alpha=float(props.get("fill_alpha", 255)) / 255.0)
        for key in ('x1','y1','x2','y2'):
            if key in props:
                metadata[key]=float(props[key])*units_per_mm
        if props.get('shape_type')=='line' and 'y1' not in props:
            metadata['y1']=metadata['y2']=float(props.get('height_mm',12))*units_per_mm/2
    return LayoutObject(
        object_id=annotation.annotation_uid, object_type=annotation.type,
        x=annotation.x_mm*units_per_mm, y=annotation.y_mm*units_per_mm,
        width=float(props.get("width_mm", 55.0))*units_per_mm,
        height=float(props.get("height_mm", 20.0))*units_per_mm,
        rotation_deg=float(props.get("rotation_deg", 0.0)),
        z_order=annotation.layer_index, locked=annotation.locked, hidden=not annotation.visible,
        text=str(props.get("content", "")), metadata=metadata,
        style=LayoutObjectStyle(font_family=props.get("font_family", "Arial"),
            font_size_pt=float(props.get("font_size_pt", 8.0)),
            bold=bool(props.get("font_bold", False)), italic=bool(props.get("font_italic", False)),
            text_color=props.get("color", "#000000"),
            stroke_color=props.get("stroke_color", "#000000"),
            fill_color=props.get("fill_color", "transparent"),
            stroke_width=float(props.get("stroke_width_pt", 1.0))),
    )


def update_annotation_from_layout(annotation, obj, units_per_mm=4.0):
    annotation.x_mm, annotation.y_mm = obj.x/units_per_mm, obj.y/units_per_mm
    annotation.layer_index, annotation.locked, annotation.visible = obj.z_order, obj.locked, not obj.hidden
    props = annotation.properties
    props.update(width_mm=obj.width/units_per_mm, height_mm=obj.height/units_per_mm,
                 rotation_deg=obj.rotation_deg)
    if annotation.type == "text":
        props.update(content=obj.text, text=deepcopy(obj.to_dict()["metadata"].get("text", {})),
                     font_family=obj.style.font_family, font_size_pt=obj.style.font_size_pt,
                     font_bold=obj.style.bold, font_italic=obj.style.italic, color=obj.style.text_color)
    else:
        props.update(shape_type=obj.metadata.get("shape_kind", "rectangle"),
                     stroke_color=obj.style.stroke_color, stroke_width_pt=obj.style.stroke_width,
                     fill_color=obj.style.fill_color, stroke_dash=obj.metadata.get("stroke_dash", "solid"),
                     fill_alpha=round(float(obj.metadata.get("fill_alpha", 1.0))*255))
        for key in ('x1','y1','x2','y2'):
            if key in obj.metadata:
                props[key]=float(obj.metadata[key])/units_per_mm
