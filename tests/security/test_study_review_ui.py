from pathlib import Path


def read(name):
    return (Path(__file__).parents[2] / "ui" / name).read_text(encoding="utf-8")


def test_review_controls_exist():
    html = read("index.html")
    for element_id in (
        "studyCard", "studyCardFront", "studyCardBack", "studyCardFlipBtn",
        "studyCardPosition", "studyPrevCard", "studyNextCard", "studyShuffleBtn",
        "studyQuizPosition", "studyQuizQuestion", "studyQuizOptions",
        "studyQuizSubmit", "studyQuizFeedback", "studyQuizExplanation",
        "studyQuizNext", "studyQuizScore", "studyQuizRestart",
    ):
        assert f'id="{element_id}"' in html
    assert 'role="button"' in html
    assert 'tabindex="0"' in html
    assert 'role="group"' in html


def test_review_functions_and_keyboard_support_exist():
    js = read("app.js")
    for name in (
        "function renderStudyCards", "function renderStudyCard",
        "function flipStudyCard", "function moveStudyCard",
        "function shuffleStudyCards", "function renderStudyQuiz",
        "function renderStudyQuestion", "function submitStudyAnswer",
        "function nextStudyQuestion", "function finishStudyQuiz",
        "function restartStudyQuiz",
    ):
        assert name in js
    assert "studyCardEl.addEventListener('click', flipStudyCard)" in js
    assert "studyCardEl.addEventListener('keydown'" in js
    assert "e.key === 'Enter' || e.key === ' '" in js
    assert "studyQuizSubmit.addEventListener('click', submitStudyAnswer)" in js
    assert "studyQuizRestart.addEventListener('click', restartStudyQuiz)" in js


def test_quiz_options_are_real_radio_inputs():
    js = read("app.js")
    assert "input.type = 'radio'" in js
    assert "input.name = 'studyQuizChoice'" in js
    assert "input.value = String(index)" in js
    assert "item.answer_index" in js
    assert "studyQuizCorrect += 1" in js
    assert "item.explanation" in js


def test_review_content_uses_text_content_only():
    js = read("app.js")
    study = js.split("/* === STUDY === */")[1].split("/* === TTS === */")[0]
    assert "innerHTML" not in study
    assert "studyCardFront.textContent" in study
    assert "studyQuizQuestion.textContent" in study
    assert "studyQuizExplanation.textContent" in study


def test_review_labels_exist_in_both_languages():
    js = read("app.js")
    for key in (
        "study_flip", "study_hide_answer", "study_card_position", "study_prev",
        "study_next", "study_shuffle", "study_no_cards", "study_quiz_position",
        "study_no_questions", "study_submit", "study_pick_answer",
        "study_correct", "study_incorrect", "study_answer_was",
        "study_next_question", "study_restart", "study_score",
    ):
        assert js.count(f"        {key}:") == 2, key


def test_language_refresh_does_not_reset_an_answered_question():
    js = read("app.js")
    assert "!studyQuizAnswered" in js
