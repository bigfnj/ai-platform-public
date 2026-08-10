# bouquet backend — a FastAPI surface over the bouquet core, for the platform gateway.
#
# All GPU/model work (vision identification + report writing) goes through the platform
# broker (host.docker.internal:11500), so this image is torch-free and pure-Python wheels
# only (fastapi/uvicorn/httpx/pillow).
#
# Build (from the bouquet rail root):  docker build -f deploy/Dockerfile -t bouquet-backend .
# In the platform stack it's built for you by deploy/docker-compose.yml.
FROM python:3.11-slim

WORKDIR /srv

# Install the package; its [project.dependencies] pull fastapi/uvicorn/httpx/pillow.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# The flower knowledge base: read-only reference data (profiles + references + licensed
# reference photos). Baked into the image and read directly (never written), so it needs
# no volume. BOUQUET_KB_DIR points the app at it.
COPY seed ./seed

# Mutable state (the analyses DB + saved uploaded photos) lives under /srv/var — a mounted
# named volume in compose. Paths are read from BOUQUET_DATA_DIR, so WORKDIR is incidental.
ENV BOUQUET_DATA_DIR=/srv/var \
    BOUQUET_KB_DIR=/srv/seed/knowledge-base \
    BOUQUET_BROKER_URL=http://host.docker.internal:11500 \
    BOUQUET_VISION_MODEL=@vision \
    BOUQUET_DESCRIPTION_MODEL=@chat-large \
    BOUQUET_ANALYSIS_MODEL=@chat-large \
    PYTHONUNBUFFERED=1
VOLUME ["/srv/var"]
EXPOSE 8840

# --factory: bouquet.api:create_api() builds the app. A single worker keeps the
# single-writer SQLite model simple; the pipeline runs in FastAPI's threadpool.
CMD ["uvicorn", "--factory", "bouquet.api:create_api", "--host", "0.0.0.0", "--port", "8840"]
