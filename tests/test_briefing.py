"""Tests for core.briefing module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_detect_city():
    from core.briefing import detect_city
    city = detect_city()
    assert isinstance(city, str)
    assert len(city) > 0


def test_get_weather():
    from core.briefing import get_weather
    weather = get_weather("Soliman")
    assert isinstance(weather, str)
    assert len(weather) > 10


def test_get_news():
    from core.briefing import get_news
    news = get_news(2)
    assert isinstance(news, str)


def test_build_briefing():
    from core.briefing import build_briefing
    briefing = build_briefing()
    assert isinstance(briefing, str)
    assert "Good" in briefing
