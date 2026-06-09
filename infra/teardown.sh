#!/usr/bin/env bash
# One-shot teardown for BenefitNavigator Malaysia.
# Deletes the entire resource group and purges soft-deleted Cognitive Services
# accounts (so their globally-unique names are released and retention stops).
set -euo pipefail

RG="rg-benefitnav-my"

echo "This will DELETE resource group '${RG}' and everything in it."
read -r -p "Type the RG name to confirm: " confirm
if [[ "${confirm}" != "${RG}" ]]; then
  echo "Aborted."
  exit 1
fi

echo "Deleting resource group ${RG} ..."
az group delete -n "${RG}" --yes --no-wait

echo "Purging any soft-deleted Cognitive Services accounts ..."
for loc in swedencentral southeastasia; do
  for acct in benefitnav-ai-sc-79c45 benefitnav-ai-79c45; do
    az cognitiveservices account purge -g "${RG}" -n "${acct}" -l "${loc}" 2>/dev/null \
      && echo "  purged ${acct} (${loc})" || true
  done
done

echo "Teardown requested. Verify with: az group exists -n ${RG}"
