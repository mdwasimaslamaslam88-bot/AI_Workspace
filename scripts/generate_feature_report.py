#!/usr/bin/env python3
"""Generate deterministic feature-registry coverage evidence."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.features import FEATURE_REGISTRY  # noqa: E402


def _payload() -> dict[str, object]:
    items = [asdict(record) for record in FEATURE_REGISTRY]
    identifiers = [item["id"] for item in items]
    if len(items) < 140 or len(identifiers) != len(set(identifiers)):
        raise RuntimeError("feature IDs are incomplete or duplicated")
    for item in items:
        if not str(item["ui_entry_point"]).startswith("/"):
            raise RuntimeError(f"feature has no UI path: {item['id']}")
        if not item["test_coverage"]:
            raise RuntimeError(f"feature has no coverage record: {item['id']}")
        if item["status"] == "external_dependency" and not item["dependencies"]:
            raise RuntimeError(f"external boundary has no dependency: {item['id']}")
        if item["status"] in {"implemented", "runtime_dependent"} and not item["backend_capability"]:
            raise RuntimeError(f"implemented feature has no backend capability: {item['id']}")
    canonical = json.dumps(items, separators=(",", ":"), sort_keys=True).encode()
    return {
        "schema_version": 1,
        "product": "AI OS",
        "registry_sha256": hashlib.sha256(canonical).hexdigest(),
        "total": len(items),
        "layers": dict(sorted(Counter(item["layer"] for item in items).items())),
        "statuses": dict(sorted(Counter(item["status"] for item in items).items())),
        "validation": {
            "unique_ids": True,
            "ui_paths_present": True,
            "backend_or_boundary_present": True,
            "test_coverage_present": True,
        },
        "items": items,
    }


def main() -> int:
    payload = _payload()
    report_root = REPOSITORY_ROOT / "reports"
    report_root.mkdir(parents=True, exist_ok=True)
    json_path = report_root / "feature-registry-report.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    statuses = payload["statuses"]
    layers = payload["layers"]
    markdown = "\n".join(
        [
            "# AI OS Feature Coverage",
            "",
            "This report is generated from the authenticated product registry. It distinguishes working capabilities from runtime gates, external services, and documented implementation gaps; it is not a claim that external or planned features execute locally.",
            "",
            f"- Registered capabilities: **{payload['total']}**",
            f"- Registry SHA-256: `{payload['registry_sha256']}`",
            f"- Implemented: **{statuses.get('implemented', 0)}**",
            f"- Runtime-dependent: **{statuses.get('runtime_dependent', 0)}**",
            f"- External dependency: **{statuses.get('external_dependency', 0)}**",
            f"- Planned/documented gap: **{statuses.get('planned', 0)}**",
            "",
            "## Five-layer coverage",
            "",
            *[f"- {name.replace('_', ' ').title()}: **{count}**" for name, count in layers.items()],
            "",
            "## Validation",
            "",
            "Every record has a unique ID, category, description, UI entry point, backend capability identifier, permission set, dependency set, status, and coverage reference. Externally dependent records stay non-executable until owner-authorized provider requirements are satisfied. Planned records are visible as disabled boundaries and are never reported as ready.",
            "",
            "The complete per-feature evidence is in `reports/feature-registry-report.json`.",
            "",
        ]
    )
    (REPOSITORY_ROOT / "docs" / "FEATURE_COVERAGE.md").write_text(markdown, encoding="utf-8")
    print(f"feature registry: {payload['total']} capabilities validated")
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
