# VAADA: one container. FastAPI serves the API and the built React app on $PORT.

# --- 1. build the frontend ---------------------------------------------------
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. backend runtime --------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# libgomp: OpenMP runtime needed by XGBoost
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist
# Fit + calibrate Nirnay at build time so startup is instant (cached by source hash).
RUN python -c "import data_and_nirnay as dn; dn.initialize()"
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
