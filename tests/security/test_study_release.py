"""Release wiring for the Study Workspace: version, frozen spec, and docs.

Static checks only: no PyInstaller run, no server import, no model calls.
"""
from pathlib import Path

import core.config as config

ROOT = Path(__file__).resolve().parents[2]
RELEASE = "3.1.0"


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_version_file_and_config_agree():
    assert f'MyAppVersion "{RELEASE}"' in read("VERSION")
    assert config.VERSION == RELEASE


def test_frozen_spec_bundles_study_modules_and_parsers():
    text = read("jarvis.spec")
    for name in (
        "core.study.extractors",
        "core.study.generation",
        "core.study.paths",
        "core.study.service",
        "core.study.store",
        "core.tools.study_tools",
        "pypdf",
        "docx",
        "pptx",
        "openpyxl",
        "lxml.etree",
        "et_xmlfile",
    ):
        assert f'"{name}"' in text, name
    # winocr is optional: the spec may only reference it behind find_spec().
    assert 'find_spec("winocr")' in text
    assert "hiddenimports +=" in text


def test_changelog_has_the_study_release_entry():
    text = read("CHANGELOG.md")
    assert f"## {RELEASE} (" in text
    assert "### Study Workspace" in text
    assert "study.db" in text
    assert "pypdf==" + "7.0.0" in text


def test_security_doc_covers_the_study_boundary():
    text = read("SECURITY.md")
    assert "## Study workspace" in text
    assert "study_extract" in text
    assert "~/Documents" in text
    assert "study.db" in text
    assert "study.read" in text and "study.write" in text and "study.delete" in text
    assert "15 minutes" in text
    assert "AI provider" in text


def test_readme_documents_the_study_workspace():
    text = read("README.md")
    assert "Study Workspace" in text
    assert "study.db" in text
    assert "Documents" in text
    assert "winocr" in text
