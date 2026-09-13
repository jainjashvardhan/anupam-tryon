FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static/ ./static/

# Cloud Run injects PORT (8080 by default); fall back to 8080 when run locally.
ENV PORT=8080
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}
