FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Tokyo \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl_cffi は libcurl を要求する。tzdata は TZ 設定用
RUN apt-get update && apt-get install -y --no-install-recommends \
      tzdata \
      ca-certificates \
      libcurl4 \
      libssl3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY infra ./infra
COPY db ./db
COPY scheduler ./scheduler
COPY config ./config
COPY ui ./ui
COPY scripts ./scripts
COPY docs ./docs
COPY mockup ./mockup
COPY README.md pyproject.toml ./

RUN mkdir -p /app/data /workspace/.secrets \
 && useradd -u 1026 -g 100 -o -m appuser \
 && chown -R 1026:100 /app

USER 1026:100

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; \
u='http://127.0.0.1:8080/healthz'; \
r=urllib.request.urlopen(u,timeout=3); \
sys.exit(0 if r.status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
