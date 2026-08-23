from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

import app.models  # noqa: F401  # Populate the model registry.
from app.models import Asset, Message, MessageAsset


def _checks(table) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_asset_columns_constraints_indexes_and_owner_foreign_key():
    table = Asset.__table__
    assert set(table.c.keys()) == {
        "id",
        "owner_id",
        "original_filename",
        "media_type",
        "byte_size",
        "content_sha256",
        "storage_key",
        "upload_idempotency_key",
        "created_at",
        "deleted_at",
        "provenance_kind",
        "source_asset_id",
        "runtime_id",
        "model_id",
    }
    assert isinstance(table.c.id.type, Uuid)
    assert table.c.id.type.python_type is UUID
    assert table.c.id.primary_key is True

    owner_fk = next(
        foreign_key
        for foreign_key in table.c.owner_id.foreign_keys
        if foreign_key.target_fullname == "users.id"
    )
    assert owner_fk.target_fullname == "users.id"
    assert owner_fk.ondelete == "RESTRICT"
    assert table.c.original_filename.nullable is True
    assert isinstance(table.c.original_filename.type, Text)
    assert table.c.media_type.type.length == 255
    assert isinstance(table.c.byte_size.type, BigInteger)
    assert table.c.content_sha256.type.length == 64
    assert table.c.storage_key.type.length == 128
    assert table.c.upload_idempotency_key.nullable is True
    assert table.c.provenance_kind.type.length == 32
    assert table.c.source_asset_id.nullable is True
    assert table.c.runtime_id.type.length == 64
    assert table.c.model_id.type.length == 96
    source_fk = next(iter(table.c.source_asset_id.foreign_keys))
    assert source_fk.target_fullname == "assets.id"
    assert source_fk.ondelete == "RESTRICT"
    assert {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {
        ("storage_key",),
        ("id", "owner_id"),
        ("owner_id", "upload_idempotency_key"),
    }
    assert table.c.created_at.type.timezone is True
    assert table.c.created_at.server_default is not None
    assert table.c.deleted_at.type.timezone is True
    assert table.c.deleted_at.nullable is True

    assert _checks(table) == {
        "ck_assets_byte_size_positive": "byte_size > 0",
        "ck_assets_content_sha256_lowercase_hex": (
            "content_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        "ck_assets_storage_key_generated": (
            "storage_key ~ '^objects/[0-9a-f]{2}/[0-9a-f]{2}/"
            "[0-9a-f]{32}$'"
        ),
        "ck_assets_deleted_at_not_before_created_at": (
            "deleted_at IS NULL OR deleted_at >= created_at"
        ),
        "ck_assets_provenance_kind_known": (
            "provenance_kind IN ('upload', 'image_generation', "
            "'image_editing', 'speech_synthesis')"
        ),
        "ck_assets_runtime_id_safe": (
            "runtime_id IS NULL OR runtime_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'"
        ),
        "ck_assets_model_id_public": (
            "model_id IS NULL OR model_id ~ "
            "'^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$'"
        ),
        "ck_assets_provenance_consistent": (
            "(provenance_kind = 'upload' AND source_asset_id IS NULL "
            "AND runtime_id IS NULL AND model_id IS NULL) OR "
            "(provenance_kind IN ('image_generation', 'speech_synthesis') "
            "AND source_asset_id IS NULL AND runtime_id IS NOT NULL "
            "AND model_id IS NOT NULL) OR "
            "(provenance_kind = 'image_editing' AND source_asset_id IS NOT NULL "
            "AND runtime_id IS NOT NULL AND model_id IS NOT NULL)"
        ),
        "ck_assets_source_not_self": (
            "source_asset_id IS NULL OR source_asset_id <> id"
        ),
    }
    index_ddl = {
        str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        for index in table.indexes
    }
    assert index_ddl == {
        "CREATE INDEX ix_assets_owner_created_at_id "
        "ON assets (owner_id, created_at DESC, id DESC)",
        "CREATE INDEX ix_assets_deleted_at ON assets (deleted_at) "
        "WHERE deleted_at IS NOT NULL",
        "CREATE INDEX ix_assets_source_asset_id ON assets (source_asset_id) "
        "WHERE source_asset_id IS NOT NULL",
    }


def test_message_asset_is_an_ordered_single_use_attachment_relation():
    table = MessageAsset.__table__
    assert set(table.c.keys()) == {"message_id", "asset_id", "position"}
    assert tuple(table.primary_key.columns.keys()) == ("message_id", "asset_id")
    assert isinstance(table.c.position.type, Integer)
    assert table.c.position.nullable is False
    assert _checks(table) == {
        "ck_message_assets_position_positive": "position >= 1"
    }

    message_fk = next(iter(table.c.message_id.foreign_keys))
    asset_fk = next(iter(table.c.asset_id.foreign_keys))
    assert message_fk.target_fullname == "messages.id"
    assert message_fk.ondelete == "CASCADE"
    assert asset_fk.target_fullname == "assets.id"
    assert asset_fk.ondelete == "RESTRICT"
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique_columns == {("message_id", "position"), ("asset_id",)}


def test_attachment_relationships_use_safe_eager_loading_and_order():
    message_links = Message.__mapper__.relationships["asset_links"]
    asset_link = Asset.__mapper__.relationships["message_link"]
    link_asset = MessageAsset.__mapper__.relationships["asset"]

    assert message_links.mapper.class_ is MessageAsset
    assert message_links.lazy == "selectin"
    assert tuple(message_links.order_by) == (MessageAsset.__table__.c.position,)
    assert asset_link.mapper.class_ is MessageAsset
    assert asset_link.uselist is False
    assert link_asset.mapper.class_ is Asset
    assert link_asset.lazy == "joined"
