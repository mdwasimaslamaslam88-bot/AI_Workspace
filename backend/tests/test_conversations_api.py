from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.dependencies as authentication_module
import app.api.v1.conversations as conversations_module
from app.api.dependencies import get_current_user
from app.core.security import digest_access_token
from app.db.dependencies import get_db_session
from app.main import app
from app.models import Conversation, Message, MessageRole, User
from app.repositories.conversation import (
    DEFAULT_CONVERSATION_PAGE_SIZE,
    MAX_CONVERSATION_PAGE_SIZE,
    ConversationCursor,
    ConversationPage,
    ConversationPagination,
)
from app.repositories.message import (
    DEFAULT_MESSAGE_PAGE_SIZE,
    MAX_MESSAGE_PAGE_SIZE,
    MessageCursor,
    MessagePage,
    MessagePagination,
)
from app.services.message import MessageAppendConflictError


@pytest.fixture
def conversation_api(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    current_user = User(
        id=uuid4(),
        created_at=datetime(2026, 8, 11, 8, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 11, 8, 31, tzinfo=timezone.utc),
    )
    conversation = Conversation(
        id=uuid4(),
        owner_id=current_user.id,
        title="  Exact title  ",
        created_at=datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 11, 9, 1, tzinfo=timezone.utc),
    )
    initial_message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="  Exact initial content  ",
        sequence_number=1,
        created_at=datetime(2026, 8, 11, 9, 0, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 11, 9, 0, 2, tzinfo=timezone.utc),
    )
    appended_message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="  Exact follow-up content  ",
        sequence_number=2,
        created_at=datetime(2026, 8, 11, 9, 2, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 11, 9, 3, tzinfo=timezone.utc),
    )
    create = AsyncMock(return_value=(conversation, initial_message))
    get_conversation = AsyncMock(return_value=conversation)
    rename_conversation = AsyncMock(return_value=conversation)
    delete_conversation = AsyncMock(return_value=True)
    list_conversations = AsyncMock(
        return_value=ConversationPage(items=(conversation,), next_cursor=None)
    )
    service = Mock(
        create_with_initial_message_for_owner=create,
        delete_for_owner=delete_conversation,
        get_for_owner=get_conversation,
        list_for_owner=list_conversations,
        rename_for_owner=rename_conversation,
    )
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(
        conversations_module,
        "ConversationService",
        service_factory,
    )
    append = AsyncMock(return_value=appended_message)
    list_messages = AsyncMock(
        return_value=MessagePage(
            items=(initial_message, appended_message),
            next_cursor=None,
        )
    )
    message_service = Mock(
        append_for_owner=append,
        list_for_owner=list_messages,
    )
    message_service_factory = Mock(return_value=message_service)
    monkeypatch.setattr(
        conversations_module,
        "MessageService",
        message_service_factory,
    )

    async def override_db_session():
        yield session

    async def override_current_user():
        return current_user

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = override_current_user
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield {
                "client": client,
                "session": session,
                "current_user": current_user,
                "conversation": conversation,
                "initial_message": initial_message,
                "appended_message": appended_message,
                "service_factory": service_factory,
                "create": create,
                "delete_conversation": delete_conversation,
                "get_conversation": get_conversation,
                "rename_conversation": rename_conversation,
                "list_conversations": list_conversations,
                "message_service_factory": message_service_factory,
                "append": append,
                "list_messages": list_messages,
            }
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


def test_list_conversations_returns_200_with_exact_safe_default_page(
    conversation_api,
):
    api = conversation_api

    response = api["client"].get("/api/v1/conversations")

    conversation = api["conversation"]
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(conversation.id),
                "title": "  Exact title  ",
                "created_at": "2026-08-11T09:00:00Z",
                "updated_at": "2026-08-11T09:01:00Z",
            }
        ],
        "next_cursor": None,
    }
    response_text = response.text.lower()
    assert "owner_id" not in response_text
    assert "next_message_sequence" not in response_text
    assert "messages" not in response_text
    assert "credential" not in response_text
    assert "digest" not in response_text
    api["service_factory"].assert_called_once_with(api["session"])
    api["list_conversations"].assert_awaited_once_with(
        api["current_user"].id,
        ConversationPagination(limit=DEFAULT_CONVERSATION_PAGE_SIZE),
    )
    api["message_service_factory"].assert_not_called()
    api["session"].commit.assert_not_awaited()
    api["session"].rollback.assert_not_awaited()


def test_list_conversations_reuses_composite_cursor_without_duplicates(
    conversation_api,
):
    api = conversation_api
    newer = Conversation(
        id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        owner_id=api["current_user"].id,
        title="Newer",
        created_at=datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
    )
    older = Conversation(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_id=api["current_user"].id,
        title="Older",
        created_at=datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc),
    )
    cursor = ConversationCursor(updated_at=newer.updated_at, id=newer.id)
    api["list_conversations"].side_effect = [
        ConversationPage(items=(newer,), next_cursor=cursor),
        ConversationPage(items=(older,), next_cursor=None),
    ]

    first_response = api["client"].get(
        "/api/v1/conversations",
        params={"limit": 1},
    )
    first_payload = first_response.json()
    second_response = api["client"].get(
        "/api/v1/conversations",
        params={
            "limit": 1,
            "cursor_updated_at": first_payload["next_cursor"]["updated_at"],
            "cursor_id": first_payload["next_cursor"]["id"],
        },
    )
    second_payload = second_response.json()

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_payload["next_cursor"] == {
        "updated_at": "2026-08-11T10:00:00Z",
        "id": str(newer.id),
    }
    assert second_payload["next_cursor"] is None
    first_ids = {item["id"] for item in first_payload["items"]}
    second_ids = {item["id"] for item in second_payload["items"]}
    assert first_ids == {str(newer.id)}
    assert second_ids == {str(older.id)}
    assert first_ids.isdisjoint(second_ids)
    assert api["list_conversations"].await_args_list[0].args == (
        api["current_user"].id,
        ConversationPagination(limit=1),
    )
    assert api["list_conversations"].await_args_list[1].args == (
        api["current_user"].id,
        ConversationPagination(limit=1, cursor=cursor),
    )


