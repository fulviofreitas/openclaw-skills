---
name: flight-compare
description: Compare flight prices across Google Flights, ITA Matrix, and Amadeus simultaneously. Returns a unified ranked list highlighting the best deals and price differences between providers. Use when the user wants the best flight price or wants to compare across sources.
metadata: {"openclaw": {"emoji": "🔀", "requires": {"bins": ["python3"]}}}
---

# flight-compare

This skill orchestrates the google-flights, ita-matrix, and amadeus sibling skills concurrently, merges their results, deduplicates matching flights, ranks by price, and presents a unified comparison. Run it whenever the user wants the cheapest flight or a cross-source price check.

## Provider availability

Each provider is activated automatically based on what is available in the environment:

- **google-flights** — always active; no configuration required
- **ita-matrix** — active when the `playwright` Python package is installed
- **amadeus** — active when both `AMADEUS_API_KEY` and `AMADEUS_API_SECRET` environment variables are set (`AMADEUS_BASE_URL` is optional; defaults to `https://test.api.amadeus.com`)

Missing sibling skills are handled gracefully — the comparison runs with whichever providers are available.

## Running the skill

```bash
python3 {baseDir}/scripts/compare.py <FROM> <TO> [options]
```

### Positional arguments

| Argument | Description                    | Example |
|----------|--------------------------------|---------|
| `FROM`   | Origin airport IATA code       | `SFO`   |
| `TO`     | Destination airport IATA code  | `JFK`   |

### Options

| Flag                 | Default     | Description                                                          |
|----------------------|-------------|----------------------------------------------------------------------|
| `-d DATE`            | tomorrow    | Departure date in `YYYY-MM-DD` format                                |
| `--return-date DATE` | —           | Return date for round-trip search                                    |
| `--cabin CABIN`      | `ECONOMY`   | Cabin class: `ECONOMY`, `PREMIUM_ECONOMY`, `BUSINESS`, `FIRST`       |
| `--stops STOPS`      | `any`       | Stop filter: `any`, `nonstop`, `one`, `two`                          |
| `--adults N`         | `1`         | Number of adult passengers                                           |
| `--currency CODE`    | `USD`       | Currency code (ISO 4217)                                             |
| `--max N`            | `20`        | Maximum results to return                                            |
| `--providers LIST`   | all active  | Comma-separated subset to query: `google`, `ita`, `amadeus`          |
| `--json`             | —           | Output a JSON array instead of a human-readable table                |

## Output formats

### Default: human-readable table

One-way search:
```
Searching SFO → JFK on 2026-04-15 (Economy, 1 adult)...
Providers: ✓ Google Flights  ✓ ITA Matrix  ✓ Amadeus (test)

 #  AIRLINE        FLIGHT    DEPART  ARRIVE  DURATION  STOPS  PRICE    SOURCE
 1  United         UA 101    07:00   15:22   5h22m     0      $189     Google, Amadeus ★ BEST PRICE
 2  JetBlue        B6 415    08:30   17:05   5h35m     0      $194     Google, ITA Matrix
 3  Delta          DL 409    09:15   17:50   5h35m     0      $201     Google
 4  American       AA 1       10:00   18:30   5h30m     0      $210     Amadeus
 5  Spirit         NK 612    06:00   16:45   7h45m     1      $149     ITA Matrix

12 results from 3 providers | Google: 7  ITA: 5  Amadeus: 4 | 4 duplicates merged
```

Round-trip search (with `--return-date`):
```
Searching SFO → JFK ↩ 2026-04-22 on 2026-04-15 (Economy, 1 adult)...
Providers: ✓ Google Flights  ✓ Amadeus (test)

 #  AIRLINE        FLIGHT    DEPART  ARRIVE  DURATION  STOPS  PRICE    SOURCE
 1  United         UA 101    07:00   15:22   5h22m     0      $378     Amadeus ★ BEST PRICE | Return: UA204 08:30→11:45
 2  Delta          DL 409    09:15   17:50   5h35m     0      $401     Google

8 results from 2 providers | Google: 5  Amadeus: 3 | 0 duplicates merged
```

When Amadeus is available, round-trip results include return leg details (flight number, times) shown as annotations. Google Flights and ITA Matrix show round-trip total prices but cannot provide individual return flight details.

### --json: JSON array

Each element in the array includes a `sources` list and a `price_range` object:

```json
{
  "airline": "United",
  "airline_code": "UA",
  "flight_number": "101",
  "origin": "SFO",
  "destination": "JFK",
  "departure_time": "07:00",
  "arrival_time": "15:22",
  "departure_date": "2026-04-15",
  "duration_minutes": 322,
  "stops": 0,
  "price": 189.0,
  "currency": "USD",
  "cabin": "ECONOMY",
  "sources": ["Google", "Amadeus"],
  "price_range": {"min": 189.0, "max": 192.0, "currency": "USD"}
}
```

## Deduplication logic

Two results from different providers are considered the same flight and merged when all of the following match:

- `airline_code` + `flight_number`
- `departure_date`
- `origin` and `destination`
- `departure_time` within ±15 minutes

Merged entries report every source that returned them in the `sources` field and carry a `price_range` reflecting the spread across those sources.

## Setup

Run the skill's own setup script first:

```bash
bash {baseDir}/setup.sh
```

This installs `aiohttp` and prints instructions for enabling optional providers. For full three-provider coverage also run setup for each sibling skill:

```bash
bash {baseDir}/../google-flights/setup.sh
bash {baseDir}/../ita-matrix/setup.sh   # installs playwright
bash {baseDir}/../amadeus/setup.sh      # prompts for API credentials
```
