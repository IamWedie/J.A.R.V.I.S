import asyncio
import json
from types import SimpleNamespace

import pytest

import core.brain as brain_module
from core.brain import Brain
from core.study.generation import StudyGenerationError


def completion(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str) and item == "__sleep__":
            await asyncio.sleep(1)
            return completion("{}")
        return completion(item)


class FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


def make_brain(responses):
    brain = Brain()
    brain.client = FakeClient(responses)
    brain.model = "test-model"
    return brain


def flashcards_payload(cards):
    return json.dumps({"title": "Biology", "cards": cards})


@pytest.mark.asyncio
async def test_valid_json_returns_a_deck_without_tools_or_history():
    brain = make_brain([flashcards_payload([{"front": "F1", "back": "B1"}])])
    result = await brain.generate_study_material("material", "flashcards", 5, 3)
    assert result["title"] == "Biology"
    assert result["cards"] == [{"front": "F1", "back": "B1"}]
    assert result["quiz"] == []
    assert brain.history == []
    call = brain.client.chat.completions.calls[0]
    assert "tools" not in call
    assert call["response_format"] == {"type": "json_object"}
    assert call["model"] == "test-model"


@pytest.mark.asyncio
async def test_invalid_json_is_repaired_once(monkeypatch):
    brain = make_brain(
        [
            "not json",
            flashcards_payload([{"front": "F1", "back": "B1"}]),
        ]
    )
    result = await brain.generate_study_material("material", "flashcards", 5, 3)
    assert result["cards"][0]["front"] == "F1"
    calls = brain.client.chat.completions.calls
    assert len(calls) == 2
    assert "invalid" in calls[1]["messages"][-1]["content"].lower()


@pytest.mark.asyncio
async def test_invalid_json_twice_returns_invalid_output():
    brain = make_brain(["not json", "still not json"])
    with pytest.raises(StudyGenerationError) as exc:
        await brain.generate_study_material("material", "flashcards", 5, 3)
    assert exc.value.code == "study_invalid_output"
    assert len(brain.client.chat.completions.calls) == 2


@pytest.mark.asyncio
async def test_response_format_rejection_retries_without_it():
    brain = make_brain(
        [
            RuntimeError("response_format is not supported"),
            flashcards_payload([{"front": "F1", "back": "B1"}]),
        ]
    )
    result = await brain.generate_study_material("material", "flashcards", 5, 3)
    assert result["cards"][0]["front"] == "F1"
    calls = brain.client.chat.completions.calls
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


@pytest.mark.asyncio
async def test_long_material_is_chunked_and_merged(monkeypatch):
    material = ("paragraph one. " * 600) + "\n\n" + ("paragraph two. " * 600)
    first = flashcards_payload(
        [
            {"front": "Same", "back": "1"},
            {"front": "First", "back": "2"},
        ]
    )
    second = flashcards_payload(
        [
            {"front": "Same", "back": "3"},
            {"front": "Second", "back": "4"},
        ]
    )
    brain = make_brain([first, second])
    result = await brain.generate_study_material(material, "flashcards", 5, 3)
    assert len(brain.client.chat.completions.calls) == 2
    fronts = [card["front"] for card in result["cards"]]
    assert fronts[0] == "Same"
    assert len(fronts) == len(set(fronts))


@pytest.mark.asyncio
async def test_timeout_returns_model_error(monkeypatch):
    monkeypatch.setattr(brain_module, "STUDY_GENERATION_TIMEOUT", 0.01)
    brain = make_brain(["__sleep__"])
    with pytest.raises(StudyGenerationError) as exc:
        await brain.generate_study_material("material", "flashcards", 5, 3)
    assert exc.value.code == "study_model_error"


@pytest.mark.asyncio
async def test_missing_api_key_returns_model_unavailable(monkeypatch):
    monkeypatch.setattr(brain_module.config, "ZEN_API_KEY", "")
    brain = Brain()
    with pytest.raises(StudyGenerationError) as exc:
        await brain.generate_study_material("material", "flashcards", 5, 3)
    assert exc.value.code == "study_model_unavailable"


@pytest.mark.asyncio
async def test_provider_failure_returns_model_error():
    brain = make_brain([RuntimeError("connection reset")])
    with pytest.raises(StudyGenerationError) as exc:
        await brain.generate_study_material("material", "flashcards", 5, 3)
    assert exc.value.code == "study_model_error"
    assert len(brain.client.chat.completions.calls) == 1
