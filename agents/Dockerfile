FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml langgraph.json ./
COPY src/ ./src/

RUN pip install --no-cache-dir . "langgraph-cli[inmem]>=0.1.0"

ENV PORT=8000

EXPOSE 8000

CMD ["langgraph", "dev", "--host", "0.0.0.0", "--port", "8000", "--no-browser", "--no-reload"]
