#!/usr/bin/env python3
"""ITA Matrix flight search via headless Chromium automation.

Navigates to https://matrix.itasoftware.com/search, fills the search form,
waits for results, scrapes them, and emits newline-delimited JSON to stdout.

All errors and warnings are written exclusively to stderr so that stdout
remains a clean JSON stream suitable for piping.

Usage
-----
    python search.py JFK LAX -d 2026-05-01 --cabin ECONOMY --stops nonstop

See ``python search.py --help`` for the full CLI reference.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Playwright import guard — instructs the user to run setup.sh if missing.
# ---------------------------------------------------------------------------
try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )
except ImportError:
    print(
        "Playwright is not installed. Run setup.sh first:\n"
        "    bash skills/ita-matrix/setup.sh",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ITA_MATRIX_URL = "https://matrix.itasoftware.com/search"

# Mapping from friendly cabin names to the values ITA Matrix uses in its UI.
CABIN_LABEL_MAP: dict[str, str] = {
    "ECONOMY": "Economy",
    "PREMIUM_ECONOMY": "Premium Economy",
    "BUSINESS": "Business",
    "FIRST": "First",
}

# ITA Matrix result table selectors (as of early 2026).
# The site renders results inside a GWT-based DOM; selectors target the
# data-bearing elements inside the itinerary listing widget.
#
# If ITA Matrix updates its DOM these selectors may need revision.
# The scraper is written defensively: any failure emits a stderr warning
# and returns empty results rather than crashing.

# Outer container that appears once results have loaded.
RESULTS_CONTAINER_SELECTOR = "div.gwt-HTML, div[class*='results'], div[id*='results']"

# Each itinerary row inside the results panel.
ITINERARY_ROW_SELECTOR = "tr[class*='result'], tr[class*='itinerary'], div[class*='itinerary']"

# Within an itinerary row, the price cell.
PRICE_CELL_SELECTOR = "td[class*='price'], span[class*='price'], div[class*='price']"

# Airline name / carrier info cell.
AIRLINE_CELL_SELECTOR = "td[class*='airline'], span[class*='airline'], div[class*='carrier']"

# Departure and arrival time cells.
DEPARTURE_CELL_SELECTOR = "td[class*='depart'], span[class*='depart'], td[class*='departure']"
ARRIVAL_CELL_SELECTOR = "td[class*='arrive'], span[class*='arrive'], td[class*='arrival']"

# Duration cell (e.g. "5h 30m").
DURATION_CELL_SELECTOR = "td[class*='duration'], span[class*='duration']"

# Stops cell (e.g. "Nonstop", "1 stop").
STOPS_CELL_SELECTOR = "td[class*='stop'], span[class*='stop'], div[class*='stop']"

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class FlightResult(TypedDict):
    """Shared FlightResult schema emitted as newline-delimited JSON."""

    provider: str
    airline: str
    airline_code: str
    flight_number: str
    origin: str
    destination: str
    departure_at: str
    arrival_at: str
    duration_minutes: int
    stops: int
    layover_airports: list[str]
    cabin: str
    price: float
    currency: str
    booking_url: None
    trip_type: str
    return_leg: dict[str, Any] | None
    raw: dict[str, Any]


class SearchParams(TypedDict):
    """Validated, normalised search parameters."""

    origin: str
    destination: str
    departure_date: str
    return_date: str
    cabin: str
    stops: str
    adults: int
    currency: str
    max_results: int
    timeout_seconds: int


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def _tomorrow() -> str:
    """Return tomorrow's date as a YYYY-MM-DD string."""
    return (date.today() + timedelta(days=1)).isoformat()


