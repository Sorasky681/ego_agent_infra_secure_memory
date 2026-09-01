FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml requirements-api.lock ./
COPY apps ./apps
COPY benchmarks ./benchmarks
RUN pip install --no-cache-dir -r requirements-api.lock

ENV EGO_AGENTTEAMS_BRIDGE_DB=/data/bridge.sqlite3
VOLUME ["/data"]
EXPOSE 8010
CMD ["uvicorn", "apps.agentteams_bridge.main:app", "--host", "0.0.0.0", "--port", "8010"]
