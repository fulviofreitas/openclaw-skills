#!/usr/bin/env python3
"""Search Google Flights and emit newline-delimited JSON results.

Usage:
    python search.py <FROM> <TO> -d DATE --return-date DATE
                     [--cabin CABIN] [--stops STOPS]
                     [--adults N] [--currency CODE] [--max N]

Each result is printed as a single JSON object on its own line (NDJSON).
Errors are written to stderr and the process exits with code 1.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any, Literal, Optional

# ---------------------------------------------------------------------------
# Dependency guard
# ---------------------------------------------------------------------------

try:
    from fast_flights import (
        FlightData,
        Passengers,
        create_filter,
        get_flights_from_filter,
    )
    from fast_flights.schema import Flight
except ImportError:
    print(
        "ERROR: 'fast-flights' package is not installed.\n"
        "Run the setup script first:\n\n"
        "    bash setup.sh\n",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

CabinArg = Literal["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]
StopsArg = Literal["any", "nonstop", "one", "two"]

# Mapping from CLI cabin name -> fast_flights seat literal
_CABIN_TO_SEAT: dict[CabinArg, str] = {
    "ECONOMY": "economy",
    "PREMIUM_ECONOMY": "premium-economy",
    "BUSINESS": "business",
    "FIRST": "first",
}

# Mapping from CLI stops arg -> max_stops int (None means no restriction)
_STOPS_TO_MAX: dict[StopsArg, Optional[int]] = {
    "any": None,
    "nonstop": 0,
    "one": 1,
    "two": 2,
}


# ---------------------------------------------------------------------------
# FlightResult schema
# ---------------------------------------------------------------------------


def _build_flight_result(
    flight: Flight,
    origin: str,
    destination: str,
    departure_date: date,
    cabin: CabinArg,
    currency: str,
    return_date: date,
) -> dict[str, Any]:
    """Convert a raw ``Flight`` object into the canonical FlightResult dict.

    Args:
        flight: Raw flight object returned by fast_flights.
        origin: IATA departure airport code (upper-cased).
        destination: IATA arrival airport code (upper-cased).
        departure_date: Calendar date of the outbound leg.
        cabin: Cabin class constant string.
        currency: ISO 4217 currency code.
        return_date: Calendar date of the return leg.

    Returns:
        A FlightResult-shaped dictionary ready for JSON serialisation.
    """
    departure_at = _parse_flight_datetime(flight.departure, departure_date)

    # Flights arriving the next day carry a non-empty arrival_time_ahead field
    # (e.g. "+1"). Advance the arrival date accordingly.
    extra_days = _parse_days_ahead(flight.arrival_time_ahead)
    arrival_date = departure_date + timedelta(days=extra_days)
    arrival_at = _parse_flight_datetime(flight.arrival, arrival_date)

    duration_minutes = _parse_duration_minutes(flight.duration)
    price = _parse_price(flight.price)

    raw: dict[str, Any] = {
        "name": flight.name,
        "departure": flight.departure,
        "arrival": flight.arrival,
        "arrival_time_ahead": flight.arrival_time_ahead,
        "duration": flight.duration,
        "stops": flight.stops,
        "delay": flight.delay,
        "price": flight.price,
        "is_best": flight.is_best,
    }

    return {
        "provider": "google-flights",
        # Google Flights' public interface does not expose IATA carrier codes
        # or structured flight numbers; the carrier name is the only identifier.
        "airline": flight.name,
        "airline_code": "",
        "flight_number": "",
        "origin": origin.upper(),
        "destination": destination.upper(),
        "departure_at": departure_at,
        "arrival_at": arrival_at,
        "duration_minutes": duration_minutes,
        "stops": flight.stops if isinstance(flight.stops, int) else 0,
        # Individual layover codes are not surfaced by the library.
        "layover_airports": [],
        "cabin": cabin,
        "price": price,
        "currency": currency.upper(),
        # Deep-link booking URLs require a paid API key; not available here.
        "booking_url": None,
        "trip_type": "round-trip",
        "return_date": return_date.isoformat(),
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_flight_datetime(time_str: str, on_date: date) -> str:
    """Combine a time string with a calendar date into ISO 8601.

    The library may return bare times (``"8:00 AM"``) or richer strings that
    include the weekday and month/day (``"4:29 PM on Wed, Mar 11"``).  Both
    formats are handled; the supplied ``on_date`` is used as the canonical
    date so that results remain consistent with the requested search date.

    Args:
        time_str: Time string, either ``h:MM AM/PM`` or
            ``h:MM AM/PM on Weekday, Mon D``.
        on_date: The calendar date to attach when only a bare time is present.

    Returns:
        ISO 8601 datetime string without timezone, e.g. ``"2026-05-01T08:00:00"``.
        Falls back to midnight on ``on_date`` when parsing fails.
    """
    time_str = time_str.strip()

    # Extract just the time portion from strings like "4:29 PM on Wed, Mar 11"
    time_only_match = re.match(r"(\d{1,2}:\d{2}\s*(?:AM|PM))", time_str, re.IGNORECASE)
    if time_only_match:
        time_str = time_only_match.group(1).strip()

    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            t = datetime.strptime(time_str, fmt).time()
            return datetime.combine(on_date, t).isoformat()
        except ValueError:
            continue
    # Unparseable — return midnight on the given date rather than crashing.
    return datetime.combine(on_date, datetime.min.time()).isoformat()


def _parse_days_ahead(arrival_time_ahead: str) -> int:
    """Extract the number of extra days from an arrival-time-ahead marker.

    Args:
        arrival_time_ahead: String like ``"+1"``, ``"+2"``, or ``""``.

    Returns:
        Integer number of days to add to the departure date.  Returns ``0``
        when the string is empty or unrecognised.
    """
    match = re.search(r"\+(\d+)", arrival_time_ahead)
    return int(match.group(1)) if match else 0


def _parse_duration_minutes(duration_str: str) -> int:
    """Convert a human-readable duration string to total minutes.

    Args:
        duration_str: String like ``"5 hr 30 min"`` or ``"45 min"``.

    Returns:
        Total duration in minutes.  Returns ``0`` if the string cannot be
        parsed.
    """
    hours = 0
    minutes = 0
    hour_match = re.search(r"(\d+)\s*hr", duration_str)
    min_match = re.search(r"(\d+)\s*min", duration_str)
    if hour_match:
        hours = int(hour_match.group(1))
    if min_match:
        minutes = int(min_match.group(1))
    return hours * 60 + minutes


def _parse_price(price_str: str) -> float:
    """Extract a numeric price from a currency-prefixed string.

    Args:
        price_str: String like ``"$299"``, ``"€1,234"``, or ``"0"``.

    Returns:
        Price as a float.  Returns ``0.0`` if no numeric value is found.
    """
    # Strip currency symbols, commas, and whitespace; keep digits and dot.
    numeric = re.sub(r"[^\d.]", "", price_str.replace(",", ""))
    try:
        return float(numeric)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser.

    Returns:
        Configured :class:`argparse.ArgumentParser` instance.
    """
    parser = argparse.ArgumentParser(
        prog="search.py",
        description=(
            "Search Google Flights and print results as newline-delimited JSON."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python search.py JFK LAX -d 2026-05-01 --return-date 2026-05-10\n"
            "  python search.py JFK LAX -d 2026-05-01 --return-date 2026-05-10 --cabin BUSINESS\n"
            "  python search.py LHR CDG -d 2026-06-01 --return-date 2026-06-08 --stops nonstop"
        ),
    )
    parser.add_argument(
        "origin",
        metavar="FROM",
        help="IATA departure airport code (e.g. JFK)",
    )
    parser.add_argument(
        "destination",
        metavar="TO",
        help="IATA arrival airport code (e.g. LAX)",
    )
    parser.add_argument(
        "-d",
        dest="date",
        metavar="DATE",
        default=None,
        help="Departure date in YYYY-MM-DD format (default: tomorrow)",
    )
    parser.add_argument(
        "--return-date",
        dest="return_date",
        metavar="DATE",
        required=True,
        help="Return date in YYYY-MM-DD format (required — round-trip only)",
    )
    parser.add_argument(
        "--cabin",
        dest="cabin",
        metavar="CABIN",
        default="ECONOMY",
        choices=list(_CABIN_TO_SEAT.keys()),
        help=(
            "Cabin class: ECONOMY, PREMIUM_ECONOMY, BUSINESS, FIRST "
            "(default: ECONOMY)"
        ),
    )
    parser.add_argument(
        "--stops",
        dest="stops",
        metavar="STOPS",
        default="any",
        choices=list(_STOPS_TO_MAX.keys()),
        help="Stop filter: any, nonstop, one, two (default: any)",
    )
    parser.add_argument(
        "--adults",
        dest="adults",
        metavar="N",
        type=int,
        default=1,
        help="Number of adult passengers, 1–9 (default: 1)",
    )
    parser.add_argument(
        "--currency",
        dest="currency",
        metavar="CODE",
        default="USD",
        help="ISO 4217 currency code (default: USD)",
    )
    parser.add_argument(
        "--max",
        dest="max_results",
        metavar="N",
        type=int,
        default=20,
        help="Maximum number of results to emit (default: 20)",
    )
    return parser


