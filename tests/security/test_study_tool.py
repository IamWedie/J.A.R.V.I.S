import json

import pytest

from core.tools import study_tools


@pytest.fixture
def docs(tmp_path, monkeypatch):
    root = tmp_path / "Documents"
    root.mkdir()
    monkeypatch.setattr(study_tools, "DOCUMENTS_ROOT", str(root))
    return root


def test_extracts_a_file_inside_documents(docs):
    target = docs / "note.txt"
    target.write_text("hello study", encoding="utf-8")
    payload = json.loads(study_tools.extract_study_material(str(target)))
    assert payload["documents"][0]["text"] == "hello study"
    assert payload["total_chars"] == len("hello study")


def test_relative_path_is_rejected(docs):
    with pytest.raises(ValueError) as exc:
        study_tools.extract_study_material("note.txt")
    assert str(exc.value) == "study_path_outside"


def test_outside_documents_is_rejected(tmp_path, docs):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        study_tools.extract_study_material(str(outside))
    assert str(exc.value) == "study_path_outside"


def test_reparse_point_is_rejected(docs, monkeypatch):
    target = docs / "note.txt"
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(study_tools, "is_reparse_point", lambda path: True)
    with pytest.raises(ValueError) as exc:
        study_tools.extract_study_material(str(target))
    assert str(exc.value) == "study_path_outside"


def test_non_boolean_recursive_is_rejected(docs):
    with pytest.raises(ValueError) as exc:
        study_tools.extract_study_material(str(docs), recursive="yes")
    assert str(exc.value) == "study_invalid_request"
