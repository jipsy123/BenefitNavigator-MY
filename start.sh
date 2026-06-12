#!/usr/bin/env bash
# Start the BenefitNavigator server with secrets loaded from .env
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env if present (never committed; keeps secrets out of process args)
if [[ -f .env ]]; then
  set -a
  # shellcheck source=/dev/null
  source .env
  set +a
fi

export PYTHONPATH="$SCRIPT_DIR"
exec .venv/bin/python -m uvicorn api.app:app --port 8011
