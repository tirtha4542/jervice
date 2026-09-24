"""HTTP client for backend-owned operational routes.

The AI package intentionally does not receive an AsyncSession or import ORM
models. In local development it calls the local FastAPI service; in a deployed
environment BACKEND_API_URL can point at the cloud backend.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class BackendRouteError(RuntimeError):
    """A backend route could not provide a safe operational response."""


class BackendRouteClient:
    def __init__(
        self,
        *,
        access_token: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.backend_api_url).rstrip("/")
        self.access_token = access_token or settings.backend_api_token
        self.timeout_seconds = timeout_seconds or settings.backend_timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise BackendRouteError(f"Backend route unavailable: {path}") from exc

        if response.status_code >= 400:
            raise BackendRouteError(
                f"Backend route {path} returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise BackendRouteError(f"Backend route {path} returned invalid JSON") from exc

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    url,
                    params=params,
                    json=payload,
                    headers={**self._headers(), "Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise BackendRouteError(f"Backend route unavailable: {path}") from exc
        if response.status_code >= 400:
            raise BackendRouteError(
                f"Backend route {path} returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise BackendRouteError(f"Backend route {path} returned invalid JSON") from exc

    async def operational_context(
        self,
        *,
        branch_id: int,
        table_session_id: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"branch_id": branch_id}
        if table_session_id is not None:
            params["table_session_id"] = table_session_id
        data = await self.get_json("/api/v1/operations/context", params=params)
        if not isinstance(data, dict):
            raise BackendRouteError("Operational context response must be an object")
        return data
