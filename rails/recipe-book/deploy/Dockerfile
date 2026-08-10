# recipe-book backend — a FastAPI surface over the recipe-book core, for the platform gateway.
#
# All GPU/model work (LLM chat, embeddings, SDXL recipe icons) goes through the platform
# broker (host.docker.internal:11500), so this image is torch-free and pure-Python wheels only.
#
# Build (from the recipe-book repo root):  docker build -f deploy/Dockerfile -t recipe-book-backend .
# In the platform stack it's built for you by platform/deploy/docker-compose.yml.
FROM python:3.11-slim

WORKDIR /srv

# Install the package; its [project.dependencies] pull fastapi/uvicorn/httpx/python-multipart.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Committed seed corpus (the markdown source of truth). Baked into the image so a fresh/empty
# volume self-populates on first run (see recipe_book.seed). Not the working store — that's the
# /srv/var volume below; the DB + icons regenerate from this corpus.
COPY seed ./seed

# All mutable state lives under var/ (a mounted named volume in compose): recipe_book.db is the
# working store, recipes/ holds the markdown corpus that can rebuild it, icons/ holds generated
# SDXL clipart. Paths are read from RECIPE_BOOK_DATA_DIR, so WORKDIR is incidental.
ENV RECIPE_BOOK_DATA_DIR=/srv/var \
    RECIPE_BOOK_SEED_DIR=/srv/seed/recipes \
    RECIPE_BOOK_SEED_ICONS=/srv/seed/icons.tgz \
    RECIPE_BOOK_BROKER_URL=http://host.docker.internal:11500 \
    RECIPE_BOOK_LLM_MODEL=mistral-small3*:24b \
    PYTHONUNBUFFERED=1
VOLUME ["/srv/var"]
EXPOSE 8830

# --factory: recipe_book.api:create_api() builds the app. A single worker keeps the
# single-writer SQLite model simple; imports/rebuilds run in FastAPI's threadpool.
CMD ["uvicorn", "--factory", "recipe_book.api:create_api", "--host", "0.0.0.0", "--port", "8830"]