import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.tool import (
    ToolDescriptorPageResponse,
    ToolDescriptorResponse,
    ToolExecutionListQuery,
    ToolExecutionPageResponse,
    ToolExecutionRequest,
    ToolExecutionResponse,
)
from app.services.tool import (
    ToolConversationNotFoundError,
    ToolInputInvalidError,
    ToolNotFoundError,
    ToolService,
)

router = APIRouter(prefix="/tools", tags=["Tools"])


def _service(request: Request, session: AsyncSession) -> ToolService:
    embedding_runtime = getattr(
        request.app.state, "document_embedding_runtime", None
    )
    return ToolService(
        session,
        document_storage=getattr(request.app.state, "asset_storage", None),
        document_admission=getattr(
            request.app.state, "document_ingestion_admission", None
        ),
        **(
            {"document_embedding_runtime": embedding_runtime}
            if embedding_runtime is not None
            else {}
        ),
    )


async def _cancel_on_disconnect(
    request: Request, execution_task: asyncio.Task[object]
) -> None:
    while True:
        message = await request.receive()
        if message["type"] != "http.disconnect":
            continue
        if not execution_task.done() and execution_task.cancelling() == 0:
            execution_task.cancel()
        return


@router.get("", response_model=ToolDescriptorPageResponse)
async def list_tools(
    current_user: Annotated[User, Depends(get_current_user)],
) -> ToolDescriptorPageResponse:
    del current_user
    return ToolDescriptorPageResponse(
        items=[
            ToolDescriptorResponse(
                name=item.name,
                description=item.description,
                input_schema=item.public_schema(),
                permission=item.permission,
                timeout_seconds=item.timeout_seconds,
                max_output_characters=item.max_output_characters,
            )
            for item in ToolService.definitions()
        ]
    )


@router.get("/executions", response_model=ToolExecutionPageResponse)
async def list_tool_executions(
    query: Annotated[ToolExecutionListQuery, Query()],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ToolExecutionPageResponse:
    items = await _service(request, session).list_for_owner(
        current_user.id, limit=query.limit
    )
    return ToolExecutionPageResponse(
        items=[
            ToolExecutionResponse.model_validate(item, from_attributes=True)
            for item in items
        ]
    )


@router.post(
    "/{tool_name}/executions",
    response_model=ToolExecutionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def execute_tool(
    tool_name: str,
    body: ToolExecutionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ToolExecutionResponse:
    execution_task = asyncio.current_task()
    if execution_task is None:  # pragma: no cover
        raise RuntimeError("Tool execution request task is unavailable")
    watcher = asyncio.create_task(
        _cancel_on_disconnect(request, execution_task),
        name="tool-execution-client-disconnect-watcher",
    )
    try:
        execution = await _service(request, session).execute_for_owner(
            current_user.id,
            tool_name,
            body.arguments,
            conversation_id=body.conversation_id,
        )
    except ToolNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found",
        ) from None
    except ToolConversationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        ) from None
    except ToolInputInvalidError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Tool arguments are invalid",
        ) from None
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
    return ToolExecutionResponse.model_validate(execution, from_attributes=True)
