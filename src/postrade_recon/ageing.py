"""Ageing and severity. Age is counted in business days from the break's reference date."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import DEFAULT, Config


def bucket_for(age: int, cfg: Config = DEFAULT) -> str:
    for upper, label in cfg.age_buckets:
        if age <= upper:
            return label
    return cfg.age_overflow_label


def severity_for(notional: float, cfg: Config = DEFAULT) -> str:
    if notional >= cfg.high_notional:
        return "HIGH"
    if notional >= cfg.medium_notional:
        return "MEDIUM"
    return "LOW"


def business_days_between(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return max(int(np.busday_count(start.date(), end.date())), 0)


def add_ageing(df: pd.DataFrame, as_of: pd.Timestamp, cfg: Config = DEFAULT) -> pd.DataFrame:
    out = df.copy()
    out["age_days"] = [business_days_between(d, as_of) for d in out["reference_date"]]
    out["age_bucket"] = [bucket_for(a, cfg) for a in out["age_days"]]
    out["severity"] = [severity_for(n, cfg) for n in out["notional_usd"]]
    return out
