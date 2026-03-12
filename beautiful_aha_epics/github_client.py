from __future__ import annotations
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx


@dataclass
class GitHubEndpoints:
    host: str
    api_base: str
    graphql_url: str

    @staticmethod
    def for_host(host: str) -> "GitHubEndpoints":
        h = (host or "github.com").strip().lower()
        if h == "github.com":
            return GitHubEndpoints(
                host=h,
                api_base="https://api.github.com",
                graphql_url="https://api.github.com/graphql",
            )
        # default Enterprise endpoints
        return GitHubEndpoints(
            host=h,
            api_base=f"https://{h}/api/v3",
            graphql_url=f"https://{h}/api/graphql",
        )


class GitHubClient:
    """Minimal GitHub API client to fetch per-project Status for issues/PRs.

    Auth priority:
      1) Explicit token passed in
      2) Token from env (GITHUB_TOKEN, GH_TOKEN, GH_ENTERPRISE_TOKEN)
      3) Token from gh CLI: `gh auth token -h <host>`
    """

    def __init__(
        self,
        *,
        host: str = "github.com",
        token: Optional[str] = None,
        api_base: Optional[str] = None,
        graphql_url: Optional[str] = None,
        debug: bool = False,
    ) -> None:
        eps = GitHubEndpoints.for_host(host)
        self.endpoints = GitHubEndpoints(
            host=eps.host,
            api_base=api_base or eps.api_base,
            graphql_url=graphql_url or eps.graphql_url,
        )
        self.debug = debug
        self.token = token or self._resolve_token()
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=httpx.Timeout(20.0, connect=20.0),
        )

    def _resolve_token(self) -> str:
        # 1) Env
        for k in ("GITHUB_TOKEN", "GH_TOKEN", "GH_ENTERPRISE_TOKEN"):
            v = os.getenv(k)
            if v:
                return v
        # 2) gh CLI for a specific host
        try:
            out = subprocess.run(
                ["gh", "auth", "token", "-h", self.endpoints.host],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            tok = (out.stdout or "").strip()
            if tok:
                return tok
        except Exception:
            pass
        raise RuntimeError(
            f"No GitHub token available for {self.endpoints.host}. Login with 'gh auth login -h {self.endpoints.host}' or set GITHUB_TOKEN."
        )

    def graphql(self, query: str, variables: Dict[str, object]) -> Dict:
        r = self._client.post(self.endpoints.graphql_url, json={"query": query, "variables": variables})
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            raise RuntimeError(f"GitHub GraphQL error: {data['errors']}")
        return data.get("data") or {}

    @staticmethod
    def parse_github_url(url: str) -> Optional[Tuple[str, str, str, str, int]]:
        """Return (host, owner, repo, kind, number) if URL points to an issue/PR, else None."""
        try:
            # Remove anchors/query
            u = re.sub(r"[#?].*$", "", (url or "").strip())
            m = re.match(r"^https?://([^/]+)/([^/]+)/([^/]+)/(issues|pull|pulls)/(\d+)", u)
            if not m:
                return None
            host, owner, repo, kind, num = m.group(1), m.group(2), m.group(3), m.group(4), int(m.group(5))
            if kind == "pulls":
                kind = "pull"
            return host, owner, repo, kind, num
        except Exception:
            return None

    def fetch_project_statuses(self, owner: str, repo: str, number: int, *, is_pull: bool | None = None) -> List[str]:
        """Return list of "Project: Status" strings across Projects v2 and classic columns."""
        q = (
            "query($owner:String!,$name:String!,$number:Int!){\n"
            "  repository(owner:$owner,name:$name){\n"
            "    issue(number:$number){\n"
            "      projectItems(first:20){nodes{project{title} fieldValues(first:20){nodes{__typename\n"
            "        ... on ProjectV2ItemFieldSingleSelectValue { field { name } name }\n"
            "        ... on ProjectV2ItemFieldTextValue { field { name } text }\n"
            "      }}}}\n"
            "      projectCards(first:20){nodes{column{name} project{name}}}\n"
            "      state\n"
            "    }\n"
            "    pullRequest(number:$number){\n"
            "      projectItems(first:20){nodes{project{title} fieldValues(first:20){nodes{__typename\n"
            "        ... on ProjectV2ItemFieldSingleSelectValue { field { name } name }\n"
            "        ... on ProjectV2ItemFieldTextValue { field { name } text }\n"
            "      }}}}\n"
            "      projectCards(first:20){nodes{column{name} project{name}}}\n"
            "      state\n"
            "    }\n"
            "  }\n"
            "}"
        )
        data = self.graphql(q, {"owner": owner, "name": repo, "number": number})
        repo_data = (data or {}).get("repository") or {}
        out: List[str] = []

        def collect(node: Dict) -> None:
            # Projects v2
            try:
                for it in (node.get("projectItems") or {}).get("nodes", []) or []:
                    proj = ((it or {}).get("project") or {}).get("title") or "Project"
                    status_val = None
                    for fv in ((it or {}).get("fieldValues") or {}).get("nodes", []) or []:
                        field_name = (((fv or {}).get("field") or {}).get("name") or "").strip().lower()
                        if field_name == "status":
                            if fv.get("__typename") == "ProjectV2ItemFieldSingleSelectValue":
                                status_val = fv.get("name")
                            elif fv.get("__typename") == "ProjectV2ItemFieldTextValue":
                                status_val = fv.get("text")
                            if status_val:
                                out.append(f"{proj}: {status_val}")
                                break
            except Exception:
                pass
            # Classic projects
            try:
                for c in (node.get("projectCards") or {}).get("nodes", []) or []:
                    col = ((c or {}).get("column") or {}).get("name")
                    proj = ((c or {}).get("project") or {}).get("name")
                    if proj and col:
                        out.append(f"{proj}: {col}")
            except Exception:
                pass

        if is_pull is None or is_pull is False:
            issue = repo_data.get("issue") or {}
            if issue:
                collect(issue)
        if is_pull is None or is_pull is True:
            pr = repo_data.get("pullRequest") or {}
            if pr:
                collect(pr)
        # Deduplicate while preserving order
        seen = set()
        uniq: List[str] = []
        for s in out:
            if s not in seen:
                uniq.append(s)
                seen.add(s)
        return uniq
