#!/usr/bin/env bash
# Build + deploy the BenefitNavigator trust-core MCP server (mas.mcp_server) as the
# `benefitnav-mcp` Container App — the single surface through which the Foundry-hosted
# agents reach the deterministic eligibility core. No LLM, no agent orchestration here.
#
# No secret is committed: the Search admin key is READ from Azure at deploy time and
# injected as a Container Apps secret. The HMAC token-secret is the SOURCE that the
# conductor copies (deploy-api.sh reads `token-secret` back from THIS app), so an
# in-place update PRESERVES it untouched — regenerating it would break every
# grill_next/assess signature and silently degrade the Interview agent.
#
# Idempotent: re-running rebuilds the image from infra/Dockerfile and swaps it in place.
# Build context is the repo root (the Dockerfile lives in infra/ but COPYs compute/,
# agent/, ingest/, mas/, infra/mcp-server.requirements.txt — all root-relative).
set -euo pipefail

RG="rg-benefitnav-my"
ENVIRONMENT="benefitnav-mcp-env"
ACR="ca7f0629eef3acr"
APP="benefitnav-mcp"
SEARCH_SERVICE="benefitnav-search-79c45"
PORT=8000

cd "$(dirname "${BASH_SOURCE[0]}")/.."   # -> benefitnav/  (build context = repo root)
TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
IMAGE="${ACR}.azurecr.io/${APP}:${TAG}"

echo "==> [1/4] Building $IMAGE via ACR cloud build (verifies infra/Dockerfile)"
az acr build -r "$ACR" -t "${APP}:${TAG}" -f infra/Dockerfile .

echo "==> [2/4] Reading the Search admin key from Azure (value never printed)"
SEARCH_KEY="$(az search admin-key show -g "$RG" --service-name "$SEARCH_SERVICE" --query primaryKey -o tsv)"
[ -n "$SEARCH_KEY" ] || { echo "FATAL: empty Search admin key for $SEARCH_SERVICE" >&2; exit 1; }

if az containerapp show -n "$APP" -g "$RG" >/dev/null 2>&1; then
  echo "==> [3/4] $APP exists — refreshing search-key, PRESERVING token-secret, swapping image"
  # secret set only touches the named secret; token-secret and the env→secretref
  # mappings are left intact, and `update --image` preserves ingress/env/secrets.
  az containerapp secret set -n "$APP" -g "$RG" --secrets "search-key=$SEARCH_KEY" >/dev/null
  az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" >/dev/null
else
  echo "==> [3/4] $APP absent — bootstrapping (generating a NEW token-secret)"
  echo "    NOTE: after a from-scratch create, re-run infra/deploy-api.sh so the conductor"
  echo "          copies this new token-secret — otherwise signed tokens won't verify."
  az acr update -n "$ACR" --admin-enabled true >/dev/null
  ACR_USER="$(az acr credential show -n "$ACR" --query username -o tsv)"
  ACR_PASS="$(az acr credential show -n "$ACR" --query 'passwords[0].value' -o tsv)"
  TOKEN_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  az containerapp create \
    --name "$APP" --resource-group "$RG" --environment "$ENVIRONMENT" \
    --image "$IMAGE" \
    --registry-server "${ACR}.azurecr.io" \
    --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
    --target-port "$PORT" --ingress external \
    --min-replicas 1 --max-replicas 1 \
    --system-assigned \
    --secrets "search-key=$SEARCH_KEY" "token-secret=$TOKEN_SECRET" \
    --env-vars BENEFITNAV_SEARCH_KEY=secretref:search-key \
               BENEFITNAV_TOKEN_SECRET=secretref:token-secret >/dev/null
fi

FQDN="$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
echo "==> [4/4] DONE. MCP endpoint: https://${FQDN}/mcp"
echo "    (streamable-HTTP — reached by the Foundry agents, not browsed directly;"
echo "     provision agents against it with: BENEFITNAV_MCP_URL=https://${FQDN}/mcp)"
