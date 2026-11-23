FROM python:3.12-alpine

WORKDIR /app

# Libraries WeasyPrint needs for PDF attachments (WeasyPrint docs, "Alpine >= 3.17", with wheels),
# and Liberation Sans as the only font: SIX IG QR-bill 2.3 §3.4 permits Arial, Frutiger,
# Helvetica and Liberation Sans, so every fallback lands on a permitted font.
RUN apk add --no-cache \
        so:libgobject-2.0.so.0 so:libpango-1.0.so.0 so:libharfbuzz.so.0 so:libharfbuzz-subset.so.0 \
        so:libfontconfig.so.1 so:libpangoft2-1.0.so.0 \
        font-liberation

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy Shareomat application source from the add-on build context.
# The canonical source is ./shareomat and ./main.py in the project root.
# Run ./prepare_addon.sh before building or publishing the add-on so the
# generated add-on copy is current.
COPY shareomat/ ./shareomat/
COPY main.py generate_runtime_config.py ./

# Entrypoint
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
