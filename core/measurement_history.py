"""Select current measurements without treating recalculations as new samples."""
import json
from math import isfinite


def measurement_scope_key(measurement):
    def geometry(value):
        if isinstance(value,dict):
            return {k:geometry(v) for k,v in value.items() if k not in ('uuid','roi_uuid','active_item_uuid','selected')}
        if isinstance(value,list):return [geometry(v) for v in value]
        return value
    roi=measurement.get('roi') or {}
    if roi.get('mode')=='whole_image':roi={}
    elif roi.get('items'):
        item=next((item for item in roi['items'] if str(item.get('roi_uuid',''))==str(roi.get('active_item_uuid',''))),roi['items'][-1])
        roi={'type':item.get('type','rectangle'),'coordinates_px':item.get('coordinates_px',{})}
    return json.dumps([measurement.get('image_uid') or measurement.get('measurement_uuid') or '',geometry(roi)],sort_keys=True,ensure_ascii=False)


def latest_measurements(measurements, metric=None):
    latest={}
    for index,measurement in enumerate(measurements):
        if metric is not None:
            result=measurement.get('results',{})
            value=result.get(metric) if isinstance(result,dict) else None
            if not isinstance(value,(int,float)) or not isfinite(value):continue
        key=measurement_scope_key(measurement) if measurement.get('image_uid') or measurement.get('measurement_uuid') else str(index)
        latest[key]=measurement
    return list(latest.values())
