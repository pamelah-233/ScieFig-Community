from __future__ import annotations

from math import isnan
from typing import Any

STAT_TESTS = [
    "Unpaired t-test",
    "Welch t-test",
    "Paired t-test",
    "Mann-Whitney U",
    "Wilcoxon signed-rank",
    "One-way ANOVA",
    "Kruskal-Wallis",
]


def significance_label(p_value: float | None) -> str:
    if p_value is None or isnan(float(p_value)):
        return "n/a"
    if p_value < 0.001:
        return "***"
    if p_value < 0.01:
        return "**"
    if p_value < 0.05:
        return "*"
    return "ns"


def compute_group_statistics(rows: list[dict[str, Any]], group_assignments: dict[str, str], reference_group: str, test_name: str) -> dict[str, Any]:
    import numpy as np
    from scipy import stats

    plot_rows = []
    warnings=[]
    excluded_nonfinite=0
    for row in rows:
        try:
            numeric_value=float(row['value'])
        except (KeyError,TypeError,ValueError):
            excluded_nonfinite+=1
            continue
        if not np.isfinite(numeric_value):
            excluded_nonfinite+=1
            continue
        lane_label = str(row.get("lane_label", ""))
        sample_key = str(row.get("sample_key", lane_label))
        sample_group_key = str(row.get("sample_group_key", sample_key))
        group = group_assignments.get(sample_group_key, group_assignments.get(sample_key, group_assignments.get(lane_label, lane_label)))
        item = dict(row)
        item['value']=numeric_value
        item["group"] = group
        plot_rows.append(item)
    grouped: dict[str, list[float]] = {}
    for row in plot_rows:
        grouped.setdefault(str(row["group"]), []).append(float(row["value"]))
    summary = [
        {
            "group": group,
            "n": len(values),
            "mean": round(float(np.mean(values)), 4) if values else float("nan"),
            "sem": round(float(np.std(values, ddof=1) / np.sqrt(len(values))), 4) if len(values) > 1 else 0.0,
        }
        for group, values in grouped.items()
    ]
    comparisons = []
    groups = list(grouped.keys())
    if test_name in {"One-way ANOVA", "Kruskal-Wallis"}:
        p_value=None
        if len(groups)>=2 and all(len(grouped[group])>=2 for group in groups):
            arrays=[np.asarray(grouped[group],dtype=float) for group in groups]
            try:
                result=stats.f_oneway(*arrays) if test_name=='One-way ANOVA' else stats.kruskal(*arrays)
                p_value=float(result.pvalue) if np.isfinite(result.pvalue) else None
            except ValueError:
                warnings.append('statistics_test_unavailable')
        else:
            warnings.append('statistics_insufficient_groups')
        comparisons.append({'group1':'all','group2':'all','p_value':p_value,'label':significance_label(p_value),'test':test_name})
    else:
        reference_values = grouped.get(reference_group, [])
        for group in groups:
            if group == reference_group:
                continue
            if test_name in {'Paired t-test','Wilcoxon signed-rank'}:
                pair_maps=[]
                valid_pairs=True
                for name in (reference_group,group):
                    selected=[row for row in plot_rows if row['group']==name]
                    mapping={str(row['pair_id']).strip():row['value'] for row in selected if row.get('pair_id') is not None and str(row['pair_id']).strip()}
                    valid_pairs &= len(mapping)==len(selected) and len(mapping)>=2
                    pair_maps.append(mapping)
                if valid_pairs and pair_maps[0].keys()==pair_maps[1].keys():
                    ids=sorted(pair_maps[0])
                    p_value=_two_group_p_value([pair_maps[0][i] for i in ids],[pair_maps[1][i] for i in ids],test_name)
                else:
                    p_value=None
                    if 'statistics_pairing_required' not in warnings:warnings.append('statistics_pairing_required')
            else:
                p_value = _two_group_p_value(reference_values, grouped.get(group, []), test_name)
            comparisons.append({"group1": reference_group, "group2": group, "p_value": p_value, "label": significance_label(p_value), "test": test_name})
    if excluded_nonfinite:warnings.append('statistics_nonfinite_excluded')
    return {
        "test": test_name,
        "reference_group": reference_group,
        "plot_data": plot_rows,
        "groups": summary,
        "comparisons": comparisons,
        "warnings":warnings,
        "excluded_nonfinite_count":excluded_nonfinite,
    }


def _two_group_p_value(a: list[float], b: list[float], test_name: str) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    import numpy as np
    from scipy import stats

    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    try:
        if test_name == "Welch t-test":
            if _has_near_zero_variance(x) or _has_near_zero_variance(y):
                return None
            return float(stats.ttest_ind(x, y, equal_var=False).pvalue)
        if test_name == "Paired t-test":
            if len(x) != len(y):
                return None
            if _has_near_zero_variance(x - y):
                return None
            return float(stats.ttest_rel(x, y).pvalue)
        if test_name == "Mann-Whitney U":
            return float(stats.mannwhitneyu(x, y, alternative="two-sided").pvalue)
        if test_name == "Wilcoxon signed-rank":
            if len(x) != len(y):
                return None
            return float(stats.wilcoxon(x, y).pvalue)
        if _has_near_zero_variance(x) or _has_near_zero_variance(y):
            return None
        return float(stats.ttest_ind(x, y, equal_var=True).pvalue)
    except Exception:
        return None


def _has_near_zero_variance(values: np.ndarray) -> bool:
    import numpy as np

    finite_values = np.asarray(values, dtype=float)
    finite_values = finite_values[np.isfinite(finite_values)]
    if finite_values.size < 2:
        return True
    variance = float(np.var(finite_values, ddof=1))
    scale = max(1.0, float(np.max(np.abs(finite_values))))
    return bool(variance <= np.finfo(float).eps * scale * scale)
