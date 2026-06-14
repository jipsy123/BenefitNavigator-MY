# Trust-core MCP server image — the single surface through which Foundry-hosted
# agents reach the deterministic eligibility core. Built and deployed to Azure
# Container Apps (see infra/deploy-mcp.sh) so no local dev tunnel is needed.
#
# What runs here is ONLY transport + the deterministic core: no LLM, no agent
# orchestration, no secrets baked in. The Search admin key (for the `retrieve`
# grounding tool) is injected at runtime as a Container Apps secret →
# BENEFITNAV_SEARCH_KEY; the HMAC token secret as BENEFITNAV_TOKEN_SECRET.
# Azure-native base (MCR) rather than docker.io/python — Docker Hub's anonymous
# pull-rate limit throttles ACR's shared build runners; MCR never does.
FROM mcr.microsoft.com/azurelinux/base/python:3.12

WORKDIR /app

# Install deps first for layer caching. `python -m pip` (not the bare `pip` shim)
# so it works regardless of the base image's script PATH; ensurepip is a no-op
# when pip already exists.
COPY infra/mcp-server.requirements.txt ./requirements.txt
RUN python3 -m ensurepip --upgrade 2>/dev/null || true \
    && python3 -m pip install --no-cache-dir -r requirements.txt

# Only the packages the MCP server actually imports. corpus-fetcher/, web/, tests/
# and the heavy SDKs are intentionally absent — retrieval hits Azure Search over
# REST, it does not read the local corpus.
COPY compute/ ./compute/
COPY agent/ ./agent/
COPY ingest/ ./ingest/
COPY mas/ ./mas/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

# Serve the FastMCP streamable-HTTP ASGI app. Binding 0.0.0.0 (container) rather
# than the module's 127.0.0.1 default. The app's lifespan starts the streamable
# session manager. Honour $PORT so the platform can override it.
CMD ["sh", "-c", "python3 -m uvicorn mas.mcp_server:http_app --host 0.0.0.0 --port ${PORT}"]
