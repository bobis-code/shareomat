#!/bin/sh
# run.sh — SELF LEG Add-on entrypoint
set -e

echo "[INFO] SELF LEG Add-on starting..."

# Ensure persistent data directories exist in the HA config area
mkdir -p \
    /config/self_leg/inbox   \
    /config/self_leg/archive \
    /config/self_leg/reports \
    /config/self_leg/state

# Generate leg_config.yaml from add-on options and write env file
echo "[INFO] Generating configuration from add-on options..."
python3 /app/generate_config.py

# Load runtime environment variables produced by generate_config.py
. /tmp/self_leg_env

if [ "${SELF_LEG_DRY_RUN}" = "true" ]; then
    echo "[WARNING] DRY RUN mode enabled — processed files will NOT be archived."
fi

# Set the process timezone so Python datetime matches the configured locale.
export TZ="${SELF_LEG_TZ}"
export SELF_LEG_ERROR_DASHBOARD="true"

echo "[INFO] Timezone : ${SELF_LEG_TZ}"
echo "[INFO] Log level: ${SELF_LEG_LOG_LEVEL}"
echo "[INFO] Config   : ${SELF_LEG_CONFIG_PATH}"
echo "[INFO] Starting SELF LEG engine..."

exec python3 /app/main.py
