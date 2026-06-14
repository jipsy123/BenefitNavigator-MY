"""The trust-core MCP server — a thin transport binding over mas/trust_tools.

This is the ONLY surface through which Foundry-hosted agents reach the deterministic
core. It registers five tools (assess / optimize / grill_next / grade / prove) and
does nothing else: all logic lives in trust_tools, which is unit-tested independently.
Keeping this layer dumb means the trust-critical code never depends on the MCP/SDK runtime.

Transport: streamable-HTTP, stateless (each call is independent — matches our signed-
token model and works behind the dev tunnel / a load balancer). The server is exposed
two ways:
  - mounted into the FastAPI app at `streamable_http_path` (one dev tunnel serves both);
  - or standalone via `python -m mas.mcp_server` for local testing.

A tampered or expired token raises InvalidToken inside trust_tools; we surface it as a
clean tool error so the calling agent escalates rather than receiving a usable result.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ingest import knowledge_base as kb

from . import trust_tools
from .state import InvalidToken

MCP_PATH = "/mcp"

# DNS-rebinding (Host/Origin) protection guards *browser-origin* attacks against a
# localhost MCP server. This server is the opposite: a public, server-to-server
# endpoint behind Azure Container Apps' TLS, called by Foundry-hosted agents (no
# browser). Its real trust gate is the HMAC-signed state_token, not the Host header,
# and the agents arrive on the platform's own (non-localhost) FQDN. So we explicitly
# disable the host allowlist — leaving it on rejects every cloud caller with HTTP 421.
mcp = FastMCP(
    "benefitnav-trust-core",
    instructions=(
        "Deterministic eligibility tools for BenefitNavigator Malaysia. Every result is "
        "computed in Python from curated, citation-backed rules — never by a model. Pass "
        "the conversation's signed state_token to assess/optimize/grill_next; the tool "
        "verifies it and reads the real facts. These tools decide eligibility and amounts; "
        "callers only relay the output."
    ),
    stateless_http=True,
    streamable_http_path=MCP_PATH,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _guard_token(fn, token: str):
    """Run a token-taking trust tool, converting tamper/expiry into a clean error."""
    try:
        return fn(token)
    except InvalidToken as exc:
        # Hard-fail: a bad token must never yield a usable verdict.
        raise ValueError(f"invalid state token ({exc}) — escalate to a human") from exc


@mcp.tool(
    name="assess",
    description="Return deterministic eligibility verdicts, gaps, the guaranteed monthly "
    "floor, and the gazetted citations for the profile carried in state_token. No "
    "narrative, no amounts of your own — relay exactly what is returned.",
)
def assess(state_token: str) -> dict:
    return _guard_token(trust_tools.assess, state_token)


@mcp.tool(
    name="optimize",
    description="Return the deterministic optimal-unlock plan (ordered registration steps, "
    "the marginal RM each adds, and the programmes it unlocks) for the profile in "
    "state_token. Call after an assessment that has near-miss gaps.",
)
def optimize(state_token: str) -> dict:
    return _guard_token(trust_tools.optimize, state_token)


@mcp.tool(
    name="grill_next",
    description="Return the single most decision-relevant question to ask next (or "
    "done=true) for the profile in state_token, chosen deterministically. You phrase it; "
    "you never pick which field to ask.",
)
def grill_next(state_token: str) -> dict:
    return _guard_token(trust_tools.grill_next, state_token)


@mcp.tool(
    name="grade",
    description="Return the reading-grade of a Malay narrative (lower = easier) and whether "
    "it meets the plain-language target. Use to drive a simplify loop on a draft.",
)
def grade(text: str) -> dict:
    return trust_tools.grade(text)


@mcp.tool(
    name="prove",
    description="Fetch the gazetted .gov.my passage that proves each verdict for the "
    "profile carried in state_token. Returns one cited passage per verdict citation "
    "(document, locator, source link, passage text). Evidence only — never an eligibility "
    "decision. Fail-hard: a knowledge-base error is raised, never swallowed.",
)
def prove(state_token: str) -> dict:
    """Compute the verdict citations from the signed token, then retrieve a proving passage
    for each. Compute decides WHICH sources prove the verdicts; retrieval fetches them."""
    citations = _guard_token(trust_tools.proof_citations, state_token)
    return {"proofs": kb.fetch_proofs(citations)}


# ASGI app for mounting into FastAPI (one dev tunnel serves API + MCP).
http_app = mcp.streamable_http_app()


if __name__ == "__main__":
    # Standalone run for local testing: serves the MCP endpoint over streamable-HTTP.
    mcp.run(transport="streamable-http")
