FROM python:3.11-slim
WORKDIR /app
# curl is needed by the ECS container health check (curl -f /health)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 9001
CMD ["uvicorn", "nexus.server:app", "--host", "0.0.0.0", "--port", "9001"]
