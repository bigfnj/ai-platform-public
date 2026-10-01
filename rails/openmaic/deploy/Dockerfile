# OpenMAIC rail backend — a thin wrapper, so a thin image.
#
# This container holds no course data and runs no models. It reverse-proxies the openmaic-app
# container, translates OpenAI calls into broker calls, and answers the header's chip poll.
# Everything heavy lives in those two neighbours.
FROM python:3.11-slim
WORKDIR /srv

# [project.dependencies] pull fastapi/uvicorn/httpx/pydantic-settings.
COPY backend/pyproject.toml ./
COPY backend/openmaic_app ./openmaic_app
RUN pip install --no-cache-dir .

ENV OPENMAIC_BROKER_URL=http://host.docker.internal:11500 \
    OPENMAIC_APP_URL=http://openmaic-app:3000 \
    OPENMAIC_LLM_MODEL=@openmaic \
    OPENMAIC_EMBED_MODEL=@embed \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Non-root: this process proxies untrusted application traffic, so give it nothing to work with
# if the proxy is ever the way in.
RUN useradd --create-home --uid 10001 maic
USER maic

EXPOSE 8900

# One worker. The proxy keeps a single long-lived httpx connection pool to openmaic-app; a
# second worker would hold a second pool with no shared limit, so the app container would see
# twice the configured concurrency under load.
CMD ["uvicorn", "openmaic_app.api.app:app", "--host", "0.0.0.0", "--port", "8900"]
