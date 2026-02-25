from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any
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
    s = (url_or_text or "").lower()
    # accept github.com, github.ibm.com, and other enterprise hosts + generic 'github' marker
    return ("github" in s) or ("git" in s and "http" in s)


def _custom_to_dict(cf: Any) -> Dict[str, Any]:
    if isinstance(cf, dict):
        return cf
    if isinstance(cf, list):
        out = {}
        for item in cf:
            if isinstance(item, dict):
                k = item.get("key") or item.get("name")
                # Prefer email_value when present, then value/text/url/id
                ev = item.get("email_value")
                if isinstance(ev, list) and ev:
                    v = ev
                else:
                    v = item.get("value") if "value" in item else item.get("text_value") or item.get("url") or item.get("id")
                if k is not None:
                    out[str(k)] = v
        return out
    return {}


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
    custom = _custom_to_dict(epic.get("custom_fields"))

    # Description
    if not _norm_text(epic.get("description")):
        problems.append("Missing description")

    # Status == new (prefer the latest from workflow_status_times where ended_at is null)
    status = (epic.get("workflow_status", {}) or {}).get("name") or epic.get("status") or epic.get("workflow_status_name")
    wst = epic.get("workflow_status_times") or []
    current = None
    for rec in wst:
        if rec and rec.get("ended_at") in (None, ""):
            current = rec.get("status_name")
    if current:
        status = current
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
            vals = [f.get("url"), f.get("name"), f.get("value"), f.get("service_name")]
            if any(_has_github(v or "") for v in vals):
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
    elif pm_owner_expect and pm_value.strip().lower() != str(pm_owner_expect).strip().lower():
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


def evaluate_feature(feature: Dict, fields: FieldMap, *, required_tag_all: List[str], required_tag_one_of: List[str], pm_owner_expect: str | None) -> BeautyResult:
    problems: List[str] = []

    ref = feature.get("reference_num") or feature.get("id")
    name = feature.get("name") or "<no title>"

    # Description is nested under description.body
    desc = feature.get("description", {})
    desc_text = _norm_text((desc or {}).get("body")) if isinstance(desc, dict) else _norm_text(desc)
    if not desc_text:
        problems.append("Missing description")

    # Status == new (prefer current from workflow_status_times)
    status = (feature.get("workflow_status", {}) or {}).get("name") or feature.get("status")
    wst = feature.get("workflow_status_times") or []
    current = None
    for rec in wst:
        if rec and rec.get("ended_at") in (None, ""):
            current = rec.get("status_name")
    if current:
        status = current
    if (status or "").strip().lower() == "new":
        problems.append("Status is 'New'")

    # Custom fields normalization
    custom = _custom_to_dict(feature.get("custom_fields"))

    # Solution Value Statement
    if not _norm_text(custom.get(fields.solution_value_statement)):
        # common alt key on features
        if not _norm_text(custom.get("client_value_statement")):
            problems.append("Empty Solution Value Statement")

    # Risk status
    if not _norm_text(custom.get(fields.risk_status)):
        problems.append("Empty risk status")

    # Commitment
    if not _norm_text(custom.get(fields.commitment)):
        # some tenants keep 'committed' as key
        if not _norm_text(custom.get("committed")):
            problems.append("Empty Commitment")

    # Release present with key fields
    rel = feature.get("release")
    release_name = None
    if isinstance(rel, dict):
        release_name = rel.get("name")
        if not rel.get("start_date") or not rel.get("release_date"):
            problems.append("Release missing start_date or release_date")
    if not (feature.get("release_id") or release_name):
        problems.append("Missing Release")

    # Master epic present (relationship or managed tag)
    has_master = False
    if isinstance(feature.get("epic"), dict) or isinstance(feature.get("master_feature"), dict):
        has_master = True
    elif _norm_text(custom.get(fields.master_epic)) or _norm_text(custom.get("ibm_software_only_managed_tags_epic")):
        has_master = True
    if not has_master:
        problems.append("Missing Master Epic")

    # Integrations -> link to GitHub
    gh_found = False
    for f in feature.get("integration_fields", []) or []:
        if _has_github((f.get("value") or f.get("name") or "")):
            gh_found = True
            break
    if not gh_found:
        for k in fields.github_link:
            if _has_github(_norm_text(custom.get(k))):
                gh_found = True
                break
    if not gh_found:
        problems.append("Missing GitHub integration/link")

    # Tags
    tags = set([str(t).strip().lower() for t in (feature.get("tags") or [])])
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
    elif pm_owner_expect and pm_value.strip().lower() != pm_owner_expect.strip().lower():
        problems.append(f"Product Management owner not '{pm_owner_expect}' (is '{pm_value}')")

    # Development owner
    if not _norm_text(custom.get(fields.development_owner)):
        problems.append("Development owner is empty")

    # IBM Software GTM Themes
    if not _norm_text(custom.get(fields.ibm_software_gtm_themes)):
        problems.append("IBM Software GTM Themes is empty")

    # Priority (Data & AI) 1..10 (features may use 'priority_data_and_ai')
    prio_val = _norm_text(custom.get(fields.priority_data_ai)) or _norm_text(custom.get("priority_data_and_ai")) or _norm_text(custom.get("priority"))
    ok_num = False
    if prio_val:
        try:
            n = int(re.findall(r"\d+", prio_val)[0])
            ok_num = 1 <= n <= 10
        except Exception:
            ok_num = False
    if not ok_num:
        problems.append("Priority (Data & AI) empty or not in 1..10")

    ok = len(problems) == 0
    return BeautyResult(
        epic_id=str(feature.get("id")),
        reference_num=str(ref),
        name=str(name),
        release_name=release_name,
        ok=ok,
        problems=problems,
    )
