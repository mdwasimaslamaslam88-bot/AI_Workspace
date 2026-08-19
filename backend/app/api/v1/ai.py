from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.ai.catalog import ModelRuntimeUnavailableError
from app.api.dependencies import get_current_user
from app.core.config import settings
from app.models.user import User
from app.schemas.ai import LocalModelPageResponse, LocalModelResponse


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
        size = _checked_add(size, 1, maximum)

    return _checked_add(size, len(b']}'), maximum)


def _runtime_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Local model runtime unavailable",
    )


@router.get("/models", response_model=LocalModelPageResponse)
async def list_local_models(
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> LocalModelPageResponse:
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
