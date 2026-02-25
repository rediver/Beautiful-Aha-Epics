from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml


@dataclass
class FieldMap:
    solution_value_statement: str = "solution_value_statement"
    risk_status: str = "risk_status"
    commitment: str = "commitment"
    master_epic: str = "master_epic"
    github_link: List[str] = field(default_factory=lambda: ["github_link", "integrations_to"])  # any of these fields containing a GitHub URL
    product_management_owner: str = "product_management_owner"
    development_owner: str = "development_owner"
    ibm_software_gtm_themes: str = "ibm_software_gtm_themes"
    priority_data_ai: str = "priority_data_ai"


@dataclass
class Filters:
    releases: List[str] = field(default_factory=list)
    tags_include: List[str] = field(default_factory=lambda: ["scanners"])  # must include all
    tags_one_of: List[str] = field(default_factory=lambda: ["lineage dev commited"])  # at least one present
    pm_owner: Optional[str] = None


@dataclass
class AppConfig:
    product_name: Optional[str] = None
    account: Optional[str] = None
    filters: Filters = field(default_factory=Filters)
    fields: FieldMap = field(default_factory=FieldMap)

    @staticmethod
    def load(path: Optional[str] = None) -> "AppConfig":
        path = path or os.getenv("BAE_CONFIG", "bae.config.yaml")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        else:
            raw = {}
        cfg = AppConfig()
        # Basic
        cfg.product_name = raw.get("product_name") or os.getenv("BAE_PRODUCT_NAME")
        cfg.account = raw.get("account") or os.getenv("BAE_AHA_ACCOUNT")
        # Filters
        f_raw = raw.get("filters", {})
        cfg.filters.releases = list(f_raw.get("releases", []))
        cfg.filters.tags_include = list(f_raw.get("tags_include", cfg.filters.tags_include))
        cfg.filters.tags_one_of = list(f_raw.get("tags_one_of", cfg.filters.tags_one_of))
        cfg.filters.pm_owner = f_raw.get("pm_owner") or os.getenv("BAE_PM_OWNER")
        # Fields mapping
        m_raw: Dict[str, any] = raw.get("fields", {})
        for k in FieldMap().__dict__.keys():
            if k in m_raw and m_raw[k] is not None:
                setattr(cfg.fields, k, m_raw[k])
        return cfg

    @staticmethod
    def dump_example(path: str = "bae.config.example.yaml") -> None:
        example = {
            "product_name": "DATALIN Data Lineage by Manta",
            "account": "bigblue",
            "filters": {
                "releases": [
                    "Q1 2026 - IKC 5.3.1 and DI 2.3.1",
                    "February SaaS",
                    "June 2026 - IKC 5.4 and DI 2.4",
                    "Plan to remove -- Q3 2026",
                    "Dec 2026 - IKC 5.5 and DI 2.5",
                ],
                "tags_include": ["scanners"],
                "tags_one_of": ["lineage dev commited"],
                "pm_owner": "wojtek smajda",
            },
            "fields": {
                "solution_value_statement": "solution_value_statement",
                "risk_status": "risk_status",
                "commitment": "commitment",
                "master_epic": "master_epic",
                "github_link": ["github_link", "integrations_to"],
                "product_management_owner": "product_management_owner",
                "development_owner": "development_owner",
                "ibm_software_gtm_themes": "ibm_software_gtm_themes",
                "priority_data_ai": "priority_data_ai",
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(example, f, sort_keys=False, allow_unicode=True)
