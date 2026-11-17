from pathlib import Path


def read(name):
    return (Path(__file__).parents[2] / "ui" / name).read_text(encoding="utf-8")


def test_study_shell_controls_exist():
    html = read("index.html")
    for element_id in (
        "viewTabs", "tabChat", "tabStudy", "chatView", "studyView",
        "studySourcePanel", "studyPathInput", "studyRecursive", "studyScanBtn",
        "studyStatusLine", "studyPreview", "studyPreviewSummary",
        "studyPreviewList", "studySkippedList", "studyMode", "studyCards",
        "studyQuestions", "studyTitleInput", "studyGenerateBtn",
        "studyLibraryPanel", "studyDeckList", "studyRefreshBtn",
        "studyDeckView", "studyDeckTitle", "studyDeckMeta", "studyDeckWarning",
        "studyBackBtn", "studyDeleteBtn", "studyCardsTab", "studyQuizTab",
        "studyCardPanel", "studyQuizPanel",
    ):
        assert f'id="{element_id}"' in html
    assert 'role="tablist"' in html
    assert 'aria-live="polite"' in html
    assert 'data-i18n="study_privacy"' in html
    assert 'role="tabpanel"' in html


def test_study_events_are_handled_with_op_id():
    js = read("app.js")
    for event in (
        "study_status", "study_scan_result", "study_generate_result",
        "study_list_result", "study_deck", "study_deleted", "study_error",
    ):
        assert f"case '{event}':" in js
    assert "msg.op_id !== studyOpId" in js
    assert "sendStudy('study_scan'" in js
    assert "sendStudy('study_generate'" in js
    assert "sendStudy('study_delete'" in js
    assert "confirm: true" in js
    assert "study_busy" in js
    assert "resetStudy()" in js


def test_study_content_uses_text_content_only():
    js = read("app.js")
    study = js.split("/* === STUDY === */")[1].split("/* === TTS === */")[0]
    assert "innerHTML" not in study
    assert ".textContent" in study
    assert "document.createElement" in study


def test_study_labels_exist_in_both_languages():
    js = read("app.js")
    for key in (
        "tab_chat", "tab_study", "study_source", "study_scan", "study_preview",
        "study_mode", "study_flashcards", "study_quiz", "study_both",
        "study_cards", "study_questions", "study_generate", "study_privacy",
        "study_saved_decks", "study_refresh", "study_delete",
        "study_confirm_delete", "study_back", "study_cards_tab",
        "study_quiz_tab", "study_truncated_deck", "study_err_path_invalid",
        "study_err_path_outside", "study_err_no_supported", "study_err_busy",
        "study_skip_ocr_unavailable",
    ):
        assert js.count(f"        {key}:") == 2, key


def test_study_tabs_are_wired():
    js = read("app.js")
    assert "tabChat.addEventListener('click', showChatView)" in js
    assert "tabStudy.addEventListener('click', showStudyView)" in js
    assert "showStudyView();" in js
    assert "refreshStudyTranslations" in js
    assert "study-mode" in js
