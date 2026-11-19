# Python 3.11 is required by imports such as datetime.UTC.
FROM python:3.11-slim-bookworm

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
    TRANSFORMERS_VERBOSITY=error

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install chromium browser binary for Playwright
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

EXPOSE 8000

# Default entrypoint runs FastAPI server with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
