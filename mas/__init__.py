"""mas — the multi-agent system layer for BenefitNavigator.

Sits *above* the deterministic trust core (`compute/`) and the reused LLM
touchpoints (`agent/narrate`, `verify`, `safety`, `readability`, `localize`).
The agents only orchestrate; eligibility and amounts still come exclusively from
`compute/`, and the dual safety gate still fires in the FastAPI boundary.
"""