def build_arg_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="search.py",
        description=(
            "Search ITA Matrix (matrix.itasoftware.com) for flight prices "
            "and schedules. Outputs newline-delimited JSON to stdout."
        ),
    )
    parser.add_argument(
        "origin",
        metavar="FROM",
        type=str,
        help="IATA origin airport code (e.g. JFK)",
    )
    parser.add_argument(
        "destination",
        metavar="TO",
        type=str,
        help="IATA destination airport code (e.g. LAX)",
    )
    parser.add_argument(
        "-d",
        dest="departure_date",
        metavar="DATE",
        default=_tomorrow(),
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
        choices=list(CABIN_LABEL_MAP.keys()),
        default="ECONOMY",
        help="Cabin class (default: ECONOMY)",
    )
    parser.add_argument(
        "--stops",
        dest="stops",
        choices=["any", "nonstop"],
        default="any",
        help="Stop filter: any (default) or nonstop",
    )
    parser.add_argument(
        "--adults",
        dest="adults",
        metavar="N",
        type=int,
        default=1,
        help="Number of adult passengers (default: 1)",
    )
    parser.add_argument(
        "--currency",
        dest="currency",
        metavar="CURRENCY",
        default="USD",
        help="Currency code for displayed prices (default: USD)",
    )
    parser.add_argument(
        "--max",
        dest="max_results",
        metavar="N",
        type=int,
        default=20,
        help="Maximum number of results to return (default: 20)",
    )
    parser.add_argument(
        "--timeout",
        dest="timeout_seconds",
        metavar="SECONDS",
        type=int,
        default=30,
        help="Browser wait timeout in seconds (default: 30)",
    )
    return parser


