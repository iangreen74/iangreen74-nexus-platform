# Frontend build stage — produces overwatch-web/dist/
FROM node:20-alpine AS frontend-build
WORKDIR /app/overwatch-web
COPY overwatch-web/package*.json ./
RUN npm ci --silent
COPY overwatch-web/ ./
RUN npm run build

# Backend image — Python + FastAPI + the built frontend assets.
FROM python:3.11-slim
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
WORKDIR /app
# curl: ECS container health check (curl -f /health)
# git: code auditor clones aria-platform to run audit rules
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Frontend dist replaces any local dist/ that snuck in via `COPY .`
COPY --from=frontend-build /app/overwatch-web/dist /app/overwatch-web/dist
EXPOSE 9001
CMD ["uvicorn", "nexus.server:app", "--host", "0.0.0.0", "--port", "9001"]
