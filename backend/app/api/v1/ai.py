from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import async_object_session

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelRuntimeUnavailableError,
)
from app.api.dependencies import get_current_user
from app.core.config import settings
from app.models.user import User
from app.schemas.ai import (
    LocalModelPageResponse,
    LocalModelResponse,
    ProductCapabilityPageResponse,
    ProductCapabilityResponse,
    ProductCapabilityId,
    ProductCapabilityReason,
)


router = APIRouter(prefix="/ai", tags=["AI"])


class _ModelListResponseTooLarge(Exception):
    pass


def _checked_add(total: int, amount: int, maximum: int) -> int:
    if amount > maximum - total:
        raise _ModelListResponseTooLarge
    return total + amount


def _json_string_size(value: str, maximum: int) -> int:
    size = _checked_add(0, 2, maximum)
    for character in value:
        codepoint = ord(character)
        if character in {'"', "\\"}:
            encoded_size = 2
        elif character in {"\b", "\f", "\n", "\r", "\t"}:
            encoded_size = 2
        elif codepoint < 0x20:
            encoded_size = 6
        elif codepoint < 0x80:
            encoded_size = 1
        elif codepoint < 0x800:
            encoded_size = 2
        elif codepoint < 0x10000:
            encoded_size = 3
        else:
            encoded_size = 4
        size = _checked_add(size, encoded_size, maximum)
    return size


def _integer_json_size(value: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("JSON integer value must be an integer")

    sign_size = 1 if value < 0 else 0
    maximum_digits = maximum - sign_size
    if maximum_digits < 1:
        raise _ModelListResponseTooLarge

    magnitude = -value if value < 0 else value
    if magnitude == 0:
        return sign_size + 1

    bit_length = magnitude.bit_length()
    lower_digits = ((bit_length - 1) * 30_102) // 100_000 + 1
    if lower_digits > maximum_digits:
        raise _ModelListResponseTooLarge

    upper_digits = (bit_length * 30_103 + 99_999) // 100_000
    lower_exponent = lower_digits - 1
    upper_exponent = upper_digits
    while lower_exponent + 1 < upper_exponent:
        candidate_exponent = (lower_exponent + upper_exponent) // 2
        if magnitude >= 10**candidate_exponent:
            lower_exponent = candidate_exponent
        else:
            upper_exponent = candidate_exponent

    digit_count = lower_exponent + 1
    if digit_count > maximum_digits:
        raise _ModelListResponseTooLarge
    return sign_size + digit_count


def _model_list_response_json_size(
    response: LocalModelPageResponse,
    *,
    maximum: int,
) -> int:
    size = _checked_add(0, len(b'{"items":['), maximum)

    for item_index, item in enumerate(response.items):
        if item_index:
            size = _checked_add(size, 1, maximum)
        size = _checked_add(size, 1, maximum)

        size = _checked_add(size, len(b'"model_id":'), maximum)
        size = _checked_add(
            size,
            _json_string_size(item.model_id, maximum - size),
            maximum,
        )
        size = _checked_add(size, len(b',"display_name":'), maximum)
        size = _checked_add(
            size,
            _json_string_size(item.display_name, maximum - size),
            maximum,
        )
        size = _checked_add(size, len(b',"runtime_id":'), maximum)
        size = _checked_add(
            size,
            _json_string_size(item.runtime_id, maximum - size),
            maximum,
        )
        size = _checked_add(size, len(b',"modality":'), maximum)
        size = _checked_add(
            size,
            _json_string_size(item.modality.value, maximum - size),
            maximum,
        )

        size = _checked_add(size, len(b',"family":'), maximum)
        if item.family is None:
            size = _checked_add(size, 4, maximum)
        else:
            size = _checked_add(
                size,
                _json_string_size(item.family, maximum - size),
                maximum,
            )

        size = _checked_add(size, len(b',"parameter_class":'), maximum)
        if item.parameter_class is None:
            size = _checked_add(size, 4, maximum)
        else:
            size = _checked_add(
                size,
                _json_string_size(item.parameter_class, maximum - size),
                maximum,
            )

        size = _checked_add(size, len(b',"capabilities":['), maximum)
        for capability_index, capability in enumerate(item.capabilities):
            if capability_index:
                size = _checked_add(size, 1, maximum)
            size = _checked_add(
                size,
                _json_string_size(capability.value, maximum - size),
                maximum,
            )
        size = _checked_add(size, 1, maximum)

        size = _checked_add(size, len(b',"context_window":'), maximum)
        if item.context_window is None:
            size = _checked_add(size, 4, maximum)
        else:
            size = _checked_add(
                size,
                _integer_json_size(item.context_window, maximum - size),
                maximum,
            )

        size = _checked_add(size, len(b',"quantization":'), maximum)
        if item.quantization is None:
            size = _checked_add(size, 4, maximum)
        else:
            size = _checked_add(
                size,
                _json_string_size(item.quantization, maximum - size),
                maximum,
            )

        size = _checked_add(size, len(b',"estimated_vram_bytes":'), maximum)
        if item.estimated_vram_bytes is None:
            size = _checked_add(size, 4, maximum)
        else:
            size = _checked_add(
                size,
                _integer_json_size(
                    item.estimated_vram_bytes,
                    maximum - size,
                ),
                maximum,
            )

        size = _checked_add(size, len(b',"availability":'), maximum)
        size = _checked_add(
            size,
            _json_string_size(item.availability.value, maximum - size),
            maximum,
        )
        size = _checked_add(size, len(b',"scale_class":'), maximum)
        if item.scale_class is None:
            size = _checked_add(size, 4, maximum)
        else:
            size = _checked_add(
                size,
                _json_string_size(item.scale_class.value, maximum - size),
                maximum,
            )
        for field_name, value in (
            (b',"required_vram_bytes":', item.required_vram_bytes),
            (b',"required_ram_bytes":', item.required_ram_bytes),
        ):
            size = _checked_add(size, len(field_name), maximum)
            size = _checked_add(
                size,
                4
                if value is None
                else _integer_json_size(value, maximum - size),
                maximum,
            )
        size = _checked_add(size, len(b',"installed":'), maximum)
        size = _checked_add(size, 4 if item.installed else 5, maximum)
        size = _checked_add(size, len(b',"runnable_now":'), maximum)
        size = _checked_add(size, 4 if item.runnable_now else 5, maximum)
        size = _checked_add(size, len(b',"future_capable":'), maximum)
        size = _checked_add(size, 4 if item.future_capable else 5, maximum)
        size = _checked_add(size, len(b',"hardware_class":'), maximum)
        if item.hardware_class is None:
            size = _checked_add(size, 4, maximum)
        else:
            size = _checked_add(
                size,
                _json_string_size(item.hardware_class.value, maximum - size),
                maximum,
            )
        size = _checked_add(size, len(b',"fallback_model_id":'), maximum)
        if item.fallback_model_id is None:
            size = _checked_add(size, 4, maximum)
        else:
            size = _checked_add(
                size,
                _json_string_size(item.fallback_model_id, maximum - size),
                maximum,
            )
        size = _checked_add(size, 1, maximum)

    return _checked_add(size, len(b']}'), maximum)


def _runtime_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Local model runtime unavailable",
    )


