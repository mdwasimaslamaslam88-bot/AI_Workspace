from io import BytesIO
import multiprocessing
import threading
import zipfile

import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.documents.embedding import cosine_similarity, embed_text
from app.documents.parsers import (
    DOCX_MEDIA_TYPE,
    DocumentParseError,
    DocumentTooLargeError,
    chunk_document,
    parse_document,
)
from app.services.document import _WorkCancelled, _run_parser_process



def _docx_bytes(*paragraphs: str) -> bytes:
    document = DocxDocument()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_bytes(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(
        f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize(
    ("media_type", "data", "expected", "provenance"),
    [
        ("text/plain", b"  alpha\r\n beta  ", "alpha beta", "text"),
        (
            "text/csv",
            b"name,value\nalice,42\n",
            "name | value alice | 42",
            "row",
        ),
    ],
)
def test_text_and_csv_parse_to_normalized_provenance(
    media_type,
    data,
    expected,
    provenance,
):
    units = parse_document(data, media_type)

    assert units[0].content == expected
    assert units[0].provenance_kind == provenance
    if media_type == "text/csv":
        assert (units[0].row_start, units[0].row_end) == (1, 2)


def test_small_csv_is_one_bounded_header_aware_chunk():
    units = parse_document(
        b"name,value\nalpha,11\nbeta,29\ngamma,73\n",
        "text/csv",
    )
    chunks = chunk_document(units)

    assert len(chunks) == 1
    assert chunks[0].content == "name | value alpha | 11 beta | 29 gamma | 73"
    assert (chunks[0].row_start, chunks[0].row_end) == (1, 4)


def test_large_csv_blocks_repeat_header_and_remain_bounded():
    rows = ["name,value", *[f"row-{index},{'x' * 40}" for index in range(80)]]
    chunks = chunk_document(parse_document("\n".join(rows).encode(), "text/csv"))

    assert len(chunks) > 1
    assert all(chunk.content.startswith("name | value ") for chunk in chunks)
    assert all(len(chunk.content) <= 1_200 for chunk in chunks)
    assert chunks[0].row_start == 1
    assert chunks[-1].row_end == 81


def test_pdf_and_docx_parse_locally_with_page_and_section_provenance():
    pdf_units = parse_document(_pdf_bytes("Local PDF source"), "application/pdf")
    docx_units = parse_document(
        _docx_bytes("Local DOCX source"),
        DOCX_MEDIA_TYPE,
    )

    assert pdf_units[0].content == "Local PDF source"
    assert pdf_units[0].page_number == 1
    assert docx_units[0].content == "Local DOCX source"


def test_malformed_and_unsafe_documents_fail_without_echoing_content():
    secret = "PRIVATE_DOCUMENT_SENTINEL"
    with pytest.raises(DocumentParseError) as pdf_error:
        parse_document(f"%PDF-1.7\n{secret}".encode(), "application/pdf")
    with pytest.raises(DocumentParseError) as text_error:
        parse_document(b"safe\x00" + secret.encode(), "text/plain")

    assert secret not in str(pdf_error.value)
    assert secret not in str(text_error.value)


def test_docx_expansion_bomb_is_rejected_before_xml_parsing():
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", b"A" * 8_388_609)

    with pytest.raises(DocumentTooLargeError):
        parse_document(output.getvalue(), DOCX_MEDIA_TYPE)


def test_chunking_and_embeddings_are_deterministic_and_bounded():
    content = ("alpha beta gamma delta " * 200).encode()
    units = parse_document(content, "text/plain")

    first = chunk_document(units)
    second = chunk_document(units)
    assert first == second
    assert all(1 <= len(chunk.content) <= 1_200 for chunk in first)
    assert [chunk.ordinal for chunk in first] == list(range(1, len(first) + 1))

    alpha = embed_text(first[0].content)
    again = embed_text(first[0].content)
    unrelated = embed_text("zulu whiskey xray")
    assert alpha.packed == again.packed
    assert cosine_similarity(alpha.packed, again.packed) == pytest.approx(1.0)
    assert cosine_similarity(alpha.packed, unrelated.packed) < 1.0


def test_cancelled_parser_process_is_terminated_and_joined():
    existing = {child.pid for child in multiprocessing.active_children()}
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(_WorkCancelled):
        _run_parser_process(cancelled, b"local text", "text/plain")

    remaining = {
        child.pid
        for child in multiprocessing.active_children()
        if child.is_alive()
    }
    assert remaining <= existing
