from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multicomp import pairwise_tukeyhsd

from core.utils.json import dataframe_records


@dataclass(frozen=True)
class QPCRAnalysisResult:
    cleaned: pd.DataFrame
    technical_summary: pd.DataFrame
    delta_ct: pd.DataFrame
    plot_data: pd.DataFrame
    statistics: dict[str, Any]
    analysis: dict[str, Any]
    summary: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "cleaned_data": dataframe_records(self.cleaned),
            "technical_summary": dataframe_records(self.technical_summary),
            "delta_ct": dataframe_records(self.delta_ct),
            "delta_delta_ct": dataframe_records(self.plot_data),
            "fold_change": dataframe_records(self.plot_data),
        }


def _parse_sample(sample: str) -> tuple[str, str, str]:
    text = str(sample).strip()
    bio_match = re.search(r"(?:^|[_\-\s])bio(?:logical)?[_\-\s]?(\d+)", text, flags=re.IGNORECASE)
    rep_match = re.search(r"(?:^|[_\-\s])rep(?:licate)?[_\-\s]?(\d+)", text, flags=re.IGNORECASE)
    tech_match = re.search(r"(?:^|[_\-\s])tech(?:nical)?[_\-\s]?(\d+)", text, flags=re.IGNORECASE)
    biological = bio_match.group(1) if bio_match else (rep_match.group(1) if rep_match else text)
    technical = tech_match.group(1) if tech_match else "1"
    group = text
    for pattern in (
        r"[_\-\s]?tech(?:nical)?[_\-\s]?\d+",
        r"[_\-\s]?bio(?:logical)?[_\-\s]?\d+",
        r"[_\-\s]?rep(?:licate)?[_\-\s]?\d+",
    ):
        group = re.sub(pattern, "", group, flags=re.IGNORECASE)
    group = group.strip("_- ") or text
    return group, str(biological), str(technical)


def _sem(values: pd.Series) -> float:
    clean = values.dropna()
    if len(clean) <= 1:
        return 0.0
    return float(clean.std(ddof=1) / np.sqrt(len(clean)))


def _star(p_value: float | None) -> str:
    if p_value is None or np.isnan(p_value):
        return "n/a"
    if p_value < 0.001:
        return "***"
    if p_value < 0.01:
        return "**"
    if p_value < 0.05:
        return "*"
    return "ns"


def _default_reference_gene(df: pd.DataFrame) -> str:
    targets = list(dict.fromkeys(df["target"].dropna().astype(str).tolist()))
    for candidate in ("GAPDH", "ACTB", "18S", "HPRT1", "TBP"):
        if candidate in targets:
            return candidate
    if not targets:
        raise ValueError("No target genes found.")
    return targets[0]


def _default_control_group(df: pd.DataFrame) -> str:
    groups = list(dict.fromkeys(df["group"].dropna().astype(str).tolist()))
    if not groups:
        raise ValueError("No sample groups found.")
    return groups[0]


def prepare_qpcr_import_options(dataframe: pd.DataFrame) -> dict[str, Any]:
    cleaned = _clean_qpcr(dataframe)
    targets = [str(value) for value in dict.fromkeys(cleaned["target"].dropna().astype(str).tolist()) if value]
    groups = [str(value) for value in dict.fromkeys(cleaned["group"].dropna().astype(str).tolist()) if value]
    reference = _default_reference_gene(cleaned) if targets else ""
    return {
        "cleaned_data": dataframe_records(cleaned),
        "targets": targets,
        "groups": groups,
        "suggested_reference_gene": reference,
    }


def _clean_qpcr(
    df: pd.DataFrame,
    group_overrides: dict[str, str] | None = None,
    included_samples: set[str] | None = None,
) -> pd.DataFrame:
    cleaned = df.copy()
    parsed = cleaned["sample"].apply(_parse_sample)
    cleaned[["group", "biological_replicate", "technical_replicate"]] = pd.DataFrame(parsed.tolist(), index=cleaned.index)
    overrides = {str(sample): str(group).strip() for sample, group in dict(group_overrides or {}).items() if str(group).strip()}
    if overrides:
        cleaned["group"] = cleaned.apply(lambda row: overrides.get(str(row["sample"]), row["group"]), axis=1)
    cleaned["qc_flag"] = "ok"
    cleaned.loc[cleaned["ct"].isna(), "qc_flag"] = "undetermined"
    cleaned.loc[cleaned["ct"] > 35, "qc_flag"] = "ct_gt_35"
    cleaned.loc[cleaned["ct"] > 38, "qc_flag"] = "ct_gt_38"
    cleaned["excluded"] = cleaned["ct"].isna() | (cleaned["ct"] > 38)
    if included_samples is not None:
        cleaned.loc[~cleaned["sample"].astype(str).isin(included_samples), "excluded"] = True
    return cleaned


