"""Flight price comparison orchestrator.

Launches enabled provider scripts (google-flights, ita-matrix, amadeus) as
concurrent subprocesses, collects their newline-delimited JSON output, merges
and deduplicates results, then prints a ranked table or JSON array.

Usage:
    python compare.py <FROM> <TO> [options]

Run ``python compare.py --help`` for the full option reference.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(SKILL_DIR)

PROVIDER_SCRIPTS: dict[str, str] = {
    "google": os.path.join(REPO_ROOT, "google-flights", "scripts", "search.py"),
    "ita": os.path.join(REPO_ROOT, "ita-matrix", "scripts", "search.py"),
    "amadeus": os.path.join(REPO_ROOT, "amadeus", "scripts", "search.py"),
}

# Display names used in output and summary counts
PROVIDER_DISPLAY: dict[str, str] = {
    "google": "Google",
    "ita": "ITA Matrix",
    "amadeus": "Amadeus",
}

# ---------------------------------------------------------------------------
# Provider availability detection
# ---------------------------------------------------------------------------


def _playwright_available() -> bool:
    """Return True if the ``playwright`` package can be imported.

    Returns:
        True when playwright is importable, False otherwise.
    """
    try:
        import importlib

        importlib.import_module("playwright")
        return True
    except ImportError:
        return False


def _amadeus_configured() -> bool:
    """Return True when both Amadeus credentials are present in the environment.

    Returns:
        True when ``AMADEUS_API_KEY`` and ``AMADEUS_API_SECRET`` are both set
        to non-empty strings.
    """
    return bool(os.environ.get("AMADEUS_API_KEY")) and bool(
        os.environ.get("AMADEUS_API_SECRET")
    )


def _amadeus_env_label() -> str:
    """Return a parenthetical label for the Amadeus environment.

    Returns:
        ``" (test)"`` or ``" (production)"`` based on ``AMADEUS_BASE_URL``.
    """
    base_url = os.environ.get("AMADEUS_BASE_URL", "https://test.api.amadeus.com")
    if "test." in base_url:
        return " (test)"
    return " (production)"


def _determine_available_providers(
    requested: list[str] | None,
) -> list[str]:
    """Build the list of provider keys that should be queried.

    Applies both availability rules (playwright for ita, credentials for
    amadeus) and optional caller-requested filtering.  Providers whose scripts
    do not exist on disk are silently skipped here; the warning is emitted
    later in the async runner.

    Args:
        requested: Optional explicit list of provider keys from ``--providers``.
                   When None, all available providers are used.

    Returns:
        Ordered list of provider keys: google first, then ita, then amadeus.
    """
    availability: dict[str, bool] = {
        "google": True,
        "ita": _playwright_available(),
        "amadeus": _amadeus_configured(),
    }

    if requested is not None:
        # Filter to the requested subset, still subject to availability
        enabled = [p for p in requested if availability.get(p, False)]
        unavailable = [p for p in requested if not availability.get(p, False)]
        for p in unavailable:
            print(
                f"Warning: provider '{p}' requested but not available "
                f"(missing dependencies or credentials) — skipping.",
                file=sys.stderr,
            )
        return enabled

    return [p for p, available in availability.items() if available]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Construct and return the argument parser for compare.py.

    Returns:
        Configured :class:`argparse.ArgumentParser` instance.
    """
    parser = argparse.ArgumentParser(
        prog="compare.py",
        description=(
            "Compare flight prices across Google Flights, ITA Matrix, and "
            "Amadeus simultaneously."
        ),
    )
    parser.add_argument("origin", metavar="FROM", help="Origin airport IATA code")
    parser.add_argument("destination", metavar="TO", help="Destination airport IATA code")
    parser.add_argument(
        "-d",
        "--date",
        dest="date",
        default=str(date.today() + timedelta(days=1)),
        help="Departure date YYYY-MM-DD (default: tomorrow)",
    )
    parser.add_argument(
        "--return-date",
        dest="return_date",
        default=None,
        help="Return date YYYY-MM-DD for round-trip searches",
    )
    parser.add_argument(
        "--cabin",
        dest="cabin",
        default="ECONOMY",
        choices=["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"],
        help="Cabin class (default: ECONOMY)",
    )
    parser.add_argument(
        "--stops",
        dest="stops",
        default="any",
        choices=["any", "nonstop", "one", "two"],
        help="Stop filter (default: any)",
    )
    parser.add_argument(
        "--adults",
        dest="adults",
        type=int,
        default=1,
        help="Number of adult passengers (default: 1)",
    )
    parser.add_argument(
        "--currency",
        dest="currency",
        default="USD",
        help="Currency code ISO 4217 (default: USD)",
    )
    parser.add_argument(
        "--max",
        dest="max_results",
        type=int,
        default=20,
        help="Maximum results to return (default: 20)",
    )
    parser.add_argument(
        "--providers",
        dest="providers",
        default=None,
        help=(
            "Comma-separated provider subset to query: google, ita, amadeus "
            "(default: all available)"
        ),
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output a JSON array instead of a human-readable table",
    )
    return parser


# ---------------------------------------------------------------------------
# Provider subprocess runner
# ---------------------------------------------------------------------------


