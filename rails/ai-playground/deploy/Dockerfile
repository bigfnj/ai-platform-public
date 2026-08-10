# AI Playground rail backend — torch-free (all generative GPU work is remote via the broker).
# The Embedding Lab adds a CPU/ONNX embedder path (onnxruntime), which needs libgomp at runtime.
# Still no torch, so the image stays modest. Runs the FastAPI factory under one uvicorn worker.
FROM python:3.11-slim
WORKDIR /srv

# onnxruntime's CPU wheel needs libgomp1 (OpenMP) present on Debian slim.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install the package; [project.dependencies] pull fastapi/uvicorn/httpx/numpy/openai +
# onnxruntime/tokenizers/huggingface_hub for the Embedding Lab's CPU provider.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Read-only seed corpora baked into the image (ingested on first boot via the broker).
COPY seed ./seed

# Mutable state (SQLite DB + uploaded docs) lives on a mounted named volume under /srv/var.
# The installed package can't see the repo tree, so DATA_DIR/SEED_DIR are set explicitly.
ENV AI_PLAYGROUND_DATA_DIR=/srv/var \
    AI_PLAYGROUND_SEED_DIR=/srv/seed/corpora \
    AI_PLAYGROUND_SEED_QUERYSETS_DIR=/srv/seed/querysets \
    AI_PLAYGROUND_MODELS_DIR=/srv/var/models \
    AI_PLAYGROUND_BROKER_URL=http://host.docker.internal:11500 \
    AI_PLAYGROUND_OLLAMA_URL=http://host.docker.internal:11434 \
    AI_PLAYGROUND_CHAT_MODEL=@ai-playground \
    AI_PLAYGROUND_EMBED_MODEL=@embed \
    PYTHONUNBUFFERED=1
VOLUME ["/srv/var"]
EXPOSE 8850

# --factory: ai_playground.api:create_api() builds the app. Single worker keeps the
# single-writer SQLite model simple; retrieval/broker round-trips run in the threadpool.
CMD ["uvicorn", "--factory", "ai_playground.api:create_api", "--host", "0.0.0.0", "--port", "8850"]