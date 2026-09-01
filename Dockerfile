FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser
ENV MPT_ENV=production MPT_HOST=0.0.0.0 MPT_COOKIE_SECURE=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + (os.environ.get('PORT') or os.environ.get('MPT_PORT', '8000')) + '/api/health', timeout=2)"]
CMD ["python", "start_server.py"]
