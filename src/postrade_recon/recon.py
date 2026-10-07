"""Core reconciliation logic.

Five reconciliations, each owned by the team that would work the break:

  trade     internal trades vs custodian trades        Trade Accounting & Operation
  position  internal positions vs custodian positions  Trade Accounting / Middle Office
  cash      internal cash vs custodian cash            Trade Accounting & Operation
  margin    internal margin calls vs custodian         Treasury
  pricing   platform price vs street price             Pricing

Every break is a row in one unified schema (COLUMNS). Position and cash breaks are
*explained* where possible (caused by a trade break, a corporate action or a margin
break) so that analysts work root causes, not symptoms.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import (
    DEFAULT,
    OWNER_MIDDLE_OFFICE,
    OWNER_PRICING,
    OWNER_TRADE_ACCOUNTING,
    OWNER_TREASURY,
    Config,
)

COLUMNS = [
    "break_id", "category", "break_type", "owner_team", "account", "security_id", "reference",
    "internal_value", "custodian_value", "difference", "notional_usd", "reference_date",
    "age_days", "age_bucket", "severity", "explanation", "suggested_action",
]

SUGGESTED_ACTION = {
    "TRADE_MISSING_IN_CUSTODIAN": "Confirm trade with counterparty; check allocation and send trade to custodian.",
    "TRADE_MISSING_IN_INTERNAL": "Check for unbooked trade or wrong account; book trade or reject with custodian.",
    "TRADE_QTY_MISMATCH": "Obtain broker confirm; correct quantity on the side that disagrees.",
    "TRADE_PRICE_MISMATCH": "Obtain broker confirm; correct price and book the P&L / cash adjustment.",
    "TRADE_SETTLE_DATE_MISMATCH": "Check SSI and settlement instructions; align settle date to avoid a fail.",
    "POSITION_BREAK_TRADE_DRIVEN": "No separate action; clears when the linked trade break is resolved.",
    "POSITION_BREAK_CORP_ACTION_SPLIT": "Book the split on the platform (ratio and ex-date) and re-run.",
    "POSITION_BREAK_UNEXPLAINED": "Investigate: check opening balance, transfers and manual adjustments.",
    "POSITION_MISSING_IN_CUSTODIAN": "Confirm the holding exists; check account mapping at the custodian.",
    "POSITION_MISSING_IN_INTERNAL": "Check for unbooked holding or transfer in; book or query custodian.",
    "CASH_BREAK_UPSTREAM_DRIVEN": "No separate action; clears when linked trade, margin and corporate action breaks clear.",
    "CASH_BREAK_UNEXPLAINED_RESIDUAL": "Investigate the residual: fees, interest, FX or unbooked cash movement; book adjustment.",
    "MARGIN_MISSING_IN_CUSTODIAN": "Confirm margin call with counterparty; check whether the movement was sent or settled.",
    "MARGIN_MISSING_IN_INTERNAL": "Confirm margin call received; book the movement if valid.",
    "MARGIN_AMOUNT_MISMATCH": "Compare call statements; agree the exposure and rebook the correct amount.",
    "PRICING_VARIANCE": "Compare vendor and street source; challenge or override with documented support.",
    "PRICING_STALE": "Price not refreshed since prior day; re-pull the price feed and revalue.",
}


@dataclass
class ReconResult:
    breaks: pd.DataFrame
    stats: pd.DataFrame  # one row per category: compared, breaks, match_rate


def _row(**kw) -> dict:
    return {c: kw.get(c) for c in COLUMNS}


def _signed(side: pd.Series, qty: pd.Series) -> pd.Series:
    return pd.Series(np.where(side == "BUY", qty, -qty), index=qty.index)


# ------------------------------------------------------------------------- trades
def reconcile_trades(internal: pd.DataFrame, custodian: pd.DataFrame, cfg: Config = DEFAULT) -> list[dict]:
    m = internal.merge(custodian, on="trade_id", how="outer", suffixes=("_i", "_c"), indicator="mflag")
    rows: list[dict] = []
    for r in m.itertuples(index=False):
        if r.mflag == "left_only":
            rows.append(_row(
                category="TRADE", break_type="TRADE_MISSING_IN_CUSTODIAN", owner_team=OWNER_TRADE_ACCOUNTING,
                account=r.account_i, security_id=r.security_id_i, reference=r.trade_id,
                internal_value=f"{r.side_i} {r.quantity_i:,.0f} @ {r.price_i}", custodian_value="(missing)",
                notional_usd=r.quantity_i * r.price_i, reference_date=r.settle_date_i,
                explanation="Trade exists on the platform but not at the custodian.",
            ))
        elif r.mflag == "right_only":
            rows.append(_row(
                category="TRADE", break_type="TRADE_MISSING_IN_INTERNAL", owner_team=OWNER_TRADE_ACCOUNTING,
                account=r.account_c, security_id=r.security_id_c, reference=r.trade_id,
                internal_value="(missing)", custodian_value=f"{r.side_c} {r.quantity_c:,.0f} @ {r.price_c}",
                notional_usd=r.quantity_c * r.price_c, reference_date=r.settle_date_c,
                explanation="Trade exists at the custodian but not on the platform.",
            ))
        else:
            base = dict(category="TRADE", owner_team=OWNER_TRADE_ACCOUNTING, account=r.account_i,
                        security_id=r.security_id_i, reference=r.trade_id, reference_date=r.settle_date_i)
            if r.quantity_i != r.quantity_c:
                rows.append(_row(**base, break_type="TRADE_QTY_MISMATCH",
                                 internal_value=r.quantity_i, custodian_value=r.quantity_c,
                                 difference=r.quantity_i - r.quantity_c,
                                 notional_usd=abs(r.quantity_i - r.quantity_c) * r.price_i,
                                 explanation=f"Quantity differs by {abs(r.quantity_i - r.quantity_c):,.0f}."))
            bps = abs(r.price_i - r.price_c) / r.price_c * 1e4
            if bps > cfg.trade_price_bps:
                rows.append(_row(**base, break_type="TRADE_PRICE_MISMATCH",
                                 internal_value=r.price_i, custodian_value=r.price_c,
                                 difference=round(r.price_i - r.price_c, 4),
                                 notional_usd=r.quantity_i * abs(r.price_i - r.price_c),
                                 explanation=f"Price differs by {bps:.1f} bps (tolerance {cfg.trade_price_bps} bps)."))
            if r.settle_date_i != r.settle_date_c:
                rows.append(_row(**base, break_type="TRADE_SETTLE_DATE_MISMATCH",
                                 internal_value=r.settle_date_i.date().isoformat(),
                                 custodian_value=r.settle_date_c.date().isoformat(),
                                 notional_usd=r.quantity_i * r.price_i,
                                 explanation="Settlement dates disagree; risk of a settlement fail."))
    return rows


# ----------------------------------------------------------------------- positions
def _trade_qty_diff(internal: pd.DataFrame, custodian: pd.DataFrame) -> pd.DataFrame:
    """Signed quantity difference (internal - custodian) by account/security, with the trade ids behind it."""
    i = internal.assign(q=_signed(internal["side"], internal["quantity"]))
    c = custodian.assign(q=_signed(custodian["side"], custodian["quantity"]))
    ig = i.groupby(["account", "security_id"])["q"].sum().rename("qi")
    cg = c.groupby(["account", "security_id"])["q"].sum().rename("qc")
    d = pd.concat([ig, cg], axis=1).fillna(0.0)
    d["trade_diff"] = d["qi"] - d["qc"]
    return d[["trade_diff"]]


def reconcile_positions(
    int_pos: pd.DataFrame, cust_pos: pd.DataFrame, int_trades: pd.DataFrame, cust_trades: pd.DataFrame,
    corp_actions: pd.DataFrame, prices: pd.DataFrame, trade_break_rows: list[dict], cfg: Config = DEFAULT,
) -> tuple[list[dict], int]:
    m = int_pos.merge(cust_pos, on=["account", "security_id"], how="outer", suffixes=("_i", "_c"))
    m["quantity_i"] = m["quantity_i"].fillna(0.0)
    m["quantity_c"] = m["quantity_c"].fillna(0.0)
    tdiff = _trade_qty_diff(int_trades, cust_trades)
    price = prices.set_index("security_id")["internal_price"]
    splits = corp_actions[corp_actions["action_type"] == "SPLIT"].set_index("security_id")["ratio"].to_dict()
    refs: dict[tuple, list[str]] = {}
    for b in trade_break_rows:
        refs.setdefault((b["account"], b["security_id"]), []).append(b["reference"])

    rows: list[dict] = []
    for r in m.itertuples(index=False):
        diff = r.quantity_i - r.quantity_c
        if abs(diff) <= cfg.position_qty:
            continue
        key = (r.account, r.security_id)
        trade_part = float(tdiff["trade_diff"].get(key, 0.0)) if key in tdiff.index else 0.0
        notional = abs(diff) * float(price.get(r.security_id, np.nan))
        base = dict(category="POSITION", account=r.account, security_id=r.security_id,
                    reference=f"{r.account}|{r.security_id}", internal_value=r.quantity_i,
                    custodian_value=r.quantity_c, difference=diff, notional_usd=notional)
        ratio = splits.get(r.security_id)
        if ratio and r.quantity_i and abs(r.quantity_c - r.quantity_i * ratio) <= 1:
            rows.append(_row(**base, break_type="POSITION_BREAK_CORP_ACTION_SPLIT", owner_team=OWNER_MIDDLE_OFFICE,
                             explanation=f"Custodian quantity is exactly {ratio:g}x the platform: {ratio:g}-for-1 split not booked on the platform."))
        elif abs(diff - trade_part) <= cfg.position_qty and trade_part != 0:
            rows.append(_row(**base, break_type="POSITION_BREAK_TRADE_DRIVEN", owner_team=OWNER_TRADE_ACCOUNTING,
                             explanation="Fully explained by trade break(s): " + ", ".join(sorted(set(refs.get(key, []))))))
        elif r.quantity_c == 0 and r.quantity_i != 0:
            rows.append(_row(**base, break_type="POSITION_MISSING_IN_CUSTODIAN", owner_team=OWNER_TRADE_ACCOUNTING,
                             explanation="Holding on the platform has no matching custodian position."))
        elif r.quantity_i == 0 and r.quantity_c != 0:
            rows.append(_row(**base, break_type="POSITION_MISSING_IN_INTERNAL", owner_team=OWNER_TRADE_ACCOUNTING,
                             explanation="Custodian holds a position the platform does not."))
        else:
            rows.append(_row(**base, break_type="POSITION_BREAK_UNEXPLAINED", owner_team=OWNER_TRADE_ACCOUNTING,
                             explanation=f"Not explained by trade breaks (trade-driven part {trade_part:,.0f}) or a known corporate action."))
    return rows, len(m)


# ----------------------------------------------------------------------------- margin
def reconcile_margin(internal: pd.DataFrame, custodian: pd.DataFrame, cfg: Config = DEFAULT) -> tuple[list[dict], int]:
    m = internal.merge(custodian, on="movement_id", how="outer", suffixes=("_i", "_c"), indicator="mflag")
    rows: list[dict] = []
    for r in m.itertuples(index=False):
        if r.mflag == "left_only":
            rows.append(_row(category="MARGIN", break_type="MARGIN_MISSING_IN_CUSTODIAN", owner_team=OWNER_TREASURY,
                             account=r.account_i, reference=r.movement_id, internal_value=r.amount_i,
                             custodian_value="(missing)", notional_usd=abs(r.amount_i), reference_date=r.movement_date_i,
                             explanation=f"Margin movement with {r.counterparty_i} not found at the custodian."))
        elif r.mflag == "right_only":
            rows.append(_row(category="MARGIN", break_type="MARGIN_MISSING_IN_INTERNAL", owner_team=OWNER_TREASURY,
                             account=r.account_c, reference=r.movement_id, internal_value="(missing)",
                             custodian_value=r.amount_c, notional_usd=abs(r.amount_c), reference_date=r.movement_date_c,
                             explanation=f"Margin movement with {r.counterparty_c} not booked on the platform."))
        elif abs(r.amount_i - r.amount_c) > cfg.margin_abs:
            rows.append(_row(category="MARGIN", break_type="MARGIN_AMOUNT_MISMATCH", owner_team=OWNER_TREASURY,
                             account=r.account_i, reference=r.movement_id, internal_value=r.amount_i,
                             custodian_value=r.amount_c, difference=round(r.amount_i - r.amount_c, 2),
                             notional_usd=abs(r.amount_i - r.amount_c), reference_date=r.movement_date_i,
                             explanation=f"Amounts differ by {abs(r.amount_i - r.amount_c):,.2f} (tolerance {cfg.margin_abs:.2f})."))
    return rows, len(m)


# ------------------------------------------------------------------------------- cash
def reconcile_cash(
    int_cash: pd.DataFrame, cust_cash: pd.DataFrame, int_trades: pd.DataFrame, cust_trades: pd.DataFrame,
    int_margin: pd.DataFrame, cust_margin: pd.DataFrame, int_pos: pd.DataFrame, corp_actions: pd.DataFrame,
    cfg: Config = DEFAULT,
) -> tuple[list[dict], int]:
    """Cash difference = trade cash impact + margin difference + unbooked dividends + residual."""
    m = int_cash.merge(cust_cash, on=["account", "currency"], how="outer", suffixes=("_i", "_c")).fillna(0.0)
    divs = corp_actions[corp_actions["action_type"] == "DIVIDEND"]
    rows: list[dict] = []
    for r in m.itertuples(index=False):
        diff = r.balance_i - r.balance_c
        if abs(diff) <= cfg.cash_abs:
            continue
        ti = int_trades[int_trades["account"] == r.account]
        tc = cust_trades[cust_trades["account"] == r.account]
        trade_c = float(-(_signed(ti["side"], ti["quantity"]) * ti["price"]).sum()) - float(-(_signed(tc["side"], tc["quantity"]) * tc["price"]).sum())
        margin_c = float(int_margin.loc[int_margin["account"] == r.account, "amount"].sum()) - float(cust_margin.loc[cust_margin["account"] == r.account, "amount"].sum())
        div_c = 0.0
        for d in divs.itertuples(index=False):
            q = int_pos.loc[(int_pos["account"] == r.account) & (int_pos["security_id"] == d.security_id), "quantity"].sum()
            div_c -= float(q) * float(d.amount_per_share)
        residual = diff - (trade_c + margin_c + div_c)
        parts = f"trade breaks {trade_c:,.2f}; margin breaks {margin_c:,.2f}; unbooked dividends {div_c:,.2f}; residual {residual:,.2f}"
        unexplained = abs(residual) > cfg.cash_abs
        rows.append(_row(
            category="CASH", account=r.account, reference=f"{r.account}|{r.currency}",
            break_type="CASH_BREAK_UNEXPLAINED_RESIDUAL" if unexplained else "CASH_BREAK_UPSTREAM_DRIVEN",
            owner_team=OWNER_TRADE_ACCOUNTING, internal_value=r.balance_i, custodian_value=r.balance_c,
            difference=round(diff, 2), notional_usd=abs(residual) if unexplained else abs(diff),
            explanation=("Residual remains after known causes: " if unexplained else "Fully explained by: ") + parts,
        ))
    return rows, len(m)


# ----------------------------------------------------------------------------- pricing
def reconcile_prices(prices: pd.DataFrame, int_pos: pd.DataFrame, cfg: Config = DEFAULT) -> tuple[list[dict], int]:
    held = int_pos.groupby("security_id")["quantity"].sum()
    rows: list[dict] = []
    for r in prices.itertuples(index=False):
        tol = cfg.pricing_bps.get(r.asset_class, 10.0)
        bps = (r.internal_price - r.street_price) / r.street_price * 1e4
        if abs(bps) <= tol:
            continue
        move_bps = abs(r.street_price - r.prior_price) / r.prior_price * 1e4
        stale = r.internal_price == r.prior_price and move_bps > tol
        qty = abs(float(held.get(r.security_id, 0.0)))
        rows.append(_row(
            category="PRICING", break_type="PRICING_STALE" if stale else "PRICING_VARIANCE", owner_team=OWNER_PRICING,
            security_id=r.security_id, reference=r.security_id, internal_value=r.internal_price,
            custodian_value=r.street_price, difference=round(r.internal_price - r.street_price, 4),
            notional_usd=qty * abs(r.internal_price - r.street_price),
            explanation=(f"Platform price equals prior-day price; street moved {move_bps:.0f} bps." if stale
                         else f"Platform vs street differs by {bps:+.1f} bps (tolerance {tol:g} bps, {r.asset_class})."),
        ))
    return rows, len(prices)


# ----------------------------------------------------------------------------- driver
def finalize(rows: list[dict], as_of: pd.Timestamp, cfg: Config = DEFAULT) -> pd.DataFrame:
    from .ageing import add_ageing

    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    df["reference_date"] = pd.to_datetime(df["reference_date"]).fillna(as_of)
    df = add_ageing(df, as_of, cfg)
    df["suggested_action"] = df["break_type"].map(SUGGESTED_ACTION)
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    df = df.sort_values(["severity", "notional_usd"], key=lambda s: s.map(rank) if s.name == "severity" else -s).reset_index(drop=True)
    df["break_id"] = [f"BRK-{as_of:%Y%m%d}-{i + 1:04d}" for i in range(len(df))]
    return df[COLUMNS]


def run_recon(data: dict[str, pd.DataFrame], as_of: str | pd.Timestamp, cfg: Config = DEFAULT) -> ReconResult:
    as_of = pd.Timestamp(as_of)
    it, ct = data["internal_trades"], data["custodian_trades"]
    trade_rows = reconcile_trades(it, ct, cfg)
    pos_rows, n_pos = reconcile_positions(
        data["internal_positions"], data["custodian_positions"], it, ct, data["corporate_actions"], data["prices"], trade_rows, cfg)
    mar_rows, n_mar = reconcile_margin(data["internal_margin"], data["custodian_margin"], cfg)
    cash_rows, n_cash = reconcile_cash(
        data["internal_cash"], data["custodian_cash"], it, ct, data["internal_margin"], data["custodian_margin"],
        data["internal_positions"], data["corporate_actions"], cfg)
    px_rows, n_px = reconcile_prices(data["prices"], data["internal_positions"], cfg)

    n_trades = len(set(it["trade_id"]) | set(ct["trade_id"]))
    trade_refs = len({r["reference"] for r in trade_rows})
    stats = pd.DataFrame(
        [
            ("TRADE", n_trades, trade_refs),
            ("POSITION", n_pos, len(pos_rows)),
            ("CASH", n_cash, len(cash_rows)),
            ("MARGIN", n_mar, len(mar_rows)),
            ("PRICING", n_px, len(px_rows)),
        ],
        columns=["category", "items_compared", "items_with_breaks"],
    )
    stats["items_matched"] = stats["items_compared"] - stats["items_with_breaks"]
    stats["match_rate"] = (stats["items_matched"] / stats["items_compared"]).round(4)
    breaks = finalize(trade_rows + pos_rows + cash_rows + mar_rows + px_rows, as_of, cfg)
    return ReconResult(breaks=breaks, stats=stats)
