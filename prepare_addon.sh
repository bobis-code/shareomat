#!/bin/bash
# prepare_addon.sh
#
# Syncs the SELF LEG Python application source into the add-on build
# directory so the HA Supervisor can build it from ha_addon/ as the
# Docker build context.
#
# Run this from the project root before committing or pushing the add-on.
#
# Usage:
#   ./prepare_addon.sh

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
ADDON="$ROOT/ha_addon"

echo "Syncing SELF LEG source to add-on build directory..."

# Python package
rm -rf "$ADDON/self_leg"
cp -r "$ROOT/self_leg" "$ADDON/self_leg"

# Entry point
cp "$ROOT/main.py" "$ADDON/main.py"

echo "Sync complete. Add-on build context: $ADDON"
echo ""
echo "Local build test:"
echo "  docker build -f ha_addon/Dockerfile ."