def _technical_average(cleaned: pd.DataFrame) -> pd.DataFrame:
    included = cleaned[~cleaned["excluded"]].copy()
    grouped = included.groupby(["group", "biological_replicate", "target"], as_index=False)
    summary = grouped.agg(
        mean_ct=("ct", "mean"),
        sd_ct=("ct", lambda value: float(value.std(ddof=1)) if len(value) > 1 else 0.0),
        technical_replicates=("ct", "count"),
    )
    summary["tech_sd_flag"] = summary["sd_ct"] > 0.5
    return summary


def _reference_cv(technical: pd.DataFrame, reference_gene: str) -> float:
    ref = technical[technical["target"] == reference_gene]["mean_ct"].dropna()
    if ref.empty or ref.mean() == 0:
        return 0.0
    return float(ref.std(ddof=1) / ref.mean() * 100) if len(ref) > 1 else 0.0


def _delta_ct(technical: pd.DataFrame, reference_gene: str, control_group: str) -> tuple[pd.DataFrame, list[str]]:
    ref = technical[technical["target"] == reference_gene][["group", "biological_replicate", "mean_ct"]].rename(
        columns={"mean_ct": "reference_ct"}
    )
    targets = technical[technical["target"] != reference_gene].copy()
    merged = targets.merge(ref, on=["group", "biological_replicate"], how="inner")
    if merged.empty:
        raise ValueError("No target/reference pairs could be matched for ΔCt calculation.")
    merged["delta_ct"] = merged["mean_ct"] - merged["reference_ct"]
    control_means = (
        merged[merged["group"] == control_group]
        .groupby("target")["delta_ct"]
        .mean()
        .rename("control_mean_delta_ct")
    )
    missing_control_targets = sorted(set(merged["target"]) - set(control_means.index))
    if missing_control_targets:
        merged = merged[~merged["target"].isin(missing_control_targets)].copy()
    if merged.empty:
        raise ValueError(f"Control group '{control_group}' has no usable ΔCt values for any target.")
    merged = merged.merge(control_means, on="target", how="left")
    merged["delta_delta_ct"] = merged["delta_ct"] - merged["control_mean_delta_ct"]
    merged["fold_change"] = 2 ** (-merged["delta_delta_ct"])
    return merged, missing_control_targets


def _statistics(delta_ct: pd.DataFrame, control_group: str) -> dict[str, Any]:
    output: dict[str, Any] = {"targets": {}, "stat_performed_on": "delta_ct"}
    for target, target_df in delta_ct.groupby("target"):
        group_values = {
            group: values["delta_ct"].dropna().to_numpy(dtype=float)
            for group, values in target_df.groupby("group")
        }
        comparisons: list[dict[str, Any]] = []
        groups = list(group_values.keys())
        if len(groups) == 2:
            first, second = groups
            if len(group_values[first]) >= 2 and len(group_values[second]) >= 2:
                result = stats.ttest_ind(group_values[first], group_values[second], equal_var=True)
                p_value = float(result.pvalue)
                comparisons.append({"group1": first, "group2": second, "p_value": p_value, "label": _star(p_value)})
                stat_test = "unpaired_t_test"
            else:
                stat_test = "unpaired_t_test"
                comparisons.append({"group1": first, "group2": second, "p_value": None, "label": "n/a"})
        elif len(groups) >= 3:
            arrays = [values for values in group_values.values() if len(values) >= 2]
            stat_test = "one_way_anova_tukey"
            if len(arrays) >= 2:
                anova = stats.f_oneway(*arrays)
                tukey = pairwise_tukeyhsd(endog=target_df["delta_ct"], groups=target_df["group"], alpha=0.05)
                for row in tukey.summary().data[1:]:
                    group1, group2, meandiff, p_adj, lower, upper, reject = row
                    p_value = float(p_adj)
                    comparisons.append(
                        {
                            "group1": str(group1),
                            "group2": str(group2),
                            "p_value": p_value,
                            "label": _star(p_value),
                            "reject": bool(reject),
                        }
                    )
                output["targets"][str(target)] = {
                    "stat_test": stat_test,
                    "anova_p_value": float(anova.pvalue),
                    "comparisons": comparisons,
                }
                continue
            comparisons = [{"group1": control_group, "group2": group, "p_value": None, "label": "n/a"} for group in groups if group != control_group]
        else:
            stat_test = "insufficient_groups"
        output["targets"][str(target)] = {"stat_test": stat_test, "comparisons": comparisons}
    return output


