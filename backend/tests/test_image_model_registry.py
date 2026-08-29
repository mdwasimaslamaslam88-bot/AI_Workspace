import pytest

from app.images import ImageModelStatus, image_model_contract


def test_current_sdxl_is_a_capability_profile_not_a_permanent_special_case():
    current = image_model_contract("sdxl-base-1.0")
    future = image_model_contract("flux-family-local")

    assert current.generation and current.editing
    assert current.status is ImageModelStatus.RUNNABLE_NOW
    assert future.generation and future.editing
    assert future.status is ImageModelStatus.NOT_INSTALLED
    assert future.workflow_adapter != current.workflow_adapter


def test_unknown_image_profile_fails_closed():
    with pytest.raises(ValueError, match="not registered"):
        image_model_contract("untrusted-remote-model")
