# edu-suite dashboard — slim, torch-free image.
#
# After the P2 broker reroute, the dashboard does NO local model work: translate/
# image/tts all go to the native platform GPU/Model Broker over HTTP (BROKER_URL).
# So this image deliberately does NOT install edu-media-core's CUDA/torch stack.
# It copies the source trees and sets PYTHONPATH, installing only the light libs
# the dashboard actually imports at runtime.
#
# Build from the edu-suite repo root:  docker build -f deploy/Dockerfile .
FROM python:3.11-slim

# tesseract: OCR fallback for image-only PDF pages (edu_media_core.pdf).
# tzdata: so the container can render the workstation's local time (TZ env) for
# human-readable output names / job-folder dates instead of UTC.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tzdata \
    && rm -rf /var/lib/apt/lists/*

# Light runtime deps only (NOT torch/diffusers/TTS — those live in the broker).
RUN pip install --no-cache-dir \
        "fastapi>=0.115" "uvicorn[standard]>=0.30" "python-multipart>=0.0.9" \
        "requests>=2.31" "pdfplumber>=0.11.9" "pymupdf>=1.27" "pytesseract>=0.3.13" \
        "pillow>=10.0" "jinja2>=3.1" "numpy>=1.26" "python-docx>=1.1"

WORKDIR /app
# Only the trees the dashboard + its workflows reach (see .dockerignore for excludes).
COPY packages/edu-media-core/src/edu_media_core /app/packages/edu-media-core/src/edu_media_core
COPY apps/cvc-worksheets /app/apps/cvc-worksheets
COPY apps/teachtown /app/apps/teachtown
COPY apps/dashboard /app/apps/dashboard

# edu_media_core + the dashboard package on the path; the workflows self-insert the
# cvc-worksheets / teachtown source dirs relative to their own location.
ENV PYTHONPATH=/app/packages/edu-media-core/src:/app/apps/dashboard/src \
    EDU_LIBRARY_DIR=/library \
    BROKER_URL=http://host.docker.internal:11500 \
    PYTHONUNBUFFERED=1

EXPOSE 8800
CMD ["uvicorn", "dashboard.app:app", "--app-dir", "apps/dashboard/src", \
     "--host", "0.0.0.0", "--port", "8800"]
