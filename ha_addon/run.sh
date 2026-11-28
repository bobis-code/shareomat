#!/bin/sh
# run.sh — Shareomat Add-on entrypoint
set -e

echo "[INFO] Shareomat Add-on starting..."

# Ensure the persistent data directory exists in the HA config area
# (inbox/archive/reports went with the CSV path in 0.6.34; old ones are left alone).
mkdir -p /config/shareomat/state

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
