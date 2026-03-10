#!/bin/bash
set -e
echo "Installing ita-matrix dependencies..."
pip install -r "$(dirname "$0")/requirements.txt"
python -m playwright install chromium
echo "Done. Run: python scripts/search.py JFK LAX -d 2026-05-01"
