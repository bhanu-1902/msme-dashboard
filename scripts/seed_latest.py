#!/usr/bin/env python3
"""
seed_latest.py — generate the initial data/latest.json from seeded history.

Run once at setup. After this, fetch_data.py takes over and overwrites
latest.json on each scheduled run. This exists so the site renders real,
correctly-computed deltas from the very first commit, before any Action
has run.

Uses the exact same delta logic as the live pipeline — no hand-typed
percentages anywhere.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_data import (  # noqa: E402
    HISTORY_PATH,
    LATEST_PATH,
    build_series_block,
    load_json,
    save_json,
    series_as_timeline,
)

TRADE_PERIOD = "2026-07"
IIP_PERIOD = "2026-06"
MSME_PERIOD = "2026-02"


def main():
    history = load_json(HISTORY_PATH, {})
    if not history:
        print("ERROR: data/history.json is empty or missing")
        return 1

    now = datetime.now(timezone.utc)

    result = {
        "generated_at": now.isoformat(),
        "generated_at_display": now.strftime("%d %B %Y, %H:%M UTC"),
        "seeded": True,
        "sources_ok": ["trade", "iip", "msme"],
        "sources_failed": [],
        "trade": {
            "period": TRADE_PERIOD,
            "metrics": build_series_block(
                history, "trade", TRADE_PERIOD,
                ["exports_usd_bn", "imports_usd_bn", "deficit_usd_bn"],
            ),
            "timeline": {
                "exports": series_as_timeline(history, "trade", "exports_usd_bn"),
                "imports": series_as_timeline(history, "trade", "imports_usd_bn"),
                "deficit": series_as_timeline(history, "trade", "deficit_usd_bn"),
            },
            "source": "Ministry of Commerce & Industry — monthly trade release",
            "source_url": "https://www.commerce.gov.in/trade-statistics/latest-trade-figures/",
            "stale": False,
        },
        "iip": {
            "period": IIP_PERIOD,
            "metrics": build_series_block(
                history, "iip", IIP_PERIOD,
                ["overall_growth_pct", "manufacturing_growth_pct"],
            ),
            "timeline": {
                "overall": series_as_timeline(history, "iip", "overall_growth_pct"),
                "manufacturing": series_as_timeline(
                    history, "iip", "manufacturing_growth_pct"
                ),
            },
            "source": "MOSPI / NSO — Index of Industrial Production, base 2022-23",
            "source_url": "https://www.mospi.gov.in/iip",
            "stale": False,
        },
        "msme": {
            "period": MSME_PERIOD,
            "total_registered": history["msme"][MSME_PERIOD]["total_registered"],
            "total_crore": round(
                history["msme"][MSME_PERIOD]["total_registered"] / 10_000_000, 2
            ),
            "top_states": [
                {"state": "Maharashtra", "count": 7336363},
                {"state": "Uttar Pradesh", "count": 4980000},
                {"state": "Tamil Nadu", "count": 4160000},
                {"state": "Rajasthan", "count": 3160000},
                {"state": "Gujarat", "count": 3030000},
            ],
            "metrics": build_series_block(
                history, "msme", MSME_PERIOD, ["total_registered"]
            ),
            "timeline": series_as_timeline(history, "msme", "total_registered"),
            "source": "Ministry of MSME — Udyam Registration Portal + Udyam Assist",
            "source_url": "https://udyamregistration.gov.in",
            "stale": False,
        },
    }

    save_json(LATEST_PATH, result)

    t = result["trade"]["metrics"]
    print("\nComputed deltas (verify these look right):")
    print(f"  Exports  ${t['exports_usd_bn']['value']}B "
          f"MoM {t['exports_usd_bn']['mom']}%  "
          f"YoY {t['exports_usd_bn']['yoy']}%")
    print(f"  Imports  ${t['imports_usd_bn']['value']}B "
          f"MoM {t['imports_usd_bn']['mom']}%  "
          f"YoY {t['imports_usd_bn']['yoy']}%")
    print(f"  Deficit  ${t['deficit_usd_bn']['value']}B "
          f"MoM {t['deficit_usd_bn']['mom']}%  "
          f"YoY {t['deficit_usd_bn']['yoy']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
