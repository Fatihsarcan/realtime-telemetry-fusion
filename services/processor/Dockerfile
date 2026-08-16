# Build context: repo koku
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY services/processor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/
COPY services/processor/ /app/

RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

CMD ["python", "main.py"]
