#!/bin/sh
# run.sh — Shareomat Add-on entrypoint
set -e

echo "[INFO] Shareomat Add-on starting..."

# Ensure persistent data directories exist in the HA config area
mkdir -p \
    /config/shareomat/inbox   \
    /config/shareomat/archive \
    /config/shareomat/reports \
    /config/shareomat/state

# Generate leg_config.yaml from add-on options and write env file
echo "[INFO] Generating configuration from add-on options..."
python3 /app/generate_config.py

# Load runtime environment variables produced by generate_config.py
. /tmp/shareomat_env

if [ "${SHAREOMAT_DRY_RUN}" = "true" ]; then
    echo "[WARNING] DRY RUN mode enabled — processed files will NOT be archived."
fi

# Set the process timezone so Python datetime matches the configured locale.
export TZ="${SHAREOMAT_TZ}"
export SHAREOMAT_ERROR_DASHBOARD="true"

echo "[INFO] Timezone : ${SHAREOMAT_TZ}"
echo "[INFO] Log level: ${SHAREOMAT_LOG_LEVEL}"
echo "[INFO] Config   : ${SHAREOMAT_CONFIG_PATH}"
echo "[INFO] Starting Shareomat engine..."

exec python3 /app/main.py
