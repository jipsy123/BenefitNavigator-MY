"""Post-deploy smoke: prove the deployed conductor's AGENT PATH actually ran.

The system fails closed SILENTLY — a broken managed identity still returns HTTP 200
with deterministic fallback text — so a 200 is NOT success. We send one /chat turn and
assert the trace shows the Orchestrator agent responded (ROUTE.status == "ok", not
"fallback"). Retries cover RBAC role-assignment propagation (1-5 min). Stdlib only, so
the host's system python runs it without the project venv.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

ASK_MESSAGE = "Saya seorang ibu tunggal dengan dua anak dan tiada pendapatan tetap."
MAX_ATTEMPTS = 7
RETRY_SECONDS = 45


def _chat(base_url: str) -> dict:
    body = json.dumps({"message": ASK_MESSAGE, "lang": "ms"}).encode()
    req = urllib.request.Request(
        f"{base_url}/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def main(base_url: str) -> int:
    base_url = base_url.rstrip("/")
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = _chat(base_url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  attempt {attempt}: request error ({exc}); retrying in {RETRY_SECONDS}s")
            time.sleep(RETRY_SECONDS)
            continue
        trace = {s.get("stage"): s for s in data.get("trace", [])}
        route = trace.get("ROUTE", {})
        last = route
        if route.get("status") == "ok":
            print(f"SMOKE PASS — Orchestrator agent responded (action={route.get('action')!r}). "
                  "Managed identity -> Foundry agent path is LIVE.")
            return 0
        print(f"  attempt {attempt}: ROUTE status={route.get('status')!r} "
              "(fallback => agent did NOT fire, likely RBAC not propagated yet); "
              f"retrying in {RETRY_SECONDS}s")
        time.sleep(RETRY_SECONDS)

    print("SMOKE FAIL — ROUTE never reached 'ok' (last="
          f"{last}). The deploy returns 200 but runs entirely on DETERMINISTIC "
          "FALLBACKS: the managed identity cannot invoke Foundry agents. Verify the "
          "'Azure AI Developer' role assignment on benefitnav-ai-sc-79c45 and the app's "
          "identity.principalId.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: smoke_deployed.py <base_url>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
