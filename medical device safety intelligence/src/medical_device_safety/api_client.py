# File purpose: sends safe, retry-limited requests to the openFDA event and recall APIs.
"""Small openFDA HTTP client with bounded retries and structured metadata."""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass
from typing import Any
import requests

LOG = logging.getLogger(__name__)
BASE = "https://api.fda.gov/device"


# Step 1: represent API failures without exposing credentials in error messages.
class APIError(RuntimeError):
    """Raised for HTTP, connection, timeout, or malformed API responses."""

    def __init__(self, message: str, *, status_code: int | None = None, attempts: int = 1):
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts


# Step 2: carry response data and safe request metadata back to callers.
@dataclass
class APIResponse:
    endpoint: str
    params: dict[str, Any]
    status_code: int
    elapsed_seconds: float
    payload: dict[str, Any]
    headers: dict[str, str]
    attempts: int


# Step 3: configure a reusable HTTP session for both supported endpoints.
class OpenFDAClient:
    def __init__(self, api_key: str | None = None, timeout: float = 20,
                 max_retries: int = 3, session: requests.Session | None = None):
        self.api_key, self.timeout = api_key, timeout
        self.max_retries = max(0, max_retries)
        self.session = session or requests.Session()

    # Step 4: validate a request, add the key privately, retry transient failures, and parse JSON.
    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> APIResponse:
        if endpoint not in {"event", "recall"}:
            raise ValueError(f"Unsupported endpoint: {endpoint}")
        query = dict(params or {})
        if self.api_key:
            query["api_key"] = self.api_key
        url = f"{BASE}/{endpoint}.json"
        start = time.perf_counter()
        # Retry only temporary service responses and connection or timeout failures.
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, params=query, timeout=self.timeout)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.max_retries:
                        retry_after = response.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (2 ** attempt)
                        time.sleep(min(delay, 8))
                        continue
                # Convert successful responses to JSON and omit the API key from returned metadata.
                try:
                    response.raise_for_status()
                except requests.HTTPError as exc:
                    detail = getattr(response, "text", "")
                    raise APIError(f"openFDA HTTP {response.status_code}: {detail[:400]}",
                                   status_code=response.status_code, attempts=attempt + 1) from exc
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise APIError("openFDA returned malformed JSON") from exc
                if not isinstance(payload, dict):
                    raise APIError("Expected a JSON object from openFDA")
                safe_params = {k: v for k, v in query.items() if k != "api_key"}
                return APIResponse(endpoint, safe_params, response.status_code,
                                   time.perf_counter() - start, payload,
                                   {"Link": response.headers.get("Link", "")} if response.headers.get("Link") else {},
                                   attempt + 1)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt >= self.max_retries:
                    raise APIError(f"openFDA request failed after {attempt + 1} attempt(s): {type(exc).__name__}",
                                   attempts=attempt + 1) from exc
                time.sleep(min(0.5 * (2 ** attempt), 8))
            except requests.HTTPError as exc:
                raise APIError(f"openFDA HTTP error: {type(exc).__name__}", attempts=attempt + 1) from exc
        raise APIError("openFDA request exhausted retries")

    # Step 5: follow an API continuation URL while keeping its cursor intact.
    def get_url(self, url: str) -> APIResponse:
        """Follow an API-provided continuation URL without rebuilding its cursor."""
        start = time.perf_counter()
        query_params = {"api_key": self.api_key} if self.api_key and "api_key=" not in url else None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, params=query_params, timeout=self.timeout)
                if (response.status_code == 429 or response.status_code >= 500) and attempt < self.max_retries:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (2 ** attempt)
                    time.sleep(min(delay, 8))
                    continue
                try:
                    response.raise_for_status()
                except requests.HTTPError as exc:
                    detail = getattr(response, "text", "")
                    raise APIError(f"openFDA continuation HTTP {response.status_code}: {detail[:400]}",
                                   status_code=response.status_code, attempts=attempt + 1) from exc
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise APIError("openFDA returned malformed JSON") from exc
                if not isinstance(payload, dict):
                    raise APIError("Expected a JSON object from openFDA")
                return APIResponse("continuation", {}, response.status_code,
                                   time.perf_counter() - start,
                                   payload, {"Link": response.headers.get("Link", "")} if response.headers.get("Link") else {},
                                   attempt + 1)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt >= self.max_retries:
                    raise APIError(f"openFDA continuation failed after {attempt + 1} attempt(s): {type(exc).__name__}",
                                   attempts=attempt + 1) from exc
                time.sleep(min(0.5 * (2 ** attempt), 8))
            except requests.HTTPError as exc:
                raise APIError(f"openFDA continuation HTTP error: {type(exc).__name__}", attempts=attempt + 1) from exc
        raise APIError("openFDA continuation exhausted retries")
