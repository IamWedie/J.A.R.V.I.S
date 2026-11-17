import sys

import pytest

from core.study import extractors


def docs_root(tmp_path):
    root = tmp_path / "Documents"
    root.mkdir()
    return root


def run(root, **kwargs):
    return extractors.extract_study_material(str(root), documents_root=str(root), **kwargs)


def make_pdf(path, pages=1, text="Hello study"):
    """Build a tiny valid PDF with a Helvetica text layer."""
    objs = {}
    kids = []
    for index in range(pages):
        page_num = 4 + index * 2
        content_num = 5 + index * 2
        kids.append(page_num)
        if text:
            stream = f"BT /F1 12 Tf 72 720 Td ({text} {index + 1}) Tj ET".encode()
        else:
            stream = b"BT /F1 12 Tf 72 720 Td ET"
        objs[page_num] = (
            b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources <</Font <</F1 3 0 R>>>> /Contents "
            + str(content_num).encode() + b" 0 R>>"
        )
        objs[content_num] = (
            b"<</Length " + str(len(stream)).encode() + b">>stream\n"
            + stream + b"\nendstream"
        )
    objs[1] = b"<</Type /Catalog /Pages 2 0 R>>"
    objs[2] = (
        b"<</Type /Pages /Kids ["
        + b" ".join(f"{number} 0 R".encode() for number in kids)
        + b"] /Count " + str(pages).encode() + b">>"
    )
    objs[3] = b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objs):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objs[number] + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {max(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for number in range(1, max(objs) + 1):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<</Size {max(objs) + 1} /Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))


def test_pdf_text_is_extracted(tmp_path):
    root = docs_root(tmp_path)
    make_pdf(root / "chapter.pdf")
    result = run(root)
    doc = result["documents"][0]
    assert doc["format"] == "pdf"
    assert "Hello study" in doc["text"]
    assert doc["pages"] == 1


def test_pdf_without_text_layer_is_reported(tmp_path):
    root = docs_root(tmp_path)
    make_pdf(root / "scan.pdf", text="")
    result = run(root)
    assert result["documents"] == []
    assert result["skipped"][0]["reason"] == "no_text_layer"


def test_pdf_page_cap_sets_truncated(tmp_path):
    root = docs_root(tmp_path)
    make_pdf(root / "long.pdf", pages=81)
    result = run(root)
    doc = result["documents"][0]
    assert doc["pages"] == 80
    assert result["truncated"] is True


def test_docx_paragraphs_and_tables(tmp_path):
    from docx import Document

    root = docs_root(tmp_path)
    document = Document()
    document.add_paragraph("Paragraph text")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Cell A"
    table.cell(0, 1).text = "Cell B"
    document.save(root / "notes.docx")

    result = run(root)
    text = result["documents"][0]["text"]
    assert result["documents"][0]["format"] == "docx"
    assert "Paragraph text" in text
    assert "Cell A" in text and "Cell B" in text


def test_pptx_shapes_and_notes(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    root = docs_root(tmp_path)
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    box.text_frame.text = "Slide text"
    slide.notes_slide.notes_text_frame.text = "Speaker note"
    presentation.save(root / "deck.pptx")

    result = run(root)
    doc = result["documents"][0]
    assert doc["format"] == "pptx"
    assert "Slide text" in doc["text"]
    assert "Speaker note" in doc["text"]
    assert doc["pages"] == 1


def test_pptx_slide_cap_sets_truncated(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    root = docs_root(tmp_path)
    presentation = Presentation()
    for index in range(81):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
        box.text_frame.text = f"Slide {index}"
    presentation.save(root / "long.pptx")
    result = run(root)
    assert result["documents"][0]["pages"] == 80
    assert result["truncated"] is True


def test_xlsx_sheets_and_rows(tmp_path):
    from openpyxl import Workbook

    root = docs_root(tmp_path)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet One"
    sheet.append(["A", "B"])
    sheet.append([1, 2])
    workbook.save(root / "grades.xlsx")

    result = run(root)
    doc = result["documents"][0]
    assert doc["format"] == "xlsx"
    assert "Sheet One" in doc["text"]
    assert "A\tB" in doc["text"]


def test_xlsx_sheet_and_row_caps(tmp_path):
    from openpyxl import Workbook

    root = docs_root(tmp_path)
    workbook = Workbook()
    workbook.active.title = "Sheet1"
    for index in range(2, 12):
        workbook.create_sheet(f"Sheet{index}")
    for row in range(1, 502):
        workbook.active.cell(row=row, column=1, value=f"row{row}")
    workbook.save(root / "long.xlsx")

    result = run(root)
    text = result["documents"][0]["text"]
    assert "Sheet10" in text
    assert "Sheet11" not in text
    assert "row500" in text
    assert "row501" not in text
    assert result["truncated"] is True


def test_corrupt_docx_is_reported(tmp_path):
    root = docs_root(tmp_path)
    (root / "broken.docx").write_bytes(b"not a zip")
    result = run(root)
    assert result["documents"] == []
    assert result["skipped"][0]["reason"] == "unreadable"


def test_image_ocr_unavailable(tmp_path, monkeypatch):
    root = docs_root(tmp_path)
    (root / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setitem(sys.modules, "winocr", None)
    result = run(root)
    assert result["documents"] == []
    assert result["skipped"][0]["reason"] == "ocr_unavailable"


def test_image_ocr_mocked(tmp_path, monkeypatch):
    root = docs_root(tmp_path)
    (root / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(extractors, "_ocr_image", lambda path: "Image text")
    result = run(root)
    doc = result["documents"][0]
    assert doc["format"] == "image"
    assert doc["text"] == "Image text"