def test_list_conversations_preserves_equal_timestamp_uuid_descending_order(
    conversation_api,
):
    api = conversation_api
    shared_updated_at = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
    higher_id = Conversation(
        id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        owner_id=api["current_user"].id,
        title="Higher UUID",
        created_at=shared_updated_at,
        updated_at=shared_updated_at,
    )
    lower_id = Conversation(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        owner_id=api["current_user"].id,
        title="Lower UUID",
        created_at=shared_updated_at,
        updated_at=shared_updated_at,
    )
    api["list_conversations"].return_value = ConversationPage(
        items=(higher_id, lower_id),
        next_cursor=None,
    )

    response = api["client"].get("/api/v1/conversations")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        str(higher_id.id),
        str(lower_id.id),
    ]


def test_list_conversations_uses_each_current_user_and_returns_empty_new_user(
    conversation_api,
):
    api = conversation_api
    first_user_id = api["current_user"].id
    second_user_id = uuid4()
    new_user_id = uuid4()
    second_conversation = Conversation(
        id=uuid4(),
        owner_id=second_user_id,
        title="Second owner",
        created_at=datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc),
    )
    api["list_conversations"].side_effect = [
        ConversationPage(items=(api["conversation"],), next_cursor=None),
        ConversationPage(items=(second_conversation,), next_cursor=None),
        ConversationPage(items=(), next_cursor=None),
    ]

    first_response = api["client"].get("/api/v1/conversations")
    api["current_user"].id = second_user_id
    second_response = api["client"].get("/api/v1/conversations")
    api["current_user"].id = new_user_id
    empty_response = api["client"].get("/api/v1/conversations")

    assert [item["id"] for item in first_response.json()["items"]] == [
        str(api["conversation"].id)
    ]
    assert [item["id"] for item in second_response.json()["items"]] == [
        str(second_conversation.id)
    ]
    assert empty_response.json() == {"items": [], "next_cursor": None}
    assert [call.args[0] for call in api["list_conversations"].await_args_list] == [
        first_user_id,
        second_user_id,
        new_user_id,
    ]


