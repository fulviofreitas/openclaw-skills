---
name: google-flights
description: Search real-time flight prices and schedules from Google Flights. No API key required. Use when the user asks about flights, airfare, or travel routes.
metadata: {"openclaw": {"emoji": "✈️", "requires": {"bins": ["python3"]}}}
---

# Google Flights Search

This skill scrapes real-time flight prices and schedules from Google Flights using the `fast-flights` library. No API key or account is required. Run the search script directly and parse the newline-delimited JSON results from stdout.

## Running a Search

```bash
python3 {baseDir}/scripts/search.py <FROM> <TO> [options]
```

`FROM` and `TO` are IATA airport codes (e.g. `JFK`, `LAX`, `LHR`).

## CLI Arguments

| Flag                  | Default       | Description                                                    |
|-----------------------|---------------|----------------------------------------------------------------|
| `-d DATE`             | tomorrow      | Departure date as `YYYY-MM-DD`                                 |
| `--return-date DATE`  | (omit)        | Return date as `YYYY-MM-DD`; omit for one-way trips            |
| `--cabin CABIN`       | `ECONOMY`     | One of: `ECONOMY`, `PREMIUM_ECONOMY`, `BUSINESS`, `FIRST`      |
| `--stops STOPS`       | `any`         | One of: `any`, `nonstop`, `one`, `two`                         |
| `--adults N`          | `1`           | Number of adult passengers (1–9)                               |
| `--currency CODE`     | `USD`         | ISO 4217 currency code (e.g. `EUR`, `GBP`)                     |
| `--max N`             | `20`          | Maximum number of results to emit                              |

## Output Format

Each result is written as a single JSON object on its own line to stdout (NDJSON). Errors go to stderr. Example invocation and output:

```bash
python3 {baseDir}/scripts/search.py JFK LAX -d 2026-05-01 --max 2
```

```json
{"provider": "google-flights", "airline": "United", "airline_code": "", "flight_number": "", "origin": "JFK", "destination": "LAX", "departure_at": "2026-05-01T08:00:00", "arrival_at": "2026-05-01T11:30:00", "duration_minutes": 330, "stops": 0, "layover_airports": [], "cabin": "ECONOMY", "price": 299.0, "currency": "USD", "booking_url": null, "raw": {"name": "United", "departure": "8:00 AM", "arrival": "11:30 AM", "arrival_time_ahead": "", "duration": "5 hr 30 min", "stops": 0, "delay": null, "price": "$299", "is_best": true}}
{"provider": "google-flights", "airline": "Delta", "airline_code": "", "flight_number": "", "origin": "JFK", "destination": "LAX", "departure_at": "2026-05-01T09:15:00", "arrival_at": "2026-05-01T12:45:00", "duration_minutes": 330, "stops": 0, "layover_airports": [], "cabin": "ECONOMY", "price": 319.0, "currency": "USD", "booking_url": null, "raw": {"name": "Delta", "departure": "9:15 AM", "arrival": "12:45 PM", "arrival_time_ahead": "", "duration": "5 hr 30 min", "stops": 0, "delay": null, "price": "$319", "is_best": false}}
```

## Round-Trip Support

When `--return-date` is provided, the search is performed as a round-trip. Each result includes:

- `trip_type`: `"round-trip"` or `"one-way"`
- `return_leg`: always `null` — Google Flights via `fast-flights` does not provide separate return leg details, but the `price` reflects the full round-trip total.

## Important Limitations

- `airline_code` and `flight_number` are always empty strings — Google Flights does not expose IATA codes or flight numbers in its public interface.
- `layover_airports` is always an empty list — individual layover codes are not available.
- `booking_url` is always `null` — deep-link booking URLs require a paid API key.
- `departure_at` and `arrival_at` are ISO 8601 local times. When `arrival_time_ahead` is non-empty, the arrival date is automatically advanced by one day.
- `return_leg` is always `null` — individual return flight details are not available from the public interface.

## Setup

If `fast-flights` is not installed, the script will print an error to stderr and exit with code 1. Install dependencies by running:

```bash
bash {baseDir}/setup.sh
```

## Error Handling

- Network or parse failures are printed to stderr; the script exits with code `1`.
- On success, each flight result is printed to stdout and the script exits with code `0`.
- If no results are returned, the script exits with code `0` and produces no output.