def _validate_date(value: str, field_name: str) -> date:
    """Parse and validate a YYYY-MM-DD date string.

    Args:
        value: The raw string supplied by the user.
        field_name: Human-readable name used in the error message.

    Returns:
        Parsed :class:`datetime.date`.

    Raises:
        SystemExit: Printed to stderr and exits with code 1 on bad input.
    """
    try:
        return date.fromisoformat(value)
    except ValueError:
        print(
            f"ERROR: {field_name} must be in YYYY-MM-DD format, got: {value!r}",
            file=sys.stderr,
        )
        sys.exit(1)


def _validate_adults(value: int) -> None:
    """Ensure the adult passenger count is within the allowed range.

    Args:
        value: Number of adults supplied by the user.

    Raises:
        SystemExit: Printed to stderr and exits with code 1 on bad input.
    """
    if not (1 <= value <= 9):
        print(
            f"ERROR: --adults must be between 1 and 9, got: {value}",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments, query Google Flights, and emit NDJSON to stdout."""
    parser = _build_parser()
    args = parser.parse_args()

    # Resolve and validate dates
    departure_date: date = (
        _validate_date(args.date, "-d / departure date")
        if args.date
        else date.today() + timedelta(days=1)
    )

    return_date: date = _validate_date(args.return_date, "--return-date")
    if return_date <= departure_date:
        print(
            "ERROR: --return-date must be after the departure date.",
            file=sys.stderr,
        )
        sys.exit(1)

    _validate_adults(args.adults)

    seat: str = _CABIN_TO_SEAT[args.cabin]
    max_stops: Optional[int] = _STOPS_TO_MAX[args.stops]

    # Build fast_flights request objects (always round-trip)
    flight_data_legs: list[FlightData] = [
        FlightData(
            date=departure_date.isoformat(),
            from_airport=args.origin.upper(),
            to_airport=args.destination.upper(),
        ),
        FlightData(
            date=return_date.isoformat(),
            from_airport=args.destination.upper(),
            to_airport=args.origin.upper(),
        ),
    ]

    trip: str = "round-trip"
    passengers = Passengers(adults=args.adults)

    tfs_filter = create_filter(
        flight_data=flight_data_legs,
        trip=trip,  # type: ignore[arg-type]
        passengers=passengers,
        seat=seat,  # type: ignore[arg-type]
        max_stops=max_stops,
    )

    # Execute the search
    try:
        result = get_flights_from_filter(
            tfs_filter,
            currency=args.currency.upper(),
        )
    except RuntimeError as exc:
        print(f"ERROR: No flights returned — {exc}", file=sys.stderr)
        sys.exit(1)
    except AssertionError as exc:
        print(f"ERROR: Google Flights request failed — {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Unexpected error during search — {exc}", file=sys.stderr)
        sys.exit(1)

    # Emit results
    emitted = 0
    for flight in result.flights:
        if emitted >= args.max_results:
            break
        try:
            record = _build_flight_result(
                flight=flight,
                origin=args.origin,
                destination=args.destination,
                departure_date=departure_date,
                cabin=args.cabin,  # type: ignore[arg-type]
                currency=args.currency,
                return_date=return_date,
            )
        except Exception as exc:  # noqa: BLE001
            # A single malformed result should not abort the entire run.
            print(
                f"WARNING: Skipping a result due to conversion error — {exc}",
                file=sys.stderr,
            )
            continue

        print(json.dumps(record, ensure_ascii=False))
        emitted += 1

    if emitted == 0:
        print(
            "ERROR: Search succeeded but no results could be parsed.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
