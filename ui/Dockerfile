# Multi-stage: build frontend, serve via FastAPI

FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY package.json ./
RUN npm install
COPY index.html vite.config.ts tsconfig.json ./
COPY src ./src
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx python-multipart

COPY api/ ./api/
COPY --from=frontend-build /frontend/dist ./static

ENV STATIC_DIR=/app/static
ENV LANGGRAPH_URL=http://research-agents.agents.svc.cluster.local:8000
ENV INGESTION_URL=http://ingestion.rag.svc.cluster.local:8001

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
