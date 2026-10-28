"""Preserve Browser calibration without baking annotations into histology pixels."""
from copy import deepcopy
from core.measurement_scale import resolve_measurement_scale
from core.layout_scale_bar import seed_layout_scale_presentation, normalize_scale_bar_template


def apply_browser_histology_transfer(panel, record, payload, module):
    if module not in ('ihc', 'brightfield'):
        raise ValueError('Unsupported histology destination')
    saved = deepcopy(payload.get('calibration') or {})
    display = dict(payload.get('display') or {})
    saved['length_um'] = display.get('length_um', 50)
    scale = resolve_measurement_scale(record, saved)
    if display.get('visible') and scale.get('calibrated'):
        length = float(saved['length_um']) / float(scale['um_per_px'])
        cx, y = display.get('center_x_px'), display.get('line_y_px')
        if cx is not None and y is not None:
            scale['line_px'] = dict(x1=float(cx)-length/2, y1=float(y), x2=float(cx)+length/2, y2=float(y))
        scale['visible'] = True
    record['measurement_scale_bar'] = scale
    record['browser_scale_bar'] = deepcopy(display)
    layout = panel.data.setdefault(module+'_layout', {})
    if display.get('visible'):
        seed_layout_scale_presentation(layout, measurement_scale=scale, target_ids=[record['uid']],
                                       states_key='scale_bars', template_key='scale_bar_template')
        for state in layout.get('scale_bars', []):
            if state.get('target_id') != record['uid']:
                continue
            template = {'label_length_um': saved['length_um']}
            for key in ('line_color', 'text_color', 'font_family', 'bold', 'italic', 'text_position'):
                if key in display:
                    template[key] = display[key]
            for source, target in (('font_size_px', 'font_size'), ('line_thickness_px', 'line_thickness')):
                if source in display:
                    template[target] = display[source]
            state['template'] = normalize_scale_bar_template(template)
    panel.append_audit('browser_to_'+module, str(record['uid']))