@pytest.mark.parametrize("identity_field", ["owner_id", "user_id"])
def test_list_conversations_rejects_client_identity_before_service(
    conversation_api,
    identity_field,
):
    api = conversation_api

    response = api["client"].get(
        "/api/v1/conversations",
        params={identity_field: str(uuid4())},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["list_conversations"].assert_not_awaited()


@pytest.mark.parametrize(
    "params",
    [
        {"cursor_updated_at": "2026-08-11T10:00:00Z"},
        {"cursor_id": str(uuid4())},
    ],
)
def test_list_conversations_rejects_partial_composite_cursor_before_service(
    conversation_api,
    params,
):
    api = conversation_api

    response = api["client"].get("/api/v1/conversations", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["list_conversations"].assert_not_awaited()


@pytest.mark.parametrize(
    "limit",
    [0, -1, MAX_CONVERSATION_PAGE_SIZE + 1, "1.5", "true", "not-an-integer"],
)
def test_list_conversations_rejects_invalid_limit_before_service(
    conversation_api,
    limit,
):
    api = conversation_api

    response = api["client"].get(
        "/api/v1/conversations",
        params={"limit": limit},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["list_conversations"].assert_not_awaited()


@pytest.mark.parametrize(
    "cursor_updated_at",
    ["not-a-datetime", "2026-08-11T10:00:00"],
)
def test_list_conversations_rejects_malformed_or_naive_datetime_before_service(
    conversation_api,
    cursor_updated_at,
):
    api = conversation_api

    response = api["client"].get(
        "/api/v1/conversations",
        params={
            "cursor_updated_at": cursor_updated_at,
            "cursor_id": str(uuid4()),
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["list_conversations"].assert_not_awaited()


def test_list_conversations_rejects_malformed_cursor_uuid_before_service(
    conversation_api,
):
    api = conversation_api

    response = api["client"].get(
        "/api/v1/conversations",
        params={
            "cursor_updated_at": "2026-08-11T10:00:00Z",
            "cursor_id": "not-a-uuid",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["list_conversations"].assert_not_awaited()


def test_list_conversations_failure_uses_existing_generic_500(conversation_api):
    api = conversation_api
    api["list_conversations"].side_effect = RuntimeError(
        "sensitive persistence detail"
    )

    response = api["client"].get("/api/v1/conversations")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "sensitive persistence detail" not in response.text


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer short", f"Bearer {'U' * 43}"],
)
def test_list_conversations_requires_existing_uniform_bearer_authentication(
    monkeypatch,
    authorization,
):
    session = AsyncMock(spec=AsyncSession)
    get_by_digest = AsyncMock(return_value=None)
    authentication_service = Mock(get_by_access_token_digest=get_by_digest)
    authentication_service_factory = Mock(return_value=authentication_service)
    conversation_service_factory = Mock()
    monkeypatch.setattr(
        authentication_module,
        "UserService",
        authentication_service_factory,
    )
    monkeypatch.setattr(
        conversations_module,
        "ConversationService",
        conversation_service_factory,
    )

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    headers = {} if authorization is None else {"Authorization": authorization}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/conversations", headers=headers)
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    if authorization is not None:
        assert authorization not in response.text
    conversation_service_factory.assert_not_called()
    session.commit.assert_not_awaited()
    if authorization == f"Bearer {'U' * 43}":
        authentication_service_factory.assert_called_once_with(session)
        get_by_digest.assert_awaited_once_with(digest_access_token("U" * 43))
    else:
        authentication_service_factory.assert_not_called()
        get_by_digest.assert_not_awaited()


@pytest.mark.parametrize(
    ("title", "expected_title"),
    [("  Exact title  ", "  Exact title  "), (None, None)],
)
def test_get_conversation_returns_exact_safe_owned_response(
    conversation_api,
    title,
    expected_title,
):
    api = conversation_api
    api["conversation"].title = title

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}"
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(api["conversation"].id),
        "title": expected_title,
        "created_at": "2026-08-11T09:00:00Z",
        "updated_at": "2026-08-11T09:01:00Z",
    }
    assert set(response.json()) == {"id", "title", "created_at", "updated_at"}
    response_text = response.text.lower()
    assert "owner_id" not in response_text
    assert "next_message_sequence" not in response_text
    assert "messages" not in response_text
    assert "credential" not in response_text
    assert "digest" not in response_text
    api["service_factory"].assert_called_once_with(api["session"])
    api["get_conversation"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
    )
    api["message_service_factory"].assert_not_called()
    api["session"].commit.assert_not_awaited()
    api["session"].rollback.assert_not_awaited()


@pytest.mark.parametrize("identity_field", ["owner_id", "user_id"])
def test_get_conversation_client_identity_cannot_replace_current_user(
    conversation_api,
    identity_field,
):
    api = conversation_api
    client_identity = uuid4()

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}",
        params={identity_field: str(client_identity)},
    )

    assert response.status_code == 200
    api["get_conversation"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
    )
    assert api["get_conversation"].await_args.args[0] != client_identity


def test_get_conversation_missing_and_foreign_use_identical_generic_404(
    conversation_api,
):
    api = conversation_api
    missing_id = uuid4()
    foreign_id = api["conversation"].id
    api["get_conversation"].side_effect = [None, None]

    missing_response = api["client"].get(
        f"/api/v1/conversations/{missing_id}"
    )
    foreign_response = api["client"].get(
        f"/api/v1/conversations/{foreign_id}"
    )

    expected_error = {
        "code": "HTTP_ERROR",
        "message": "Conversation not found",
    }
    assert missing_response.status_code == foreign_response.status_code == 404
    assert missing_response.json()["error"] == expected_error
    assert foreign_response.json()["error"] == expected_error
    for response in (missing_response, foreign_response):
        error_text = str(response.json()["error"]).lower()
        assert str(api["current_user"].id) not in error_text
        assert str(api["conversation"].id) not in error_text
        assert api["conversation"].title.strip().lower() not in error_text
        assert "owner" not in error_text
        assert "persistence" not in error_text
    assert [call.args for call in api["get_conversation"].await_args_list] == [
        (api["current_user"].id, missing_id),
        (api["current_user"].id, foreign_id),
    ]
    api["message_service_factory"].assert_not_called()


def test_get_conversation_rejects_malformed_uuid_before_service(conversation_api):
    api = conversation_api

    response = api["client"].get("/api/v1/conversations/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["get_conversation"].assert_not_awaited()
    api["message_service_factory"].assert_not_called()


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer short", f"Bearer {'U' * 43}"],
)
def test_get_conversation_requires_existing_uniform_bearer_authentication(
    monkeypatch,
    authorization,
):
    session = AsyncMock(spec=AsyncSession)
    get_by_digest = AsyncMock(return_value=None)
    authentication_service = Mock(get_by_access_token_digest=get_by_digest)
    authentication_service_factory = Mock(return_value=authentication_service)
    conversation_service_factory = Mock()
    monkeypatch.setattr(
        authentication_module,
        "UserService",
        authentication_service_factory,
    )
    monkeypatch.setattr(
        conversations_module,
        "ConversationService",
        conversation_service_factory,
    )

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    headers = {} if authorization is None else {"Authorization": authorization}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(
                f"/api/v1/conversations/{uuid4()}",
                headers=headers,
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    if authorization is not None:
        assert authorization not in response.text
    conversation_service_factory.assert_not_called()
    session.commit.assert_not_awaited()
    if authorization == f"Bearer {'U' * 43}":
        authentication_service_factory.assert_called_once_with(session)
        get_by_digest.assert_awaited_once_with(digest_access_token("U" * 43))
    else:
        authentication_service_factory.assert_not_called()
        get_by_digest.assert_not_awaited()


def test_get_conversation_failure_uses_existing_generic_500(conversation_api):
    api = conversation_api
    api["get_conversation"].side_effect = RuntimeError(
        "sensitive persistence detail"
    )

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}"
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "sensitive persistence detail" not in response.text
    api["message_service_factory"].assert_not_called()


@pytest.mark.parametrize("title", ["  Renamed exactly  ", None])
def test_rename_conversation_returns_exact_safe_owned_response(
    conversation_api,
    title,
):
    api = conversation_api
    api["conversation"].title = title

    response = api["client"].patch(
        f"/api/v1/conversations/{api['conversation'].id}",
        json={"title": title},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(api["conversation"].id),
        "title": title,
        "created_at": "2026-08-11T09:00:00Z",
        "updated_at": "2026-08-11T09:01:00Z",
    }
    assert set(response.json()) == {"id", "title", "created_at", "updated_at"}
    response_text = response.text.lower()
    assert "owner_id" not in response_text
    assert "next_message_sequence" not in response_text
    assert "messages" not in response_text
    assert "credential" not in response_text
    assert "digest" not in response_text
    api["service_factory"].assert_called_once_with(api["session"])
    api["rename_conversation"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
        title,
    )
    api["message_service_factory"].assert_not_called()
    api["session"].refresh.assert_not_awaited()
    api["session"].commit.assert_not_awaited()
    api["session"].rollback.assert_not_awaited()


@pytest.mark.parametrize("identity_field", ["owner_id", "user_id"])
def test_rename_conversation_query_identity_cannot_replace_current_user(
    conversation_api,
    identity_field,
):
    api = conversation_api
    client_identity = uuid4()
    api["conversation"].title = "Renamed"

    response = api["client"].patch(
        f"/api/v1/conversations/{api['conversation'].id}",
        params={identity_field: str(client_identity)},
        json={"title": "Renamed"},
    )

    assert response.status_code == 200
    api["rename_conversation"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
        "Renamed",
    )
    assert api["rename_conversation"].await_args.args[0] != client_identity


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing-title"),
        pytest.param({"title": 123}, id="invalid-type"),
        pytest.param({"title": ""}, id="empty-title"),
        pytest.param({"title": "   "}, id="whitespace-only-title"),
        pytest.param({"title": "x" * 256}, id="overlong-title"),
    ],
)
def test_rename_conversation_rejects_invalid_body_before_service(
    conversation_api,
    payload,
):
    api = conversation_api

    response = api["client"].patch(
        f"/api/v1/conversations/{api['conversation'].id}",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["rename_conversation"].assert_not_awaited()
    api["message_service_factory"].assert_not_called()


def test_rename_conversation_rejects_malformed_json_before_service(
    conversation_api,
):
    api = conversation_api

    response = api["client"].patch(
        f"/api/v1/conversations/{api['conversation'].id}",
        content="{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["rename_conversation"].assert_not_awaited()
    api["message_service_factory"].assert_not_called()


@pytest.mark.parametrize(
    "field",
    [
        "owner_id",
        "user_id",
        "id",
        "created_at",
        "updated_at",
        "next_message_sequence",
        "messages",
        "role",
        "access_token",
        "access_token_digest",
        "credential",
    ],
)
def test_rename_conversation_rejects_unknown_internal_fields_before_service(
    conversation_api,
    field,
):
    api = conversation_api

    response = api["client"].patch(
        f"/api/v1/conversations/{api['conversation'].id}",
        json={"title": "Valid rename", field: "client-controlled"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["rename_conversation"].assert_not_awaited()
    api["message_service_factory"].assert_not_called()


def test_rename_conversation_rejects_malformed_uuid_before_service(
    conversation_api,
):
    api = conversation_api

    response = api["client"].patch(
        "/api/v1/conversations/not-a-uuid",
        json={"title": "Valid rename"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["rename_conversation"].assert_not_awaited()
    api["message_service_factory"].assert_not_called()


def test_rename_conversation_missing_and_foreign_use_identical_generic_404(
    conversation_api,
):
    api = conversation_api
    missing_id = uuid4()
    foreign_id = uuid4()
    api["rename_conversation"].side_effect = [None, None]

    missing_response = api["client"].patch(
        f"/api/v1/conversations/{missing_id}",
        json={"title": "Must not persist"},
    )
    foreign_response = api["client"].patch(
        f"/api/v1/conversations/{foreign_id}",
        json={"title": "Must not persist"},
    )

    expected_error = {
        "code": "HTTP_ERROR",
        "message": "Conversation not found",
    }
    assert missing_response.status_code == foreign_response.status_code == 404
    assert missing_response.json()["error"] == expected_error
    assert foreign_response.json()["error"] == expected_error
    for response in (missing_response, foreign_response):
        error_text = str(response.json()["error"]).lower()
        assert str(api["current_user"].id) not in error_text
        assert str(api["conversation"].id) not in error_text
        assert api["conversation"].title.strip().lower() not in error_text
        assert "owner" not in error_text
        assert "persistence" not in error_text
    assert [call.args for call in api["rename_conversation"].await_args_list] == [
        (
            api["current_user"].id,
            missing_id,
            "Must not persist",
        ),
        (
            api["current_user"].id,
            foreign_id,
            "Must not persist",
        ),
    ]
    api["message_service_factory"].assert_not_called()


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer short", f"Bearer {'U' * 43}"],
)
def test_rename_conversation_requires_existing_uniform_bearer_authentication(
    monkeypatch,
    authorization,
):
    session = AsyncMock(spec=AsyncSession)
    get_by_digest = AsyncMock(return_value=None)
    authentication_service = Mock(get_by_access_token_digest=get_by_digest)
    authentication_service_factory = Mock(return_value=authentication_service)
    conversation_service_factory = Mock()
    monkeypatch.setattr(
        authentication_module,
        "UserService",
        authentication_service_factory,
    )
    monkeypatch.setattr(
        conversations_module,
        "ConversationService",
        conversation_service_factory,
    )

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    headers = {} if authorization is None else {"Authorization": authorization}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.patch(
                f"/api/v1/conversations/{uuid4()}",
                headers=headers,
                json={"title": "Valid rename"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    if authorization is not None:
        assert authorization not in response.text
    conversation_service_factory.assert_not_called()
    session.commit.assert_not_awaited()
    if authorization == f"Bearer {'U' * 43}":
        authentication_service_factory.assert_called_once_with(session)
        get_by_digest.assert_awaited_once_with(digest_access_token("U" * 43))
    else:
        authentication_service_factory.assert_not_called()
        get_by_digest.assert_not_awaited()


def test_rename_conversation_failure_uses_existing_generic_500(conversation_api):
    api = conversation_api
    api["rename_conversation"].side_effect = RuntimeError(
        "sensitive persistence detail"
    )

    response = api["client"].patch(
        f"/api/v1/conversations/{api['conversation'].id}",
        json={"title": "Valid rename"},
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "sensitive persistence detail" not in response.text
    api["message_service_factory"].assert_not_called()


def test_delete_conversation_returns_exact_empty_204_for_owner(conversation_api):
    api = conversation_api

    response = api["client"].delete(
        f"/api/v1/conversations/{api['conversation'].id}"
    )

    assert response.status_code == 204
    assert response.content == b""
    api["service_factory"].assert_called_once_with(api["session"])
    api["delete_conversation"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
    )
    api["message_service_factory"].assert_not_called()
    api["session"].refresh.assert_not_awaited()
    api["session"].commit.assert_not_awaited()
    api["session"].rollback.assert_not_awaited()


@pytest.mark.parametrize("identity_field", ["owner_id", "user_id"])
def test_delete_conversation_client_identity_cannot_replace_current_user(
    conversation_api,
    identity_field,
):
    api = conversation_api
    client_identity = uuid4()

    response = api["client"].delete(
        f"/api/v1/conversations/{api['conversation'].id}",
        params={identity_field: str(client_identity)},
    )

    assert response.status_code == 204
    assert response.content == b""
    api["delete_conversation"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
    )
    assert api["delete_conversation"].await_args.args[0] != client_identity


def test_delete_conversation_missing_and_foreign_use_identical_generic_404(
    conversation_api,
):
    api = conversation_api
    missing_id = uuid4()
    foreign_id = uuid4()
    api["delete_conversation"].side_effect = [False, False]

    missing_response = api["client"].delete(
        f"/api/v1/conversations/{missing_id}"
    )
    foreign_response = api["client"].delete(
        f"/api/v1/conversations/{foreign_id}"
    )

    expected_error = {
        "code": "HTTP_ERROR",
        "message": "Conversation not found",
    }
    assert missing_response.status_code == foreign_response.status_code == 404
    assert missing_response.json()["error"] == expected_error
    assert foreign_response.json()["error"] == expected_error
    for response in (missing_response, foreign_response):
        error_text = str(response.json()["error"]).lower()
        assert str(api["current_user"].id) not in error_text
        assert str(api["conversation"].id) not in error_text
        assert api["conversation"].title.strip().lower() not in error_text
        assert "owner" not in error_text
        assert "persistence" not in error_text
    assert [call.args for call in api["delete_conversation"].await_args_list] == [
        (api["current_user"].id, missing_id),
        (api["current_user"].id, foreign_id),
    ]
    api["message_service_factory"].assert_not_called()


def test_delete_conversation_rejects_malformed_uuid_before_service(
    conversation_api,
):
    api = conversation_api

    response = api["client"].delete("/api/v1/conversations/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["delete_conversation"].assert_not_awaited()
    api["message_service_factory"].assert_not_called()


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer short", f"Bearer {'U' * 43}"],
)
def test_delete_conversation_requires_existing_uniform_bearer_authentication(
    monkeypatch,
    authorization,
):
    session = AsyncMock(spec=AsyncSession)
    get_by_digest = AsyncMock(return_value=None)
    authentication_service = Mock(get_by_access_token_digest=get_by_digest)
    authentication_service_factory = Mock(return_value=authentication_service)
    conversation_service_factory = Mock()
    monkeypatch.setattr(
        authentication_module,
        "UserService",
        authentication_service_factory,
    )
    monkeypatch.setattr(
        conversations_module,
        "ConversationService",
        conversation_service_factory,
    )

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    headers = {} if authorization is None else {"Authorization": authorization}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.delete(
                f"/api/v1/conversations/{uuid4()}",
                headers=headers,
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    if authorization is not None:
        assert authorization not in response.text
    conversation_service_factory.assert_not_called()
    session.commit.assert_not_awaited()
    if authorization == f"Bearer {'U' * 43}":
        authentication_service_factory.assert_called_once_with(session)
        get_by_digest.assert_awaited_once_with(digest_access_token("U" * 43))
    else:
        authentication_service_factory.assert_not_called()
        get_by_digest.assert_not_awaited()


def test_delete_conversation_failure_uses_existing_generic_500(conversation_api):
    api = conversation_api
    api["delete_conversation"].side_effect = RuntimeError(
        "sensitive persistence detail"
    )

    response = api["client"].delete(
        f"/api/v1/conversations/{api['conversation'].id}"
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "sensitive persistence detail" not in response.text
    api["message_service_factory"].assert_not_called()


def test_create_conversation_returns_201_with_exact_safe_response(conversation_api):
    api = conversation_api

    response = api["client"].post(
        "/api/v1/conversations",
        json={
            "title": "  Exact title  ",
            "initial_message": "  Exact initial content  ",
        },
    )

    conversation = api["conversation"]
    initial_message = api["initial_message"]
    assert response.status_code == 201
    assert response.json() == {
        "id": str(conversation.id),
        "title": "  Exact title  ",
        "created_at": "2026-08-11T09:00:00Z",
        "updated_at": "2026-08-11T09:01:00Z",
        "initial_message": {
            "id": str(initial_message.id),
            "conversation_id": str(conversation.id),
            "role": "user",
            "content": "  Exact initial content  ",
            "sequence_number": 1,
            "created_at": "2026-08-11T09:00:01Z",
            "updated_at": "2026-08-11T09:00:02Z",
        },
    }
    response_text = response.text.lower()
    assert "owner_id" not in response_text
    assert "next_message_sequence" not in response_text
    assert "credential" not in response_text
    assert "digest" not in response_text
    api["service_factory"].assert_called_once_with(api["session"])
    api["create"].assert_awaited_once_with(
        api["current_user"].id,
        "  Exact title  ",
        MessageRole.USER,
        "  Exact initial content  ",
    )
    api["session"].refresh.assert_awaited_once_with(
        conversation,
        attribute_names=["updated_at"],
    )
    api["session"].commit.assert_not_awaited()
    api["session"].rollback.assert_not_awaited()


def test_create_conversation_allows_omitted_title_without_changing_message(
    conversation_api,
):
    api = conversation_api
    api["conversation"].title = None

    response = api["client"].post(
        "/api/v1/conversations",
        json={"initial_message": "unchanged"},
    )

    assert response.status_code == 201
    assert response.json()["title"] is None
    api["create"].assert_awaited_once_with(
        api["current_user"].id,
        None,
        MessageRole.USER,
        "unchanged",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_id", str(uuid4())),
        ("user_id", str(uuid4())),
        ("role", "assistant"),
        ("conversation_id", str(uuid4())),
        ("sequence_number", 99),
    ],
)
def test_create_conversation_rejects_client_controlled_identity_and_sequence_fields(
    conversation_api,
    field,
    value,
):
    api = conversation_api
    payload = {"initial_message": "hello", field: value}

    response = api["client"].post("/api/v1/conversations", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["create"].assert_not_awaited()


@pytest.mark.parametrize("title", ["", "   ", "x" * 256])
def test_create_conversation_rejects_invalid_title_before_service(
    conversation_api,
    title,
):
    api = conversation_api

    response = api["client"].post(
        "/api/v1/conversations",
        json={"title": title, "initial_message": "hello"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["create"].assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"initial_message": None},
        {"initial_message": {"content": "nested"}},
    ],
)
def test_create_conversation_requires_initial_message_string(
    conversation_api,
    payload,
):
    api = conversation_api

    response = api["client"].post("/api/v1/conversations", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["service_factory"].assert_not_called()
    api["create"].assert_not_awaited()


def test_create_conversation_maps_atomic_creation_miss_to_generic_409(
    conversation_api,
):
    api = conversation_api
    api["create"].return_value = None

    response = api["client"].post(
        "/api/v1/conversations",
        json={"initial_message": "hello"},
    )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Conversation could not be created",
    }
    api["create"].assert_awaited_once_with(
        api["current_user"].id,
        None,
        MessageRole.USER,
        "hello",
    )


def test_create_conversation_failure_uses_existing_generic_500(conversation_api):
    api = conversation_api
    api["create"].side_effect = RuntimeError("sensitive persistence detail")

    response = api["client"].post(
        "/api/v1/conversations",
        json={"initial_message": "must not leak"},
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "sensitive persistence detail" not in response.text


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer short",
        f"Bearer {'U' * 43}",
    ],
)
def test_create_conversation_requires_existing_uniform_bearer_authentication(
    monkeypatch,
    authorization,
):
    session = AsyncMock(spec=AsyncSession)
    get_by_digest = AsyncMock(return_value=None)
    authentication_service = Mock(get_by_access_token_digest=get_by_digest)
    authentication_service_factory = Mock(return_value=authentication_service)
    conversation_service_factory = Mock()
    monkeypatch.setattr(
        authentication_module,
        "UserService",
        authentication_service_factory,
    )
    monkeypatch.setattr(
        conversations_module,
        "ConversationService",
        conversation_service_factory,
    )

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    headers = {} if authorization is None else {"Authorization": authorization}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/v1/conversations",
                headers=headers,
                json={"initial_message": "hello"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    if authorization is not None:
        assert authorization not in response.text
    conversation_service_factory.assert_not_called()
    session.commit.assert_not_awaited()
    if authorization == f"Bearer {'U' * 43}":
        authentication_service_factory.assert_called_once_with(session)
        get_by_digest.assert_awaited_once_with(digest_access_token("U" * 43))
    else:
        authentication_service_factory.assert_not_called()
        get_by_digest.assert_not_awaited()


def test_append_message_returns_201_with_exact_safe_response(conversation_api):
    api = conversation_api

    response = api["client"].post(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        json={"content": "  Exact follow-up content  "},
    )

    message = api["appended_message"]
    assert response.status_code == 201
    assert response.json() == {
        "id": str(message.id),
        "conversation_id": str(api["conversation"].id),
        "role": "user",
        "content": "  Exact follow-up content  ",
        "sequence_number": 2,
        "created_at": "2026-08-11T09:02:00Z",
        "updated_at": "2026-08-11T09:03:00Z",
    }
    response_text = response.text.lower()
    assert "owner_id" not in response_text
    assert "next_message_sequence" not in response_text
    assert "credential" not in response_text
    assert "digest" not in response_text
    api["message_service_factory"].assert_called_once_with(api["session"])
    api["append"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
        MessageRole.USER,
        "  Exact follow-up content  ",
    )
    api["session"].commit.assert_not_awaited()
    api["session"].rollback.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_id", str(uuid4())),
        ("user_id", str(uuid4())),
        ("role", "assistant"),
        ("sequence_number", 99),
        ("conversation_id", str(uuid4())),
    ],
)
def test_append_message_rejects_client_controlled_fields(
    conversation_api,
    field,
    value,
):
    api = conversation_api

    response = api["client"].post(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        json={"content": "hello", field: value},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["message_service_factory"].assert_not_called()
    api["append"].assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content": None},
        {"content": {"nested": "value"}},
    ],
)
def test_append_message_requires_content_string(conversation_api, payload):
    api = conversation_api

    response = api["client"].post(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["message_service_factory"].assert_not_called()
    api["append"].assert_not_awaited()


def test_append_message_rejects_invalid_conversation_uuid(conversation_api):
    api = conversation_api

    response = api["client"].post(
        "/api/v1/conversations/not-a-uuid/messages",
        json={"content": "hello"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["message_service_factory"].assert_not_called()
    api["append"].assert_not_awaited()


def test_append_message_maps_owner_scoped_miss_to_generic_404(conversation_api):
    api = conversation_api
    api["append"].return_value = None

    response = api["client"].post(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        json={"content": "hello"},
    )

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Conversation not found",
    }
    api["append"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
        MessageRole.USER,
        "hello",
    )


def test_append_message_maps_conflict_to_generic_409(conversation_api):
    api = conversation_api
    api["append"].side_effect = MessageAppendConflictError(
        "sensitive persistence detail"
    )

    response = api["client"].post(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        json={"content": "hello"},
    )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Message could not be appended",
    }
    assert "sensitive persistence detail" not in response.text


def test_append_message_failure_uses_existing_generic_500(conversation_api):
    api = conversation_api
    api["append"].side_effect = RuntimeError("sensitive persistence detail")

    response = api["client"].post(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        json={"content": "must not leak"},
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "sensitive persistence detail" not in response.text


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer short",
        f"Bearer {'U' * 43}",
    ],
)
def test_append_message_requires_existing_uniform_bearer_authentication(
    monkeypatch,
    authorization,
):
    session = AsyncMock(spec=AsyncSession)
    get_by_digest = AsyncMock(return_value=None)
    authentication_service = Mock(get_by_access_token_digest=get_by_digest)
    authentication_service_factory = Mock(return_value=authentication_service)
    message_service_factory = Mock()
    monkeypatch.setattr(
        authentication_module,
        "UserService",
        authentication_service_factory,
    )
    monkeypatch.setattr(
        conversations_module,
        "MessageService",
        message_service_factory,
    )

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    headers = {} if authorization is None else {"Authorization": authorization}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/api/v1/conversations/{uuid4()}/messages",
                headers=headers,
                json={"content": "hello"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    if authorization is not None:
        assert authorization not in response.text
    message_service_factory.assert_not_called()
    session.commit.assert_not_awaited()
    if authorization == f"Bearer {'U' * 43}":
        authentication_service_factory.assert_called_once_with(session)
        get_by_digest.assert_awaited_once_with(digest_access_token("U" * 43))
    else:
        authentication_service_factory.assert_not_called()
        get_by_digest.assert_not_awaited()


def test_list_messages_returns_200_with_exact_safe_terminal_page(conversation_api):
    api = conversation_api

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}/messages"
    )

    initial_message = api["initial_message"]
    appended_message = api["appended_message"]
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(initial_message.id),
                "conversation_id": str(api["conversation"].id),
                "role": "user",
                "content": "  Exact initial content  ",
                "sequence_number": 1,
                "created_at": "2026-08-11T09:00:01Z",
                "updated_at": "2026-08-11T09:00:02Z",
            },
            {
                "id": str(appended_message.id),
                "conversation_id": str(api["conversation"].id),
                "role": "user",
                "content": "  Exact follow-up content  ",
                "sequence_number": 2,
                "created_at": "2026-08-11T09:02:00Z",
                "updated_at": "2026-08-11T09:03:00Z",
            },
        ],
        "next_cursor": None,
    }
    response_text = response.text.lower()
    assert "owner_id" not in response_text
    assert "next_message_sequence" not in response_text
    assert "credential" not in response_text
    assert "digest" not in response_text
    assert [item["sequence_number"] for item in response.json()["items"]] == [
        1,
        2,
    ]
    api["message_service_factory"].assert_called_once_with(api["session"])
    api["list_messages"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
        MessagePagination(limit=DEFAULT_MESSAGE_PAGE_SIZE),
    )
    api["session"].commit.assert_not_awaited()
    api["session"].rollback.assert_not_awaited()


def test_list_messages_returns_last_sequence_as_next_cursor(conversation_api):
    api = conversation_api
    api["list_messages"].return_value = MessagePage(
        items=(api["initial_message"],),
        next_cursor=MessageCursor(sequence_number=1),
    )

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        params={"limit": 1},
    )

    assert response.status_code == 200
    assert [item["sequence_number"] for item in response.json()["items"]] == [1]
    assert response.json()["next_cursor"] == 1
    api["list_messages"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
        MessagePagination(limit=1),
    )


def test_list_messages_maps_cursor_to_existing_pagination(conversation_api):
    api = conversation_api
    api["list_messages"].return_value = MessagePage(
        items=(api["appended_message"],),
        next_cursor=None,
    )

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        params={"limit": 1, "cursor": 1},
    )

    assert response.status_code == 200
    assert [item["sequence_number"] for item in response.json()["items"]] == [2]
    assert response.json()["next_cursor"] is None
    api["list_messages"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
        MessagePagination(limit=1, cursor=MessageCursor(sequence_number=1)),
    )


