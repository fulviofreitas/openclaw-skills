"""Deduplication, ranking, and formatting for flight-compare results.

Two results represent the same physical flight when they share:
  - airline_code + flight_number
  - departure_date (date portion only)
  - origin + destination
  - departure_time within ±15 minutes of each other

When duplicates are detected the lowest price is kept as the primary price,
all provider names are collected into ``sources``, and the full price spread
is tracked in ``price_range``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p")


def _parse_time(value: str) -> datetime | None:
    """Parse a time string into a datetime (date part is irrelevant).

    Args:
        value: Time string in any of the supported formats.

    Returns:
        A datetime on the epoch date, or None if parsing fails.
    """
    value = value.strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _times_within_minutes(t1: str, t2: str, window: int = 15) -> bool:
    """Return True if two time strings are within *window* minutes of each other.

    Args:
        t1: First time string.
        t2: Second time string.
        window: Tolerance in minutes (default 15).

    Returns:
        True when the absolute difference is at most *window* minutes, or when
        either value cannot be parsed (treated as a non-match → False).
    """
    dt1 = _parse_time(t1)
    dt2 = _parse_time(t2)
    if dt1 is None or dt2 is None:
        return False
    delta = abs((dt1 - dt2).total_seconds()) / 60
    # Handle midnight wrap-around (e.g. 23:55 vs 00:05)
    if delta > 720:
        delta = 1440 - delta
    return delta <= window


def _normalize_flight_number(raw: str | None) -> str:
    """Strip airline prefix and leading zeros from a flight number string.

    Args:
        raw: Raw flight number such as ``"UA 0101"``, ``"ua101"``, ``"101"``.

    Returns:
        Normalised numeric string, e.g. ``"101"``.  Returns ``""`` on None.
    """
    if not raw:
        return ""
    # Remove letters and spaces, then strip leading zeros
    digits = re.sub(r"[A-Za-z\s]", "", raw)
    return digits.lstrip("0") or "0"


def _is_duplicate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Return True if two flight dicts represent the same physical flight.

    Args:
        a: First flight result dict.
        b: Second flight result dict.

    Returns:
        True when all four deduplication criteria are satisfied.
    """
    # Airline code must match (case-insensitive)
    if (a.get("airline_code") or "").upper() != (b.get("airline_code") or "").upper():
        return False

    # Numeric flight number must match
    if _normalize_flight_number(
        str(a.get("flight_number", ""))
    ) != _normalize_flight_number(str(b.get("flight_number", ""))):
        return False

    # Departure date (date portion only) — extracted from departure_at (ISO 8601)
    def _date_part(v: Any) -> str:
        return str(v or "").split("T")[0][:10]

    if _date_part(a.get("departure_at")) != _date_part(b.get("departure_at")):
        return False

    # Origin / destination
    if (a.get("origin") or "").upper() != (b.get("origin") or "").upper():
        return False
    if (a.get("destination") or "").upper() != (b.get("destination") or "").upper():
        return False

    # Departure time within ±15 minutes — extract time from departure_at
    def _time_part(v: Any) -> str:
        s = str(v or "")
        if "T" in s:
            return s.split("T")[1][:8]
        return s

    if not _times_within_minutes(
        _time_part(a.get("departure_at")),
        _time_part(b.get("departure_at")),
    ):
        return False

    return True


