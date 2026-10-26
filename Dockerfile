# One image, one process: FastAPI serves the API under /api and the built screens at /.
# Build and run locally (from the repo root):
#   docker build -t crata .
#   docker run --rm -p 8000:8000 --env-file backend/.env crata
# Hosts such as Render pass the port in $PORT; locally it defaults to 8000.

# Stage 1: build the screens (type-check, then bundle into frontend/dist).
FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: the backend, with the built screens next to it (app/main.py looks for ../frontend/dist).
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install -e .
COPY --from=frontend /src/frontend/dist /app/frontend/dist

# Run as a plain user; the SQLite file lives in backend/data, which that user owns.
# No .env in the image: keys come from the host's environment settings.
RUN useradd --create-home crata && mkdir -p /app/backend/data && chown -R crata /app/backend/data
USER crata

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