def _plot_data(delta_ct: pd.DataFrame, error_bar: str) -> pd.DataFrame:
    agg = delta_ct.groupby(["target", "group"], as_index=False).agg(
        mean_fold_change=("fold_change", "mean"),
        sd=("fold_change", lambda value: float(value.std(ddof=1)) if len(value) > 1 else 0.0),
        sem=("fold_change", _sem),
        n=("fold_change", "count"),
    )
    agg["error"] = agg["sem" if error_bar.upper() == "SEM" else "sd"]
    return delta_ct.merge(agg, on=["target", "group"], how="left")


def _analysis_warnings(cleaned: pd.DataFrame, skipped_control_targets: list[str] | None = None) -> list[str]:
    warnings: list[str] = []
    missing_ct_count = int((cleaned["qc_flag"] == "undetermined").sum())
    if missing_ct_count:
        warnings.append(f"{missing_ct_count} missing Ct technical replicate(s) were excluded before averaging.")
    if skipped_control_targets:
        warnings.append(
            "Skipped "
            f"{len(skipped_control_targets)} target(s) without a usable control-group ΔCt baseline: "
            f"{', '.join(skipped_control_targets)}."
        )
    return warnings


def _summary(
    cleaned: pd.DataFrame,
    technical: pd.DataFrame,
    reference_gene: str,
    reference_cv: float,
    statistics: dict[str, Any],
    error_bar: str,
    warnings: list[str] | None = None,
) -> str:
    per_group_n = technical[["group", "biological_replicate"]].drop_duplicates().groupby("group").size()
    biological_n = int(per_group_n.min()) if not per_group_n.empty else 0
    excluded_count = int(cleaned["excluded"].sum())
    stat_tests = {target_info["stat_test"] for target_info in statistics["targets"].values()}
    stat_label = "ANOVA + Tukey" if "one_way_anova_tukey" in stat_tests else "t-test"
    warning_text = ""
    if warnings:
        warning_text = " " + " ".join(warning.replace(" were excluded ", " excluded ") for warning in warnings)
    return (
        f"n = {biological_n} biological replicates; technical replicates averaged; "
        f"reference gene: {reference_gene} (CV = {reference_cv:.1f}%); "
        f"statistics: {stat_label} on ΔCt; plotted as 2^(-ΔΔCt); "
        f"error bars: {error_bar.upper()}; {excluded_count} sample excluded."
        f"{warning_text}"
    )


def analyze_qpcr_dataframe(
    dataframe: pd.DataFrame,
    reference_gene: str | None = None,
    control_group: str | None = None,
    error_bar: str = "SEM",
    group_overrides: dict[str, str] | None = None,
    included_samples: set[str] | None = None,
) -> QPCRAnalysisResult:
    cleaned = _clean_qpcr(dataframe, group_overrides=group_overrides, included_samples=included_samples)
    technical = _technical_average(cleaned)
    resolved_reference = reference_gene or _default_reference_gene(cleaned)
    resolved_control = control_group or _default_control_group(cleaned)
    delta, skipped_control_targets = _delta_ct(technical, resolved_reference, resolved_control)
    plot = _plot_data(delta, error_bar)
    stats_payload = _statistics(delta, resolved_control)
    reference_cv = _reference_cv(technical, resolved_reference)
    per_group_n = technical[["group", "biological_replicate"]].drop_duplicates().groupby("group").size()
    biological_n = int(per_group_n.min()) if not per_group_n.empty else 0
    exclusions = dataframe_records(cleaned[cleaned["excluded"]][["sample", "target", "ct", "qc_flag"]])
    warnings = _analysis_warnings(cleaned, skipped_control_targets)
    analysis = {
        "normalization": "delta_delta_ct",
        "reference_gene": resolved_reference,
        "control_group": resolved_control,
        "replicate_structure": {
            "biological_replicates": biological_n,
            "biological_replicates_per_group": {str(group): int(count) for group, count in per_group_n.items()},
            "technical_averaged": True,
        },
        "reference_gene_cv_percent": reference_cv,
        "exclusions": exclusions,
        "group_overrides": dict(group_overrides or {}),
        "included_samples": sorted(included_samples) if included_samples is not None else None,
        "stat_test": "one_way_anova_tukey" if len(cleaned["group"].unique()) >= 3 else "unpaired_t_test",
        "stat_performed_on": "delta_ct",
        "plotted_as": "fold_change_2neg_ddct",
        "error_bar": error_bar.upper(),
        "statistics": stats_payload,
        "warnings": warnings,
    }
    summary = _summary(cleaned, technical, resolved_reference, reference_cv, stats_payload, error_bar, warnings)
    return QPCRAnalysisResult(
        cleaned=cleaned,
        technical_summary=technical,
        delta_ct=delta,
        plot_data=plot,
        statistics=stats_payload,
        analysis=analysis,
        summary=summary,
    )