@pytest.mark.parametrize("ownership_case", ["empty", "missing", "foreign-owned"])
def test_list_messages_preserves_uniform_empty_page(
    conversation_api,
    ownership_case,
):
    api = conversation_api
    api["list_messages"].return_value = MessagePage(items=(), next_cursor=None)

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}/messages"
    )

    assert ownership_case in {"empty", "missing", "foreign-owned"}
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
    api["list_messages"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
        MessagePagination(limit=DEFAULT_MESSAGE_PAGE_SIZE),
    )


def test_list_messages_ignores_client_identity_as_an_authorization_source(
    conversation_api,
):
    api = conversation_api

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        params={"owner_id": str(uuid4()), "user_id": str(uuid4())},
    )

    assert response.status_code == 200
    api["list_messages"].assert_awaited_once_with(
        api["current_user"].id,
        api["conversation"].id,
        MessagePagination(limit=DEFAULT_MESSAGE_PAGE_SIZE),
    )


def test_list_messages_rejects_invalid_conversation_uuid(conversation_api):
    api = conversation_api

    response = api["client"].get(
        "/api/v1/conversations/not-a-uuid/messages"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["message_service_factory"].assert_not_called()
    api["list_messages"].assert_not_awaited()


@pytest.mark.parametrize(
    "limit",
    [0, -1, MAX_MESSAGE_PAGE_SIZE + 1, "1.5", "true", "not-an-integer"],
)
def test_list_messages_rejects_invalid_limit_before_service(
    conversation_api,
    limit,
):
    api = conversation_api

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        params={"limit": limit},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["message_service_factory"].assert_not_called()
    api["list_messages"].assert_not_awaited()


@pytest.mark.parametrize(
    "cursor",
    [0, -1, "1.5", "true", "not-an-integer"],
)
def test_list_messages_rejects_invalid_cursor_before_service(
    conversation_api,
    cursor,
):
    api = conversation_api

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}/messages",
        params={"cursor": cursor},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    api["message_service_factory"].assert_not_called()
    api["list_messages"].assert_not_awaited()


