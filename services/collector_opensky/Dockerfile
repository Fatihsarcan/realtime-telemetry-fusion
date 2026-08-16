# Build context: repo koku (shared/ paketine erisebilmek icin)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY services/collector_opensky/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/
COPY services/collector_opensky/ /app/

# Root olmayan kullanici
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

CMD ["python", "main.py"]
