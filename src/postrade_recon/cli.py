"""Command line: generate sample data, run the reconciliation, or do both."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import DEFAULT
from .datagen import generate, read_csv, write_csv
from .recon import run_recon
from .report import build_report

log = logging.getLogger("postrade_recon")


def _run(data_dir: Path, as_of: str, out_dir: Path) -> None:
    data = read_csv(data_dir)
    result = run_recon(data, as_of, DEFAULT)
    out_dir.mkdir(parents=True, exist_ok=True)
    result.breaks.to_csv(out_dir / "breaks.csv", index=False)
    xlsx = build_report(result, as_of, out_dir / "recon_report.xlsx", DEFAULT)
    log.info("Reconciliation as of %s", as_of)
    for r in result.stats.itertuples(index=False):
        log.info("  %-9s compared %4d | with breaks %3d | match rate %5.1f%%", r.category, r.items_compared, r.items_with_breaks, r.match_rate * 100)
    b = result.breaks
    log.info("Total break lines: %d (HIGH %d, MEDIUM %d, LOW %d); root-cause notional at risk %s USD",
             len(b), (b.severity == "HIGH").sum(), (b.severity == "MEDIUM").sum(), (b.severity == "LOW").sum(), f"{b.loc[~b.break_type.str.contains('DRIVEN'), 'notional_usd'].sum():,.0f}")
    log.info("Wrote %s and %s", xlsx, out_dir / "breaks.csv")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="postrade-recon", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write synthetic internal/custodian CSVs with planted breaks")
    g.add_argument("--as-of", default="2026-10-07")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--out", type=Path, default=Path("data/sample"))
    r = sub.add_parser("run", help="reconcile a folder of CSVs and write the Excel report")
    r.add_argument("--data", type=Path, default=Path("data/sample"))
    r.add_argument("--as-of", default="2026-10-07")
    r.add_argument("--out", type=Path, default=Path("output"))
    d = sub.add_parser("demo", help="generate sample data, then run the reconciliation")
    d.add_argument("--as-of", default="2026-10-07")
    d.add_argument("--seed", type=int, default=42)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if a.cmd == "generate":
        write_csv(generate(a.as_of, a.seed), a.out)
        log.info("Wrote sample data to %s", a.out)
    elif a.cmd == "run":
        _run(a.data, a.as_of, a.out)
    else:
        write_csv(generate(a.as_of, a.seed), Path("data/sample"))
        _run(Path("data/sample"), a.as_of, Path("output"))


if __name__ == "__main__":
    main()
