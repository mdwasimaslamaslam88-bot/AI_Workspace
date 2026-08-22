from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.repositories.base import BaseRepository


MAX_DOCUMENT_LIST_ITEMS = 100
MAX_RETRIEVAL_CANDIDATE_CHUNKS = 2_048


@dataclass(frozen=True, slots=True)
class DocumentAssetSnapshot:
    document_id: UUID
    asset_id: UUID
    media_type: str
    byte_size: int
    content_sha256: str
    storage_key: str
    original_filename: str | None


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: UUID
    asset_id: UUID
    content: str
    embedding: bytes
    embedding_model: str
    embedding_dimensions: int
    provenance_kind: str
    page_number: int | None
    row_start: int | None
    row_end: int | None
    section: str | None
    original_filename: str | None


class DocumentRepository(BaseRepository):
    async def get_for_owner(
        self,
        owner_id: UUID,
        document_id: UUID,
    ) -> Document | None:
        result = await self.session.execute(
            select(Document)
            .join(Asset, Asset.id == Document.asset_id)
            .where(
                Document.id == document_id,
                Document.owner_id == owner_id,
                Asset.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_owner_asset(
        self,
        owner_id: UUID,
        asset_id: UUID,
    ) -> Document | None:
        result = await self.session.execute(
            select(Document)
            .join(Asset, Asset.id == Document.asset_id)
            .where(
                Document.asset_id == asset_id,
                Document.owner_id == owner_id,
                Asset.id == asset_id,
                Asset.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_asset(
        self,
        owner_id: UUID,
        asset_id: UUID,
    ) -> Asset | None:
        result = await self.session.execute(
            select(Asset).where(
                Asset.id == asset_id,
                Asset.owner_id == owner_id,
                Asset.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def create_pending(
        self,
        owner_id: UUID,
        asset_id: UUID,
    ) -> Document:
        document = Document(
            owner_id=owner_id,
            asset_id=asset_id,
            status=DocumentStatus.PENDING,
            chunk_count=0,
            character_count=0,
        )
        self.session.add(document)
        await self.session.flush()
        return document

    async def claim(
        self,
        owner_id: UUID,
        document_id: UUID,
        ingestion_token: UUID,
    ) -> bool:
        active_asset = (
            select(Asset.id)
            .where(
                Asset.id == Document.asset_id,
                Asset.owner_id == owner_id,
                Asset.deleted_at.is_(None),
            )
            .exists()
        )
        result = await self.session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.owner_id == owner_id,
                Document.status.in_(
                    (
                        DocumentStatus.PENDING,
                        DocumentStatus.FAILED,
                        DocumentStatus.CANCELLED,
                    )
                ),
                active_asset,
            )
            .values(
                status=DocumentStatus.PROCESSING,
                ingestion_token=ingestion_token,
                failure_code=None,
                completed_at=None,
                updated_at=func.now(),
            )
        )
        return result.rowcount == 1

    async def snapshot_for_processing(
        self,
        owner_id: UUID,
        document_id: UUID,
        ingestion_token: UUID,
    ) -> DocumentAssetSnapshot | None:
        result = await self.session.execute(
            select(
                Document.id,
                Asset.id,
                Asset.media_type,
                Asset.byte_size,
                Asset.content_sha256,
                Asset.storage_key,
                Asset.original_filename,
            )
            .join(Asset, Asset.id == Document.asset_id)
            .where(
                Document.id == document_id,
                Document.owner_id == owner_id,
                Document.status == DocumentStatus.PROCESSING,
                Document.ingestion_token == ingestion_token,
                Asset.owner_id == owner_id,
                Asset.deleted_at.is_(None),
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return DocumentAssetSnapshot(
            document_id=row[0],
            asset_id=row[1],
            media_type=row[2],
            byte_size=row[3],
            content_sha256=row[4],
            storage_key=row[5],
            original_filename=row[6],
        )

    async def complete(
        self,
        owner_id: UUID,
        document_id: UUID,
        ingestion_token: UUID,
        chunks: tuple[DocumentChunk, ...],
        character_count: int,
    ) -> bool:
        active_asset = (
            select(Asset.id)
            .where(
                Asset.id == Document.asset_id,
                Asset.owner_id == owner_id,
                Asset.deleted_at.is_(None),
            )
            .exists()
        )
        result = await self.session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.owner_id == owner_id,
                Document.status == DocumentStatus.PROCESSING,
                Document.ingestion_token == ingestion_token,
                active_asset,
            )
            .values(
                status=DocumentStatus.READY,
                ingestion_token=None,
                chunk_count=len(chunks),
                character_count=character_count,
                failure_code=None,
                completed_at=func.now(),
                updated_at=func.now(),
            )
        )
        if result.rowcount != 1:
            return False
        await self.session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.owner_id == owner_id,
            )
        )
        self.session.add_all(chunks)
        await self.session.flush()
        return True

    async def finish_unsuccessfully(
        self,
        owner_id: UUID,
        document_id: UUID,
        ingestion_token: UUID,
        *,
        status: DocumentStatus,
        failure_code: str,
    ) -> bool:
        if status not in {DocumentStatus.FAILED, DocumentStatus.CANCELLED}:
            raise ValueError("unsuccessful document status is invalid")
        result = await self.session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.owner_id == owner_id,
                Document.status == DocumentStatus.PROCESSING,
                Document.ingestion_token == ingestion_token,
            )
            .values(
                status=status,
                ingestion_token=None,
                chunk_count=0,
                character_count=0,
                failure_code=failure_code,
                completed_at=func.now(),
                updated_at=func.now(),
            )
        )
        return result.rowcount == 1

    async def cancel_for_owner(
        self,
        owner_id: UUID,
        document_id: UUID,
    ) -> bool:
        result = await self.session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.owner_id == owner_id,
                Document.status == DocumentStatus.PROCESSING,
            )
            .values(
                status=DocumentStatus.CANCELLED,
                ingestion_token=None,
                chunk_count=0,
                character_count=0,
                failure_code="cancelled",
                completed_at=func.now(),
                updated_at=func.now(),
            )
        )
        return result.rowcount == 1

    async def list_for_owner(
        self,
        owner_id: UUID,
        *,
        limit: int = MAX_DOCUMENT_LIST_ITEMS,
    ) -> tuple[Document, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("document list limit must be an integer")
        if not 1 <= limit <= MAX_DOCUMENT_LIST_ITEMS:
            raise ValueError("document list limit is outside its bound")
        result = await self.session.execute(
            select(Document)
            .join(Asset, Asset.id == Document.asset_id)
            .where(
                Document.owner_id == owner_id,
                Asset.owner_id == owner_id,
            )
            .order_by(Document.updated_at.desc(), Document.id.desc())
            .limit(limit)
        )
        return tuple(result.scalars().all())

    async def list_retrieval_candidates(
        self,
        owner_id: UUID,
        *,
        limit: int = MAX_RETRIEVAL_CANDIDATE_CHUNKS,
    ) -> tuple[RetrievalCandidate, ...]:
        if not 1 <= limit <= MAX_RETRIEVAL_CANDIDATE_CHUNKS:
            raise ValueError("retrieval candidate limit is outside its bound")
        result = await self.session.execute(
            select(
                DocumentChunk.id,
                DocumentChunk.asset_id,
                DocumentChunk.content,
                DocumentChunk.embedding,
                DocumentChunk.embedding_model,
                DocumentChunk.embedding_dimensions,
                DocumentChunk.provenance_kind,
                DocumentChunk.page_number,
                DocumentChunk.row_start,
                DocumentChunk.row_end,
                DocumentChunk.section,
                Asset.original_filename,
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .join(Asset, Asset.id == DocumentChunk.asset_id)
            .where(
                DocumentChunk.owner_id == owner_id,
                Document.owner_id == owner_id,
                Document.status == DocumentStatus.READY,
                Asset.owner_id == owner_id,
                Asset.deleted_at.is_(None),
            )
            .order_by(DocumentChunk.document_id.asc(), DocumentChunk.ordinal.asc())
            .limit(limit)
        )
        return tuple(
            RetrievalCandidate(
                chunk_id=row[0],
                asset_id=row[1],
                content=row[2],
                embedding=row[3],
                embedding_model=row[4],
                embedding_dimensions=row[5],
                provenance_kind=row[6],
                page_number=row[7],
                row_start=row[8],
                row_end=row[9],
                section=row[10],
                original_filename=row[11],
            )
            for row in result.all()
        )
