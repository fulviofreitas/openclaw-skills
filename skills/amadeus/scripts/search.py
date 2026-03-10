#!/usr/bin/env python3
"""Amadeus Flight Offers Search API v2 client.

Searches real-time flight prices and schedules via the Amadeus GDS API.
Outputs results as newline-delimited JSON (NDJSON) to stdout.

Usage:
    search.py <FROM> <TO> [-d DATE] [--return-date DATE]
              [--cabin CLASS] [--stops any|nonstop]
              [--adults N] [--currency CODE] [--max N]

Environment variables:
    AMADEUS_API_KEY     Required. Amadeus API key.
    AMADEUS_API_SECRET  Required. Amadeus API secret.
    AMADEUS_ENV         Optional. "test" (default) or "production".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://test.api.amadeus.com"

TOKEN_PATH = "/v1/security/oauth2/token"
SEARCH_PATH = "/v2/shopping/flight-offers"

VALID_CABINS: frozenset[str] = frozenset(
    {"ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"}
)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class AmadeusAuthError(RuntimeError):
    """Raised when OAuth2 token acquisition fails."""


class AmadeusAPIError(RuntimeError):
    """Raised when the flight search endpoint returns an error."""


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------


def parse_iso_duration(duration: str) -> int:
    """Parse an ISO 8601 duration string into total minutes.

    Args:
        duration: ISO 8601 duration string, e.g. "PT5H30M" or "PT45M".

    Returns:
        Total duration in minutes as an integer.

    Raises:
        ValueError: If the duration string cannot be parsed.
    """
    pattern = re.compile(
        r"^P"
        r"(?:(?P<days>\d+)D)?"
        r"(?:T"
        r"(?:(?P<hours>\d+)H)?"
        r"(?:(?P<minutes>\d+)M)?"
        r"(?:(?P<seconds>\d+)S)?"
        r")?$"
    )
    match = pattern.match(duration)
    if not match:
        raise ValueError(f"Cannot parse ISO 8601 duration: {duration!r}")

    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)

    return days * 24 * 60 + hours * 60 + minutes + round(seconds / 60)


# ---------------------------------------------------------------------------
# OAuth2 token acquisition
# ---------------------------------------------------------------------------


async def fetch_access_token(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
    api_secret: str,
) -> str:
    """Obtain an OAuth2 client-credentials access token from Amadeus.

    Args:
        session: Active aiohttp client session.
        base_url: Amadeus environment base URL.
        api_key: Amadeus API key (client_id).
        api_secret: Amadeus API secret (client_secret).

    Returns:
        Bearer access token string.

    Raises:
        AmadeusAuthError: If the token request fails for any reason.
    """
    url = f"{base_url}{TOKEN_PATH}"
    payload = {
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": api_secret,
    }

    try:
        async with session.post(url, data=payload) as response:
            body = await response.json(content_type=None)

            if response.status != 200:
                error_msg = body.get("error_description") or body.get("error") or str(body)
                raise AmadeusAuthError(
                    f"Token request failed (HTTP {response.status}): {error_msg}"
                )

            token: str = body["access_token"]
            return token

    except aiohttp.ClientError as exc:
        raise AmadeusAuthError(f"Network error during token acquisition: {exc}") from exc


# ---------------------------------------------------------------------------
# Flight search
# ---------------------------------------------------------------------------


async def search_flights(
    session: aiohttp.ClientSession,
    base_url: str,
    token: str,
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None,
    cabin: str,
    nonstop_only: bool,
    adults: int,
    currency: str,
    max_results: int,
) -> dict[str, Any]:
    """Call the Amadeus v2 Flight Offers Search endpoint.

    Args:
        session: Active aiohttp client session.
        base_url: Amadeus environment base URL.
        token: Valid OAuth2 bearer token.
        origin: IATA code of the origin airport.
        destination: IATA code of the destination airport.
        departure_date: Departure date in YYYY-MM-DD format.
        return_date: Optional return date for round trips (YYYY-MM-DD).
        cabin: Amadeus cabin class string (e.g. "ECONOMY").
        nonstop_only: If True, request only non-stop flights.
        adults: Number of adult passengers.
        currency: Currency code for quoted prices.
        max_results: Maximum number of offers to retrieve.

    Returns:
        Parsed JSON response body as a dictionary.

    Raises:
        AmadeusAPIError: If the API returns a non-200 status or an error body.
    """
    url = f"{base_url}{SEARCH_PATH}"
    headers = {"Authorization": f"Bearer {token}"}
    params: dict[str, str | int] = {
        "originLocationCode": origin.upper(),
        "destinationLocationCode": destination.upper(),
        "departureDate": departure_date,
        "adults": adults,
        "travelClass": cabin,
        "currencyCode": currency.upper(),
        "max": max_results,
    }

    if return_date:
        params["returnDate"] = return_date

    if nonstop_only:
        params["nonStop"] = "true"

    try:
        async with session.get(url, headers=headers, params=params) as response:
            body = await response.json(content_type=None)

            if response.status == 401:
                raise AmadeusAuthError(
                    "Authentication failed: access token was rejected. "
                    "Verify AMADEUS_API_KEY and AMADEUS_API_SECRET."
                )

            if response.status == 429:
                raise AmadeusAPIError(
                    "Rate limit exceeded. Please wait before retrying."
                )

            if response.status != 200:
                errors = body.get("errors", [])
                if errors:
                    detail = "; ".join(
                        e.get("detail") or e.get("title") or str(e) for e in errors
                    )
                else:
                    detail = str(body)
                raise AmadeusAPIError(
                    f"Flight search failed (HTTP {response.status}): {detail}"
                )

            return body  # type: ignore[return-value]

    except aiohttp.ClientError as exc:
        raise AmadeusAPIError(f"Network error during flight search: {exc}") from exc


# ---------------------------------------------------------------------------
# Response normalisation
# ---------------------------------------------------------------------------


def resolve_carrier_name(code: str, dictionaries: dict[str, Any]) -> str:
    """Resolve an IATA carrier code to a full airline name.

    Args:
        code: Two-letter IATA carrier code.
        dictionaries: The ``dictionaries`` block from the Amadeus response.

    Returns:
        Full carrier name if found in dictionaries, otherwise the code itself.
    """
    carriers: dict[str, str] = dictionaries.get("carriers", {})
    return carriers.get(code, code)


def normalise_offer(
    offer: dict[str, Any],
    dictionaries: dict[str, Any],
    currency: str,
) -> dict[str, Any]:
    """Convert a single Amadeus flight offer into the FlightResult schema.

    Only the first outbound itinerary is represented per result object.
    Round-trip offers produce a single result for the outbound leg.

    Args:
        offer: A single flight offer object from the Amadeus response.
        dictionaries: The ``dictionaries`` block from the Amadeus response.
        currency: Currency code used for the search (fallback if not in offer).

    Returns:
        A FlightResult-conformant dictionary.
    """
    # Use the first itinerary (outbound leg)
    itinerary: dict[str, Any] = offer["itineraries"][0]
    segments: list[dict[str, Any]] = itinerary["segments"]

    first_seg = segments[0]
    last_seg = segments[-1]

    airline_code: str = offer.get("validatingAirlineCodes", [first_seg["carrierCode"]])[0]
    airline_name: str = resolve_carrier_name(airline_code, dictionaries)

    flight_number: str = (
        f"{first_seg['carrierCode']}{first_seg['number']}"
    )

    origin: str = first_seg["departure"]["iataCode"]
    destination: str = last_seg["arrival"]["iataCode"]
    departure_at: str = first_seg["departure"]["at"]
    arrival_at: str = last_seg["arrival"]["at"]

    stops: int = len(segments) - 1
    layover_airports: list[str] = [
        seg["departure"]["iataCode"] for seg in segments[1:]
    ]

    duration_str: str = itinerary.get("duration", "PT0M")
    try:
        duration_minutes: int = parse_iso_duration(duration_str)
    except ValueError:
        duration_minutes = 0

    # Cabin: read from the first traveller pricing segment
    cabin: str = "ECONOMY"
    traveller_pricings: list[dict[str, Any]] = offer.get("travelerPricings", [])
    if traveller_pricings:
        fare_details = traveller_pricings[0].get("fareDetailsBySegment", [])
        if fare_details:
            cabin = fare_details[0].get("cabin", cabin)

    price_info: dict[str, Any] = offer.get("price", {})
    try:
        price: float = float(price_info.get("grandTotal") or price_info.get("total") or 0.0)
    except (TypeError, ValueError):
        price = 0.0

    result_currency: str = price_info.get("currency") or currency.upper()

    return {
        "provider": "amadeus",
        "airline": airline_name,
        "airline_code": airline_code,
        "flight_number": flight_number,
        "origin": origin,
        "destination": destination,
        "departure_at": departure_at,
        "arrival_at": arrival_at,
        "duration_minutes": duration_minutes,
        "stops": stops,
        "layover_airports": layover_airports,
        "cabin": cabin,
        "price": price,
        "currency": result_currency,
        "booking_url": None,
        "raw": offer,
    }


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct and return the CLI argument parser.

    Returns:
        Configured ArgumentParser instance.
    """
    tomorrow: str = (date.today() + timedelta(days=1)).isoformat()

    parser = argparse.ArgumentParser(
        prog="search.py",
        description=(
            "Search real-time flight offers via the Amadeus GDS API. "
            "Outputs newline-delimited JSON to stdout."
        ),
    )
    parser.add_argument(
        "origin",
        metavar="FROM",
        help="Origin IATA airport code (e.g. JFK).",
    )
    parser.add_argument(
        "destination",
        metavar="TO",
        help="Destination IATA airport code (e.g. LHR).",
    )
    parser.add_argument(
        "-d",
        dest="date",
        default=tomorrow,
        metavar="DATE",
        help=f"Departure date in YYYY-MM-DD format. Defaults to tomorrow ({tomorrow}).",
    )
    parser.add_argument(
        "--return-date",
        default=None,
        metavar="DATE",
        help="Return date for round-trip search (YYYY-MM-DD). Omit for one-way.",
    )
    parser.add_argument(
        "--cabin",
        default="ECONOMY",
        choices=sorted(VALID_CABINS),
        metavar="CLASS",
        help=(
            "Cabin class: ECONOMY, PREMIUM_ECONOMY, BUSINESS, FIRST. "
            "Defaults to ECONOMY."
        ),
    )
    parser.add_argument(
        "--stops",
        default="any",
        choices=["any", "nonstop"],
        help="Filter by number of stops. Defaults to any.",
    )
    parser.add_argument(
        "--adults",
        type=int,
        default=1,
        metavar="N",
        help="Number of adult passengers. Defaults to 1.",
    )
    parser.add_argument(
        "--currency",
        default="USD",
        metavar="CODE",
        help="Currency code for prices (e.g. USD, EUR). Defaults to USD.",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=20,
        dest="max_results",
        metavar="N",
        help="Maximum number of results to return. Defaults to 20.",
    )
    return parser


