"""Conservative geometry and rendered-color evidence for PDF chart marks."""
from collections import Counter
from statistics import median

import fitz
import numpy as np

from .demo_common import color_to_hex, evidence_item, page_norm_bbox, unique_hexes


def clip_segment(seg, rect):
    """Clip the line itself, never project an exterior line onto the crop edge."""
    x, y, x1, y1 = seg
    dx, dy = x1-x, y1-y
    lo, hi = 0., 1.
    for p, q in ((-dx, x-rect.x0), (dx, rect.x1-x), (-dy, y-rect.y0), (dy, rect.y1-y)):
        if abs(p) < 1e-9:
            if q < 0:
                return None
        elif p < 0:
            lo = max(lo, q/p)
        else:
            hi = min(hi, q/p)
        if lo > hi:
            return None
    return x+lo*dx, y+lo*dy, x+hi*dx, y+hi*dy


def visible_drawings(page, clip):
    clips = {}
    out = []
    for item in page.get_drawings(extended=True):
        level = item.get('level', 0)
        clips = {key: value for key, value in clips.items() if key < level}
        if item['type'] == 'clip':
            clips[level] = fitz.Rect(item['scissor'])
            continue
        if item['type'] == 'group':
            continue
        region = fitz.Rect(clip)
        for active in clips.values():
            region &= active
        if region.is_empty:
            continue
        rect = fitz.Rect(item['rect']) + (-.7, -.7, .7, .7)
        if rect.intersects(region):
            if (item.get('fill') is not None and item.get('fill_opacity', 0) == 1
                    and [i[0] for i in item['items']] == ['re']
                    and item['rect'].contains(clip) and region.contains(clip)):
                # A later opaque panel background erases earlier PDF objects.
                out.clear()
            out.append(dict(item, visible_clip=region))
    return out


def marker_shape(items):
    ops = [item[0] for item in items]
    if ops == ['re']:
        return 'square'
    if len(ops) == 4 and set(ops) == {'c'}:
        return 'circle'
    if len(ops) in (3, 4) and set(ops) == {'l'}:
        if all(abs(i[1].x-i[2].x) > .1 and abs(i[1].y-i[2].y) > .1 for i in items):
            return 'diamond'
    return None


def visible_axis_strokes(page, clip, candidates):
    if not candidates:
        return []
    scale = 4
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    visible = []
    for item in candidates:
        x0,y0,x1,y1 = item['seg']
        hits = 0
        for t in np.linspace(.08, .92, 25):
            x = min(pix.width-1, max(0, round((x0+t*(x1-x0))*scale)-pix.x))
            y = min(pix.height-1, max(0, round((y0+t*(y1-y0))*scale)-pix.y))
            hits += max(pix.pixel(x,y)[:3]) < 130
        if hits >= 20:
            visible.append(item)
    return visible


