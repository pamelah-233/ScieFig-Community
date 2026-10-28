"""Shared inline SVG text editing, with an explicit SVG→pixmap coordinate adapter."""
from copy import deepcopy
from PyQt6 import sip
from PyQt6.QtCore import QObject,QTimer,QPointF
from PyQt6.QtGui import QFontMetricsF,QTransform
from PyQt6.QtSvg import QSvgRenderer
from core.models.layout_editor import LayoutObject,LayoutObjectStyle
from core.utils.svg_text import svg_text_entries,build_svg_text_override
from ui.widgets.shared_layout_text import SharedTextItem


class SvgEditableTextItem(SharedTextItem):
    def __init__(self,binding,entry):
        self.binding=binding;self.entry=entry
        self._font_factor=72.0/binding.logical_dpi
        self.display_font_size_pt=float(entry.style.get('font_size_pt',10))
        style=entry.style
        family=str(style.get('font_family','Arial')).split(',')[0].strip(" '\"")
        obj=LayoutObject(f'svg-text:{entry.index}','text',0,0,1000,80,text=entry.text,
            style=LayoutObjectStyle(font_family=family,font_size_pt=self.display_font_size_pt*self._font_factor,
                bold=bool(style.get('bold')),italic=bool(style.get('italic')),text_color=style.get('color','#000000')))
        if entry.text_payload:
            rich=deepcopy(entry.text_payload)
            for run in rich['runs']:
                if 'font_size_pt' in run:run['font_size_pt']*=self._font_factor
            payload=obj.to_dict();payload['metadata']['text']=rich
            obj=LayoutObject.from_dict(payload)
        super().__init__(obj,parent=binding.visual)
        self.resize_handles_enabled=False
        self.setData(0,'shared_svg_text');self.setData(1,entry.index)
        self.document().setTextWidth(-1)
        width=max(1,self.document().idealWidth())
        self.setTextWidth(width)
        self.prepareGeometryChange()
        payload=self.layout_object.to_dict()
        payload.update(width=width,height=self.document().size().height())
        self.layout_object=LayoutObject.from_dict(payload)
        self._ascent=QFontMetricsF(self.document().defaultFont()).ascent()
        self._anchor={'start':0,'middle':.5,'end':1}.get(style.get('anchor','start'),0)
        transform=QTransform();transform.scale(binding.sx,binding.sy)
        transform.translate(width*self._anchor,self._ascent)
        transform.rotate(entry.rotation_deg)
        transform.translate(-width*self._anchor,-self._ascent)
        self.setTransform(transform)
        self.setRotation(0)
        self.set_position_without_callback((entry.x-binding.vb.x()-width*self._anchor)*binding.sx,
            (entry.y-binding.vb.y()-self._ascent)*binding.sy)
        self._text_changed_callback=self._commit_text
        self._style_changed_callback=self._commit_style
        self._geometry_changed_callback=self._commit_position

    def _svg_text_payload(self):
        payload=deepcopy(self.to_layout_object().to_dict()['metadata'].get('text',{}))
        payload['html']=''
        for run in payload.get('runs',[]):
            if 'font_size_pt' in run:run['font_size_pt']/=self._font_factor
        return payload

    def apply_text_style(self,style):
        delta=dict(style)
        if 'font_size_pt' in delta:
            self.display_font_size_pt=float(delta['font_size_pt'])
            delta['font_size_pt']*=self._font_factor
        super().apply_text_style(delta)

    def apply_text_range_style(self,start,length,style):
        delta=dict(style)
        if 'font_size_pt' in delta:delta['font_size_pt']*=self._font_factor
        super().apply_text_range_style(start,length,delta)

    def _commit_text(self,*args):
        self.binding.change(self.entry.index,{'text':self.toPlainText(),'text_payload':self._svg_text_payload()})

    def _commit_style(self,_item,delta):
        style=self.layout_object.style.to_dict()
        style['underline']=bool(self._text_payload.get('underline',False))
        if 'alignment' in delta:
            style['anchor']={'left':'start','center':'middle','right':'end'}.get(delta['alignment'],'start')
        if 'font_size_pt' in style:style['font_size_pt']/=self._font_factor
        self.binding.change(self.entry.index,{'style':style,'text_payload':self._svg_text_payload()})

    def _commit_position(self,*args):
        x=self.pos().x()/self.binding.sx+self.binding.vb.x()+self.textWidth()*self._anchor
        y=self.pos().y()/self.binding.sy+self.binding.vb.y()+self._ascent
        self.binding.change(self.entry.index,{'x':x,'y':y})


class SvgTextBinding:
    def __init__(self,owner,visual,get_svg,get_overrides,store):
        self.owner,self.visual=owner,visual
        self.get_svg,self.get_overrides,self.store=get_svg,get_overrides,store
        self.items=[]
        self.pending=False

    def rebuild(self):
        if sip.isdeleted(self.owner) or sip.isdeleted(self.visual):return
        previous={item.entry.index:(item.isSelected(),item.retained_text_selection) for item in self.items if not sip.isdeleted(item)}
        for item in self.items:
            if not sip.isdeleted(item):
                item.setVisible(False);item.setParentItem(None)
                if item.scene():item.scene().removeItem(item)
        self.items=[]
        if self.owner.scene() is None:return
        svg=self.get_svg()
        if not svg:return
        renderer=QSvgRenderer(svg)
        self.vb=renderer.viewBoxF()
        size=self.visual.pixmap().size()
        self.sx=size.width()/max(1,self.vb.width());self.sy=size.height()/max(1,self.vb.height())
        from PyQt6.QtGui import QImage
        self.logical_dpi=float(QImage(1,1,QImage.Format.Format_RGB32).logicalDpiY())
        for entry in svg_text_entries(svg):
            if entry.x is None or entry.y is None:continue
            item=SvgEditableTextItem(self,entry)
            item.setZValue(2)
            if entry.index in previous:
                selected,retained=previous[entry.index]
                item.setSelected(selected);item.retained_text_selection=retained
            self.items.append(item)

    def change(self,index,delta):
        existing=self.get_overrides()
        value=build_svg_text_override(existing.get(str(index)),**delta)
        self.store(index,value)
        if not self.pending:
            self.pending=True
            QTimer.singleShot(0,self._refresh)

    def _refresh(self):
        self.pending=False
        if not sip.isdeleted(self.owner):
            self.owner._refresh_svg_payload()
            self.rebuild()
            self.owner._svg_text_items=self.items
