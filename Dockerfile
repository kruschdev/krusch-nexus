FROM python:3.11-slim

WORKDIR /app

# Install system dependencies: build tools, PostgreSQL client libs, Poppler utilities, and Tesseract OCR
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    git \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml alembic.ini ./
COPY alembic/ alembic/
COPY src/ src/
RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "krusch_nexus.api:app", "--host", "0.0.0.0", "--port", "8000"]
