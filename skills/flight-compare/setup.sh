#!/bin/bash
set -e
echo "Installing flight-compare dependencies..."
pip install -r "$(dirname "$0")/requirements.txt"
echo ""
echo "flight-compare orchestrates these sibling skills:"
echo "  • google-flights  (always active — no config needed)"
echo "  • ita-matrix      (active if playwright is installed)"
echo "  • amadeus         (active if AMADEUS_API_KEY + AMADEUS_API_SECRET are set)"
echo ""
echo "To enable all providers, run setup.sh in each sibling skill directory."
