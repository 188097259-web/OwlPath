FROM node:20-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OWLPATH_DATA_DIR=/app/data
WORKDIR /app

COPY backend/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --disable-pip-version-check --upgrade "pip==26.2.1" \
    && python -m pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.lock \
    && groupadd --system owlpath \
    && useradd --system --gid owlpath --home-dir /app owlpath

COPY backend/ /app/backend/
COPY config/ /app/config/
COPY --from=frontend-builder /build/frontend/dist/ /app/frontend/dist/
RUN mkdir -p /app/data && chown -R owlpath:owlpath /app/data

USER owlpath
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000"]
