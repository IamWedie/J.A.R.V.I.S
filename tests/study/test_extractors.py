import os

import pytest

from core.study import extractors


def docs_root(tmp_path):
    root = tmp_path / "Documents"
    root.mkdir()
    return root


def run(root, target=None, **kwargs):
    return extractors.extract_study_material(
        str(target or root), documents_root=str(root), **kwargs
    )


def test_text_file_extracts_utf8_with_bom(tmp_path):
    root = docs_root(tmp_path)
    (root / "notes.md").write_text("\ufeffHello study", encoding="utf-8")
    result = run(root)
    doc = result["documents"][0]
    assert doc["path"] == "notes.md"
    assert doc["format"] == "text"
    assert doc["text"] == "Hello study"
    assert result["total_chars"] == len("Hello study")


def test_source_code_is_plain_text(tmp_path):
    root = docs_root(tmp_path)
    (root / "main.py").write_text("print('hi')", encoding="utf-8")
    result = run(root)
    assert result["documents"][0]["format"] == "code"
    assert "print" in result["documents"][0]["text"]


def test_nul_byte_file_is_binary_skip(tmp_path):
    root = docs_root(tmp_path)
    (root / "blob.txt").write_bytes(b"ok\x00bad")
    result = run(root)
    assert result["documents"] == []
    assert result["skipped"][0]["reason"] == "binary"


def test_unsupported_extension_is_reported(tmp_path):
    root = docs_root(tmp_path)
    (root / "app.exe").write_bytes(b"MZ")
    result = run(root)
    assert result["documents"] == []
    assert result["skipped"][0]["reason"] == "unsupported"


def test_hidden_and_excluded_entries_are_reported(tmp_path):
    root = docs_root(tmp_path)
    (root / "visible.txt").write_text("keep", encoding="utf-8")
    (root / ".secret.txt").write_text("hide", encoding="utf-8")
    (root / "~$lock.docx").write_bytes(b"lock")
    (root / "Thumbs.db").write_bytes(b"db")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("x", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.py").write_text("x", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "lib.py").write_text("x", encoding="utf-8")

    result = run(root)
    assert [d["path"] for d in result["documents"]] == ["visible.txt"]
    reasons = {item["path"]: item["reason"] for item in result["skipped"]}
    assert reasons[".secret.txt"] == "hidden"
    assert reasons["~$lock.docx"] == "hidden"
    assert reasons["Thumbs.db"] == "excluded"
    assert reasons[".git"] == "excluded"
    assert reasons["node_modules"] == "excluded"
    assert reasons["__pycache__"] == "excluded"
    assert reasons[".venv"] == "excluded"


def test_non_recursive_scan_ignores_subfolders(tmp_path):
    root = docs_root(tmp_path)
    (root / "sub").mkdir()
    (root / "sub" / "a.txt").write_text("a", encoding="utf-8")
    result = run(root, recursive=False)
    assert result["documents"] == []


def test_depth_beyond_six_is_skipped(tmp_path):
    root = docs_root(tmp_path)
    current = root
    for level in range(1, 8):
        current = current / f"d{level}"
        current.mkdir()
        (current / "ok.txt").write_text(f"level {level}", encoding="utf-8")
    result = run(root)
    paths = [d["path"] for d in result["documents"]]
    assert "d1\\d2\\d3\\d4\\d5\\d6\\ok.txt" in paths
    assert all("d7" not in path for path in paths)
    assert {"path": "d1\\d2\\d3\\d4\\d5\\d6\\d7", "reason": "depth_limit"} in result["skipped"]


def test_per_file_cap_sets_truncated(tmp_path):
    root = docs_root(tmp_path)
    (root / "big.txt").write_text("x" * 25000, encoding="utf-8")
    result = run(root)
    assert len(result["documents"][0]["text"]) == 20000
    assert result["truncated"] is True


def test_total_cap_stops_at_90000(tmp_path):
    root = docs_root(tmp_path)
    for index in range(6):
        (root / f"f{index}.txt").write_text("x" * 20000, encoding="utf-8")
    result = run(root)
    assert result["total_chars"] == 90000
    assert result["truncated"] is True
    assert any(item["reason"] == "truncated" for item in result["skipped"])


def test_max_files_hard_cap(tmp_path):
    root = docs_root(tmp_path)
    for index in range(50):
        (root / f"f{index:02d}.txt").write_text("x", encoding="utf-8")
    result = run(root, max_files=40)
    assert len(result["documents"]) == 40
    assert sum(1 for item in result["skipped"] if item["reason"] == "max_files") == 10
    assert result["truncated"] is True


def test_oversized_file_is_reported(tmp_path):
    root = docs_root(tmp_path)
    huge = root / "huge.txt"
    with open(huge, "wb") as handle:
        handle.truncate(extractors.MAX_FILE_BYTES + 1)
    result = run(root)
    assert result["documents"] == []
    assert result["skipped"][0]["reason"] == "too_large"


def test_unreadable_file_is_reported(tmp_path, monkeypatch):
    root = docs_root(tmp_path)
    (root / "locked.txt").write_text("x", encoding="utf-8")

    def boom(path):
        raise OSError("locked")

    monkeypatch.setattr(extractors, "_read_text_file", boom)
    result = run(root)
    assert result["documents"] == []
    assert result["skipped"][0]["reason"] == "unreadable"


def test_exact_file_scan(tmp_path):
    root = docs_root(tmp_path)
    study = root / "Study"
    study.mkdir()
    target = study / "one.txt"
    target.write_text("one", encoding="utf-8")
    result = run(root, target)
    assert result["root"] == "Study\\one.txt"
    assert result["documents"][0]["path"] == "one.txt"


def test_root_display_is_documents_relative(tmp_path):
    root = docs_root(tmp_path)
    target = root / "Study" / "Biology"
    target.mkdir(parents=True)
    (target / "a.txt").write_text("a", encoding="utf-8")
    result = run(root, target)
    assert result["root"] == "Study\\Biology"


def test_missing_root_raises(tmp_path):
    root = docs_root(tmp_path)
    with pytest.raises(ValueError):
        run(root, root / "missing")


def test_skipped_list_is_bounded(tmp_path):
    root = docs_root(tmp_path)
    for index in range(250):
        (root / f"f{index:03d}.exe").write_bytes(b"MZ")
    result = run(root)
    assert len(result["skipped"]) == 201
    assert result["skipped"][-1] == {"path": ".", "reason": "skipped_limit"}
    assert result["truncated"] is True
