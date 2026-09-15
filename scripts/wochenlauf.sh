#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/oberstaufen.py "$@"
python3 -m unittest discover -s tests
python3 scripts/pruefen.py
