#!/usr/bin/env bash
# Build + deploy the BenefitNavigator conductor (api.app) as the `benefitnav-api`
# Container App, alongside the existing `benefitnav-mcp` trust core. After this runs,
# nothing the demo depends on runs on a laptop.
#
# No secret is committed: keys are READ from Azure at deploy time and injected as
# Container Apps secrets. The HMAC token secret is COPIED verbatim from benefitnav-mcp
# so the conductor's signed tokens verify on the trust-core container — regenerating it
# would break every grill_next/assess signature and silently degrade the Interview agent.
#
# Idempotent: re-running updates the image + secrets in place.
set -euo pipefail

RG="rg-benefitnav-my"
ENVIRONMENT="benefitnav-mcp-env"
ACR="ca7f0629eef3acr"
APP="benefitnav-api"
MCP_APP="benefitnav-mcp"
AOAI_ACCOUNT="benefitnav-ai-sc-79c45"
SEARCH_SERVICE="benefitnav-search-79c45"
ROLE="Azure AI Developer"
PORT=8000

cd "$(dirname "${BASH_SOURCE[0]}")/.."   # -> benefitnav/
TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
IMAGE="${ACR}.azurecr.io/${APP}:${TAG}"

echo "==> [1/6] Building $IMAGE via ACR cloud build (verifies Dockerfile.api)"
az acr build -r "$ACR" -t "${APP}:${TAG}" -f Dockerfile.api .

echo "==> [2/6] Reading runtime secrets from Azure (values never printed)"
AOAI_KEY="$(az cognitiveservices account keys list -g "$RG" -n "$AOAI_ACCOUNT" --query key1 -o tsv)"
SEARCH_KEY="$(az search admin-key show -g "$RG" --service-name "$SEARCH_SERVICE" --query primaryKey -o tsv)"
TOKEN_SECRET="$(az containerapp secret show -g "$RG" -n "$MCP_APP" --secret-name token-secret --query value -o tsv)"
[ -n "$TOKEN_SECRET" ] || { echo "FATAL: empty token-secret copied from $MCP_APP" >&2; exit 1; }

echo "==> [3/6] Ensuring ACR admin creds for the registry pull"
az acr update -n "$ACR" --admin-enabled true >/dev/null
ACR_USER="$(az acr credential show -n "$ACR" --query username -o tsv)"
ACR_PASS="$(az acr credential show -n "$ACR" --query 'passwords[0].value' -o tsv)"

if az containerapp show -n "$APP" -g "$RG" >/dev/null 2>&1; then
  echo "==> [4/6] $APP exists — updating secrets + image in place"
  az containerapp secret set -n "$APP" -g "$RG" \
    --secrets aoai-key="$AOAI_KEY" search-key="$SEARCH_KEY" token-secret="$TOKEN_SECRET" >/dev/null
  az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" \
    --set-env-vars BENEFITNAV_AOAI_KEY=secretref:aoai-key \
                   BENEFITNAV_SEARCH_KEY=secretref:search-key \
                   BENEFITNAV_TOKEN_SECRET=secretref:token-secret >/dev/null
else
  echo "==> [4/6] Creating $APP in $ENVIRONMENT"
  az containerapp create \
    --name "$APP" --resource-group "$RG" --environment "$ENVIRONMENT" \
    --image "$IMAGE" \
    --registry-server "${ACR}.azurecr.io" \
    --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
    --target-port "$PORT" --ingress external \
    --min-replicas 1 --max-replicas 1 \
    --system-assigned \
    --secrets aoai-key="$AOAI_KEY" search-key="$SEARCH_KEY" token-secret="$TOKEN_SECRET" \
    --env-vars BENEFITNAV_AOAI_KEY=secretref:aoai-key \
               BENEFITNAV_SEARCH_KEY=secretref:search-key \
               BENEFITNAV_TOKEN_SECRET=secretref:token-secret >/dev/null
fi

echo "==> [5/6] Granting the app's managed identity '$ROLE' on $AOAI_ACCOUNT"
MI_OID="$(az containerapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)"
[ -n "$MI_OID" ] || { echo "FATAL: app has no managed identity principalId" >&2; exit 1; }
AISERVICES_ID="$(az cognitiveservices account show -g "$RG" -n "$AOAI_ACCOUNT" --query id -o tsv)"
az role assignment create \
  --assignee-object-id "$MI_OID" --assignee-principal-type ServicePrincipal \
  --role "$ROLE" --scope "$AISERVICES_ID" >/dev/null 2>&1 \
  || echo "    (role assignment already present — continuing)"

FQDN="$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
echo "==> [6/6] Smoke test (asserts the AGENT path ran, retries for RBAC propagation)"
echo "    Deployed at: https://${FQDN}"
python3 infra/smoke_deployed.py "https://${FQDN}"
echo "==> DONE. App: https://${FQDN}  (UI at / , API at /chat)"
