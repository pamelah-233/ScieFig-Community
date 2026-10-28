"""Explicit per-source pairing; identifiers never depend on the displayed row order."""
from copy import deepcopy


def sample_identity(row):
    if row.get('sample_group_key'):
        return str(row['sample_group_key'])
    if row.get('sample_key'):
        return str(row['sample_key'])
    if row.get('strip_id'):
        return f"{row['strip_id']}:{row.get('lane_index', '')}"
    return str(row.get('image_uid') or row.get('lane_label') or '')


def save_pair_ids(panel, source, mapping):
    all_sources = deepcopy(panel.analysis.get('statistics_pair_ids', {}))
    current = all_sources.setdefault(source, {})
    for key, value in mapping.items():
        text = str(value).strip() if value is not None else ''
        if text:
            current[str(key)] = text
        else:
            current.pop(str(key), None)
    panel.analysis['statistics_pair_ids'] = all_sources


def attach_pair_ids(panel, source, rows):
    mapping = panel.analysis.get('statistics_pair_ids', {}).get(source, {})
    result = deepcopy(rows)
    for row in result:
        key = sample_identity(row)
        if key in mapping:
            row['pair_id'] = mapping[key]
    return result
