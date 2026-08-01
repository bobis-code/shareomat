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

# Generate the technical runtime config from add-on options and write env file.
# Community/participants/meters/tariffs are NOT generated here — they live in
# the SQLite admin database, managed through the Shareomat web interface.
echo "[INFO] Generating runtime configuration from add-on options..."
python3 /app/generate_runtime_config.py

# Load runtime environment variables produced by generate_runtime_config.py
. /tmp/shareomat_env

# Set the process timezone so Python datetime matches the configured locale.
export TZ="${SHAREOMAT_TZ}"

echo "[INFO] Timezone : ${SHAREOMAT_TZ}"
echo "[INFO] Log level: ${SHAREOMAT_LOG_LEVEL}"
echo "[INFO] Config   : ${SHAREOMAT_CONFIG_PATH}"
echo "[INFO] Database : ${SHAREOMAT_DB_PATH}"
echo "[INFO] Starting Shareomat engine..."

exec python3 /app/main.py
