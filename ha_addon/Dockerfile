FROM python:3.12-alpine

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy SELF LEG application source.
# Build context must be the project root (not the add-on subdirectory).
# For HA Supervisor builds: run prepare_addon.sh first so that
# self_leg/ and main.py are present in the add-on directory.
# For local builds: docker build -f self_leg_addon/self_leg/Dockerfile .
COPY self_leg/ ./self_leg/
COPY main.py generate_config.py ./

# Entrypoint
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
