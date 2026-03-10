# openclaw-skills

Search and compare flight prices across multiple providers from your terminal.

## What is this?

A suite of OpenClaw skills for searching and comparing flight prices across multiple providers. Each skill wraps a different flight data source -- Google Flights, ITA Matrix, and Amadeus -- and the `flight-compare` skill orchestrates all of them to surface the lowest fares automatically. Install one provider or all of them depending on your needs.

## Quick Start

Install `flight-compare` and run your first search:

```bash
npx skills add fulviofreitas/openclaw-skills/skills/flight-compare
# Then in OpenClaw:
python3 skills/flight-compare/scripts/compare.py SFO JFK -d 2026-05-01
```

That's it. `flight-compare` queries every available provider and returns results ranked by price.

## Install Individual Skills

You can install each provider skill independently:

```bash
npx skills add fulviofreitas/openclaw-skills/skills/google-flights
npx skills add fulviofreitas/openclaw-skills/skills/ita-matrix
npx skills add fulviofreitas/openclaw-skills/skills/amadeus
npx skills add fulviofreitas/openclaw-skills/skills/flight-compare
```

## Provider Comparison

| Provider | API Key? | Data Source | Speed | Best For |
|---|---|---|---|---|
| Google Flights | No | Live consumer fares | Fast | Everyday fare searches |
| ITA Matrix | No | Live pro-grade fares | Slower | Hidden/complex fare discovery |
| Amadeus | Yes (free) | GDS/live | Fast | Professional GDS data |

## Setup

Run the setup script for each skill you plan to use:

**Google Flights** -- installs the `fast-flights` library. No API key needed.

```bash
bash skills/google-flights/setup.sh
```

**ITA Matrix** -- installs Playwright and downloads a Chromium browser. No API key needed.

```bash
bash skills/ita-matrix/setup.sh
```

**Amadeus** -- requires API credentials before running setup. See the next section for how to get them.

```bash
bash skills/amadeus/setup.sh
```

## Amadeus Credentials

1. Go to [https://developers.amadeus.com](https://developers.amadeus.com)
2. Sign up for a free account
3. Create an app and select the "Flight Offers Search" API
4. Copy your API Key and API Secret
5. Set the environment variables:

```bash
export AMADEUS_API_KEY=your_key
export AMADEUS_API_SECRET=your_secret
# Optional: switch to production fares (default is test/sandbox)
export AMADEUS_BASE_URL=https://api.amadeus.com
```

## Usage Examples

**Basic one-way search:**

```bash
python3 skills/google-flights/scripts/search.py JFK LAX -d 2026-05-01
```

**Round-trip business class:**

```bash
python3 skills/google-flights/scripts/search.py SFO NRT -d 2026-06-01 --return-date 2026-06-15 --cabin BUSINESS
```

**Compare prices across all providers:**

```bash
python3 skills/flight-compare/scripts/compare.py SFO JFK -d 2026-04-15
```

**JSON output for scripting:**

```bash
python3 skills/flight-compare/scripts/compare.py SFO JFK -d 2026-04-15 --json
```

**Restrict to specific providers:**

```bash
python3 skills/flight-compare/scripts/compare.py SFO JFK -d 2026-04-15 --providers google,amadeus
```

## Repo Structure

```
openclaw-skills/
├── README.md
└── skills/
    ├── google-flights/    # No API key needed
    ├── ita-matrix/        # No API key, needs Playwright
    ├── amadeus/           # Free API key required
    └── flight-compare/    # Orchestrates all providers
```

## Security

- No hardcoded credentials -- all secrets are passed via environment variables
- All dependencies pinned to exact versions
- Search only -- no autonomous booking or purchases
- No PII collected, stored, or transmitted
- All SKILL.md files contain no prompt injection

## Contributing

1. Fork this repository
2. Create a feature branch
3. Make your changes
4. Ensure all security requirements above are met
5. Open a pull request

## License

MIT
