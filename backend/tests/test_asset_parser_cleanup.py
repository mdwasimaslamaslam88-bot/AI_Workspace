from unittest.mock import AsyncMock, Mock

import pytest
from python_multipart.exceptions import MultipartParseError

import app.api.v1.assets as assets_module


@pytest.mark.asyncio
async def test_low_level_multipart_failure_closes_every_partial_spooled_file(
    monkeypatch,
):
    first = Mock()
    second = Mock()
    parser = Mock()
    parser.parse = AsyncMock(side_effect=MultipartParseError("private parser detail"))
    parser._files_to_close_on_error = [first, second]
    parser_factory = Mock(return_value=parser)
    monkeypatch.setattr(assets_module, "MultiPartParser", parser_factory)
    request = Mock()
    request.headers = {"Content-Type": "multipart/form-data; boundary=safe"}
    request.stream.return_value = object()

    with pytest.raises(MultipartParseError):
        await assets_module._parse_multipart(request)

    first.close.assert_called_once_with()
    second.close.assert_called_once_with()
