"""Tolerances and thresholds. Every number that decides 'is this a break?' lives here."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    # Trade price: relative tolerance in basis points (1 bp = 0.01%).
    trade_price_bps: float = 1.0
    # Position quantity: absolute tolerance in shares / face units.
    position_qty: float = 0.0
    # Cash and margin: absolute tolerance in currency units.
    cash_abs: float = 1.00
    margin_abs: float = 1.00
    # Pricing: platform price vs street price, tolerance in bps by asset class.
    pricing_bps: dict = field(
        default_factory=lambda: {"EQUITY": 10.0, "ETF": 5.0, "FIXED_INCOME": 25.0}
    )
    # Severity by absolute notional (USD).
    high_notional: float = 500_000.0
    medium_notional: float = 50_000.0
    # Ageing buckets in business days: (upper bound inclusive, label).
    age_buckets: tuple = ((1, "0-1d"), (5, "2-5d"), (10, "6-10d"))
    age_overflow_label: str = "11+d"


DEFAULT = Config()

OWNER_TRADE_ACCOUNTING = "Trade Accounting & Operation"
OWNER_MIDDLE_OFFICE = "Middle Office"
OWNER_TREASURY = "Treasury"
OWNER_PRICING = "Pricing"
TEAMS = [OWNER_TRADE_ACCOUNTING, OWNER_MIDDLE_OFFICE, OWNER_TREASURY, OWNER_PRICING]
