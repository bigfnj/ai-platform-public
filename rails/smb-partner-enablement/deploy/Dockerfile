# SMB Partner Enablement rail backend — torch-free. All model work is remote via the broker,
# and retrieval is numpy cosine over vectors the broker produced, so the image stays small.
FROM python:3.11-slim
WORKDIR /srv

# [project.dependencies] pull fastapi/uvicorn/httpx/numpy/websockets.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Read-only SME knowledge base baked into the image; ingested on first boot via the broker.
# Bind-mount over /srv/seed/knowledge-base to iterate on content without a rebuild.
COPY seed ./seed

# Mutable state (chunk index + uploaded docs) lives on a mounted named volume under /srv/var.
# The installed package can't see the repo tree, so DATA_DIR/SEED_DIR are set explicitly.
ENV SMB_PARTNER_DATA_DIR=/srv/var \
    SMB_PARTNER_SEED_DIR=/srv/seed/knowledge-base \
    SMB_PARTNER_BROKER_URL=http://host.docker.internal:11500 \
    SMB_PARTNER_RAG_MODEL=@smb-partner-rag \
    SMB_PARTNER_EMBED_MODEL=@embed \
    PYTHONUNBUFFERED=1
VOLUME ["/srv/var"]
EXPOSE 8870

# Single worker keeps the single-writer SQLite model simple; the retrieval matrix is per-process
# in-memory state, so a second worker would hold a second copy and diverge after an ingest.
CMD ["uvicorn", "--factory", "smb_partner.api:create_api", "--host", "0.0.0.0", "--port", "8870"]