"""Synthetic data generator.

Builds an 'internal platform' view and a 'custodian / administrator' view of the same
fund book, then injects a known set of breaks. Because the injected counts are known,
the test suite can assert that the reconciler finds exactly what was planted.

All data is synthetic. No real securities, counterparties or clients are used.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ACCOUNTS = ["FUND_ALPHA", "FUND_BETA", "FUND_GAMMA"]
COUNTERPARTIES = [
    "Northgate Securities",
    "Harbor Prime",
    "Meridian Capital",
    "Summit Brokers",
    "Atlas Markets",
]
OPENING_CASH = {"FUND_ALPHA": 10_000_000.0, "FUND_BETA": 15_000_000.0, "FUND_GAMMA": 8_000_000.0}

SPLIT_SECURITIES = {"SEC004": 2.0, "SEC009": 2.0}  # custodian applied, platform did not
DIVIDEND_SECURITY, DIVIDEND_PER_SHARE = "SEC006", 0.45  # custodian paid, platform not booked
CUSTODIAN_FEES = {"FUND_BETA": -125.00}  # bank fee only the custodian booked

# Counts of planted breaks (tests assert these).
PLANTED = {
    "trade_missing_in_custodian": 6,
    "trade_missing_in_internal": 4,
    "trade_qty_mismatch": 8,
    "trade_price_mismatch": 8,
    "trade_price_within_tolerance": 6,  # must NOT become breaks
    "trade_settle_date_mismatch": 5,
    "margin_missing_in_custodian": 1,
    "margin_missing_in_internal": 1,
    "margin_amount_mismatch": 1,
    "margin_within_tolerance": 1,  # must NOT become a break
    "pricing_variance": 3,
    "pricing_stale": 2,
}


def _signed(side: pd.Series, qty: pd.Series) -> pd.Series:
    return np.where(side == "BUY", qty, -qty)


def _positions(opening: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    t = trades.assign(signed_qty=_signed(trades["side"], trades["quantity"]))
    moved = t.groupby(["account", "security_id"], as_index=False)["signed_qty"].sum()
    out = opening.merge(moved, on=["account", "security_id"], how="outer").fillna(0.0)
    out["quantity"] = out["opening_qty"] + out["signed_qty"]
    out = out[out["quantity"] != 0]
    return out[["account", "security_id", "quantity"]].sort_values(["account", "security_id"]).reset_index(drop=True)


def _cash(opening: dict, trades: pd.DataFrame, margin: pd.DataFrame, extra: dict | None = None) -> pd.DataFrame:
    rows = []
    for acct, start in opening.items():
        t = trades[trades["account"] == acct]
        signed = _signed(t["side"], t["quantity"])
        spent = float((signed * t["price"]).sum())
        m = float(margin.loc[margin["account"] == acct, "amount"].sum())
        bal = start - spent + m + (extra or {}).get(acct, 0.0)
        rows.append({"account": acct, "currency": "USD", "balance": round(bal, 2)})
    return pd.DataFrame(rows)


def generate(as_of: str = "2026-10-07", seed: int = 42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    as_of_ts = pd.Timestamp(as_of)
    bdays = pd.bdate_range(end=as_of_ts, periods=12)

    # ---- security master and prices -------------------------------------------------
    classes = ["EQUITY"] * 14 + ["ETF"] * 4 + ["FIXED_INCOME"] * 6
    sec_ids = [f"SEC{i:03d}" for i in range(1, 25)]
    sec = pd.DataFrame({"security_id": sec_ids, "asset_class": classes, "currency": "USD"})
    sec["description"] = [f"Sample {c.replace('_', ' ').title()} {i:03d}" for i, c in zip(range(1, 25), classes)]
    base = np.where(
        sec["asset_class"] == "FIXED_INCOME",
        rng.uniform(92, 108, len(sec)),
        np.where(sec["asset_class"] == "ETF", rng.uniform(50, 250, len(sec)), rng.uniform(20, 400, len(sec))),
    )
    street = np.round(base, 4)
    prior = np.round(street * (1 + rng.normal(0, 0.008, len(sec))), 4)
    internal = np.round(street * (1 + rng.normal(0, 0.00003, len(sec))), 4)

    reserved = set(SPLIT_SECURITIES) | {DIVIDEND_SECURITY}
    free_idx = [i for i, s in enumerate(sec_ids) if s not in reserved]
    pick = rng.permutation(free_idx)
    variance_idx, stale_idx = pick[: PLANTED["pricing_variance"]], pick[PLANTED["pricing_variance"] : PLANTED["pricing_variance"] + PLANTED["pricing_stale"]]
    for i in variance_idx:
        internal[i] = round(street[i] * (1 + rng.choice([-1, 1]) * rng.uniform(0.004, 0.02)), 4)
    for i in stale_idx:
        prior[i] = round(prior[i], 4)
        street[i] = round(prior[i] * (1 + rng.choice([-1, 1]) * rng.uniform(0.01, 0.03)), 4)
        internal[i] = prior[i]  # platform still carries yesterday's price
    prices = pd.DataFrame(
        {"security_id": sec_ids, "asset_class": classes, "internal_price": internal, "street_price": street, "prior_price": prior}
    )

    # ---- trades ----------------------------------------------------------------------
    n = 300
    t_sec = rng.choice(sec_ids, n)
    px = prices.set_index("security_id")["street_price"]
    trade_dates = rng.choice(bdays[:-1], n)
    trades = pd.DataFrame(
        {
            "trade_id": [f"TRD{100000 + i}" for i in range(n)],
            "account": rng.choice(ACCOUNTS, n),
            "security_id": t_sec,
            "side": rng.choice(["BUY", "SELL"], n),
            "quantity": rng.integers(1, 201, n) * 100,
            "price": np.round(px.loc[t_sec].to_numpy() * (1 + rng.normal(0, 0.005, n)), 4),
            "trade_date": pd.to_datetime(trade_dates),
            "counterparty": rng.choice(COUNTERPARTIES, n),
        }
    )
    trades["settle_date"] = trades["trade_date"] + pd.offsets.BDay(1)
    trades["currency"] = "USD"

    # ---- opening positions -------------------------------------------------------------
    pairs = [(a, s) for a in ACCOUNTS for s in sec_ids if rng.random() < 0.6]
    for must in [("FUND_ALPHA", "SEC004"), ("FUND_BETA", "SEC009"), ("FUND_ALPHA", DIVIDEND_SECURITY), ("FUND_BETA", DIVIDEND_SECURITY)]:
        if must not in pairs:
            pairs.append(must)
    opening = pd.DataFrame(pairs, columns=["account", "security_id"])
    opening["opening_qty"] = rng.integers(10, 500, len(opening)) * 100.0

    # ---- margin movements --------------------------------------------------------------
    n_m = 10
    margin = pd.DataFrame(
        {
            "movement_id": [f"MRG{1000 + i}" for i in range(n_m)],
            "account": rng.choice(ACCOUNTS, n_m),
            "counterparty": rng.choice(COUNTERPARTIES, n_m),
            "movement_date": pd.to_datetime(rng.choice(bdays[-5:], n_m)),
            "amount": np.round(rng.choice([-1, 1], n_m) * rng.uniform(50_000, 750_000, n_m), 2),
        }
    )

    # ---- build the custodian view and plant breaks -------------------------------------
    cust = trades.copy()
    eligible = trades.index[~trades["security_id"].isin(reserved)].to_numpy()
    order = iter(rng.permutation(eligible))

    def take(k: int) -> list:
        return [next(order) for _ in range(k)]

    drop_idx = take(PLANTED["trade_missing_in_custodian"])
    qty_idx = take(PLANTED["trade_qty_mismatch"])
    price_idx = take(PLANTED["trade_price_mismatch"])
    tiny_idx = take(PLANTED["trade_price_within_tolerance"])
    settle_idx = take(PLANTED["trade_settle_date_mismatch"])

    for i in qty_idx:
        delta = int(rng.choice([-1, 1]) * rng.integers(1, 10) * 100)
        if cust.at[i, "quantity"] + delta <= 0:
            delta = abs(delta)
        cust.at[i, "quantity"] = cust.at[i, "quantity"] + delta
    for i in price_idx:
        cust.at[i, "price"] = round(cust.at[i, "price"] * (1 + rng.choice([-1, 1]) * rng.uniform(0.005, 0.02)), 4)
    for i in tiny_idx:
        cust.at[i, "price"] = round(cust.at[i, "price"] * (1 + rng.choice([-1, 1]) * 0.00002), 4)
    for i in settle_idx:
        cust.at[i, "settle_date"] = cust.at[i, "settle_date"] + pd.offsets.BDay(1)
    cust = cust.drop(index=drop_idx)

    ok_secs = [s for s in sec_ids if s not in reserved]
    extra = pd.DataFrame(
        {
            "trade_id": [f"TRD9{i:04d}" for i in range(PLANTED["trade_missing_in_internal"])],
            "account": rng.choice(ACCOUNTS, PLANTED["trade_missing_in_internal"]),
            "security_id": rng.choice(ok_secs, PLANTED["trade_missing_in_internal"]),
            "side": rng.choice(["BUY", "SELL"], PLANTED["trade_missing_in_internal"]),
            "quantity": rng.integers(1, 51, PLANTED["trade_missing_in_internal"]) * 100,
            "trade_date": pd.to_datetime(rng.choice(bdays[:-1], PLANTED["trade_missing_in_internal"])),
            "counterparty": rng.choice(COUNTERPARTIES, PLANTED["trade_missing_in_internal"]),
            "currency": "USD",
        }
    )
    extra["price"] = np.round(px.loc[extra["security_id"]].to_numpy(), 4)
    extra["settle_date"] = extra["trade_date"] + pd.offsets.BDay(1)
    cust = pd.concat([cust, extra[cust.columns]], ignore_index=True)

    cust_margin = margin.copy()
    cust_margin = cust_margin.drop(index=[0])  # missing in custodian
    cust_margin.loc[1, "amount"] = round(cust_margin.loc[1, "amount"] * 1.025, 2)  # amount mismatch
    cust_margin.loc[2, "amount"] = round(cust_margin.loc[2, "amount"] + 0.50, 2)  # within tolerance
    cust_margin = pd.concat(
        [
            cust_margin,
            pd.DataFrame(
                [{"movement_id": "MRG9001", "account": "FUND_GAMMA", "counterparty": "Harbor Prime", "movement_date": bdays[-1], "amount": 120_000.00}]
            ),
        ],
        ignore_index=True,
    )

    # ---- positions and cash -------------------------------------------------------------
    int_pos = _positions(opening, trades)
    cust_pos = _positions(opening, cust)
    for s, ratio in SPLIT_SECURITIES.items():
        mask = cust_pos["security_id"] == s
        cust_pos.loc[mask, "quantity"] = cust_pos.loc[mask, "quantity"] * ratio

    dividend = {
        a: float(cust_pos.loc[(cust_pos["account"] == a) & (cust_pos["security_id"] == DIVIDEND_SECURITY), "quantity"].sum() * DIVIDEND_PER_SHARE)
        for a in ACCOUNTS
    }
    extras = {a: dividend[a] + CUSTODIAN_FEES.get(a, 0.0) for a in ACCOUNTS}
    int_cash = _cash(OPENING_CASH, trades, margin)
    cust_cash = _cash(OPENING_CASH, cust, cust_margin, extras)

    corp_actions = pd.DataFrame(
        [{"security_id": s, "action_type": "SPLIT", "ratio": r, "amount_per_share": np.nan, "ex_date": bdays[-2]} for s, r in SPLIT_SECURITIES.items()]
        + [{"security_id": DIVIDEND_SECURITY, "action_type": "DIVIDEND", "ratio": np.nan, "amount_per_share": DIVIDEND_PER_SHARE, "ex_date": bdays[-1]}]
    )

    return {
        "securities": sec,
        "prices": prices,
        "internal_trades": trades,
        "custodian_trades": cust,
        "internal_positions": int_pos,
        "custodian_positions": cust_pos,
        "internal_cash": int_cash,
        "custodian_cash": cust_cash,
        "internal_margin": margin,
        "custodian_margin": cust_margin,
        "corporate_actions": corp_actions,
    }


def write_csv(data: dict[str, pd.DataFrame], out_dir: str | Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, df in data.items():
        df.to_csv(out / f"{name}.csv", index=False)


def read_csv(in_dir: str | Path) -> dict[str, pd.DataFrame]:
    d = Path(in_dir)
    date_cols = {"trade_date", "settle_date", "movement_date", "ex_date"}
    data = {}
    for f in sorted(d.glob("*.csv")):
        df = pd.read_csv(f)
        for c in date_cols & set(df.columns):
            df[c] = pd.to_datetime(df[c])
        data[f.stem] = df
    return data
