from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

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
    service = Mock(create_with_initial_message_for_owner=create)
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(
        conversations_module,
        "ConversationService",
        service_factory,
    )
    append = AsyncMock(return_value=appended_message)
    message_service = Mock(append_for_owner=append)
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
                "message_service_factory": message_service_factory,
                "append": append,
            }
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


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


def test_no_conversation_crud_or_standalone_message_routes_were_added(
    conversation_api,
):
    client = conversation_api["client"]

    assert client.get("/api/v1/conversations").status_code == 405
    assert client.get(f"/api/v1/conversations/{uuid4()}").status_code == 404
    assert (
        client.get(f"/api/v1/conversations/{uuid4()}/messages").status_code
        == 405
    )
    assert client.post("/api/v1/messages", json={}).status_code == 404