def validate_args(args: argparse.Namespace) -> SearchParams:
    """Validate parsed arguments and return a ``SearchParams`` dict.

    Args:
        args: Parsed namespace from argparse.

    Returns:
        Validated and normalised search parameters.

    Raises:
        SystemExit: On any validation failure, after printing to stderr.
    """

    def _validate_date(value: str, flag: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            print(
                f"error: {flag} must be in YYYY-MM-DD format, got: {value!r}",
                file=sys.stderr,
            )
            sys.exit(1)
        return value

    departure_date = _validate_date(args.departure_date, "-d")
    return_date: str = _validate_date(args.return_date, "--return-date")

    if args.adults < 1:
        print("error: --adults must be >= 1", file=sys.stderr)
        sys.exit(1)

    if args.max_results < 1:
        print("error: --max must be >= 1", file=sys.stderr)
        sys.exit(1)

    if args.timeout_seconds < 5:
        print("error: --timeout must be >= 5 seconds", file=sys.stderr)
        sys.exit(1)

    return SearchParams(
        origin=args.origin.upper().strip(),
        destination=args.destination.upper().strip(),
        departure_date=departure_date,
        return_date=return_date,
        cabin=args.cabin,
        stops=args.stops,
        adults=args.adults,
        currency=args.currency.upper().strip(),
        max_results=args.max_results,
        timeout_seconds=args.timeout_seconds,
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_price(text: str) -> float | None:
    """Extract a numeric price from a string like '$189' or '1,234.56'.

    Args:
        text: Raw price text from the DOM.

    Returns:
        Parsed float price, or ``None`` if parsing fails.
    """
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_duration(text: str) -> int:
    """Convert a duration string like '5h 30m' or '2h' to minutes.

    Args:
        text: Duration text from the DOM.

    Returns:
        Total duration in minutes, or 0 if parsing fails.
    """
    hours = 0
    minutes = 0
    hour_match = re.search(r"(\d+)\s*h", text, re.IGNORECASE)
    minute_match = re.search(r"(\d+)\s*m", text, re.IGNORECASE)
    if hour_match:
        hours = int(hour_match.group(1))
    if minute_match:
        minutes = int(minute_match.group(1))
    return hours * 60 + minutes


def _parse_stops(text: str) -> tuple[int, list[str]]:
    """Parse stops text like 'Nonstop', '1 stop (ORD)', '2 stops'.

    Args:
        text: Stops text from the DOM.

    Returns:
        Tuple of (stop_count, list_of_layover_airport_codes).
    """
    lower = text.lower().strip()
    if "nonstop" in lower or lower == "0":
        return 0, []

    stop_count_match = re.search(r"(\d+)\s*stop", lower)
    stop_count = int(stop_count_match.group(1)) if stop_count_match else 1

    # Extract parenthesised airport codes e.g. "(ORD, DFW)"
    airports: list[str] = re.findall(r"\b([A-Z]{3})\b", text)
    return stop_count, airports


def _parse_airline_code(airline_name: str, flight_number: str) -> str:
    """Infer the IATA airline code from the flight number or airline name.

    Args:
        airline_name: Full airline name.
        flight_number: Raw flight number string (e.g. 'AA 100').

    Returns:
        Two-letter IATA code, or empty string if not determinable.
    """
    # Flight numbers typically begin with the two-letter IATA code.
    match = re.match(r"^([A-Z]{2})\s*\d+", flight_number.strip())
    if match:
        return match.group(1)
    return ""


def _format_datetime(date_str: str, time_str: str) -> str:
    """Combine a date string and a time string into an ISO 8601 datetime.

    Attempts several common time formats. Falls back to midnight if parsing
    fails.

    Args:
        date_str: Date in YYYY-MM-DD format.
        time_str: Time as shown in the UI, e.g. '8:00 AM', '14:35'.

    Returns:
        ISO 8601 datetime string without timezone, e.g. '2026-05-01T08:00:00'.
    """
    time_str = time_str.strip()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M", "%H:%M:%S"):
        try:
            parsed_time = datetime.strptime(time_str, fmt)
            return f"{date_str}T{parsed_time.strftime('%H:%M:%S')}"
        except ValueError:
            continue
    # Fallback: return midnight if we cannot parse the time.
    return f"{date_str}T00:00:00"


# ---------------------------------------------------------------------------
# Browser interaction
# ---------------------------------------------------------------------------


async def _fill_origin_destination(page: Page, origin: str, destination: str) -> None:
    """Fill the origin and destination fields in the ITA Matrix search form.

    ITA Matrix uses auto-complete inputs. We type the IATA code, wait for the
    suggestion dropdown, then press Enter or Tab to confirm the selection.

    Args:
        page: Active Playwright page.
        origin: IATA origin code.
        destination: IATA destination code.
    """
    # Origin input — ITA Matrix labels it "From" and it is the first text input
    # on the page. The selector targets input fields whose placeholder or
    # aria-label contains "From" or "Origin".
    origin_selectors = [
        "input[placeholder*='From']",
        "input[aria-label*='From']",
        "input[aria-label*='Origin']",
        "input[placeholder*='Origin']",
        # Fallback: first visible text input on the page
        "input[type='text']:first-of-type",
    ]
    for selector in origin_selectors:
        try:
            await page.fill(selector, origin, timeout=3_000)
            await page.press(selector, "Tab")
            break
        except Exception:
            continue

    # Destination input — similarly labelled "To" or "Destination".
    dest_selectors = [
        "input[placeholder*='To']",
        "input[aria-label*='To']",
        "input[aria-label*='Destination']",
        "input[placeholder*='Destination']",
    ]
    for selector in dest_selectors:
        try:
            await page.fill(selector, destination, timeout=3_000)
            await page.press(selector, "Tab")
            break
        except Exception:
            continue


async def _fill_date(page: Page, date_str: str, field_label: str = "Depart") -> None:
    """Fill a date field in the ITA Matrix search form.

    Args:
        page: Active Playwright page.
        date_str: Date in YYYY-MM-DD format.
        field_label: Partial label text used to locate the input
            (e.g. 'Depart', 'Return').
    """
    # ITA Matrix date fields can be labelled "Depart", "Return", etc.
    date_selectors = [
        f"input[aria-label*='{field_label}']",
        f"input[placeholder*='{field_label}']",
        f"input[name*='{field_label.lower()}']",
    ]
    # Convert YYYY-MM-DD to the format ITA Matrix expects (MM/DD/YYYY).
    parsed = datetime.strptime(date_str, "%Y-%m-%d")
    formatted_date = parsed.strftime("%m/%d/%Y")

    for selector in date_selectors:
        try:
            await page.fill(selector, formatted_date, timeout=3_000)
            await page.press(selector, "Tab")
            return
        except Exception:
            continue

    print(
        f"warning: could not fill {field_label} date field — "
        "selector not matched; the form may have changed",
        file=sys.stderr,
    )


async def _select_cabin(page: Page, cabin: str) -> None:
    """Select the cabin class in the search form.

    ITA Matrix renders cabin class as a <select> dropdown or a set of radio
    buttons depending on the trip type. We attempt both approaches.

    Args:
        page: Active Playwright page.
        cabin: Cabin key (e.g. 'ECONOMY').
    """
    cabin_label = CABIN_LABEL_MAP.get(cabin, "Economy")

    # Try <select> dropdown first.
    cabin_select_selectors = [
        "select[aria-label*='cabin' i]",
        "select[aria-label*='class' i]",
        "select[name*='cabin' i]",
        "select[name*='class' i]",
    ]
    for selector in cabin_select_selectors:
        try:
            await page.select_option(selector, label=cabin_label, timeout=3_000)
            return
        except Exception:
            continue

    # Try clicking a labelled radio button or option element.
    try:
        cabin_option = page.get_by_text(cabin_label, exact=True)
        if await cabin_option.count() > 0:
            await cabin_option.first.click(timeout=3_000)
            return
    except Exception:
        pass

    print(
        f"warning: could not select cabin '{cabin_label}' — "
        "selector not matched; defaulting to site default",
        file=sys.stderr,
    )


async def _set_passengers(page: Page, adults: int) -> None:
    """Set the number of adult passengers in the search form.

    Args:
        page: Active Playwright page.
        adults: Number of adult passengers (>= 1).
    """
    if adults == 1:
        # Most forms default to 1 adult; skip to avoid unnecessary interaction.
        return

    passenger_selectors = [
        "input[aria-label*='adult' i]",
        "input[name*='adult' i]",
        "select[aria-label*='adult' i]",
        "select[name*='adult' i]",
    ]
    for selector in passenger_selectors:
        try:
            element = page.locator(selector)
            tag = await element.evaluate("el => el.tagName.toLowerCase()", timeout=3_000)
            if tag == "select":
                await page.select_option(selector, str(adults), timeout=3_000)
            else:
                await page.fill(selector, str(adults), timeout=3_000)
            return
        except Exception:
            continue

    print(
        "warning: could not set passenger count — selector not matched",
        file=sys.stderr,
    )


async def _apply_nonstop_filter(page: Page) -> None:
    """Enable the nonstop filter after results load, if available.

    Args:
        page: Active Playwright page with results already rendered.
    """
    nonstop_selectors = [
        "input[type='checkbox'][aria-label*='nonstop' i]",
        "input[type='checkbox'][aria-label*='non-stop' i]",
        "label:has-text('Nonstop') input[type='checkbox']",
        "label:has-text('Non-stop') input[type='checkbox']",
    ]
    for selector in nonstop_selectors:
        try:
            checkbox = page.locator(selector)
            if await checkbox.count() > 0:
                is_checked = await checkbox.first.is_checked()
                if not is_checked:
                    await checkbox.first.click(timeout=3_000)
                return
        except Exception:
            continue

    print(
        "warning: nonstop filter checkbox not found — "
        "results may include connecting flights",
        file=sys.stderr,
    )


async def _submit_search(page: Page) -> None:
    """Click the search / submit button on the ITA Matrix form.

    Args:
        page: Active Playwright page with the form filled.
    """
    submit_selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Search')",
        "button:has-text('Find Flights')",
        # GWT-based buttons often have role="button"
        "div[role='button']:has-text('Search')",
    ]
    for selector in submit_selectors:
        try:
            btn = page.locator(selector)
            if await btn.count() > 0:
                await btn.first.click(timeout=5_000)
                return
        except Exception:
            continue

    # Last resort: submit via keyboard from the active element.
    await page.keyboard.press("Enter")


# ---------------------------------------------------------------------------
# Result scraping
# ---------------------------------------------------------------------------


async def _scrape_results(
    page: Page,
    params: SearchParams,
    timeout_ms: int,
) -> list[FlightResult]:
    """Wait for and scrape itinerary rows from the ITA Matrix results page.

    Args:
        page: Active Playwright page after search has been submitted.
        params: Validated search parameters (used to populate fixed fields).
        timeout_ms: Maximum milliseconds to wait for the results container.

    Returns:
        List of ``FlightResult`` dicts, up to ``params['max_results']`` items.
    """
    results: list[FlightResult] = []

    # Wait for the results container to appear. ITA Matrix renders results
    # asynchronously via GWT RPC — we wait for any element that signals the
    # results panel has rendered. The selector is intentionally broad because
    # GWT-generated class names are not stable across deploys.
    results_ready_selectors = [
        # Primary: a table or div that contains price information
        "td[class*='price']",
        "span[class*='price']",
        "div[class*='price']",
        # Fallback: any element with dollar amounts (heuristic)
        "td:has-text('$')",
        "span:has-text('$')",
    ]

    container_found = False
    for selector in results_ready_selectors:
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
            container_found = True
            break
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue

    if not container_found:
        print(
            "warning: results did not render within the timeout window — "
            "ITA Matrix may have changed its layout or blocked the request",
            file=sys.stderr,
        )
        return results

    # Attempt to locate itinerary rows using multiple selectors in priority order.
    row_selectors = [
        ITINERARY_ROW_SELECTOR,
        "tr[class*='row']",
        "div[class*='row'][class*='result']",
        "li[class*='itinerary']",
    ]

    rows_locator = None
    for selector in row_selectors:
        candidate = page.locator(selector)
        count = await candidate.count()
        if count > 0:
            rows_locator = candidate
            break

    if rows_locator is None:
        print(
            "warning: no itinerary rows found in the DOM — "
            "scraping selectors may need updating",
            file=sys.stderr,
        )
        return results

    total_rows = await rows_locator.count()
    limit = min(total_rows, params["max_results"])

    for i in range(limit):
        row = rows_locator.nth(i)
        result = await _extract_row(row, params)
        if result is not None:
            results.append(result)

    return results


async def _extract_row(row: Any, params: SearchParams) -> FlightResult | None:
    """Extract a single ``FlightResult`` from an itinerary DOM row.

    All field extraction is wrapped in individual try/except blocks so that a
    missing element in one field does not prevent extraction of other fields.

    Args:
        row: Playwright Locator pointing at a single itinerary row element.
        params: Search parameters used to fill in known fields.

    Returns:
        Populated ``FlightResult`` dict, or ``None`` if the row appears to
        contain no meaningful flight data (e.g. a header row).
    """
    raw: dict[str, Any] = {}

    # --- Price ---
    price: float = 0.0
    try:
        price_el = row.locator(PRICE_CELL_SELECTOR).first
        price_text = await price_el.inner_text(timeout=2_000)
        raw["price_raw"] = price_text
        parsed_price = _parse_price(price_text)
        if parsed_price is not None:
            price = parsed_price
    except Exception:
        pass

    # Skip rows with no price — likely header or separator rows.
    if price == 0.0:
        return None

    # --- Airline ---
    airline = ""
    try:
        airline_el = row.locator(AIRLINE_CELL_SELECTOR).first
        airline = (await airline_el.inner_text(timeout=2_000)).strip()
        raw["airline_raw"] = airline
    except Exception:
        pass

    # --- Departure time ---
    departure_time = "00:00"
    try:
        dep_el = row.locator(DEPARTURE_CELL_SELECTOR).first
        departure_time = (await dep_el.inner_text(timeout=2_000)).strip()
        raw["departure_raw"] = departure_time
    except Exception:
        pass

    # --- Arrival time ---
    arrival_time = "00:00"
    try:
        arr_el = row.locator(ARRIVAL_CELL_SELECTOR).first
        arrival_time = (await arr_el.inner_text(timeout=2_000)).strip()
        raw["arrival_raw"] = arrival_time
    except Exception:
        pass

    # --- Duration ---
    duration_minutes = 0
    try:
        dur_el = row.locator(DURATION_CELL_SELECTOR).first
        duration_text = (await dur_el.inner_text(timeout=2_000)).strip()
        raw["duration_raw"] = duration_text
        duration_minutes = _parse_duration(duration_text)
    except Exception:
        pass

    # --- Stops ---
    stops_count = 0
    layover_airports: list[str] = []
    try:
        stops_el = row.locator(STOPS_CELL_SELECTOR).first
        stops_text = (await stops_el.inner_text(timeout=2_000)).strip()
        raw["stops_raw"] = stops_text
        stops_count, layover_airports = _parse_stops(stops_text)
    except Exception:
        pass

    # --- Flight number ---
    # ITA Matrix sometimes renders the flight number in the airline cell or a
    # sibling cell. We search broadly within the row.
    flight_number = ""
    try:
        # Look for text matching an IATA flight number pattern: 2-letter code + digits.
        row_text = await row.inner_text(timeout=2_000)
        fn_match = re.search(r"\b([A-Z]{2})\s*(\d{1,4})\b", row_text)
        if fn_match:
            flight_number = f"{fn_match.group(1)} {fn_match.group(2)}"
            raw["flight_number_raw"] = flight_number
    except Exception:
        pass

    airline_code = _parse_airline_code(airline, flight_number)

    departure_at = _format_datetime(params["departure_date"], departure_time)
    # Arrival date: could be next day if duration crosses midnight. For
    # simplicity we use departure_date; the caller can infer from duration.
    arrival_at = _format_datetime(params["departure_date"], arrival_time)

    return FlightResult(
        provider="ita-matrix",
        airline=airline,
        airline_code=airline_code,
        flight_number=flight_number,
        origin=params["origin"],
        destination=params["destination"],
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=duration_minutes,
        stops=stops_count,
        layover_airports=layover_airports,
        cabin=params["cabin"],
        price=price,
        currency=params["currency"],
        booking_url=None,
        trip_type="round-trip",
        return_leg=None,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_search(params: SearchParams) -> list[FlightResult]:
    """Execute the full ITA Matrix search flow.

    Launches a headless Chromium browser, fills the search form, waits for
    results, scrapes them, and returns a list of ``FlightResult`` dicts.

    The browser is always closed before returning, even if an error occurs.

    Args:
        params: Validated search parameters.

    Returns:
        List of flight results (may be empty on timeout or scraping failure).
    """
    timeout_ms = params["timeout_seconds"] * 1_000

    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                # Reduce fingerprinting surface.
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context: BrowserContext = await browser.new_context(
            # Mimic a realistic desktop viewport and user-agent so the site
            # renders its full desktop layout rather than a mobile fallback.
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )

        try:
            page: Page = await context.new_page()

            # Suppress resource types that are unnecessary for scraping,
            # reducing bandwidth and latency.
            await page.route(
                "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,otf}",
                lambda route: route.abort(),
            )

            # Navigate to the ITA Matrix search page.
            try:
                await page.goto(ITA_MATRIX_URL, timeout=timeout_ms, wait_until="domcontentloaded")
            except PlaywrightTimeoutError:
                print(
                    f"warning: timed out loading {ITA_MATRIX_URL} "
                    f"after {params['timeout_seconds']}s",
                    file=sys.stderr,
                )
                return []
            except Exception as exc:
                print(f"error: failed to navigate to ITA Matrix: {exc}", file=sys.stderr)
                return []

            # Fill in the search form fields.
            await _fill_origin_destination(page, params["origin"], params["destination"])
            await _fill_date(page, params["departure_date"], "Depart")
            await _fill_date(page, params["return_date"], "Return")

            await _select_cabin(page, params["cabin"])
            await _set_passengers(page, params["adults"])

            # Submit the form and wait for navigation / results to load.
            await _submit_search(page)

            # Scrape results once the results container is visible.
            results = await _scrape_results(page, params, timeout_ms)

            # Apply nonstop filter post-load if requested. ITA Matrix exposes
            # this as a client-side filter rather than a search parameter.
            if params["stops"] == "nonstop" and results:
                await _apply_nonstop_filter(page)
                # Re-scrape after filter is applied.
                results = await _scrape_results(page, params, timeout_ms)

            return results

        except Exception as exc:
            print(f"error: unexpected error during search: {exc}", file=sys.stderr)
            return []
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def emit_results(results: list[FlightResult]) -> None:
    """Write results as newline-delimited JSON to stdout.

    Args:
        results: List of flight result dicts to emit.
    """
    for result in results:
        print(json.dumps(result, ensure_ascii=False))


def main() -> None:
    """Parse CLI arguments, run the search, and emit results."""
    parser = build_arg_parser()
    args = parser.parse_args()
    params = validate_args(args)

    results = asyncio.run(run_search(params))
    emit_results(results)

    if not results:
        print(
            "warning: no results returned — check arguments or increase --timeout",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
