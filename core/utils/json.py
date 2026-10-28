from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
if TYPE_CHECKING:
    import pandas as pd


def make_json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (np.datetime64, np.timedelta64)):
        return None if np.isnat(value) else value
    # pandas NA/NaT and other non-primitive scalars retain their original semantics.
    # Ordinary startup metadata never needs to initialize the DataFrame backend.
    import pandas as pd

    if pd.isna(value):
        return None
    return value


def dataframe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        records.append({str(key): make_json_safe(value) for key, value in row.items()})
    return records
