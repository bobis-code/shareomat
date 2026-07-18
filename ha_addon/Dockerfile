FROM python:3.12-alpine

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy Shareomat application source from the add-on build context.
# The canonical source is ./shareomat and ./main.py in the project root.
# Run ./prepare_addon.sh before building or publishing the add-on so the
# generated add-on copy is current.
COPY shareomat/ ./shareomat/
COPY main.py generate_config.py ./

# Entrypoint
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
