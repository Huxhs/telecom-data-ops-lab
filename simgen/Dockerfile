# Container image for the simgen worker.
FROM python:3.12-slim

# Run as an unprivileged user (uid 1001 matches the SFTP user in compose).
RUN useradd --uid 1001 --create-home simgen \
 && mkdir -p /data/inbox /data/processing /data/outbox /data/archive /data/error /logs /keys \
 && chown -R simgen:simgen /data /logs /keys

WORKDIR /app
COPY pyproject.toml ./
COPY simgen ./simgen
RUN pip install --no-cache-dir ".[postgres,oracle]"

USER simgen
ENV SIMGEN_ROOT=/data SIMGEN_LOG=/logs/events.log SIMGEN_KEYS=/keys/lab-keys.json PYTHONUNBUFFERED=1
CMD ["simgen", "watch"]
