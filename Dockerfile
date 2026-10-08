FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/render/project/src

# Keep the existing PDF extraction and OCR stack available in production.
RUN apt-get update \
    && apt-get install --no-install-recommends -y poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["sh", "-c", "python -m app.db.init_db && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