def validate_date(value: str, label: str) -> None:
    """Validate that a string is a parseable YYYY-MM-DD date.

    Args:
        value: The date string to validate.
        label: Human-readable label used in error messages.

    Raises:
        SystemExit: If the date string is invalid.
    """
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        print(
            f"error: {label} must be in YYYY-MM-DD format, got: {value!r}",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def main() -> None:
    """Main async entrypoint: parse args, authenticate, search, and emit results."""

    # --- Credential check (fail fast, no defaults) ---
    api_key: str | None = os.environ.get("AMADEUS_API_KEY")
    api_secret: str | None = os.environ.get("AMADEUS_API_SECRET")

    missing: list[str] = []
    if not api_key:
        missing.append("AMADEUS_API_KEY")
    if not api_secret:
        missing.append("AMADEUS_API_SECRET")

    if missing:
        print(
            f"error: missing required environment variable(s): {', '.join(missing)}\n"
            "Get free credentials at: https://developers.amadeus.com\n"
            "Then set them:\n"
            "  export AMADEUS_API_KEY=your_key\n"
            "  export AMADEUS_API_SECRET=your_secret",
            file=sys.stderr,
        )
        sys.exit(1)

    # Type narrowing: both are confirmed non-empty strings beyond this point.
    assert api_key is not None
    assert api_secret is not None

    # --- Base URL selection ---
    base_url: str = os.environ.get("AMADEUS_BASE_URL", DEFAULT_BASE_URL)

    # --- CLI arguments ---
    parser = build_arg_parser()
    args = parser.parse_args()

    validate_date(args.date, "-d / departure date")
    if args.return_date is not None:
        validate_date(args.return_date, "--return-date")

    if args.adults < 1:
        print("error: --adults must be at least 1.", file=sys.stderr)
        sys.exit(1)

    if args.max_results < 1:
        print("error: --max must be at least 1.", file=sys.stderr)
        sys.exit(1)

    nonstop_only: bool = args.stops == "nonstop"

    # --- HTTP session, auth, and search ---
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            token = await fetch_access_token(session, base_url, api_key, api_secret)
        except AmadeusAuthError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

        try:
            response_body = await search_flights(
                session=session,
                base_url=base_url,
                token=token,
                origin=args.origin,
                destination=args.destination,
                departure_date=args.date,
                return_date=args.return_date,
                cabin=args.cabin,
                nonstop_only=nonstop_only,
                adults=args.adults,
                currency=args.currency,
                max_results=args.max_results,
            )
        except (AmadeusAuthError, AmadeusAPIError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    # --- Normalise and emit ---
    offers: list[dict[str, Any]] = response_body.get("data", [])
    dictionaries: dict[str, Any] = response_body.get("dictionaries", {})

    for offer in offers:
        result = normalise_offer(offer, dictionaries, args.currency)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
