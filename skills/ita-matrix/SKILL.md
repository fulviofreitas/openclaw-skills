---
name: ita-matrix
description: Search flight prices and schedules via ITA Matrix (matrix.itasoftware.com) — Google's professional-grade flight search engine. Finds fares often not visible on consumer sites. Requires Playwright (Chromium). No API key needed.
metadata: {"openclaw": {"emoji": "🔭", "requires": {"bins": ["python3"]}}}
---

# ita-matrix

This skill automates a headless Chromium browser to query ITA Matrix (matrix.itasoftware.com), Google's professional-grade flight search engine. It surfaces published fares and itineraries that are frequently absent from consumer booking sites. No API key or account is required.

## Setup

Run once before first use to install Playwright and download the Chromium binary:

```bash
bash {baseDir}/setup.sh
```

## How to Run

```bash
python3 {baseDir}/scripts/search.py <FROM> <TO> [options]
```

### Positional Arguments

| Argument | Description |
|----------|-------------|
| `FROM` | IATA origin airport code (e.g. `JFK`) |
| `TO` | IATA destination airport code (e.g. `LAX`) |

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `-d DATE` | tomorrow | Departure date in `YYYY-MM-DD` format |
| `--return-date DATE` | _(none)_ | Return date for round-trip search (`YYYY-MM-DD`) |
| `--cabin` | `ECONOMY` | Cabin class: `ECONOMY`, `PREMIUM_ECONOMY`, `BUSINESS`, or `FIRST` |
| `--stops` | `any` | Stop filter: `any` or `nonstop` |
| `--adults N` | `1` | Number of adult passengers |
| `--currency CODE` | `USD` | Currency code for displayed prices |
| `--max N` | `20` | Maximum number of results to return |
| `--timeout SECONDS` | `30` | Browser wait timeout in seconds |

## Output Format

Results are written to **stdout** as newline-delimited JSON (NDJSON) — one object per line. Errors and warnings go to **stderr** only and are never mixed into the JSON stream.

Each result follows the shared `FlightResult` schema:

```json
{
  "provider": "ita-matrix",
  "airline": "American Airlines",
  "airline_code": "AA",
  "flight_number": "AA 100",
  "origin": "JFK",
  "destination": "LAX",
  "departure_at": "2026-05-01T08:00:00",
  "arrival_at": "2026-05-01T11:30:00",
  "duration_minutes": 330,
  "stops": 0,
  "layover_airports": [],
  "cabin": "ECONOMY",
  "price": 189.00,
  "currency": "USD",
  "booking_url": null,
  "raw": {}
}
```

`booking_url` is always `null`. ITA Matrix is a search-only tool — it does not support ticket purchase. Use the result as a fare reference on the airline's own site or a GDS-connected OTA.

## Example Invocations

One-way economy, departing tomorrow:

```bash
python3 {baseDir}/scripts/search.py JFK LAX
```

Specific date, nonstop only:

```bash
python3 {baseDir}/scripts/search.py JFK LAX -d 2026-05-01 --stops nonstop
```

Round-trip business class, up to 5 results:

```bash
python3 {baseDir}/scripts/search.py LHR SFO -d 2026-06-10 --return-date 2026-06-20 --cabin BUSINESS --max 5
```

Two adults, first class, prices in JPY:

```bash
python3 {baseDir}/scripts/search.py ORD NRT -d 2026-07-04 --cabin FIRST --adults 2 --currency JPY
```

## Limitations

- **Slower than other providers.** Each search spins up a headless Chromium browser, navigates the full ITA Matrix UI, and waits for dynamic results to render. Expect 15-60 seconds per query depending on network and `--timeout`.
- **booking_url is always null.** ITA Matrix does not expose booking links.
- **DOM selectors may need updating.** If ITA Matrix changes its page layout, the scraper may return zero results and emit a warning to stderr. In that case, update the selectors in `{baseDir}/scripts/search.py` to match the new structure.
- If scraping fails, the script exits with code `0` and emits no result lines, rather than raising an unhandled exception.
