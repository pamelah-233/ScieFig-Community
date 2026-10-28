from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass,field
import re
from typing import Any
from xml.etree import ElementTree


@dataclass(frozen=True)
class SvgTextEntry:
    index: int
    text: str
    x: float | None = None
    y: float | None = None
    rotation_deg: float = 0.0
    style: dict[str,Any] = field(default_factory=dict)
    text_payload: dict[str,Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SvgTextOverride:
    text: str | None = None
    dx: float | None = None
    dy: float | None = None
    x: float | None = None
    y: float | None = None
    style: dict[str,Any] = field(default_factory=dict)
    text_payload: dict[str,Any] | None = None

    def to_payload(self) -> str | dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.text is not None:
            payload["text"] = self.text
        if self.dx is not None:
            payload["dx"] = self.dx
        if self.dy is not None:
            payload["dy"] = self.dy
        if self.x is not None:
            payload["x"] = self.x
        if self.y is not None:
            payload["y"] = self.y
        if self.style:payload['style']=dict(self.style)
        if self.text_payload is not None:payload['text_payload']=dict(self.text_payload)
        if set(payload) == {"text"}:
            return str(payload["text"])
        return payload


def svg_text_entries(svg_bytes: bytes) -> list[SvgTextEntry]:
    root = ElementTree.fromstring(svg_bytes)
    entries: list[SvgTextEntry] = []

    def visit(element: ElementTree.Element, rotation: float, inherited=None) -> None:
        current_rotation = rotation + _rotation_from_transform(element.get("transform"))
        css=dict(inherited or {})
        css.update(_css_properties(element.get('style','')))
        for key in ('font-family','font-size','font-weight','font-style','text-anchor','fill','text-decoration'):
            if element.get(key) is not None:css[key]=element.get(key)
        if _local_name(element.tag) == "text":
            text = "".join(element.itertext()).strip()
            if text:
                entries.append(
                    SvgTextEntry(
                        index=int(element.get('data-sciefig-text-index',len(entries))),
                        text=text,
                        x=_attribute_float(element, "x"),
                        y=_attribute_float(element, "y"),
                        rotation_deg=current_rotation,
                        style=_canonical_text_style(css),
                        text_payload=_svg_rich_payload(element,css),
                    )
                )
        for child in list(element):
            visit(child, current_rotation,css)

    visit(root, 0.0)
    return entries


def normalize_svg_text_overrides(overrides: Mapping[str, Any] | None) -> dict[str, str | dict[str, Any]]:
    normalized: dict[str, str | dict[str, Any]] = {}
    for key, value in dict(overrides or {}).items():
        override = _coerce_override(value)
        if override is None:
            continue
        normalized[str(key)] = override.to_payload()
    return normalized


def update_svg_text_overrides(
    overrides: Mapping[str, Any] | None,
    index: int,
    *,
    text: str | None = None,
    dx: float | None = None,
    dy: float | None = None,
    x: float | None = None,
    y: float | None = None,
    style: Mapping[str,Any] | None = None,
    text_payload: Mapping[str,Any] | None = None,
) -> dict[str, str | dict[str, Any]]:
    normalized = normalize_svg_text_overrides(overrides)
    key = str(int(index))
    normalized[key] = build_svg_text_override(
        normalized.get(key),
        text=text,
        dx=dx,
        dy=dy,
        x=x,
        y=y,
        style=style,
        text_payload=text_payload,
    )
    return normalized


def apply_svg_text_overrides(svg_bytes: bytes, overrides: Mapping[str, Any] | None) -> bytes:
    normalized = {str(key): override for key, override in ((_k, _coerce_override(_v)) for _k, _v in dict(overrides or {}).items()) if override is not None}
    if not normalized:
        return bytes(svg_bytes)
    root = ElementTree.fromstring(svg_bytes)
    text_elements = [element for element in root.iter() if _local_name(element.tag) == "text" and "".join(element.itertext()).strip()]
    for index, element in enumerate(text_elements):
        index=int(element.get('data-sciefig-text-index',index))
        element.set('data-sciefig-text-index',str(index))
        override = normalized.get(str(index))
        if override is None:
            continue
        if override.text is not None:
            for child in list(element):
                element.remove(child)
            element.text = override.text
        current_x = _attribute_float(element, "x")
        current_y = _attribute_float(element, "y")
        target_x = override.x
        target_y = override.y
        if target_x is None and override.dx is not None and current_x is not None:
            target_x = current_x + override.dx
        if target_y is None and override.dy is not None and current_y is not None:
            target_y = current_y + override.dy
        if target_x is not None:
            element.set("x", _format_svg_number(target_x))
        if target_y is not None:
            element.set("y", _format_svg_number(target_y))
        if (target_x is not None or target_y is not None) and current_x is not None and current_y is not None:
            def move_pivot(match):
                parts=[float(value) for value in re.split(r'[\s,]+',match.group(1).strip())]
                if len(parts)==3 and abs(parts[1]-current_x)<1e-8 and abs(parts[2]-current_y)<1e-8:
                    return 'rotate({} {} {})'.format(_format_svg_number(parts[0]),
                        _format_svg_number(target_x if target_x is not None else current_x),
                        _format_svg_number(target_y if target_y is not None else current_y))
                return match.group(0)
            transform=element.get('transform','')
            if transform:element.set('transform',re.sub(r'rotate\(([^)]+)\)',move_pivot,transform))
        if override.style:
            css=_css_properties(element.get('style',''))
            css.update(_style_css(override.style))
            element.set('style',';'.join(f'{k}:{v}' for k,v in css.items()))
        if override.text_payload is not None:
            _apply_rich_text(element,override.text_payload)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def strip_svg_text_layers(svg_bytes: bytes) -> bytes:
    root = ElementTree.fromstring(svg_bytes)
    if not _remove_text_children(root):
        return bytes(svg_bytes)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def build_svg_text_override(
    existing: Any = None,
    *,
    text: str | None = None,
    dx: float | None = None,
    dy: float | None = None,
    x: float | None = None,
    y: float | None = None,
    style: Mapping[str,Any] | None = None,
    text_payload: Mapping[str,Any] | None = None,
) -> str | dict[str, Any]:
    current = _coerce_override(existing)
    use_relative = dx is not None or dy is not None
    use_absolute = x is not None or y is not None
    override = SvgTextOverride(
        text=text if text is not None else (current.text if current is not None else None),
        dx=(dx if dx is not None else (current.dx if current is not None and not use_absolute else None)) if use_relative or (current is not None and current.dx is not None and not use_absolute) else None,
        dy=(dy if dy is not None else (current.dy if current is not None and not use_absolute else None)) if use_relative or (current is not None and current.dy is not None and not use_absolute) else None,
        x=(x if x is not None else (current.x if current is not None and not use_relative else None)) if use_absolute or (current is not None and current.x is not None and not use_relative) else None,
        y=(y if y is not None else (current.y if current is not None and not use_relative else None)) if use_absolute or (current is not None and current.y is not None and not use_relative) else None,
        style={**(current.style if current else {}),**dict(style or {})},
        text_payload=dict(text_payload) if text_payload is not None else (current.text_payload if current else None),
    )
    return override.to_payload()


def _coerce_override(value: Any) -> SvgTextOverride | None:
    if value is None:
        return None
    if isinstance(value, SvgTextOverride):
        return value
    if isinstance(value, str):
        return SvgTextOverride(text=value)
    if isinstance(value, Mapping):
        text = value.get("text")
        dx = value.get("dx")
        dy = value.get("dy")
        x = value.get("x")
        y = value.get("y")
        override = SvgTextOverride(
            text=str(text) if text is not None else None,
            dx=_coerce_float(dx),
            dy=_coerce_float(dy),
            x=_coerce_float(x),
            y=_coerce_float(y),
            style=dict(value.get('style') or {}),
            text_payload=dict(value['text_payload']) if isinstance(value.get('text_payload'),Mapping) else None,
        )
        if override.text is None and override.dx is None and override.dy is None and override.x is None and override.y is None and not override.style and override.text_payload is None:
            return None
        return override
    return SvgTextOverride(text=str(value))


def _css_properties(css):
    return {k.strip():v.strip() for part in str(css).split(';') if ':' in part for k,v in [part.split(':',1)]}


def _canonical_text_style(css):
    match=re.search(r'[\d.]+',str(css.get('font-size','10')))
    weight=str(css.get('font-weight','normal'))
    return {'font_family':str(css.get('font-family','Arial')).strip(" '\""),
        'font_size_pt':float(match.group()) if match else 10.0,
        'bold':weight in {'bold','bolder'} or (weight.isdigit() and int(weight)>=600),
        'italic':css.get('font-style')=='italic','underline':'underline' in css.get('text-decoration',''),
        'color':css.get('fill','#000000'),'anchor':css.get('text-anchor','start')}


def _style_css(style):
    css={}
    mapping={'font_family':'font-family','font_size_pt':'font-size','color':'fill','text_color':'fill'}
    for key,target in mapping.items():
        if key in style:css[target]=f'{float(style[key]):g}px' if key=='font_size_pt' else str(style[key])
    if 'bold' in style:css['font-weight']='bold' if style['bold'] else 'normal'
    if 'italic' in style:css['font-style']='italic' if style['italic'] else 'normal'
    if 'underline' in style:css['text-decoration']='underline' if style['underline'] else 'none'
    if 'anchor' in style:css['text-anchor']=str(style['anchor'])
    if 'vertical_align' in style:css['baseline-shift']={'superscript':'super','subscript':'sub'}.get(style['vertical_align'],'baseline')
    return css


def _svg_rich_payload(element,base_css):
    parts=[];runs=[]
    def walk(node,css):
        current={**css,**_css_properties(node.get('style',''))}
        if node.text:
            start=sum(map(len,parts));parts.append(node.text)
            if node is not element:
                style=_canonical_text_style(current)
                style['vertical_align']={'super':'superscript','sub':'subscript'}.get(current.get('baseline-shift'),'normal')
                runs.append({'start':start,'length':len(node.text),**style})
        for child in node:
            walk(child,current)
            if child.tail:parts.append(child.tail)
    walk(element,base_css)
    raw=''.join(parts);offset=len(raw)-len(raw.lstrip());plain=raw.strip()
    for run in runs:run['start']=max(0,run['start']-offset)
    return {'plain_text':plain,'runs':runs,'alignment':'left',
        'underline':'underline' in base_css.get('text-decoration',''),
        'line_spacing':float(element.get('data-sciefig-line-spacing','1'))}


def _apply_rich_text(element,payload):
    text=str(payload.get('plain_text',''.join(element.itertext())))
    for child in list(element):element.remove(child)
    element.text=''
    namespace=element.tag.split('}')[0]+'}' if '}' in element.tag else ''
    runs=payload.get('runs',[])
    if '\n' in text:
        spacing=max(1.0,float(payload.get('line_spacing',1)))
        element.set('data-sciefig-line-spacing',str(spacing))
        font_size=_canonical_text_style(_css_properties(element.get('style','')))['font_size_pt']
        offset=0
        for line_number,line in enumerate(text.split('\n')):
            span=ElementTree.SubElement(element,namespace+'tspan',{
                'x':element.get('x','0'),'y':_format_svg_number(float(element.get('y','0'))+line_number*font_size*1.2*spacing)})
            local_runs=[]
            for run in runs:
                start=max(offset,int(run.get('start',0)))
                end=min(offset+len(line),int(run.get('start',0))+int(run.get('length',0)))
                if end>start:local_runs.append({**run,'start':start-offset,'length':end-start})
            _apply_rich_text(span,{'plain_text':line,'runs':local_runs})
            if line_number<len(text.split('\n'))-1:span.tail='\n'
            offset+=len(line)+1
        return
    breaks={0,len(text)}
    for run in runs:
        start=max(0,min(len(text),int(run.get('start',0))))
        end=min(len(text),start+max(0,int(run.get('length',0))))
        breaks.update((start,end))
    cuts=sorted(breaks)
    for start,end in zip(cuts,cuts[1:]):
        style={}
        for run in runs:
            if int(run.get('start',0))<=start<int(run.get('start',0))+int(run.get('length',0)):
                style.update(_style_css(run))
        span=ElementTree.SubElement(element,namespace+'tspan')
        span.text=text[start:end]
        if style:span.set('style',';'.join(f'{k}:{v}' for k,v in style.items()))


def _attribute_float(element: ElementTree.Element, name: str) -> float | None:
    value = element.get(name)
    return _coerce_float(value)


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _format_svg_number(value: float) -> str:
    return f"{float(value):.12g}"


def _rotation_from_transform(transform: str | None) -> float:
    if not transform:
        return 0.0
    rotation = 0.0
    for match in re.finditer(r"rotate\(\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", transform):
        rotation += _coerce_float(match.group(1)) or 0.0
    for match in re.finditer(r"matrix\(\s*([^)]+)\)", transform):
        parts = [part.strip() for part in re.split(r"[\s,]+", match.group(1).strip()) if part.strip()]
        if len(parts) < 6:
            continue
        try:
            import math

            a = float(parts[0])
            b = float(parts[1])
            rotation += math.degrees(math.atan2(b, a))
        except ValueError:
            continue
    return rotation


def _remove_text_children(parent: ElementTree.Element) -> bool:
    removed = False
    for child in list(parent):
        if _local_name(child.tag) == "text" and "".join(child.itertext()).strip():
            parent.remove(child)
            removed = True
            continue
        removed = _remove_text_children(child) or removed
    return removed


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]
