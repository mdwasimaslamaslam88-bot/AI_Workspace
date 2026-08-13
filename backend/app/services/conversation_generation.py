from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.catalog import ModelAvailability, ModelCatalog
from app.ai.generation import (
    TextGenerationMessage,
    TextGenerationRole,
    TextGenerationRouter,
)
from app.models.message import Message, MessageRole
from app.services.conversation import ConversationService
from app.services.message import MessageService


MAX_GENERATION_CONTEXT_MESSAGES = 100
MAX_GENERATION_CONTEXT_CHARACTERS = 100_000
MAX_GENERATION_OUTPUT_TOKENS = 1_024


class ConversationGenerationNotFoundError(RuntimeError):
    """The current user does not own the requested Conversation."""


class ConversationGenerationModelNotFoundError(RuntimeError):
    """The public model ID is not present in the local catalog."""


class ConversationGenerationModelUnavailableError(RuntimeError):
    """The selected local model is currently unavailable."""


class ConversationGenerationNotReadyError(RuntimeError):
    """The Conversation history is not a supported generation state."""


class ConversationGenerationContextTooLargeError(RuntimeError):
    """The Conversation history exceeds the fixed first-slice bound."""


class ConversationChangedDuringGenerationError(RuntimeError):
    """The Conversation changed after its generation context was captured."""


class ConversationGenerationService:
    def __init__(
        self,
        session: AsyncSession,
        catalog: ModelCatalog,
        generation_router: TextGenerationRouter,
    ) -> None:
        self.session = session
        self.catalog = catalog
        self.generation_router = generation_router

    async def generate_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        model_id: str,
    ) -> Message:
        conversation = await ConversationService(self.session).get_for_owner(
            owner_id,
            conversation_id,
        )
        if conversation is None:
            await self.session.rollback()
            raise ConversationGenerationNotFoundError(
                "conversation is not available to the current user"
            )

        messages = await MessageService(
            self.session
        ).list_generation_context_for_owner(
            owner_id,
            conversation_id,
            max_messages=MAX_GENERATION_CONTEXT_MESSAGES,
        )
        expected_sequence_number = conversation.next_message_sequence
        snapshot = tuple(
            (
                message.role,
                message.content,
                message.sequence_number,
            )
            for message in messages
        )

        # Do not hold a database transaction open during local inference.
        await self.session.rollback()

        if len(snapshot) > MAX_GENERATION_CONTEXT_MESSAGES:
            raise ConversationGenerationContextTooLargeError(
                "conversation contains too many messages"
            )
        if sum(len(content) for _role, content, _sequence in snapshot) > (
            MAX_GENERATION_CONTEXT_CHARACTERS
        ):
            raise ConversationGenerationContextTooLargeError(
                "conversation context is too large"
            )
        if not snapshot:
            raise ConversationGenerationNotReadyError(
                "conversation has no user message"
            )
        if tuple(sequence for _role, _content, sequence in snapshot) != tuple(
            range(1, expected_sequence_number)
        ):
            raise ConversationChangedDuringGenerationError(
                "conversation sequence changed while context was captured"
            )

        context: list[TextGenerationMessage] = []
        for role, content, _sequence in snapshot:
            try:
                generation_role = TextGenerationRole(role.value)
            except ValueError:
                raise ConversationGenerationNotReadyError(
                    "conversation contains an unsupported message role"
                ) from None
            context.append(
                TextGenerationMessage(
                    role=generation_role,
                    content=content,
                )
            )
        if snapshot[-1][0] is not MessageRole.USER:
            raise ConversationGenerationNotReadyError(
                "conversation must end with a user message"
            )

        model = await self.catalog.resolve_model(model_id)
        if model is None:
            raise ConversationGenerationModelNotFoundError(
                "model is not present in the local catalog"
            )
        if model.descriptor.availability is ModelAvailability.UNAVAILABLE:
            raise ConversationGenerationModelUnavailableError(
                "model is not currently available"
            )
        generated = await self.generation_router.generate(
            model,
            tuple(context),
            max_output_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        )
        message = await MessageService(self.session).append_for_owner(
            owner_id,
            conversation_id,
            MessageRole.ASSISTANT,
            generated.content,
            expected_sequence_number=expected_sequence_number,
        )
        if message is None:
            raise ConversationChangedDuringGenerationError(
                "conversation changed during generation"
            )
        return message
