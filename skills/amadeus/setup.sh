#!/bin/bash
set -e
echo "Installing amadeus dependencies..."
pip install -r "$(dirname "$0")/requirements.txt"
echo ""
echo "Done. Before using, set your credentials:"
echo "  export AMADEUS_API_KEY=your_key"
echo "  export AMADEUS_API_SECRET=your_secret"
echo "Get free credentials at: https://developers.amadeus.com"
