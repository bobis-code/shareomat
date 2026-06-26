#!/bin/bash
# Sync the canonical SELF LEG source into the Home Assistant add-on build
# directory. The Python implementation is developed in ./self_leg and ./main.py;
# ha_addon/self_leg and ha_addon/main.py are release/build copies.

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

python3 "$ROOT/tools/prepare_addon.py" "$@"

echo ""
echo "Local add-on build test:"
echo "  docker build -f ha_addon/Dockerfile ha_addon"
