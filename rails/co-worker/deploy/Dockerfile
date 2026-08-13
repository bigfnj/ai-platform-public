# Co-Worker rail backend.
# Base matches the other rails (python:3.11-slim = Debian bookworm).
#
# Build (from the co-worker rail root):
#   docker build -f deploy/Dockerfile -t co-worker-backend .
# In the platform stack the compose file builds it.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "pydantic>=2.7" \
    "pydantic-settings>=2.2"

COPY backend /app/backend
RUN pip install --no-cache-dir /app/backend

# Non-root runtime user.
# /data/inbox is the mount point for the drop-zone volume/bind-mount.
RUN useradd --create-home --uid 10001 coworker \
    && mkdir -p /data/inbox && chown -R coworker:coworker /data

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER coworker
WORKDIR /home/coworker

EXPOSE 8860
CMD ["uvicorn", "co_worker_app.main:app", "--host", "0.0.0.0", "--port", "8860"]
