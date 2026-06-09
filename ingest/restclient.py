"""Thin Azure AI Search REST helper with explicit api-version control."""
from __future__ import annotations

import requests

from . import config


def search_request(method: str, path: str, api_version: str,
                   body: dict | None = None, timeout: int = 90) -> dict | None:
    """Call the Search data-plane REST API. Raises RuntimeError on HTTP >= 400."""
    url = f"{config.SEARCH_ENDPOINT}/{path.lstrip('/')}"
    headers = {"api-key": config.search_key(), "Content-Type": "application/json"}
    response = requests.request(
        method, url, params={"api-version": api_version},
        json=body, headers=headers, timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"{method} {path} -> {response.status_code}: {response.text[:1000]}"
        )
    if not response.text:
        return None
    try:
        return response.json()
    except ValueError:
        return None
