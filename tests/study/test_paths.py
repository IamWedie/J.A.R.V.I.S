import pytest

from core.study import paths


def setup_docs(tmp_path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    return docs


def test_accepts_absolute_and_documents_relative_forms(tmp_path):
    docs = setup_docs(tmp_path)
    expected = docs / "Study" / "Biology"
    expected.mkdir(parents=True)
    forms = [
        str(expected),
        "Study\\Biology",
        "Documents\\Study\\Biology",
        "Documents/Study/Biology",
    ]
    for raw in forms:
        assert paths.normalize_study_path(raw, documents_root=str(docs)) == str(expected)


def test_accepts_tilde_documents_form(tmp_path, monkeypatch):
    docs = setup_docs(tmp_path)
    expected = docs / "Study"
    expected.mkdir()
    monkeypatch.setattr(
        paths.os.path,
        "expanduser",
        lambda p: str(tmp_path) + p[1:] if p.startswith("~") else p,
    )
    assert paths.normalize_study_path("~\\Documents\\Study", documents_root=str(docs)) == str(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "\\\\server\\share",
        "//server/share",
        "\\\\?\\C:\\tmp",
        "\\rooted",
        "/rooted",
        "C:relative",
        "Study\\..\\Secret",
        "..\\Secret",
        "Study\\file.txt:stream",
        "PROGRA~1\\x",
        "Study\\name.",
        "Study\\name ",
        "~otheruser\\Documents",
    ],
)
def test_rejects_unsafe_forms(raw, tmp_path):
    docs = setup_docs(tmp_path)
    with pytest.raises(paths.StudyPathError) as exc:
        paths.normalize_study_path(raw, documents_root=str(docs))
    assert exc.value.code == "study_path_invalid"


def test_rejects_outside_documents(tmp_path):
    docs = setup_docs(tmp_path)
    outside = tmp_path / "Secret"
    outside.mkdir()
    with pytest.raises(paths.StudyPathError) as exc:
        paths.normalize_study_path(str(outside), documents_root=str(docs))
    assert exc.value.code == "study_path_outside"


def test_documents_relative_display(tmp_path):
    docs = setup_docs(tmp_path)
    target = docs / "Study" / "Biology"
    target.mkdir(parents=True)
    assert paths.documents_relative(str(target), documents_root=str(docs)) == "Study\\Biology"
    assert paths.documents_relative(str(docs), documents_root=str(docs)) == "Documents"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert paths.documents_relative(str(outside), documents_root=str(docs)) == "elsewhere"
