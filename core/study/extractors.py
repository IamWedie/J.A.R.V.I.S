"""Bounded extraction of text, code, and binary files inside the study workspace.

The walker never leaves the requested root, never follows reparse points,
and keeps the payload small enough for the signed study tool: at most
``MAX_FILES_HARD_CAP`` documents, ``MAX_TOTAL_CHARS`` characters of text,
and ``MAX_SKIPPED_REPORTED`` skip entries.

PDF, Word, PowerPoint, Excel, and image files are read by ``_BINARY_READERS``.
A scanned PDF with no text layer and an image that cannot reach ``winocr``
are reported as skips, not failures. Every parser is imported lazily inside
its reader, so the dependencies stay optional at import time.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.security.scopes import is_reparse_point
from core.study.paths import documents_relative

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_FILE_CHARS = 20_000
MAX_TOTAL_CHARS = 90_000
MAX_FILES_DEFAULT = 20
MAX_FILES_HARD_CAP = 40
MAX_DEPTH = 6
MAX_PDF_PAGES = 80
MAX_SLIDES = 80
MAX_SHEETS = 10
MAX_ROWS = 500
MAX_SKIPPED_REPORTED = 200

SUPPORTED_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".rst", ".csv", ".log"})
SUPPORTED_CODE_EXTENSIONS = frozenset({
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".java", ".kt", ".php", ".py",
    ".js", ".ts", ".jsx", ".tsx", ".rb", ".go", ".rs", ".swift", ".sql",
    ".sh", ".ps1", ".html", ".css", ".json", ".xml", ".yaml", ".yml", ".ini",
    ".toml",
})
PDF_EXTENSIONS = frozenset({".pdf"})
DOCX_EXTENSIONS = frozenset({".docx"})
PPTX_EXTENSIONS = frozenset({".pptx"})
XLSX_EXTENSIONS = frozenset({".xlsx", ".xlsm"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"})
SUPPORTED_EXTENSIONS = (
    SUPPORTED_TEXT_EXTENSIONS | SUPPORTED_CODE_EXTENSIONS | PDF_EXTENSIONS
    | DOCX_EXTENSIONS | PPTX_EXTENSIONS | XLSX_EXTENSIONS | IMAGE_EXTENSIONS
)

_BINARY_DETECTION_BYTES = 8192

_EXCLUDED_NAMES = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "site-packages", ".mypy_cache", ".pytest_cache",
})
# System artifacts are "excluded"; dotfiles and ``~$`` Office locks are
# "hidden". Excluded names win when both match.
_EXCLUDED_FILES = frozenset({"thumbs.db"})

@dataclass(frozen=True)
class _Extracted:
    format: str
    text: str
    pages: int | None = None
    truncated: bool = False


class _Skip(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _read_text_file(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data[:_BINARY_DETECTION_BYTES]:
        raise _Skip("binary")
    return data.decode("utf-8-sig", errors="replace")


def _format_for(path: Path) -> str:
    if path.suffix.casefold() in SUPPORTED_CODE_EXTENSIONS:
        return "code"
    return "text"


def _extract_one(path: Path) -> _Extracted:
    extension = path.suffix.casefold()
    if extension in SUPPORTED_TEXT_EXTENSIONS or extension in SUPPORTED_CODE_EXTENSIONS:
        return _Extracted(format=_format_for(path), text=_read_text_file(path))
    reader = _BINARY_READERS.get(extension)
    if reader is None:
        raise _Skip("unsupported")
    return reader(path)


def _extract_pdf(path: Path) -> _Extracted:
    import pypdf

    reader = pypdf.PdfReader(str(path))
    all_pages = list(reader.pages)
    pages = all_pages[:MAX_PDF_PAGES]
    text = "\n".join((page.extract_text() or "") for page in pages)
    if not text.strip():
        raise _Skip("no_text_layer")
    return _Extracted("pdf", text, len(pages), len(all_pages) > MAX_PDF_PAGES)


def _extract_docx(path: Path) -> _Extracted:
    import docx

    document = docx.Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    text = "\n".join(parts)
    if not text.strip():
        raise _Skip("empty_text")
    return _Extracted("docx", text)


def _extract_pptx(path: Path) -> _Extracted:
    import pptx

    presentation = pptx.Presentation(str(path))
    all_slides = list(presentation.slides)
    slides = all_slides[:MAX_SLIDES]
    parts = []
    for slide in slides:
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                parts.append(shape.text_frame.text)
        try:
            notes = slide.notes_slide.notes_text_frame.text
        except Exception:
            notes = ""
        if notes:
            parts.append(notes)
    text = "\n".join(part for part in parts if part)
    if not text.strip():
        raise _Skip("empty_text")
    return _Extracted("pptx", text, len(slides), len(all_slides) > MAX_SLIDES)


def _extract_xlsx(path: Path) -> _Extracted:
    import openpyxl

    workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        all_sheets = list(workbook.worksheets)
        sheets = all_sheets[:MAX_SHEETS]
        row_truncated = False
        parts = []
        for sheet in sheets:
            parts.append(sheet.title)
            for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
                if row_index >= MAX_ROWS:
                    row_truncated = True
                    break
                parts.append("\t".join("" if value is None else str(value) for value in row))
        text = "\n".join(parts)
        if not text.strip():
            raise _Skip("empty_text")
        return _Extracted(
            "xlsx", text, None, len(all_sheets) > MAX_SHEETS or row_truncated
        )
    finally:
        workbook.close()


def _ocr_image(path: str) -> str:
    try:
        import winocr
    except Exception:
        raise _Skip("ocr_unavailable") from None
    try:
        from PIL import Image

        with Image.open(path) as image:
            result = winocr.recognize_pil(image)
            if hasattr(result, "__await__"):
                import asyncio

                result = asyncio.run(result)
            if isinstance(result, dict):
                text = result.get("text", "")
            else:
                text = getattr(result, "text", "")
            return str(text or "")
    except _Skip:
        raise
    except Exception:
        raise _Skip("ocr_failed") from None


def _extract_image(path: Path) -> _Extracted:
    text = _ocr_image(str(path))
    if not text.strip():
        raise _Skip("empty_text")
    return _Extracted("image", text)


_BINARY_READERS: dict[str, Callable[[Path], _Extracted]] = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".pptx": _extract_pptx,
    ".xlsx": _extract_xlsx,
    ".xlsm": _extract_xlsx,
    ".png": _extract_image,
    ".jpg": _extract_image,
    ".jpeg": _extract_image,
    ".bmp": _extract_image,
    ".webp": _extract_image,
    ".tif": _extract_image,
    ".tiff": _extract_image,
}


def _is_hidden(name: str) -> bool:
    return name.startswith(".") or name.startswith("~$")


def _is_excluded_dir(name: str) -> bool:
    folded = name.casefold()
    return folded in _EXCLUDED_NAMES or folded.startswith("venv")


def _is_excluded_file(name: str) -> bool:
    return name.casefold() in _EXCLUDED_FILES


def _add_skip(skipped: list[dict[str, str]], path: str, reason: str) -> bool:
    if len(skipped) < MAX_SKIPPED_REPORTED:
        skipped.append({"path": path, "reason": reason})
        return True
    if len(skipped) == MAX_SKIPPED_REPORTED:
        skipped.append({"path": ".", "reason": "skipped_limit"})
    return False


def _join(relative: str, name: str) -> str:
    return os.path.join(relative, name) if relative else name


def _walk(
    directory: Path,
    relative: str,
    depth: int,
    recursive: bool,
    skipped: list[dict[str, str]],
    candidates: list[tuple[Path, str]],
) -> None:
    try:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
    except OSError:
        _add_skip(skipped, relative or ".", "unreadable")
        return

    for entry in entries:
        name = entry.name
        child_relative = _join(relative, name)
        if entry.is_dir(follow_symlinks=False):
            if _is_excluded_dir(name):
                _add_skip(skipped, child_relative, "excluded")
                continue
            if _is_hidden(name):
                _add_skip(skipped, child_relative, "hidden")
                continue
            if is_reparse_point(entry.path):
                _add_skip(skipped, child_relative, "reparse_point")
                continue
            if not recursive:
                continue
            if depth + 1 > MAX_DEPTH:
                _add_skip(skipped, child_relative, "depth_limit")
                continue
            _walk(
                Path(entry.path),
                child_relative,
                depth + 1,
                recursive,
                skipped,
                candidates,
            )
            continue

        if _is_excluded_file(name):
            _add_skip(skipped, child_relative, "excluded")
            continue
        if _is_hidden(name):
            _add_skip(skipped, child_relative, "hidden")
            continue
        if is_reparse_point(entry.path):
            _add_skip(skipped, child_relative, "reparse_point")
            continue
        if Path(name).suffix.casefold() not in SUPPORTED_EXTENSIONS:
            _add_skip(skipped, child_relative, "unsupported")
            continue
        candidates.append((Path(entry.path), child_relative))


def _exact_file_candidate(
    path: Path, skipped: list[dict[str, str]]
) -> tuple[Path, str] | None:
    name = path.name
    if _is_excluded_file(name):
        _add_skip(skipped, name, "excluded")
        return None
    if _is_hidden(name):
        _add_skip(skipped, name, "hidden")
        return None
    if is_reparse_point(path):
        _add_skip(skipped, name, "reparse_point")
        return None
    if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
        _add_skip(skipped, name, "unsupported")
        return None
    return path, name


def extract_study_material(
    path: str,
    *,
    recursive: bool = True,
    max_files: int = MAX_FILES_DEFAULT,
    documents_root: str | None = None,
) -> dict:
    """Extract bounded text from *path* (a folder or exact file).

    Raises ``ValueError`` when *path* is missing, is itself a reparse point,
    or when *max_files* is not an integer.
    """
    if isinstance(max_files, bool) or not isinstance(max_files, int):
        raise ValueError("max_files_invalid")
    effective_max_files = max(1, min(max_files, MAX_FILES_HARD_CAP))

    root_path = Path(path)
    if not root_path.exists() or is_reparse_point(root_path):
        raise ValueError("study_path_invalid")

    skipped: list[dict[str, str]] = []
    candidates: list[tuple[Path, str]] = []
    if root_path.is_file():
        candidate = _exact_file_candidate(root_path, skipped)
        if candidate is not None:
            candidates.append(candidate)
    else:
        _walk(root_path, "", 0, recursive, skipped, candidates)

    documents: list[dict] = []
    total_chars = 0
    truncated = False
    attempted = 0

    for file_path, relative in candidates:
        if attempted >= effective_max_files:
            _add_skip(skipped, relative, "max_files")
            truncated = True
            continue
        attempted += 1
        if total_chars >= MAX_TOTAL_CHARS:
            _add_skip(skipped, relative, "truncated")
            truncated = True
            continue
        try:
            size = file_path.stat().st_size
        except OSError:
            _add_skip(skipped, relative, "unreadable")
            continue
        if size > MAX_FILE_BYTES:
            _add_skip(skipped, relative, "too_large")
            continue
        try:
            extracted = _extract_one(file_path)
        except _Skip as skip:
            _add_skip(skipped, relative, skip.reason)
            continue
        except Exception:
            _add_skip(skipped, relative, "unreadable")
            continue
        if not extracted.text.strip():
            _add_skip(skipped, relative, "empty_text")
            continue

        if extracted.truncated:
            truncated = True
        text = extracted.text
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS]
            truncated = True
        remaining = MAX_TOTAL_CHARS - total_chars
        if len(text) > remaining:
            text = text[:remaining]
            truncated = True

        total_chars += len(text)
        documents.append({
            "path": relative,
            "format": extracted.format,
            "chars": len(text),
            "pages": extracted.pages,
            "text": text,
        })

    if any(item["reason"] == "skipped_limit" for item in skipped):
        truncated = True

    return {
        "root": documents_relative(str(root_path), documents_root=documents_root),
        "documents": documents,
        "skipped": skipped,
        "total_chars": total_chars,
        "truncated": truncated,
        "limits": {
            "max_files": effective_max_files,
            "max_total_chars": MAX_TOTAL_CHARS,
        },
    }
