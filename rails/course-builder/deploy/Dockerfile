FROM python:3.11-slim

WORKDIR /srv

COPY backend/pyproject.toml ./
COPY backend/course_builder_app/ ./course_builder_app/

RUN pip install --no-cache-dir .

# Non-root user; same uid convention as other rails.
RUN useradd -u 10001 -m cb
USER cb

EXPOSE 8901
CMD ["uvicorn", "course_builder_app.api.app:app", "--host", "0.0.0.0", "--port", "8901"]
