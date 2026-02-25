from __future__ import annotations
import os
from typing import Any, Dict, Iterable, List, Optional

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=20.0)


class AhaClient:
    """Thin HTTP client for Aha! v1 REST API.

    Auth: Bearer token via BAE_AHA_TOKEN env var (or aha_token param).
    Account: subdomain via BAE_AHA_ACCOUNT (e.g., "bigblue").
    """

    def __init__(
        self,
        *,
        account: Optional[str] = None,
        token: Optional[str] = None,
        user_agent: str = "beautiful-aha-epics/1.0 (+bae)",
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.account = account or os.getenv("BAE_AHA_ACCOUNT") or os.getenv("AHA_ACCOUNT")
        self.token = token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
        if not self.account:
            raise RuntimeError("Missing Aha! account subdomain. Set BAE_AHA_ACCOUNT.")
        if not self.token:
            raise RuntimeError("Missing Aha! API token. Set BAE_AHA_TOKEN.")
        self.base_url = f"https://{self.account}.aha.io/api/v1"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        }
        self._client = httpx.Client(headers=self.headers, timeout=timeout)

    # ---------- Generic helpers ----------
    def _get(self, path: str, **params: Any) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        r = self._client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    # ---------- Products ----------
    def list_products(self) -> List[Dict[str, Any]]:
        data = self._get("/products")
        return data.get("products", [])

    def find_product_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        for p in self.list_products():
            if p.get("name", "").strip().lower() == name.strip().lower():
                return p
        return None

    # ---------- Releases ----------
    def list_releases_for_product(self, product_id: str) -> List[Dict[str, Any]]:
        data = self._get(f"/products/{product_id}/releases")
        return data.get("releases", [])

    def map_release_names_to_ids(self, product_id: str, release_names: Iterable[str]) -> Dict[str, Optional[str]]:
        releases = self.list_releases_for_product(product_id)
        name_to_id = {}
        index = {r.get("name", "").strip().lower(): r for r in releases}
        for name in release_names:
            rid = index.get(name.strip().lower(), {}).get("id")
            name_to_id[name] = rid
        return name_to_id

    # ---------- Epics ----------
    def list_epics(self, *, page: int = 1, per_page: int = 50, tag: Optional[str] = None, assigned_to_user: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"page": page, "per_page": per_page}
        if tag:
            params["tag"] = tag
        if assigned_to_user:
            params["assigned_to_user"] = assigned_to_user
        data = self._get("/epics", **params)
        return data.get("epics", [])

    def list_epics_for_release(self, release_id: str, *, page: int = 1, per_page: int = 50, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"page": page, "per_page": per_page}
        if tag:
            params["tag"] = tag
        data = self._get(f"/releases/{release_id}/epics", **params)
        return data.get("epics", [])

    def get_epic(self, epic_id: str) -> Dict[str, Any]:
        return self._get(f"/epics/{epic_id}").get("epic", {})

    def iter_all_epics(self, *, tag: Optional[str] = None, assigned_to_user: Optional[str] = None) -> Iterable[Dict[str, Any]]:
        page = 1
        while True:
            batch = self.list_epics(page=page, tag=tag, assigned_to_user=assigned_to_user)
            if not batch:
                break
            for e in batch:
                yield e
            page += 1

    def iter_release_epics(self, release_id: str, *, tag: Optional[str] = None) -> Iterable[Dict[str, Any]]:
        page = 1
        while True:
            batch = self.list_epics_for_release(release_id, page=page, tag=tag)
            if not batch:
                break
            for e in batch:
                yield e
            page += 1