def test_list_messages_failure_uses_existing_generic_500(conversation_api):
    api = conversation_api
    api["list_messages"].side_effect = RuntimeError("sensitive persistence detail")

    response = api["client"].get(
        f"/api/v1/conversations/{api['conversation'].id}/messages"
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "sensitive persistence detail" not in response.text


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer short",
        f"Bearer {'U' * 43}",
    ],
)
def test_list_messages_requires_existing_uniform_bearer_authentication(
    monkeypatch,
    authorization,
):
    session = AsyncMock(spec=AsyncSession)
    get_by_digest = AsyncMock(return_value=None)
    authentication_service = Mock(get_by_access_token_digest=get_by_digest)
    authentication_service_factory = Mock(return_value=authentication_service)
    message_service_factory = Mock()
    monkeypatch.setattr(
        authentication_module,
        "UserService",
        authentication_service_factory,
    )
    monkeypatch.setattr(
        conversations_module,
        "MessageService",
        message_service_factory,
    )

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    headers = {} if authorization is None else {"Authorization": authorization}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(
                f"/api/v1/conversations/{uuid4()}/messages",
                headers=headers,
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    if authorization is not None:
        assert authorization not in response.text
    message_service_factory.assert_not_called()
    session.commit.assert_not_awaited()
    if authorization == f"Bearer {'U' * 43}":
        authentication_service_factory.assert_called_once_with(session)
        get_by_digest.assert_awaited_once_with(digest_access_token("U" * 43))
    else:
        authentication_service_factory.assert_not_called()
        get_by_digest.assert_not_awaited()


def test_only_delete_by_id_route_was_added(conversation_api):
    api = conversation_api
    client = api["client"]
    conversation_id = api["conversation"].id

    assert client.get("/api/v1/conversations").status_code == 200
    assert client.post(
        "/api/v1/conversations",
        json={"initial_message": "unchanged route"},
    ).status_code == 201
    assert client.get(
        f"/api/v1/conversations/{conversation_id}"
    ).status_code == 200
    assert client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "unchanged nested append"},
    ).status_code == 201
    assert client.get(
        f"/api/v1/conversations/{conversation_id}/messages"
    ).status_code == 200
    assert client.get("/api/v1/users/me").status_code == 200

    route_methods = {
        method
        for route in conversations_module.router.routes
        if getattr(route, "path", None) == "/conversations/{conversation_id}"
        for method in getattr(route, "methods", ())
    }
    assert route_methods == {"DELETE", "GET", "PATCH"}
    assert client.patch(
        f"/api/v1/conversations/{conversation_id}",
        json={"title": "Allowed rename"},
    ).status_code == 200
    assert client.put(
        f"/api/v1/conversations/{conversation_id}",
        json={"title": "Must not update"},
    ).status_code == 405
    delete_response = client.delete(
        f"/api/v1/conversations/{conversation_id}"
    )
    assert delete_response.status_code == 204
    assert delete_response.content == b""
    assert client.get("/api/v1/messages").status_code == 404
    assert client.post("/api/v1/messages", json={}).status_code == 404
