from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd


InstrumentName = Literal["auto", "applied_biosystems", "roche_lightcycler"]


@dataclass(frozen=True)
class QPCRParseResult:
    dataframe: pd.DataFrame
    instrument: str
    source_path: Path
    header_row: int | None
    sheet_name: str | int | None = None


COLUMN_ALIASES = {
    "well": {"well", "position"},
    "sample": {"sample name", "sample", "name", "sample_name"},
    "target": {"target name", "target", "gene", "assay", "detector", "target_name"},
    "ct": {"ct", "cq", "cp", "c t", "threshold cycle"},
}


def _normalized_column_name(value: object) -> str:
    return str(value).strip().lower().replace("\ufeff", "")


def _canonical_columns(columns: list[object]) -> dict[str, str]:
    normalized = {_normalized_column_name(column): str(column) for column in columns}
    canonical: dict[str, str] = {}
    for required, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                canonical[required] = normalized[alias]
                break
    return canonical


def _clean_text_series(series: pd.Series) -> pd.Series:
    cleaned = series.where(series.notna(), "")
    cleaned = cleaned.astype(str).str.strip()
    return cleaned.mask(cleaned.str.lower().isin({"nan", "none", "null", "<na>"}), "")


def _find_header_row_csv(path: Path, sep: str) -> int | None:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for index, line in enumerate(handle):
            cells = [cell.strip().lower() for cell in line.split(sep)]
            if any(cell == "well" for cell in cells) and any(cell in {"ct", "cq", "cp"} for cell in cells):
                return index
            if any(cell == "name" for cell in cells) and any(cell == "cp" for cell in cells):
                return index
    return None


def _find_header_row_in_dataframe(preview: pd.DataFrame) -> int | None:
    for index, row in preview.iterrows():
        cells = {_normalized_column_name(value) for value in row.dropna().tolist()}
        if "well" in cells and ({"ct", "cq", "cp"} & cells):
            return int(index)
        if "name" in cells and "cp" in cells:
            return int(index)
    return None


def _sheet_score(sheet_name: str | int, dataframe: pd.DataFrame, header_row: int) -> tuple[int, int, int]:
    name = str(sheet_name).strip().casefold()
    preferred_name_score = 0
    if name == "results":
        preferred_name_score = 100
    elif "analysis result" in name:
        preferred_name_score = 40
    elif "result" in name:
        preferred_name_score = 30

    canonical = _canonical_columns(list(dataframe.columns))
    schema_score = 0
    if {"well", "sample", "target", "ct"}.issubset(canonical):
        schema_score = 20
    elif {"sample", "target", "ct"}.issubset(canonical):
        schema_score = 10

    return preferred_name_score + schema_score, len(dataframe.dropna(how="all")), -header_row


def _read_excel_table(path: Path) -> tuple[pd.DataFrame, int | None, str | int | None]:
    sheets = pd.read_excel(path, sheet_name=None, header=None, nrows=80)
    candidates: list[tuple[tuple[int, int, int], pd.DataFrame, int, str | int]] = []
    for sheet_name, preview in sheets.items():
        header_row = _find_header_row_in_dataframe(preview)
        if header_row is None:
            continue
        dataframe = pd.read_excel(path, sheet_name=sheet_name, header=header_row)
        candidates.append((_sheet_score(sheet_name, dataframe, header_row), dataframe, header_row, sheet_name))
    if candidates:
        _score, dataframe, header_row, sheet_name = max(candidates, key=lambda candidate: candidate[0])
        return dataframe, header_row, sheet_name
    first_sheet = next(iter(sheets)) if sheets else 0
    dataframe = pd.read_excel(path, sheet_name=first_sheet)
    return dataframe, None, first_sheet


def _read_table(path: Path) -> tuple[pd.DataFrame, int | None, str | int | None]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt", ".tsv"}:
        sep = "\t" if suffix in {".txt", ".tsv"} else ","
        header_row = _find_header_row_csv(path, sep)
        dataframe = pd.read_csv(path, sep=sep, header=header_row)
        return dataframe, header_row, None
    if suffix in {".xls", ".xlsx"}:
        return _read_excel_table(path)
    raise ValueError(f"Unsupported qPCR file type: {suffix}")


def _detect_instrument(canonical: dict[str, str], selected: InstrumentName) -> str:
    if selected != "auto":
        return selected
    if "well" in canonical and "ct" in canonical and "target" in canonical:
        return "applied_biosystems"
    if "sample" in canonical and "ct" in canonical and "well" not in canonical:
        return "roche_lightcycler"
    raise ValueError("Unable to detect qPCR instrument format from column names.")


def parse_qpcr_file(path: str | Path, instrument: InstrumentName = "auto") -> QPCRParseResult:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)

    dataframe, header_row, sheet_name = _read_table(source)
    dataframe = dataframe.dropna(how="all").copy()
    canonical = _canonical_columns(list(dataframe.columns))
    required = {"sample", "target", "ct"}
    missing = required - set(canonical)
    if missing:
        raise ValueError(f"Missing required qPCR columns: {', '.join(sorted(missing))}")

    parsed = pd.DataFrame()
    parsed["well"] = _clean_text_series(dataframe[canonical["well"]]) if "well" in canonical else ""
    parsed["sample"] = _clean_text_series(dataframe[canonical["sample"]])
    parsed["target"] = _clean_text_series(dataframe[canonical["target"]])
    parsed["ct_raw"] = _clean_text_series(dataframe[canonical["ct"]])
    parsed["ct"] = pd.to_numeric(parsed["ct_raw"].replace({"Undetermined": None, "undetermined": None, "": None}), errors="coerce")
    parsed = parsed[(parsed["sample"] != "") & (parsed["target"] != "")]
    parsed["source_row"] = parsed.index.astype(int) + 1
    parsed.reset_index(drop=True, inplace=True)

    if parsed.empty:
        raise ValueError("No qPCR data rows were found.")

    return QPCRParseResult(
        dataframe=parsed,
        instrument=_detect_instrument(canonical, instrument),
        source_path=source,
        header_row=header_row,
        sheet_name=sheet_name,
    )
