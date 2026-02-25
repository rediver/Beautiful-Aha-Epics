from __future__ import annotations
import os
import re
import asyncio
from typing import Any, Dict, Iterable, List, Optional

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=20.0)


def _norm_name(name: Optional[str]) -> str:
    # drop codes like "(20A11)", collapse whitespace, lowercase
    s = (name or "").strip()
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.lower().strip()


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

    def _put(self, path: str, json: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        r = self._client.put(url, json=json)
        r.raise_for_status()
        return r.json()

    # ---------- Products & product lines ----------
    def list_products(self, *, page: int = 1, per_page: int = 100) -> List[Dict[str, Any]]:
        params = {"page": page, "per_page": per_page}
        data = self._get("/products", **params)
        return data.get("products", [])

    def iter_products(self) -> Iterable[Dict[str, Any]]:
        page = 1
        while True:
            batch = self.list_products(page=page)
            if not batch:
                break
            for p in batch:
                yield p
            page += 1

    def get_product(self, product_id: str) -> Dict[str, Any]:
        # product_id may be numeric ID or product key like "DATALIN"; API usually supports both
        return self._get(f"/products/{product_id}").get("product", {})

    def get_product_line(self, product_line_id: str) -> Dict[str, Any]:
        # product line schema contains name and parent references
        return self._get(f"/product_lines/{product_line_id}").get("product_line", {})

    def _collect_ancestor_names(self, product: Dict[str, Any]) -> List[str]:
        names: List[str] = []
        seen = set()
        # Aha! returns `product_line` on product; climb via `parent` / `product_line` fields
        node = product.get("product_line") or {}
        hops = 0
        while isinstance(node, dict) and node and hops < 20:
            nm = node.get("name")
            if nm:
                names.append(nm)
            pid = node.get("id")
            if not pid or pid in seen:
                break
            seen.add(pid)
            # fetch full node to get its parent
            full = self.get_product_line(str(pid)) or {}
            node = full.get("product_line") or full  # defensive
            # try common parent keys
            node = node.get("parent") or node.get("product_line") or node.get("parent_product_line") or {}
            hops += 1
        return names  # from immediate parent up to root order

    def find_product_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        target = _norm_name(name)
        exact: Optional[Dict[str, Any]] = None
        partial: Optional[Dict[str, Any]] = None
        for p in self.iter_products():
            pn = _norm_name(p.get("name"))
            if pn == target:
                exact = p
                break
            # allow either direction of substring containment to tolerate prefixes like "DATALIN "
            if target in pn or pn in target:
                partial = partial or p
        return exact or partial

    def find_product_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        target = (key or "").strip().lower()
        # First try direct fetch (works on many Aha! tenants)
        try:
            prod = self.get_product(key)
            if prod and _norm_name(prod.get("name")):
                return prod
        except Exception:
            pass
        # Fallback: scan list
        for p in self.iter_products():
            if (p.get("reference_prefix") or p.get("key") or p.get("id") or "").__str__().strip().lower() == target:
                return p
        return None

    def find_product_by_path(self, path_segments: Iterable[str]) -> Optional[Dict[str, Any]]:
        segs = [s for s in (path_segments or []) if s]
        if not segs:
            return None
        leaf = _norm_name(segs[-1])
        candidates = [p for p in self.iter_products() if _norm_name(p.get("name")) == leaf]
        if not candidates:
            # try contains match as fallback
            candidates = [p for p in self.iter_products() if leaf in _norm_name(p.get("name"))]
        if len(candidates) == 1 or len(segs) == 1:
            return candidates[0] if candidates else None
        # Disambiguate by ancestor path
        desired_anc = [_norm_name(s) for s in segs[:-1]][::-1]  # from parent up
        best: Optional[Dict[str, Any]] = None
        for c in candidates:
            anc = [_norm_name(n) for n in self._collect_ancestor_names(self.get_product(str(c.get("id"))))]
            # anc is [parent, grandparent, ...]; check that desired is subsequence of anc
            i = 0
            for n in anc:
                if i < len(desired_anc) and desired_anc[i] in n:
                    i += 1
            if i == len(desired_anc):
                best = c
                break
        return best or (candidates[0] if candidates else None)

    # ---------- Releases ----------
    def list_releases_for_product(self, product_id: str) -> List[Dict[str, Any]]:
        data = self._get(f"/products/{product_id}/releases")
        return data.get("releases", [])

    def iter_release_ids_for_product(self, product_id: str) -> Iterable[str]:
        for r in self.list_releases_for_product(product_id) or []:
            rid = r.get("id")
            if rid:
                yield str(rid)

    def map_release_names_to_ids(self, product_id: str, release_names: Iterable[str]) -> Dict[str, Optional[str]]:
        releases = self.list_releases_for_product(product_id)
        name_to_id = {}
        index = {(_norm_name(r.get("name"))): r for r in releases}
        for name in release_names:
            rid = index.get(_norm_name(name), {}).get("id")
            name_to_id[name] = rid
        return name_to_id

    # ---------- Epics ----------
    def get_feature(self, feature_id: str) -> Dict[str, Any]:
        return self._get(f"/features/{feature_id}").get("feature", {})

    # ---------- Parallel helpers ----------
    async def _aget(self, async_client: httpx.AsyncClient, path: str) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        r = await async_client.get(url)
        r.raise_for_status()
        return r.json()

    async def _fetch_features_many(self, ids: List[str], concurrency: int = 15) -> List[Dict[str, Any]]:
        sem = asyncio.Semaphore(concurrency)
        async with httpx.AsyncClient(headers=self.headers, timeout=DEFAULT_TIMEOUT) as ac:
            async def one(fid: str):
                async with sem:
                    try:
                        data = await self._aget(ac, f"/features/{fid}")
                        return data.get("feature", {})
                    except Exception:
                        return {}
            tasks = [one(str(fid)) for fid in ids]
            return await asyncio.gather(*tasks)

    def fetch_features_many(self, ids: List[str], concurrency: int = 15) -> List[Dict[str, Any]]:
        if not ids:
            return []
        return asyncio.run(self._fetch_features_many(ids, concurrency=concurrency))

    def list_epics(self, *, page: int = 1, per_page: int = 50, tag: Optional[str] = None, assigned_to_user: Optional[str] = None, q: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"page": page, "per_page": per_page}
        if tag:
            params["tag"] = tag
        if assigned_to_user:
            params["assigned_to_user"] = assigned_to_user
        if q:
            params["q"] = q
        data = self._get("/epics", **params)
        return data.get("epics", [])

    def find_epics_by_query(self, q: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        page = 1
        while len(out) < limit:
            batch = self.list_epics(page=page, q=q)
            if not batch:
                break
            out.extend(batch)
            page += 1
        return out[:limit]

    def list_epics_for_release(self, release_id: str, *, page: int = 1, per_page: int = 50, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"page": page, "per_page": per_page}
        if tag:
            params["tag"] = tag
        data = self._get(f"/releases/{release_id}/epics", **params)
        return data.get("epics", [])

    def list_epics_for_product(self, product_id: str, *, page: int = 1, per_page: int = 50, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"page": page, "per_page": per_page}
        if tag:
            params["tag"] = tag
        data = self._get(f"/products/{product_id}/epics", **params)
        return data.get("epics", [])

    # ---------- Features ----------
    def list_features_for_release(self, release_id: str, *, page: int = 1, per_page: int = 200, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"page": page, "per_page": per_page}
        if tag:
            params["tag"] = tag
        data = self._get(f"/releases/{release_id}/features", **params)
        return data.get("features", [])

    def get_epic(self, epic_id: str) -> Dict[str, Any]:
        return self._get(f"/epics/{epic_id}").get("epic", {})

    def update_epic_tags(self, epic_id: str, tags: List[str]) -> Dict[str, Any]:
        # API: PUT /api/v1/epics/:id with body {"epic": {"tags": [..]}}
        payload = {"epic": {"tags": list(tags)}}
        return self._put(f"/epics/{epic_id}", json=payload).get("epic", {})

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

    def iter_release_features(self, release_id: str, *, tag: Optional[str] = None) -> Iterable[Dict[str, Any]]:
        page = 1
        while True:
            batch = self.list_features_for_release(release_id, page=page, per_page=200, tag=tag)
            if not batch:
                break
            for f in batch:
                yield f
            page += 1

    def iter_product_epics(self, product_id: str, *, tag: Optional[str] = None) -> Iterable[Dict[str, Any]]:
        page = 1
        while True:
            batch = self.list_epics_for_product(product_id, page=page, tag=tag)
            if not batch:
                break
            for e in batch:
                yield e
            page += 1
