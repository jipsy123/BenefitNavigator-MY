#!/usr/bin/env bash
# One-shot teardown for BenefitNavigator Malaysia.
#
# Deletes the entire resource group and purges soft-deleted Cognitive Services
# accounts (so their globally-unique names are released and retention stops).
#
# What the RG delete covers (everything lives in rg-benefitnav-my):
#   - Azure AI Search Basic               (~$2.45/day meter — the main idle cost)
#   - Container App `benefitnav-mcp`      (the trust-core MCP server; minReplicas=1)
#   - Container Apps env + Log Analytics  (auto-created for the app)
#   - Container Registry (ACR)            (built the MCP image)
#   - Foundry account `benefitnav-ai-sc-79c45` → project `benefitnav-proj`
#         → the 6 hosted agents + any connections (all destroyed with the account)
#   - Storage + the AOAI/embedding deployments
#
# We delete the Container App + its environment explicitly FIRST so the ACA meter
# stops immediately, without waiting for the (async) group delete to finish.
set -euo pipefail

RG="rg-benefitnav-my"
ACA_APP="benefitnav-mcp"
ACA_ENV="benefitnav-mcp-env"

echo "This will DELETE resource group '${RG}' and everything in it"
echo "(Search, the MCP Container App, the Foundry account + all 6 agents)."
read -r -p "Type the RG name to confirm: " confirm
if [[ "${confirm}" != "${RG}" ]]; then
  echo "Aborted."
  exit 1
fi

# 1) Stop the Container Apps meter immediately (best-effort; the RG delete also covers it).
echo "Deleting Container App ${ACA_APP} (stops the ACA meter now) ..."
az containerapp delete -g "${RG}" -n "${ACA_APP}" --yes 2>/dev/null \
  && echo "  deleted ${ACA_APP}" || echo "  (already gone)"
az containerapp env delete -g "${RG}" -n "${ACA_ENV}" --yes 2>/dev/null \
  && echo "  deleted env ${ACA_ENV}" || echo "  (env already gone)"

# 2) Delete the whole resource group — covers Search, ACR, Foundry account + agents, etc.
echo "Deleting resource group ${RG} ..."
az group delete -n "${RG}" --yes --no-wait

# 3) Purge soft-deleted Cognitive Services accounts so their names + retention are freed.
echo "Purging any soft-deleted Cognitive Services accounts ..."
for loc in swedencentral southeastasia; do
  for acct in benefitnav-ai-sc-79c45 benefitnav-ai-79c45; do
    az cognitiveservices account purge -g "${RG}" -n "${acct}" -l "${loc}" 2>/dev/null \
      && echo "  purged ${acct} (${loc})" || true
  done
done

echo
echo "Teardown requested. Verify with: az group exists -n ${RG}"
echo "(To delete ONLY the agents and keep the project, instead run:"
echo "  PYTHONPATH=\"\$PWD\" .venv/bin/python -m mas.teardown_agents )"
