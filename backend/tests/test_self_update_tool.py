import importlib.util
from pathlib import Path
from unittest.mock import Mock

from app.maintenance import UpdateState, UpdateStatus


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "self_update_tool.py"
SPEC = importlib.util.spec_from_file_location("self_update_tool", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
self_update_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(self_update_tool)


def test_public_state_does_not_offer_a_failed_checkpoint():
    manager = Mock(
        state=Mock(
            return_value=UpdateState(
                status=UpdateStatus.FAILED,
                checkpoint_id="checkpoint-one",
                failure_code="candidate_superseded",
            )
        )
    )

    payload = self_update_tool._public_state(manager)

    assert payload["status"] == "failed"
    assert payload["checkpoint_ready"] is False
    assert payload["rollback_ready"] is False


def test_public_state_offers_only_an_eligible_ready_checkpoint():
    manager = Mock(
        state=Mock(
            return_value=UpdateState(
                status=UpdateStatus.READY,
                checkpoint_id="checkpoint-two",
            )
        )
    )

    payload = self_update_tool._public_state(manager)

    assert payload["checkpoint_ready"] is True
    assert payload["rollback_ready"] is True