def _merge_pair(primary: dict[str, Any], duplicate: dict[str, Any]) -> dict[str, Any]:
    """Merge *duplicate* into *primary*, keeping the lowest price.

    Args:
        primary: The result that will be kept and mutated.
        duplicate: The result to absorb.

    Returns:
        The updated *primary* dict (mutated in place and also returned).
    """
    # Merge sources lists
    primary_sources: list[str] = primary.get("sources") or [
        primary.get("source", "Unknown")
    ]
    dup_sources: list[str] = duplicate.get("sources") or [
        duplicate.get("source", "Unknown")
    ]
    merged_sources: list[str] = primary_sources.copy()
    for src in dup_sources:
        if src not in merged_sources:
            merged_sources.append(src)
    primary["sources"] = merged_sources

    # Track price range
    primary_price = float(primary.get("price") or 0)
    dup_price = float(duplicate.get("price") or 0)
    currency = primary.get("currency") or duplicate.get("currency") or "USD"

    existing_range: dict[str, Any] = primary.get("price_range") or {
        "min": primary_price,
        "max": primary_price,
        "currency": currency,
    }
    new_min = min(existing_range["min"], dup_price, primary_price)
    new_max = max(existing_range["max"], dup_price, primary_price)
    primary["price_range"] = {"min": new_min, "max": new_max, "currency": currency}

    # Keep the lowest price as the headline figure
    if dup_price and (not primary_price or dup_price < primary_price):
        primary["price"] = dup_price

    # Preserve return_leg from whichever result has it
    if not primary.get("return_leg") and duplicate.get("return_leg"):
        primary["return_leg"] = duplicate["return_leg"]
    if not primary.get("trip_type"):
        primary["trip_type"] = duplicate.get("trip_type", "one-way")

    return primary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def merge_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate and rank a combined list of flight results.

    Two results are considered the same flight when they share airline_code,
    flight_number, departure_date, origin, destination, and departure_time
    within ±15 minutes.  When duplicates are found their provider names are
    merged into ``sources``, the lowest price is kept as ``price``, and the
    full spread is stored in ``price_range``.

    The returned list is sorted by price ascending, then duration ascending.

    Args:
        results: Raw flight dicts collected from one or more provider scripts.
                 Each dict may contain a ``source`` string or a ``sources``
                 list.

    Returns:
        Deduplicated, merged, and ranked list of flight dicts.
    """
    merged: list[dict[str, Any]] = []

    for result in results:
        # Ensure every result has a normalised ``sources`` list
        if "sources" not in result:
            result = dict(result)  # don't mutate the caller's data
            result["sources"] = [result.get("source", "Unknown")]

        found = False
        for existing in merged:
            if _is_duplicate(existing, result):
                _merge_pair(existing, result)
                found = True
                break

        if not found:
            merged.append(dict(result))

    # Sort by price ascending, then duration ascending
    def _sort_key(r: dict[str, Any]) -> tuple[float, int]:
        price = float(r.get("price") or 0)
        duration = int(r.get("duration_minutes") or 0)
        return (price, duration)

    merged.sort(key=_sort_key)
    return merged


def format_table(
    results: list[dict[str, Any]],
    origin: str,
    dest: str,
    date: str,
    cabin: str,
    adults: int,
    provider_counts: dict[str, int],
    return_date: str,
) -> str:
    """Render a human-readable summary table.

    Args:
        results: Merged and ranked flight result dicts.
        origin: Origin airport code (e.g. ``"SFO"``).
        dest: Destination airport code (e.g. ``"JFK"``).
        date: Departure date string (e.g. ``"2026-04-15"``).
        cabin: Cabin class label (e.g. ``"Economy"``).
        adults: Number of adult passengers.
        provider_counts: Mapping of provider display-name → raw result count
                         before deduplication, e.g.
                         ``{"Google": 7, "ITA Matrix": 5}``.
        return_date: Return date string (e.g. ``"2026-04-22"``).

    Returns:
        Multi-line string ready for printing to stdout.
    """
    lines: list[str] = []

    # Header
    cabin_display = cabin.replace("_", " ").title()
    adult_label = f"{adults} adult" + ("s" if adults != 1 else "")
    route_str = f"{origin.upper()} \u2192 {dest.upper()} \u21a9 {return_date}"
    lines.append(
        f"Searching {route_str} on {date}"
        f" ({cabin_display}, {adult_label})..."
    )

    # Provider status line
    provider_status_parts: list[str] = []
    provider_label_map = {
        "google": "Google Flights",
        "ita": "ITA Matrix",
        "amadeus": "Amadeus",
    }
    for key, label in provider_label_map.items():
        # Match by prefix against provider_counts keys
        matched = next(
            (v for k, v in provider_counts.items() if k.lower().startswith(key)),
            None,
        )
        if matched is not None:
            provider_status_parts.append(f"\u2713 {label}")
    lines.append("Providers: " + "  ".join(provider_status_parts))
    lines.append("")

    if not results:
        lines.append("No results found.")
        return "\n".join(lines)

    # Determine best price for annotation
    best_price = min(
        (float(r.get("price") or 0) for r in results if r.get("price")),
        default=0.0,
    )

    # Column headers
    col_fmt = (
        " {rank:>2}  {airline:<14} {flight:<9} {dep:<7} {arr:<7}"
        " {dur:<9} {stops:<6} {price:<8} {source}"
    )
    header = col_fmt.format(
        rank="#",
        airline="AIRLINE",
        flight="FLIGHT",
        dep="DEPART",
        arr="ARRIVE",
        dur="DURATION",
        stops="STOPS",
        price="PRICE",
        source="SOURCE",
    )
    lines.append(header)
    lines.append("-" * len(header))

    for rank, r in enumerate(results, start=1):
        airline = r.get("airline") or r.get("airline_code") or "—"
        code = r.get("airline_code") or ""
        num = r.get("flight_number") or ""
        flight_str = f"{code} {num}".strip() if code or num else "—"

        dep_at = str(r.get("departure_at") or "")
        arr_at = str(r.get("arrival_at") or "")
        dep = dep_at.split("T")[1][:5] if "T" in dep_at else "—"
        arr = arr_at.split("T")[1][:5] if "T" in arr_at else "—"

        dur_mins = int(r.get("duration_minutes") or 0)
        if dur_mins:
            hours, mins = divmod(dur_mins, 60)
            dur_str = f"{hours}h{mins:02d}m"
        else:
            dur_str = "—"

        stops = str(r.get("stops") if r.get("stops") is not None else "—")

        price_val = r.get("price")
        currency = r.get("currency") or "USD"
        if price_val is not None:
            symbol = "$" if currency == "USD" else f"{currency} "
            price_str = f"{symbol}{float(price_val):.0f}"
        else:
            price_str = "—"

        sources: list[str] = r.get("sources") or [r.get("source", "Unknown")]
        source_str = ", ".join(sources)

        annotation = ""
        if price_val and float(price_val) == best_price:
            annotation = " \u2605 BEST PRICE"

        # Return leg annotation for round-trip results
        ret = r.get("return_leg")
        if ret:
            ret_fn = ret.get("flight_number") or "\u2014"
            ret_dep_at = str(ret.get("departure_at") or "")
            ret_arr_at = str(ret.get("arrival_at") or "")
            ret_dep = ret_dep_at.split("T")[1][:5] if "T" in ret_dep_at else "\u2014"
            ret_arr = ret_arr_at.split("T")[1][:5] if "T" in ret_arr_at else "\u2014"
            annotation += f" | Return: {ret_fn} {ret_dep}\u2192{ret_arr}"

        line = col_fmt.format(
            rank=rank,
            airline=airline[:14],
            flight=flight_str[:9],
            dep=dep,
            arr=arr,
            dur=dur_str,
            stops=stops,
            price=price_str,
            source=source_str + annotation,
        )
        lines.append(line)

    # Summary footer
    total_raw = sum(provider_counts.values())
    total_merged = len(results)
    duplicates_merged = total_raw - total_merged
    provider_detail = "  ".join(
        f"{name}: {count}" for name, count in provider_counts.items()
    )
    n_providers = len(provider_counts)
    provider_word = "provider" + ("s" if n_providers != 1 else "")
    dup_word = "duplicate" + ("s" if duplicates_merged != 1 else "")
    lines.append("")
    lines.append(
        f"{total_merged} results from {n_providers} {provider_word}"
        f" | {provider_detail}"
        f" | {duplicates_merged} {dup_word} merged"
    )

    return "\n".join(lines)


def format_json(results: list[dict[str, Any]]) -> str:
    """Serialise a list of flight result dicts to a JSON array string.

    Args:
        results: Merged and ranked flight result dicts.

    Returns:
        Pretty-printed JSON array string.
    """
    return json.dumps(results, indent=2, ensure_ascii=False)
