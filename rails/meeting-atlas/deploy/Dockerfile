# Meeting Atlas backend. Base matches the other rails (python:3.11-slim = Debian
# bookworm; already cached on the host).
#
# Build (from the rail root):  docker build -f deploy/Dockerfile -t meeting-atlas-backend .
# In the platform stack it is built for you by deploy/docker-compose.yml.
#
# Deliberately thin: this rail does no inference, has no GPU path, and needs no system
# packages beyond what python:3.11-slim already carries. Its only dependencies are
# FastAPI, pydantic-settings and tzdata.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# The backend package (pyproject pulls fastapi/uvicorn/pydantic-settings/tzdata).
COPY backend /app/backend
RUN pip install --no-cache-dir /app/backend

# Non-root runtime user. The recordings mount is read-only, so this user needs no
# write access anywhere outside /tmp — the index lives in memory.
RUN useradd --create-home --uid 10002 atlas \
    && mkdir -p /data/recordings && chown -R atlas:atlas /data

USER atlas
WORKDIR /home/atlas

EXPOSE 8740
CMD ["uvicorn", "meeting_atlas_app.main:app", "--host", "0.0.0.0", "--port", "8740"]
