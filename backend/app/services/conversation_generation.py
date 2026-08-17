from __future__ import annotations

import math
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
MAX_GENERATION_TEMPERATURE = 2.0
MAX_GENERATION_SEED = 2_147_483_647
MAX_GENERATION_TOP_P = 1.0
MAX_GENERATION_TOP_K = 100
MAX_GENERATION_MIN_P = 1.0
MIN_GENERATION_REPEAT_PENALTY = 0.5
MAX_GENERATION_REPEAT_PENALTY = 2.0
MAX_GENERATION_REPEAT_LAST_N = 2_048


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
        *,
        user_message: str | None = None,
        max_output_tokens: int = MAX_GENERATION_OUTPUT_TOKENS,
        temperature: float | None = None,
        seed: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        min_p: float | None = None,
        repeat_penalty: float | None = None,
        repeat_last_n: int | None = None,
    ) -> Message:
        if isinstance(max_output_tokens, bool) or not isinstance(
            max_output_tokens,
            int,
        ):
            raise TypeError("max_output_tokens must be an integer")
        if not 1 <= max_output_tokens <= MAX_GENERATION_OUTPUT_TOKENS:
            raise ValueError(
                "max_output_tokens must be between 1 and "
                f"{MAX_GENERATION_OUTPUT_TOKENS}"
            )
        if temperature is not None:
            if isinstance(temperature, bool) or not isinstance(
                temperature,
                (int, float),
            ):
                raise TypeError("temperature must be numeric")
            try:
                is_finite_temperature = math.isfinite(temperature)
            except OverflowError:
                is_finite_temperature = False
            if (
                not is_finite_temperature
                or not 0.0 <= temperature <= MAX_GENERATION_TEMPERATURE
            ):
                raise ValueError(
                    "temperature must be finite and between 0.0 and "
                    f"{MAX_GENERATION_TEMPERATURE}"
                )
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise TypeError("seed must be an integer")
            if not 0 <= seed <= MAX_GENERATION_SEED:
                raise ValueError(
                    "seed must be between 0 and "
                    f"{MAX_GENERATION_SEED}"
                )
        if top_p is not None:
            if isinstance(top_p, bool) or not isinstance(
                top_p,
                (int, float),
            ):
                raise TypeError("top_p must be numeric")
            try:
                is_finite_top_p = math.isfinite(top_p)
            except OverflowError:
                is_finite_top_p = False
            if not is_finite_top_p or not 0.0 <= top_p <= MAX_GENERATION_TOP_P:
                raise ValueError(
                    "top_p must be finite and between 0.0 and "
                    f"{MAX_GENERATION_TOP_P}"
                )
        if top_k is not None:
            if isinstance(top_k, bool) or not isinstance(top_k, int):
                raise TypeError("top_k must be an integer")
            if not 1 <= top_k <= MAX_GENERATION_TOP_K:
                raise ValueError(
                    "top_k must be between 1 and "
                    f"{MAX_GENERATION_TOP_K}"
                )
        if min_p is not None:
            if isinstance(min_p, bool) or not isinstance(
                min_p,
                (int, float),
            ):
                raise TypeError("min_p must be numeric")
            try:
                is_finite_min_p = math.isfinite(min_p)
            except OverflowError:
                is_finite_min_p = False
            if not is_finite_min_p or not 0.0 <= min_p <= MAX_GENERATION_MIN_P:
                raise ValueError(
                    "min_p must be finite and between 0.0 and "
                    f"{MAX_GENERATION_MIN_P}"
                )
        if repeat_penalty is not None:
            if isinstance(repeat_penalty, bool) or not isinstance(
                repeat_penalty,
                (int, float),
            ):
                raise TypeError("repeat_penalty must be numeric")
            try:
                is_finite_repeat_penalty = math.isfinite(repeat_penalty)
            except OverflowError:
                is_finite_repeat_penalty = False
            if (
                not is_finite_repeat_penalty
                or not MIN_GENERATION_REPEAT_PENALTY
                <= repeat_penalty
                <= MAX_GENERATION_REPEAT_PENALTY
            ):
                raise ValueError(
                    "repeat_penalty must be finite and between "
                    f"{MIN_GENERATION_REPEAT_PENALTY} and "
                    f"{MAX_GENERATION_REPEAT_PENALTY}"
                )
        if repeat_last_n is not None:
            if isinstance(repeat_last_n, bool) or not isinstance(
                repeat_last_n,
                int,
            ):
                raise TypeError("repeat_last_n must be an integer")
            if not 0 <= repeat_last_n <= MAX_GENERATION_REPEAT_LAST_N:
                raise ValueError(
                    "repeat_last_n must be between 0 and "
                    f"{MAX_GENERATION_REPEAT_LAST_N}"
                )

        conversation = await ConversationService(self.session).get_for_owner(
            owner_id,
            conversation_id,
        )
        if conversation is None:
            await self.session.rollback()
            raise ConversationGenerationNotFoundError(
                "conversation is not available to the current user"
            )

        appended_user_sequence: int | None = None
        if user_message is not None:
            appended_user = await MessageService(self.session).append_for_owner(
                owner_id,
                conversation_id,
                MessageRole.USER,
                user_message,
            )
            if appended_user is None:
                raise ConversationGenerationNotFoundError(
                    "conversation is not available to the current user"
                )
            appended_user_sequence = appended_user.sequence_number

        messages = await MessageService(
            self.session
        ).list_generation_context_for_owner(
            owner_id,
            conversation_id,
            max_messages=MAX_GENERATION_CONTEXT_MESSAGES,
        )
        expected_sequence_number = (
            appended_user_sequence + 1
            if appended_user_sequence is not None
            else conversation.next_message_sequence
        )
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

        if appended_user_sequence is not None and (
            not snapshot
            or snapshot[-1][2] != appended_user_sequence
        ):
            raise ConversationChangedDuringGenerationError(
                "conversation changed before generation context was captured"
            )

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
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            seed=seed,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
            repeat_last_n=repeat_last_n,
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
