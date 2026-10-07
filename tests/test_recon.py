import pandas as pd
import pytest

from postrade_recon.ageing import bucket_for, business_days_between, severity_for
from postrade_recon.config import Config
from postrade_recon.datagen import CUSTODIAN_FEES, PLANTED, SPLIT_SECURITIES, generate
from postrade_recon.recon import reconcile_margin, reconcile_trades, run_recon


def types(result, t):
    return result.breaks[result.breaks["break_type"] == t]


# ---- planted breaks are found exactly ---------------------------------------------------
@pytest.mark.parametrize("break_type,planted", [
    ("TRADE_MISSING_IN_CUSTODIAN", "trade_missing_in_custodian"),
    ("TRADE_MISSING_IN_INTERNAL", "trade_missing_in_internal"),
    ("TRADE_QTY_MISMATCH", "trade_qty_mismatch"),
    ("TRADE_PRICE_MISMATCH", "trade_price_mismatch"),
    ("TRADE_SETTLE_DATE_MISMATCH", "trade_settle_date_mismatch"),
    ("MARGIN_MISSING_IN_CUSTODIAN", "margin_missing_in_custodian"),
    ("MARGIN_MISSING_IN_INTERNAL", "margin_missing_in_internal"),
    ("MARGIN_AMOUNT_MISMATCH", "margin_amount_mismatch"),
    ("PRICING_VARIANCE", "pricing_variance"),
    ("PRICING_STALE", "pricing_stale"),
])
def test_planted_break_counts(result, break_type, planted):
    assert len(types(result, break_type)) == PLANTED[planted]


def test_within_tolerance_items_are_not_breaks(data):
    # Trades whose custodian price differs by only 0.2 bps must not appear as price breaks.
    rows = reconcile_trades(data["internal_trades"], data["custodian_trades"])
    price_refs = {r["reference"] for r in rows if r["break_type"] == "TRADE_PRICE_MISMATCH"}
    m = data["internal_trades"].merge(data["custodian_trades"], on="trade_id", suffixes=("_i", "_c"))
    bps = (m["price_i"] - m["price_c"]).abs() / m["price_c"] * 1e4
    tiny = set(m.loc[(bps > 0) & (bps <= 1.0), "trade_id"])
    assert len(tiny) == PLANTED["trade_price_within_tolerance"]
    assert tiny.isdisjoint(price_refs)


def test_margin_within_tolerance_not_a_break(data):
    rows, _ = reconcile_margin(data["internal_margin"], data["custodian_margin"])
    assert len(rows) == 3  # 3 real breaks; the 0.50 difference is inside the 1.00 tolerance


# ---- explanation logic ---------------------------------------------------------------------
def test_split_position_breaks_are_classified_as_corporate_action(result):
    split = result.breaks[result.breaks["security_id"].isin(SPLIT_SECURITIES) & (result.breaks["category"] == "POSITION")]
    assert len(split) >= 2
    assert set(split["break_type"]) == {"POSITION_BREAK_CORP_ACTION_SPLIT"}
    assert set(split["owner_team"]) == {"Middle Office"}


def test_trade_driven_position_breaks_reference_trade_ids(result):
    driven = types(result, "POSITION_BREAK_TRADE_DRIVEN")
    assert len(driven) > 0
    assert driven["explanation"].str.contains("TRD").all()


def test_cash_residual_isolates_the_unbooked_bank_fee(result):
    resid = types(result, "CASH_BREAK_UNEXPLAINED_RESIDUAL")
    assert len(resid) == 1
    row = resid.iloc[0]
    assert row["account"] in CUSTODIAN_FEES
    assert row["notional_usd"] == pytest.approx(abs(CUSTODIAN_FEES[row["account"]]), abs=0.01)


def test_other_cash_breaks_fully_explained_by_upstream_causes(result):
    up = types(result, "CASH_BREAK_UPSTREAM_DRIVEN")
    assert len(up) == 2
    assert up["explanation"].str.contains("residual 0.00").all()


def test_clean_data_produces_no_breaks(data):
    clean = dict(data)
    clean["custodian_trades"] = data["internal_trades"].copy()
    clean["custodian_positions"] = data["internal_positions"].copy()
    clean["custodian_cash"] = data["internal_cash"].copy()
    clean["custodian_margin"] = data["internal_margin"].copy()
    clean["prices"] = data["prices"].assign(internal_price=data["prices"]["street_price"])
    assert len(run_recon(clean, "2026-10-07").breaks) == 0


# ---- pricing logic ---------------------------------------------------------------------------
def test_stale_price_equals_prior_day_price(result, data):
    stale_ids = set(types(result, "PRICING_STALE")["security_id"])
    p = data["prices"].set_index("security_id")
    assert all(p.loc[s, "internal_price"] == p.loc[s, "prior_price"] for s in stale_ids)


def test_pricing_tolerance_depends_on_asset_class(data):
    tight = Config(pricing_bps={"EQUITY": 0.0, "ETF": 0.0, "FIXED_INCOME": 0.0})
    loose = Config(pricing_bps={"EQUITY": 1e6, "ETF": 1e6, "FIXED_INCOME": 1e6})
    assert len(run_recon(data, "2026-10-07", tight).breaks.query("category == 'PRICING'")) > 5
    assert len(run_recon(data, "2026-10-07", loose).breaks.query("category == 'PRICING'")) == 0


# ---- ageing and severity -----------------------------------------------------------------------
@pytest.mark.parametrize("age,label", [(0, "0-1d"), (1, "0-1d"), (2, "2-5d"), (5, "2-5d"), (6, "6-10d"), (10, "6-10d"), (11, "11+d")])
def test_age_buckets(age, label):
    assert bucket_for(age) == label


@pytest.mark.parametrize("notional,sev", [(49_999.99, "LOW"), (50_000, "MEDIUM"), (499_999, "MEDIUM"), (500_000, "HIGH")])
def test_severity_thresholds(notional, sev):
    assert severity_for(notional) == sev


def test_business_days_skip_weekends():
    fri, mon = pd.Timestamp("2026-10-02"), pd.Timestamp("2026-10-05")
    assert business_days_between(fri, mon) == 1
    assert business_days_between(mon, fri) == 0  # never negative


# ---- output integrity -------------------------------------------------------------------------------
def test_unified_schema_and_unique_ids(result):
    b = result.breaks
    assert b["break_id"].is_unique
    assert b["suggested_action"].notna().all()
    assert set(b["severity"]) <= {"HIGH", "MEDIUM", "LOW"}
    assert (b["notional_usd"] >= 0).all()


def test_generator_is_deterministic():
    a, b = generate("2026-10-07", 42), generate("2026-10-07", 42)
    pd.testing.assert_frame_equal(a["custodian_trades"], b["custodian_trades"])


def test_stats_match_rate(result):
    s = result.stats.set_index("category")
    assert s.loc["TRADE", "items_with_breaks"] == 31
    assert (s["match_rate"].between(0, 1)).all()
