from __future__ import annotations

import hashlib
import json
import re

from app.agent_os.contracts import (
    AgentExecution,
    AgentPlanStep,
    VerificationCheck,
    VerificationFailure,
)


SOURCE_BLOCK_START = "BEGIN_UNTRUSTED_MARKET_SOURCES\n"
SOURCE_BLOCK_END = "\nEND_UNTRUSTED_MARKET_SOURCES"
_MARKET_TASK = re.compile(r"^Market task: (research|strategy)$", re.MULTILINE)
_PROFIT_CLAIM = re.compile(
    r"\b(?:guaranteed?|assured?)\s+(?:returns?|profits?|gains?)\b"
    r"|\b(?:will|always)\s+(?:rise|increase|profit|gain)\b",
    re.IGNORECASE,
)
_NEGATED_CLAIM = re.compile(
    r"\b(?:no|not|without|never|cannot|can't|doesn't|do not)\b[^.!?\n]{0,32}$",
    re.IGNORECASE,
)


def _source_references(instruction: str) -> tuple[str, ...] | None:
    before, marker, remainder = instruction.partition(SOURCE_BLOCK_START)
    if not marker or not _MARKET_TASK.search(before):
        return None
    payload, closing, trailing = remainder.partition(SOURCE_BLOCK_END)
    if not closing or SOURCE_BLOCK_START in trailing or SOURCE_BLOCK_END in trailing:
        raise ValueError("market source envelope is invalid")
    decoded = json.loads(payload)
    if not isinstance(decoded, list) or not 1 <= len(decoded) <= 16:
        raise ValueError("market sources are invalid")
    references: list[str] = []
    for item in decoded:
        if (
            not isinstance(item, dict)
            or set(item) != {"fact", "source_reference"}
            or not isinstance(item["fact"], str)
            or not item["fact"]
            or not isinstance(item["source_reference"], str)
            or not item["source_reference"]
        ):
            raise ValueError("market source entry is invalid")
        references.append(item["source_reference"])
    if len(set(references)) != len(references):
        raise ValueError("market source references must be unique")
    return tuple(references)


async def verify_grounded_market_output(
    step: AgentPlanStep,
    execution: AgentExecution,
) -> VerificationCheck | None:
    """Validate the untouched market response against its typed source envelope."""

    references = _source_references(step.instruction)
    if references is None:
        return None
    task_match = _MARKET_TASK.search(step.instruction)
    if task_match is None:  # pragma: no cover - guarded by _source_references
        raise ValueError("market task is unavailable")
    required_terms = (
        ("uncert", "counter")
        if task_match.group(1) == "research"
        else ("entry", "exit", "risk", "invalid")
    )
    folded = execution.output.casefold()
    makes_profit_claim = any(
        _NEGATED_CLAIM.search(execution.output[: match.start()]) is None
        for match in _PROFIT_CLAIM.finditer(execution.output)
    )
    passed = (
        all(reference in execution.output for reference in references)
        and all(term in folded for term in required_terms)
        and not makes_profit_claim
    )
    evidence = hashlib.sha256(
        ("\x00".join(references) + "\x00" + execution.output).encode("utf-8")
    ).hexdigest()
    return VerificationCheck(
        check_id="grounded-market-contract",
        passed=passed,
        failure=(
            VerificationFailure.NONE
            if passed
            else VerificationFailure.OBJECTIVE_CHECK_FAILED
        ),
        evidence_sha256=evidence,
    )
