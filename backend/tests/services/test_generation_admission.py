import asyncio
from uuid import uuid4

import pytest

from app.core.config import MAX_GENERATION_ACTIVE_PER_PROCESS
from app.services.generation_admission import (
    GenerationAdmissionController,
    GenerationAdmissionRejectedError,
)


@pytest.mark.parametrize(
    "max_active",
    [
        None,
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_GENERATION_ACTIVE_PER_PROCESS + 1,
    ],
)
def test_admission_controller_rejects_invalid_process_caps(max_active):
    with pytest.raises((TypeError, ValueError)):
        GenerationAdmissionController(max_active)


@pytest.mark.parametrize(
    "max_active",
    [1, MAX_GENERATION_ACTIVE_PER_PROCESS],
)
def test_admission_controller_accepts_bounded_process_caps(max_active):
    controller = GenerationAdmissionController(max_active)

    assert controller.max_active == max_active
    assert controller._active_users == set()
    assert controller._active_count == 0


@pytest.mark.asyncio
async def test_same_user_is_rejected_without_waiting_and_released_once():
    controller = GenerationAdmissionController(2)
    user_id = uuid4()

    async with controller.admit(user_id):
        assert controller._active_users == {user_id}
        assert controller._active_count == 1
        with pytest.raises(GenerationAdmissionRejectedError):
            async with controller.admit(user_id):
                pytest.fail("same user must never be admitted twice")
        assert controller._active_users == {user_id}
        assert controller._active_count == 1

    assert controller._active_users == set()
    assert controller._active_count == 0

    async with controller.admit(user_id):
        assert controller._active_users == {user_id}
        assert controller._active_count == 1

    assert controller._active_users == set()
    assert controller._active_count == 0


@pytest.mark.asyncio
async def test_different_users_are_bounded_by_global_process_cap():
    controller = GenerationAdmissionController(2)
    first_user = uuid4()
    second_user = uuid4()
    rejected_user = uuid4()
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(user_id, entered):
        async with controller.admit(user_id):
            entered.set()
            await release.wait()

    first = asyncio.create_task(hold(first_user, first_entered))
    second = asyncio.create_task(hold(second_user, second_entered))
    await first_entered.wait()
    await second_entered.wait()

    assert controller._active_users == {first_user, second_user}
    assert controller._active_count == 2
    with pytest.raises(GenerationAdmissionRejectedError):
        async with controller.admit(rejected_user):
            pytest.fail("global process capacity must fail fast")

    release.set()
    await asyncio.gather(first, second)

    assert controller._active_users == set()
    assert controller._active_count == 0
    async with controller.admit(rejected_user):
        assert controller._active_users == {rejected_user}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("domain failure"), ValueError("unexpected failure")],
)
async def test_exceptions_release_permit_and_allow_next_request(failure):
    controller = GenerationAdmissionController(1)
    user_id = uuid4()

    with pytest.raises(type(failure), match=str(failure)):
        async with controller.admit(user_id):
            raise failure

    assert controller._active_users == set()
    assert controller._active_count == 0
    async with controller.admit(user_id):
        assert controller._active_count == 1


@pytest.mark.asyncio
async def test_cancelled_request_releases_permit_and_allows_next_request():
    controller = GenerationAdmissionController(1)
    user_id = uuid4()
    entered = asyncio.Event()
    blocked = asyncio.Event()

    async def admitted_work():
        async with controller.admit(user_id):
            entered.set()
            await blocked.wait()

    task = asyncio.create_task(admitted_work())
    await entered.wait()
    assert controller._active_users == {user_id}
    assert controller._active_count == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert controller._active_users == set()
    assert controller._active_count == 0
    async with controller.admit(user_id):
        assert controller._active_count == 1


@pytest.mark.asyncio
async def test_admission_requires_stable_user_uuid():
    controller = GenerationAdmissionController(1)

    with pytest.raises(TypeError, match="user_id must be a UUID"):
        async with controller.admit("bearer-token-text"):
            pytest.fail("bearer text must not become the admission identity")

    assert controller._active_users == set()
    assert controller._active_count == 0