def _build_provider_argv(
    provider_key: str,
    args: argparse.Namespace,
) -> list[str]:
    """Build the argv list to pass to a provider's search.py.

    All relevant CLI arguments are forwarded so that each provider receives
    the same search parameters.

    Args:
        provider_key: One of ``"google"``, ``"ita"``, ``"amadeus"``.
        args: Parsed arguments from the compare.py CLI.

    Returns:
        List of strings suitable for ``asyncio.create_subprocess_exec``.
    """
    script = PROVIDER_SCRIPTS[provider_key]
    argv = [sys.executable, script, args.origin, args.destination]

    argv += ["-d", args.date]

    if args.return_date:
        argv += ["--return-date", args.return_date]

    argv += ["--cabin", args.cabin]
    argv += ["--stops", args.stops]
    argv += ["--adults", str(args.adults)]
    argv += ["--currency", args.currency]
    argv += ["--max", str(args.max_results)]

    return argv


async def _run_provider(
    provider_key: str,
    args: argparse.Namespace,
) -> tuple[str, list[dict[str, Any]]]:
    """Launch a single provider subprocess and parse its NDJSON output.

    Args:
        provider_key: Provider identifier (``"google"``, ``"ita"``, ``"amadeus"``).
        args: Parsed CLI arguments to forward to the provider.

    Returns:
        A tuple of ``(display_name, list_of_result_dicts)``.  On any error the
        list will be empty; warnings are printed to stderr.
    """
    display = PROVIDER_DISPLAY[provider_key]
    script_path = PROVIDER_SCRIPTS[provider_key]

    if not os.path.isfile(script_path):
        print(
            f"Warning: {display} script not found at {script_path!r} — skipping.",
            file=sys.stderr,
        )
        return display, []

    argv = _build_provider_argv(provider_key, args)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
    except OSError as exc:
        print(
            f"Warning: failed to launch {display} ({exc}) — skipping.",
            file=sys.stderr,
        )
        return display, []

    if stderr_bytes:
        # Forward any provider-level warnings to our own stderr
        stderr_text = stderr_bytes.decode(errors="replace").rstrip()
        if stderr_text:
            print(f"[{display}] {stderr_text}", file=sys.stderr)

    results: list[dict[str, Any]] = []
    raw_stdout = stdout_bytes.decode(errors="replace")

    # The output may be a JSON array or newline-delimited JSON objects
    raw_stdout = raw_stdout.strip()
    if not raw_stdout:
        return display, []

    # Try to parse as a JSON array first
    if raw_stdout.startswith("["):
        try:
            parsed = json.loads(raw_stdout)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        item.setdefault("source", display)
                        results.append(item)
                return display, results
        except json.JSONDecodeError:
            pass  # fall through to line-by-line parsing

    # Newline-delimited JSON (NDJSON)
    for lineno, line in enumerate(raw_stdout.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                obj.setdefault("source", display)
                results.append(obj)
            else:
                print(
                    f"Warning: {display} line {lineno} is not a JSON object — skipping.",
                    file=sys.stderr,
                )
        except json.JSONDecodeError as exc:
            print(
                f"Warning: {display} line {lineno} is malformed JSON ({exc}) — skipping.",
                file=sys.stderr,
            )

    return display, results


# ---------------------------------------------------------------------------
# Orchestration entry point
# ---------------------------------------------------------------------------


async def _main() -> int:
    """Parse arguments, run providers concurrently, merge, and output results.

    Returns:
        Exit code: 0 on success, 1 when all providers failed or no results.
    """
    parser = _build_parser()
    args = parser.parse_args()

    # Resolve provider list
    requested: list[str] | None = None
    if args.providers:
        requested = [p.strip().lower() for p in args.providers.split(",") if p.strip()]

    active_providers = _determine_available_providers(requested)

    if not active_providers:
        print(
            "Error: no providers are available. "
            "Ensure google-flights is installed and any optional credentials are set.",
            file=sys.stderr,
        )
        return 1

    # Print header unless outputting raw JSON
    if not args.output_json:
        cabin_display = args.cabin.replace("_", " ").title()
        adult_label = f"{args.adults} adult" + ("s" if args.adults != 1 else "")
        print(
            f"Searching {args.origin.upper()} \u2192 {args.destination.upper()}"
            f" on {args.date} ({cabin_display}, {adult_label})..."
        )

        # Build provider status line with availability markers
        provider_label_map = {
            "google": "Google Flights",
            "ita": "ITA Matrix",
            "amadeus": "Amadeus" + _amadeus_env_label(),
        }
        status_parts = [
            f"\u2713 {provider_label_map[p]}" for p in active_providers
        ]
        print("Providers: " + "  ".join(status_parts))
        print()

    # Run all providers concurrently
    tasks = [_run_provider(p, args) for p in active_providers]
    provider_results: list[tuple[str, list[dict[str, Any]]]] = await asyncio.gather(
        *tasks
    )

    # Aggregate results and per-provider counts
    all_results: list[dict[str, Any]] = []
    provider_counts: dict[str, int] = {}

    for display_name, results in provider_results:
        if results:
            provider_counts[display_name] = len(results)
            all_results.extend(results)

    if not all_results:
        print(
            "Error: all providers returned no results or failed.",
            file=sys.stderr,
        )
        return 1

    # Import merge module from the same scripts directory
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    from merge import format_json, format_table, merge_results  # noqa: PLC0415

    merged = merge_results(all_results)

    # Apply --max cap after merging
    if args.max_results and len(merged) > args.max_results:
        merged = merged[: args.max_results]

    if args.output_json:
        print(format_json(merged))
    else:
        table = format_table(
            results=merged,
            origin=args.origin.upper(),
            dest=args.destination.upper(),
            date=args.date,
            cabin=args.cabin,
            adults=args.adults,
            provider_counts=provider_counts,
        )
        print(table)

    return 0


def main() -> None:
    """Synchronous entry point — wraps :func:`_main` with ``asyncio.run``."""
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
