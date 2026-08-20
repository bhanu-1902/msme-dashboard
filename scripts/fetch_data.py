#!/usr/bin/env python3
"""
fetch_data.py — MSME Intelligence Dashboard data pipeline

Runs on a schedule (GitHub Actions). Fetches from official Indian government
sources, computes YoY / MoM deltas, and writes data/latest.json which the
static site reads.

Design principles:
  - NEVER fabricate a number. If a source fails, keep the last known good
    value and mark it stale. A wrong number is worse than an old number.
  - Every series carries its own source attribution and as-of date.
  - History is appended, never overwritten, so YoY/MoM are computed from
    real prior observations rather than assumptions.

Sources:
  - data.gov.in REST API  (Udyam MSME registrations)      [real API, needs key]
  - PIB press releases    (monthly merchandise trade)     [HTML parse]
  - PIB press releases    (monthly IIP)                   [HTML parse]

Env vars:
  DATAGOVIN_API_KEY   - free key from https://data.gov.in (optional but
                        recommended; without it the MSME series is skipped)
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# --------------------------------------------------------------------------
# Paths & constants
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LATEST_PATH = DATA_DIR / "latest.json"
HISTORY_PATH = DATA_DIR / "history.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 30

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)}


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    log(f"wrote {path.relative_to(ROOT)}")


def get(url, **kwargs):
    """GET with retries and a browser-like UA (many .gov.in hosts require it)."""
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001 - we want any failure to retry
            last_err = e
            log(f"  attempt {attempt + 1} failed: {type(e).__name__}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET failed after 3 attempts: {url} ({last_err})")


def pct_change(current, previous):
    """Percent change, rounded to 2dp. None if not computable."""
    if current is None or previous is None:
        return None
    try:
        if previous == 0:
            return None
        return round(((current - previous) / abs(previous)) * 100, 2)
    except (TypeError, ZeroDivisionError):
        return None


def point_change(current, previous):
    """
    Percentage-POINT change, for metrics that are themselves rates.

    IIP is already published as a YoY growth rate. Computing a percent change
    on it (7.8% vs 3.6% -> '+116%') is arithmetically valid but meaningless.
    The correct reading is '+4.2 percentage points'.
    """
    if current is None or previous is None:
        return None
    try:
        return round(current - previous, 2)
    except TypeError:
        return None


# Fields that are themselves rates -> compare in percentage points, not percent.
RATE_FIELDS = {"overall_growth_pct", "manufacturing_growth_pct"}


def period_key(year, month):
    """Canonical sortable period key: '2026-07'."""
    return f"{year:04d}-{month:02d}"


def shift_period(key, months_back):
    """'2026-07' shifted back N months -> '2025-07' for 12."""
    y, m = int(key[:4]), int(key[5:7])
    total = y * 12 + (m - 1) - months_back
    return period_key(total // 12, total % 12 + 1)


# --------------------------------------------------------------------------
# Source 1: data.gov.in — Udyam MSME registrations (real REST API)
# --------------------------------------------------------------------------

# District-wise Udyam registration resource on data.gov.in.
# Verify/replace this ID at https://data.gov.in if the catalog changes.
UDYAM_RESOURCE_ID = "d9c2c4a2-2f4b-4b0f-9c7f-8a5e3f1d6b2c"


def fetch_msme():
    """
    Pull Udyam registration totals from the data.gov.in REST API.

    Returns dict or None. Requires DATAGOVIN_API_KEY.
    """
    api_key = os.environ.get("DATAGOVIN_API_KEY", "").strip()
    if not api_key:
        log("MSME: no DATAGOVIN_API_KEY set, skipping (will reuse last value)")
        return None

    url = f"https://api.data.gov.in/resource/{UDYAM_RESOURCE_ID}"
    params = {"api-key": api_key, "format": "json", "limit": 1000}

    log("MSME: querying data.gov.in API")
    try:
        r = get(url, params=params)
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        log(f"MSME: FAILED — {e}")
        return None

    records = payload.get("records", [])
    if not records:
        log("MSME: API returned no records")
        return None

    # Sum whatever numeric registration column the resource exposes.
    # Column names on data.gov.in vary; try the common ones in order.
    candidate_cols = [
        "total_msme", "total", "count", "no_of_msme",
        "registered_enterprises", "total_enterprises",
    ]
    col = next(
        (c for c in candidate_cols if records and c in records[0]),
        None,
    )
    if col is None:
        log(f"MSME: no known count column in {list(records[0].keys())[:8]}")
        return None

    total = 0
    by_state = {}
    for rec in records:
        try:
            val = int(float(str(rec.get(col, 0)).replace(",", "")))
        except (ValueError, TypeError):
            continue
        total += val
        state = rec.get("state_name") or rec.get("state") or "Unknown"
        by_state[state] = by_state.get(state, 0) + val

    top_states = sorted(by_state.items(), key=lambda kv: -kv[1])[:5]

    log(f"MSME: {total:,} total across {len(by_state)} states")
    return {
        "total_registered": total,
        "total_crore": round(total / 10_000_000, 2),
        "top_states": [{"state": s, "count": c} for s, c in top_states],
        "source": "data.gov.in — Ministry of MSME, Udyam Registration",
        "source_url": "https://data.gov.in/catalog/udyam-registration-msme-registration",
    }


# --------------------------------------------------------------------------
# Source 2: PIB — monthly merchandise trade
# --------------------------------------------------------------------------

PIB_SEARCH = "https://www.pib.gov.in/allRel.aspx"
COMMERCE_RELEASES = "https://www.commerce.gov.in/trade-statistics/latest-trade-figures/"


def _parse_billions(text, *patterns):
    """Find the first 'US$ NN.NN Billion' style figure matching any pattern."""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def fetch_trade():
    """
    Scrape the latest monthly merchandise trade figures.

    The Commerce Ministry publishes a monthly release around the 15th.
    We parse exports / imports in US$ billion.
    """
    log("TRADE: fetching commerce.gov.in latest figures")
    try:
        r = get(COMMERCE_RELEASES)
        html = r.text
    except Exception as e:  # noqa: BLE001
        log(f"TRADE: FAILED — {e}")
        return None

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    exports = _parse_billions(
        text,
        r"[Mm]erchandise exports?[^.]{0,80}?US\$?\s*([\d,]+\.?\d*)\s*[Bb]illion",
        r"exports?\s+(?:were|was|stood at|at)\s+US\$?\s*([\d,]+\.?\d*)\s*[Bb]illion",
    )
    imports = _parse_billions(
        text,
        r"[Mm]erchandise imports?[^.]{0,80}?US\$?\s*([\d,]+\.?\d*)\s*[Bb]illion",
        r"imports?\s+(?:were|was|stood at|at)\s+US\$?\s*([\d,]+\.?\d*)\s*[Bb]illion",
    )

    # Identify the reporting month from the page text.
    month_num = year = None
    m = re.search(
        r"\b(" + "|".join(MONTHS) + r")\s+(20\d{2})\b", text
    )
    if m:
        month_num = MONTHS.index(m.group(1)) + 1
        year = int(m.group(2))

    if exports is None or imports is None:
        log("TRADE: could not parse figures from page")
        return None

    deficit = round(imports - exports, 2)
    log(f"TRADE: exports ${exports}B, imports ${imports}B, deficit ${deficit}B")

    return {
        "exports_usd_bn": exports,
        "imports_usd_bn": imports,
        "deficit_usd_bn": deficit,
        "period": period_key(year, month_num) if year and month_num else None,
        "source": "Ministry of Commerce & Industry — monthly trade release",
        "source_url": COMMERCE_RELEASES,
    }


# --------------------------------------------------------------------------
# Source 3: MOSPI / PIB — Index of Industrial Production
# --------------------------------------------------------------------------

MOSPI_IIP = "https://www.mospi.gov.in/iip"


def fetch_iip():
    """
    Scrape the latest IIP headline and manufacturing growth rates.
    NSO publishes monthly Quick Estimates around the 28th.
    """
    log("IIP: fetching mospi.gov.in")
    try:
        r = get(MOSPI_IIP)
        html = r.text
    except Exception as e:  # noqa: BLE001
        log(f"IIP: FAILED — {e}")
        return None

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    overall = _parse_billions(
        text,
        r"IIP[^.]{0,60}?growth\s+of\s+([\d.]+)\s*(?:per\s*cent|%)",
        r"growth\s+rate\s+of\s+([\d.]+)\s*(?:per\s*cent|%)[^.]{0,40}IIP",
    )
    manufacturing = _parse_billions(
        text,
        r"[Mm]anufacturing[^.]{0,60}?([\d.]+)\s*(?:per\s*cent|%)",
    )

    if overall is None:
        log("IIP: could not parse growth rate")
        return None

    log(f"IIP: overall {overall}%, manufacturing {manufacturing}%")
    return {
        "overall_growth_pct": overall,
        "manufacturing_growth_pct": manufacturing,
        "source": "MOSPI / NSO — Index of Industrial Production, base 2022-23",
        "source_url": MOSPI_IIP,
    }


# --------------------------------------------------------------------------
# History + delta computation
# --------------------------------------------------------------------------

def append_history(history, series_name, period, values):
    """Append an observation to a series, replacing same-period entries."""
    if not period:
        return history
    series = history.setdefault(series_name, {})
    series[period] = values
    return history


def compute_deltas(history, series_name, period, field):
    """
    Compute MoM and YoY change for a field, using real prior observations.

    Rate fields (IIP growth rates) are compared in percentage points; level
    fields (trade values, registration counts) in percent.

    Returns (mom, yoy, prev_month_value, prev_year_value, delta_kind).
    """
    series = history.get(series_name, {})
    current = (series.get(period) or {}).get(field)

    prev_m = (series.get(shift_period(period, 1)) or {}).get(field) if period else None
    prev_y = (series.get(shift_period(period, 12)) or {}).get(field) if period else None

    if field in RATE_FIELDS:
        return (
            point_change(current, prev_m),
            point_change(current, prev_y),
            prev_m,
            prev_y,
            "points",
        )

    return (
        pct_change(current, prev_m),
        pct_change(current, prev_y),
        prev_m,
        prev_y,
        "percent",
    )


def build_series_block(history, series_name, period, fields):
    """Package current values plus MoM/YoY deltas for the frontend."""
    block = {}
    for field in fields:
        mom, yoy, prev_m, prev_y, kind = compute_deltas(
            history, series_name, period, field
        )
        current = (history.get(series_name, {}).get(period) or {}).get(field)
        block[field] = {
            "value": current,
            "mom": mom,
            "yoy": yoy,
            "delta_kind": kind,
            "prev_month": prev_m,
            "prev_year": prev_y,
        }
    return block


def series_as_timeline(history, series_name, field, limit=13):
    """Last N observations of a field, oldest first — for charting."""
    series = history.get(series_name, {})
    keys = sorted(series.keys())[-limit:]
    return [
        {"period": k, "value": series[k].get(field)}
        for k in keys
        if series[k].get(field) is not None
    ]


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    log("=== MSME Dashboard data refresh ===")

    history = load_json(HISTORY_PATH, {})
    previous = load_json(LATEST_PATH, {})

    now = datetime.now(timezone.utc)
    result = {
        "generated_at": now.isoformat(),
        "generated_at_display": now.strftime("%d %B %Y, %H:%M UTC"),
        "sources_ok": [],
        "sources_failed": [],
    }

    # ---- Trade -----------------------------------------------------------
    trade = fetch_trade()
    if trade:
        period = trade.get("period") or period_key(now.year, now.month)
        append_history(history, "trade", period, {
            "exports_usd_bn": trade["exports_usd_bn"],
            "imports_usd_bn": trade["imports_usd_bn"],
            "deficit_usd_bn": trade["deficit_usd_bn"],
        })
        result["trade"] = {
            "period": period,
            "metrics": build_series_block(
                history, "trade", period,
                ["exports_usd_bn", "imports_usd_bn", "deficit_usd_bn"],
            ),
            "timeline": {
                "exports": series_as_timeline(history, "trade", "exports_usd_bn"),
                "imports": series_as_timeline(history, "trade", "imports_usd_bn"),
                "deficit": series_as_timeline(history, "trade", "deficit_usd_bn"),
            },
            "source": trade["source"],
            "source_url": trade["source_url"],
            "stale": False,
        }
        result["sources_ok"].append("trade")
    else:
        prior = previous.get("trade")
        if prior:
            prior["stale"] = True
            result["trade"] = prior
        result["sources_failed"].append("trade")

    # ---- IIP -------------------------------------------------------------
    iip = fetch_iip()
    if iip:
        period = period_key(now.year, now.month)
        append_history(history, "iip", period, {
            "overall_growth_pct": iip["overall_growth_pct"],
            "manufacturing_growth_pct": iip["manufacturing_growth_pct"],
        })
        result["iip"] = {
            "period": period,
            "metrics": build_series_block(
                history, "iip", period,
                ["overall_growth_pct", "manufacturing_growth_pct"],
            ),
            "timeline": {
                "overall": series_as_timeline(history, "iip", "overall_growth_pct"),
                "manufacturing": series_as_timeline(
                    history, "iip", "manufacturing_growth_pct"
                ),
            },
            "source": iip["source"],
            "source_url": iip["source_url"],
            "stale": False,
        }
        result["sources_ok"].append("iip")
    else:
        prior = previous.get("iip")
        if prior:
            prior["stale"] = True
            result["iip"] = prior
        result["sources_failed"].append("iip")

    # ---- MSME ------------------------------------------------------------
    msme = fetch_msme()
    if msme:
        period = period_key(now.year, now.month)
        append_history(history, "msme", period, {
            "total_registered": msme["total_registered"],
        })
        result["msme"] = {
            "period": period,
            "total_registered": msme["total_registered"],
            "total_crore": msme["total_crore"],
            "top_states": msme["top_states"],
            "metrics": build_series_block(
                history, "msme", period, ["total_registered"]
            ),
            "timeline": series_as_timeline(history, "msme", "total_registered"),
            "source": msme["source"],
            "source_url": msme["source_url"],
            "stale": False,
        }
        result["sources_ok"].append("msme")
    else:
        prior = previous.get("msme")
        if prior:
            prior["stale"] = True
            result["msme"] = prior
        result["sources_failed"].append("msme")

    # ---- Persist ---------------------------------------------------------
    save_json(HISTORY_PATH, history)
    save_json(LATEST_PATH, result)

    log(f"OK: {result['sources_ok']}  FAILED: {result['sources_failed']}")

    # Exit non-zero only if EVERY source failed and we have no fallback.
    if not result["sources_ok"] and not previous:
        log("FATAL: no sources succeeded and no prior data exists")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
