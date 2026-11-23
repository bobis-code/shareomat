FROM python:3.12-slim

WORKDIR /app

# Libraries WeasyPrint needs for PDF attachments (WeasyPrint docs, Debian >= 11, with wheels),
# and Liberation Sans as the only font (permitted for the QR payment part, SIX IG QR-bill 2.3 §3.4).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data/inbox data/archive data/reports data/state

CMD ["python", "main.py"]