async def _release_authentication_session(current_user: User) -> None:
    authentication_session = async_object_session(current_user)
    if authentication_session is None:
        raise RuntimeError("Authenticated user session is not available")
    await authentication_session.rollback()


def _product_capability(
    capability_id: ProductCapabilityId,
    *blocking_reasons: ProductCapabilityReason,
) -> ProductCapabilityResponse:
    return ProductCapabilityResponse(
        id=capability_id,
        status="unavailable" if blocking_reasons else "available",
        blocking_reasons=list(blocking_reasons),
    )


@router.get(
    "/capabilities",
    response_model=ProductCapabilityPageResponse,
)
async def list_product_capabilities(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
) -> ProductCapabilityPageResponse:
    await _release_authentication_session(current_user)
    catalog = getattr(request.app.state, "model_catalog", None)
    model_runtime_unavailable = catalog is None
    models = ()
    if catalog is not None:
        try:
            models = await catalog.list_models()
        except ModelRuntimeUnavailableError:
            model_runtime_unavailable = True

    text_model_available = any(
        model.availability is ModelAvailability.AVAILABLE
        and model.runnable_now
        and ModelCapability.TEXT_GENERATION in model.capabilities
        for model in models
    )
    vision_model_available = any(
        model.availability is ModelAvailability.AVAILABLE
        and model.runnable_now
        and ModelCapability.TEXT_GENERATION in model.capabilities
        and ModelCapability.VISION_INPUT in model.capabilities
        for model in models
    )
    storage_available = getattr(request.app.state, "asset_storage", None) is not None
    model_runtime_reason: ProductCapabilityReason | None = (
        "local_model_runtime_unavailable"
        if model_runtime_unavailable
        else None
    )
    chat_reason: ProductCapabilityReason | None = (
        model_runtime_reason
        or (None if text_model_available else "allowlisted_text_model_required")
    )
    vision_reasons: list[ProductCapabilityReason] = []
    if not storage_available:
        vision_reasons.append("asset_storage_required")
    if model_runtime_reason is not None:
        vision_reasons.append(model_runtime_reason)
    elif not vision_model_available:
        vision_reasons.append("allowlisted_vision_model_required")
    storage_reasons: tuple[ProductCapabilityReason, ...] = (
        () if storage_available else ("asset_storage_required",)
    )
    speech_recognition_available = (
        getattr(request.app.state, "speech_recognition_runtime", None) is not None
        and any(
            model.availability is ModelAvailability.AVAILABLE
            and model.installed
            and model.runnable_now
            and ModelCapability.SPEECH_RECOGNITION in model.capabilities
            for model in models
        )
    )
    speech_synthesis_available = (
        getattr(request.app.state, "speech_synthesis_runtime", None) is not None
        and any(
            model.availability is ModelAvailability.AVAILABLE
            and model.installed
            and model.runnable_now
            and ModelCapability.SPEECH_SYNTHESIS in model.capabilities
            for model in models
        )
    )
    image_generation_available = (
        getattr(request.app.state, "image_generation_runtime", None) is not None
        and any(
            model.availability is ModelAvailability.AVAILABLE
            and model.installed
            and model.runnable_now
            and ModelCapability.IMAGE_GENERATION in model.capabilities
            for model in models
        )
    )
    image_editing_available = (
        getattr(request.app.state, "image_editing_runtime", None) is not None
        and any(
            model.availability is ModelAvailability.AVAILABLE
            and model.installed
            and model.runnable_now
            and ModelCapability.IMAGE_EDITING in model.capabilities
            for model in models
        )
    )

    return ProductCapabilityPageResponse(
        items=[
            _product_capability("chat", *(() if chat_reason is None else (chat_reason,))),
            _product_capability("vision_input", *vision_reasons),
            _product_capability(
                "attachments",
                *(() if storage_available else ("asset_storage_required",)),
            ),
            _product_capability(
                "documents_rag",
                *(() if storage_available else ("asset_storage_required",)),
            ),
            _product_capability("personal_memory"),
            _product_capability("bounded_tools"),
            _product_capability("bounded_workflows"),
            _product_capability(
                "image_generation",
                *storage_reasons,
                *(
                    ()
                    if image_generation_available
                    else ("local_image_runtime_and_model_required",)
                ),
            ),
            _product_capability(
                "image_editing",
                *storage_reasons,
                *(
                    ()
                    if image_editing_available
                    else ("local_image_edit_runtime_and_model_required",)
                ),
            ),
            _product_capability(
                "voice_input",
                *storage_reasons,
                *(
                    ()
                    if speech_recognition_available
                    else ("local_voice_runtime_and_models_required",)
                ),
            ),
            _product_capability(
                "voice_output",
                *storage_reasons,
                *(
                    ()
                    if speech_synthesis_available
                    else ("local_voice_runtime_and_models_required",)
                ),
            ),
        ]
    )


