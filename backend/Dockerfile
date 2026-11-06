# ---- builder: resolve wheels once ----
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
# CPU-only torch keeps the image ~2 GB smaller than the default CUDA wheel
RUN pip install --no-cache-dir --prefix=/install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt \
 && pip install --no-cache-dir --prefix=/install "psycopg[binary]>=3.1"

# ---- runtime ----
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/* && useradd -m carrom
WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .
USER carrom
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD curl -fsS http://localhost:8000/health || exit 1
# ONE worker on purpose: a room's authoritative game state lives in the process that owns it.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
