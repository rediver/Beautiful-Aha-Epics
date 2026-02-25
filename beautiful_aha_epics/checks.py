from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple
import re

from .config import FieldMap

CHECKMARK = "✅"
CROSS = "❌"
SPARKLES = "✨"
WARNING = "⚠️"
GITHUB = "🐙"


def _norm_text(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = ", ".join([str(x) for x in v if x is not None])
    s = str(v)
    # strip trivial HTML wrappers from Aha descriptions
    s = re.sub(r"<[^>]+>", " ", s)
    return s.strip()


def _has_github(url_or_text: str) -> bool:
    return "github.com" in (url_or_text or "").lower()


@dataclass
class BeautyResult:
    epic_id: str
    reference_num: str
    name: str
    release_name: str | None
    ok: bool
    problems: List[str]


def evaluate_epic(epic: Dict, fields: FieldMap, *, required_tag_all: List[str], required_tag_one_of: List[str], pm_owner_expect: str | None) -> BeautyResult:
    problems: List[str] = []

    ref = epic.get("reference_num") or epic.get("reference_num_with_prefix") or epic.get("reference_num_with_sequence") or epic.get("reference_num_prefix") or epic.get("id")
    name = epic.get("name") or epic.get("title") or "<no title>"
    release = None
    if isinstance(epic.get("release"), dict):
        release = epic.get("release", {}).get("name")
    release = release or epic.get("release_name")

    # Enrich: custom fields
    custom = epic.get("custom_fields") or {}

    # Description
    if not _norm_text(epic.get("description")):
        problems.append("Missing description")

    # Status == new
    status = (epic.get("workflow_status", {}) or {}).get("name") or epic.get("status") or epic.get("workflow_status_name")
    if (status or "").strip().lower() == "new":
        problems.append("Status is 'New'")

    # Solution Value Statement
    if not _norm_text(custom.get(fields.solution_value_statement)):
        problems.append("Empty Solution Value Statement")

    # Risk status
    if not _norm_text(custom.get(fields.risk_status)):
        problems.append("Empty risk status")

    # Commitment
    if not _norm_text(custom.get(fields.commitment)):
        problems.append("Empty Commitment")

    # Release present
    if not (epic.get("release_id") or release):
        problems.append("Missing Release")

    # Master epic present
    if not _norm_text(custom.get(fields.master_epic)):
        problems.append("Missing Master Epic")

    # Integrations -> link to GitHub
    gh_found = False
    for k in fields.github_link:
        if _has_github(_norm_text(custom.get(k))):
            gh_found = True
            break
    # some accounts expose integration_fields
    if not gh_found:
        for f in epic.get("integration_fields", []) or []:
            if _has_github(f.get("url") or f.get("name")):
                gh_found = True
                break
    if not gh_found:
        problems.append("Missing GitHub integration/link")

    # Tags
    tags_raw = epic.get("tags") or []
    tags = set([str(t).strip().lower() for t in tags_raw])
    for t in required_tag_all:
        if t.lower() not in tags:
            problems.append(f"Missing required tag '{t}'")
    if required_tag_one_of:
        if not any(t.lower() in tags for t in required_tag_one_of):
            problems.append("Missing at least one of tags: " + ", ".join(required_tag_one_of))

    # Product Management owner
    pm_value = _norm_text(custom.get(fields.product_management_owner))
    if not pm_value:
        problems.append("Product Management owner is empty")
    elif pm_owner_expect and pm_value.lower() != pm_owner_expect.strip().lower():
        problems.append(f"Product Management owner not '{pm_owner_expect}' (is '{pm_value}')")

    # Development owner
    if not _norm_text(custom.get(fields.development_owner)):
        problems.append("Development owner is empty")

    # IBM Software GTM Themes
    if not _norm_text(custom.get(fields.ibm_software_gtm_themes)):
        problems.append("IBM Software GTM Themes is empty")

    # Priority (Data & AI) 1..10
    prio_raw = _norm_text(custom.get(fields.priority_data_ai))
    ok_num = False
    if prio_raw:
        try:
            n = int(re.findall(r"\d+", prio_raw)[0])
            ok_num = 1 <= n <= 10
        except Exception:
            ok_num = False
    if not ok_num:
        problems.append("Priority (Data & AI) empty or not in 1..10")

    ok = len(problems) == 0
    return BeautyResult(
        epic_id=str(epic.get("id")),
        reference_num=str(ref),
        name=str(name),
        release_name=release,
        ok=ok,
        problems=problems,
    )