@router.get("/models", response_model=LocalModelPageResponse)
async def list_local_models(
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> LocalModelPageResponse:
    if isinstance(_current_user, User):
        await _release_authentication_session(_current_user)

    catalog = getattr(request.app.state, "model_catalog", None)
    if catalog is None:
        raise RuntimeError("Local model catalog is not configured")
    maximum_response_bytes = getattr(
        request.app.state,
        "model_list_max_response_bytes",
        settings.MODEL_LIST_MAX_RESPONSE_BYTES,
    )

    try:
        models = await catalog.list_models()
    except ModelRuntimeUnavailableError:
        raise _runtime_unavailable() from None

    response = LocalModelPageResponse(
        items=[
            LocalModelResponse(
                model_id=model.model_id,
                display_name=model.display_name,
                runtime_id=model.runtime_id,
                modality=model.modality,
                family=model.family,
                parameter_class=model.parameter_class,
                capabilities=list(model.capabilities),
                context_window=model.context_window,
                quantization=model.quantization,
                estimated_vram_bytes=model.estimated_vram_bytes,
                availability=model.availability,
                scale_class=model.scale_class,
                required_vram_bytes=model.required_vram_bytes,
                required_ram_bytes=model.required_ram_bytes,
                installed=model.installed,
                runnable_now=model.runnable_now,
                future_capable=model.future_capable,
                hardware_class=model.hardware_class,
                fallback_model_id=model.fallback_model_id,
            )
            for model in models
        ]
    )
    try:
        _model_list_response_json_size(
            response,
            maximum=maximum_response_bytes,
        )
    except _ModelListResponseTooLarge:
        raise _runtime_unavailable() from None
    return response
