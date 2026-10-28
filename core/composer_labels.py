"""Panel captions are independent text annotations with optional image following."""
from core.models.composer import AnnotationObject


def label_for_reference(figure, ref_uid):
    return next((a for a in figure.annotations if a.properties.get('role')=='panel_label'
                 and a.properties.get('panel_ref_uid')==ref_uid),None)


def sync_panel_labels(figure, units_per_mm=4.0):
    for ref in figure.panel_refs:
        label=label_for_reference(figure,ref.ref_uid)
        if label is None:
            if not ref.label_visible:
                continue
            label=AnnotationObject(type='text',x_mm=ref.x_mm+3/units_per_mm,y_mm=ref.y_mm,
                layer_index=ref.layer_index+1,locked=ref.label_locked,
                properties={'role':'panel_label','panel_ref_uid':ref.ref_uid,'follow_panel':True,
                    'offset_x_mm':3/units_per_mm,'offset_y_mm':0.0,'content':ref.figure_label,
                    'label_auto':ref.label_auto,'font_family':'Arial','font_size_pt':10.0,
                    'font_bold':True,'color':'#111111','width_mm':25.0,'height_mm':8.0})
            figure.annotations.append(label)
        if label.properties.get('label_auto',False) and ref.label_auto:
            if label.properties.get('content')!=ref.figure_label:
                label.properties['content']=ref.figure_label
                rich=label.properties.get('text',{})
                rich.update(plain_text=ref.figure_label,html='')
        else:
            ref.figure_label=str(label.properties.get('content',''))
            ref.label_auto=False
        ref.label_visible=label.visible
        ref.label_locked=label.locked
        if label.properties.get('follow_panel',True):
            label.x_mm=ref.x_mm+float(label.properties.get('offset_x_mm',0))
            label.y_mm=ref.y_mm+float(label.properties.get('offset_y_mm',0))


def label_is_visible(figure, annotation):
    if not annotation.visible:
        return False
    props=annotation.properties
    if props.get('role')!='panel_label' or not props.get('follow_panel',True):
        return True
    ref=next((r for r in figure.panel_refs if r.ref_uid==props.get('panel_ref_uid')),None)
    return ref is not None and ref.visible