def chart_mark_signals(page, clip, drawings, signals):
    """Only recover roles when repeated native geometry identifies them."""
    fills = [d for d in drawings if d.get('fill') is not None and
             d['visible_clip'].contains(d['rect'])]
    bars = [d for d in fills if [i[0] for i in d['items']] == ['re']
            and 4 < d['rect'].width < clip.width*.35
            and d['rect'].height > d['rect'].width*1.35
            and color_to_hex(d['fill']) != '#FFFFFF']
    if bars:
        baseline = Counter(round(d['rect'].y1, 1) for d in bars).most_common(1)[0][0]
        bars = [d for d in bars if abs(d['rect'].y1-baseline) < .2]
    if len(bars) < 2:
        bars = []
    points = [d for d in fills if 1 <= min(d['rect'].width, d['rect'].height)
              and max(d['rect'].width, d['rect'].height) <= 10
              and .75 < d['rect'].width/d['rect'].height < 1.35
              and marker_shape(d['items']) and color_to_hex(d['fill']) != '#FFFFFF']
    if len(points) < 3:
        points = []
    if not bars and not points:
        return
    # PDF primitive RGB values can differ from the visible ICC-managed colors.
    # Sample the core of native marks from the same PDF renderer used by the UI.
    scale = 4
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    color_counts = {}
    for d in points + bars:
        r = d['rect']
        x0 = max(0, int((r.x0+r.width*.15)*scale)-pix.x)
        x1 = min(pix.width, int((r.x1-r.width*.15)*scale)-pix.x)
        y0 = max(0, int((r.y0+r.height*.15)*scale)-pix.y)
        y1 = min(pix.height, int((r.y1-r.height*.15)*scale)-pix.y)
        counts = color_counts.setdefault(tuple(d['fill']), Counter())
        counts.update(map(tuple, pixels[y0:y1,x0:x1,:3].reshape(-1,3)))

    def rendered_color(drawing):
        counts = color_counts[tuple(drawing['fill'])]
        if not counts:
            return color_to_hex(drawing['fill'])
        rgb = counts.most_common(1)[0][0]
        return '#%02X%02X%02X' % rgb

    def put(key, value, drawing, detail, method='pdf_geometry'):
        signals[key] = value
        signals['methods'][key] = method
        signals['evidence'][key] = [evidence_item('geometry', page_norm_bbox(page.rect, drawing['rect']), detail)]

    if points:
        sizes = [(d['rect'].width+d['rect'].height)/2 for d in points]
        put('point_size', median(sizes), points[0], 'Native marker diameter in PDF points', 'pdf_native')
        shapes = {marker_shape(d['items']) for d in points}
        if len(shapes) == 1:
            put('point_shape', shapes.pop(), points[0], 'Repeated native marker path geometry', 'pdf_geometry')
        else:
            signals['point_shape'] = None
            signals['methods'].pop('point_shape', None)
            signals['warnings'].append('Mixed marker shapes; a single marker shape cannot recover this panel')
        put('point_line_width', median(float(d.get('width') or 0) for d in points), points[0],
            'Marker outline width; fill-only paths have no outline', 'pdf_native')
        points.sort(key=lambda d: (d['rect'].x0, d['rect'].y0))
        colors = unique_hexes([rendered_color(d) for d in points], tol=14, limit=None)
        put('series_colors', colors, points[0], 'Rendered native marker colors, left-to-right', 'pdf_rendered')
        signals['series_order_reason'] = 'Rendered native marker colors, left-to-right; verify group correspondence'
        signals['warnings'] = [w for w in signals['warnings'] if not w.startswith('No legend patches found; series colors')]
    if bars:
        bars.sort(key=lambda d: d['rect'].x0)
        colors = unique_hexes([rendered_color(d) for d in bars], tol=14, limit=None)
        if len(colors) == 1:
            put('bar_fill_color', colors[0], bars[0], 'Common rendered fill of repeated bars', 'pdf_rendered')
            put('bar_use_series_colors', False, bars[0], 'Uniform bars are independent of marker palette')
        else:
            put('bar_use_series_colors', True, bars[0], 'Bars use a series palette')
            if not points:
                put('series_colors', colors, bars[0], 'Rendered bar colors, left-to-right', 'pdf_rendered')
        centers = [(d['rect'].x0+d['rect'].x1)/2 for d in bars]
        gaps = [b-a for a,b in zip(centers, centers[1:]) if b>a]
        if gaps:
            put('bar_width_fraction', min(.95, median(d['rect'].width for d in bars)/median(gaps)),
                bars[0], 'Bar width relative to median category spacing; grouped layouts require review')
        outlines = [d for d in drawings if d.get('color') is not None and any(
            max(abs(a-b) for a,b in zip(d['rect'], bar['rect'])) < .1 for bar in bars)]
        if outlines:
            put('bar_edge_color', color_to_hex(outlines[0]['color']), outlines[0], 'Native bar outline color', 'pdf_native')
            put('bar_line_width', median(float(d['width']) for d in outlines), outlines[0], 'Native bar outline width', 'pdf_native')
        errors = []
        for d in drawings:
            if d.get('color') is None:
                continue
            lines = [i for i in d['items'] if i[0] == 'l']
            unique = {}
            for i in lines:
                unique[tuple(sorted((tuple(i[1]), tuple(i[2]))))] = i
            lines = list(unique.values())
            if len(lines) not in (3, 4) or any(i[0] != 'l' for i in d['items']):
                continue
            hs = [i for i in lines if abs(i[1].y-i[2].y)<.05 and abs(i[1].x-i[2].x)>1]
            vs = [i for i in lines if abs(i[1].x-i[2].x)<.05]
            if len(hs) != 2 or not vs:
                continue
            cx = (hs[0][1].x+hs[0][2].x)/2
            if not all(abs((i[1].x+i[2].x)/2-cx)<.1 for i in hs+vs):
                continue
            if any(abs(cx-c)<.3 and abs(d['rect'].y0-bar['rect'].y0)<12 for c,bar in zip(centers,bars)):
                errors.append((d, abs(hs[0][1].x-hs[0][2].x)/2))
        if len(errors) >= 2:
            put('sem_line_width', median(float(d['width']) for d,c in errors), errors[0][0],
                'Native capped error-bar stroke; SD/SEM meaning is not inferred')
            put('sem_cap_thick', signals['sem_line_width'], errors[0][0], 'Native cap stroke')
            put('sem_cap_size', median(c for d,c in errors), errors[0][0], 'Half cap width in PDF points')

