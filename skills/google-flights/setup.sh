#!/bin/bash
set -e
echo "Installing google-flights dependencies..."
pip install -r "$(dirname "$0")/requirements.txt"
echo "Done. Run: python scripts/search.py JFK LAX -d 2026-05-01"
