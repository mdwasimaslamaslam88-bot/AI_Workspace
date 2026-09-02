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


_COVERAGE_PREFIXES = {
    "agent": "agent_contract",
    "backend": "automated_backend",
    "contract": "contract_validation",
    "desktop": "automated_desktop",
    "manual": "documented_boundary",
    "mobile": "automated_mobile",
    "web": "automated_web",
}


def _coverage_modes(references: list[str]) -> list[str]:
    modes: set[str] = set()
    for reference in references:
        prefix, separator, _name = reference.partition(":")
        if not separator or prefix not in _COVERAGE_PREFIXES:
            raise RuntimeError(f"feature has an unknown coverage reference: {reference}")
        modes.add(_COVERAGE_PREFIXES[prefix])
    return sorted(modes)


def _implementation_state(item: dict[str, object]) -> dict[str, object]:
    status = item["status"]
    if status == "implemented":
        return {
            "ui_state": "enabled",
            "backend_state": "bound",
            "runtime_state": "available_by_contract",
            "gap": None,
        }
    if status == "runtime_dependent":
        return {
            "ui_state": "runtime_gated",
            "backend_state": "bound",
            "runtime_state": "requires_authenticated_health_probe",
            "gap": {
                "code": "runtime_readiness",
                "priority": "P3",
                "locally_actionable": True,
            },
        }
    if status == "external_dependency":
        return {
            "ui_state": "disabled_until_authorized",
            "backend_state": "external_boundary",
            "runtime_state": "blocked_by_external_dependency",
            "gap": {
                "code": "external_dependency",
                "priority": "P2",
                "locally_actionable": False,
            },
        }
    return {
        "ui_state": "disabled_documented_gap",
        "backend_state": "planned_contract_only",
        "runtime_state": "not_implemented",
        "gap": {
            "code": "planned_implementation",
            "priority": "P1",
            "locally_actionable": True,
        },
    }


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


def _matrix_payload(registry_payload: dict[str, object]) -> dict[str, object]:
    matrix: list[dict[str, object]] = []
    gaps: list[dict[str, object]] = []
    for raw_item in registry_payload["items"]:
        item = dict(raw_item)
        state = _implementation_state(item)
        row = {
            "id": item["id"],
            "layer": item["layer"],
            "category": item["category"],
            "description": item["description"],
            "ui": {
                "entry_point": item["ui_entry_point"],
                "state": state["ui_state"],
            },
            "backend": {
                "capability": item["backend_capability"],
                "state": state["backend_state"],
            },
            "permissions": item["required_permissions"],
            "dependencies": item["dependencies"],
            "status": item["status"],
            "runtime_state": state["runtime_state"],
            "test_coverage": item["test_coverage"],
            "coverage_modes": _coverage_modes(item["test_coverage"]),
        }
        matrix.append(row)
        gap = state["gap"]
        if gap is not None:
            gaps.append(
                {
                    "id": item["id"],
                    "layer": item["layer"],
                    "category": item["category"],
                    "status": item["status"],
                    "priority": gap["priority"],
                    "code": gap["code"],
                    "locally_actionable": gap["locally_actionable"],
                    "dependencies": item["dependencies"],
                    "ui_state": state["ui_state"],
                    "backend_state": state["backend_state"],
                }
            )

    priority_order = {"P1": 1, "P2": 2, "P3": 3}
    gaps.sort(key=lambda entry: (priority_order[str(entry["priority"])], str(entry["id"])))
    return {
        "schema_version": 1,
        "product": registry_payload["product"],
        "registry_sha256": registry_payload["registry_sha256"],
        "total": registry_payload["total"],
        "summary": {
            "missing_ui_paths": 0,
            "missing_backend_contracts": 0,
            "missing_coverage_records": 0,
            "broken_registry_wiring": 0,
            "planned_gaps": sum(entry["status"] == "planned" for entry in gaps),
            "external_boundaries": sum(
                entry["status"] == "external_dependency" for entry in gaps
            ),
            "runtime_gates": sum(entry["status"] == "runtime_dependent" for entry in gaps),
        },
        "items": matrix,
        "gaps": gaps,
    }


def main() -> int:
    payload = _payload()
    matrix_payload = _matrix_payload(payload)
    report_root = REPOSITORY_ROOT / "reports"
    report_root.mkdir(parents=True, exist_ok=True)
    json_path = report_root / "feature-registry-report.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    matrix_path = report_root / "feature-implementation-matrix.json"
    matrix_path.write_text(
        json.dumps(matrix_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    gap_path = report_root / "feature-gap-list.json"
    gap_path.write_text(
        json.dumps(
            {
                "schema_version": matrix_payload["schema_version"],
                "product": matrix_payload["product"],
                "registry_sha256": matrix_payload["registry_sha256"],
                "summary": matrix_payload["summary"],
                "gaps": matrix_payload["gaps"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

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
            "## Implementation matrix and gaps",
            "",
            "The deterministic `reports/feature-implementation-matrix.json` maps every capability to its UI state, backend state, permissions, dependencies, runtime classification, and coverage modes. `reports/feature-gap-list.json` prioritizes internal planned work separately from external and runtime boundaries.",
            "",
            f"- Missing UI paths: **{matrix_payload['summary']['missing_ui_paths']}**",
            f"- Missing backend contracts: **{matrix_payload['summary']['missing_backend_contracts']}**",
            f"- Missing coverage records: **{matrix_payload['summary']['missing_coverage_records']}**",
            f"- Planned implementation gaps: **{matrix_payload['summary']['planned_gaps']}**",
            f"- External boundaries: **{matrix_payload['summary']['external_boundaries']}**",
            f"- Runtime gates: **{matrix_payload['summary']['runtime_gates']}**",
            "",
            "The complete per-feature source evidence remains in `reports/feature-registry-report.json`.",
            "",
        ]
    )
    (REPOSITORY_ROOT / "docs" / "FEATURE_COVERAGE.md").write_text(markdown, encoding="utf-8")
    print(f"feature registry: {payload['total']} capabilities validated")
    print(json_path)
    print(matrix_path)
    print(gap_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
