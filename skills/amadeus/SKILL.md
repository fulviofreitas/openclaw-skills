---
name: amadeus
description: Search real-time flight prices, schedules, and availability via the Amadeus GDS API. Access to fares not always visible on consumer search engines. Requires a free Amadeus API key.
metadata: {"openclaw": {"emoji": "🛰️", "requires": {"bins": ["python3"], "env": ["AMADEUS_API_KEY", "AMADEUS_API_SECRET"]}, "primaryEnv": "AMADEUS_API_KEY"}}
---

# Amadeus Flight Search Skill

This skill provides a command-line client for the Amadeus Global Distribution System (GDS) API. Use it to search real-time flight prices, schedules, and seat availability. Results are emitted as NDJSON to stdout, making them composable with other skills and tools.

## Setup

Run once to install Python dependencies:

```bash
bash {baseDir}/setup.sh
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `AMADEUS_API_KEY` | Yes | API key from https://developers.amadeus.com |
| `AMADEUS_API_SECRET` | Yes | API secret from https://developers.amadeus.com |
| `AMADEUS_BASE_URL` | No | Defaults to `https://test.api.amadeus.com`. Set to `https://api.amadeus.com` for production fares. |

## Credential Setup

1. Go to https://developers.amadeus.com and sign up for a free account.
2. Create a new app and enable the "Flight Offers Search" API.
3. Copy your API Key and API Secret from the app dashboard.
4. Export the credentials in your shell:
   ```bash
   export AMADEUS_API_KEY=your_key_here
   export AMADEUS_API_SECRET=your_secret_here
   ```
5. Optionally set the base URL for production access (requires Amadeus production approval):
   ```bash
   export AMADEUS_BASE_URL=https://api.amadeus.com
   ```

Note: the default test environment (`https://test.api.amadeus.com`) returns simulated data. It is safe to use for development and integration testing without incurring costs or affecting live inventory.

## Running the Skill

```bash
python3 {baseDir}/scripts/search.py <FROM> <TO> [options]
```

### Positional Arguments

| Argument | Description |
|---|---|
| `FROM` | Origin IATA airport code (e.g. `JFK`) |
| `TO` | Destination IATA airport code (e.g. `LHR`) |

### Optional Arguments

| Flag | Default | Description |
|---|---|---|
| `-d DATE` | Tomorrow | Departure date (`YYYY-MM-DD`) |
| `--return-date DATE` | (none) | Return date for round-trip search (`YYYY-MM-DD`) |
| `--cabin CLASS` | `ECONOMY` | Cabin class: `ECONOMY`, `PREMIUM_ECONOMY`, `BUSINESS`, `FIRST` |
| `--stops TYPE` | `any` | Stop filter: `any` or `nonstop` |
| `--adults N` | `1` | Number of adult passengers |
| `--currency CODE` | `USD` | Pricing currency (e.g. `USD`, `EUR`, `GBP`) |
| `--max N` | `20` | Maximum number of results to return |

## Example Invocations

One-way economy search departing tomorrow:
```bash
python3 {baseDir}/scripts/search.py JFK LHR
```

Specific date, business class, nonstop only:
```bash
python3 {baseDir}/scripts/search.py SFO NRT -d 2026-04-15 --cabin BUSINESS --stops nonstop
```

Round-trip with 2 adults, prices in EUR:
```bash
python3 {baseDir}/scripts/search.py CDG BOS -d 2026-05-01 --return-date 2026-05-10 --adults 2 --currency EUR
```

## Output Format

Each result is written as one JSON object per line (NDJSON) to stdout. Errors are written to stderr. Example record:

```json
{"provider": "amadeus", "airline": "British Airways", "airline_code": "BA", "flight_number": "BA178", "origin": "JFK", "destination": "LHR", "departure_at": "2026-03-11T19:05:00", "arrival_at": "2026-03-12T07:15:00", "duration_minutes": 430, "stops": 0, "layover_airports": [], "cabin": "ECONOMY", "price": 542.30, "currency": "USD", "booking_url": null, "raw": {}}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `provider` | string | Always `"amadeus"` |
| `airline` | string | Full carrier name resolved from API dictionaries |
| `airline_code` | string | IATA code of the validating airline |
| `flight_number` | string | Carrier code + flight number of the first segment |
| `origin` | string | Departure IATA airport code |
| `destination` | string | Final arrival IATA airport code |
| `departure_at` | string | ISO 8601 departure datetime of the first segment |
| `arrival_at` | string | ISO 8601 arrival datetime of the last segment |
| `duration_minutes` | integer | Total itinerary duration parsed from ISO 8601 duration |
| `stops` | integer | Number of stops (segments minus one) |
| `layover_airports` | array | IATA codes of intermediate airports |
| `cabin` | string | Cabin class of the first segment |
| `price` | float | Total price for all passengers |
| `currency` | string | Currency of the quoted price |
| `booking_url` | null | Not supported — booking must be completed via an agent |
| `raw` | object | Full raw offer object from the Amadeus API response |

## Behaviour Notes

- OAuth2 access tokens are fetched fresh on every invocation and are never written to disk.
- Multi-leg itineraries emit one result per outbound itinerary.
- This skill performs read-only searches. No booking or PII handling occurs.
- Production access requires additional approval from Amadeus. See https://developers.amadeus.com/get-started/get-started-with-amadeus-apis-334
