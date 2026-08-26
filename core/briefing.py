"""Morning briefing — weather, Tunisia news, reminders."""
import json
import urllib.request
from datetime import datetime

from core.greeter import _time_greeting


def detect_city():
    try:
        r = urllib.request.urlopen("https://ipwho.is/", timeout=5)
        data = json.loads(r.read())
        city = data.get("city", "")
        country = data.get("country", "")
        if city:
            return f"{city}, {country}" if country else city
    except Exception:
        pass
    return "Tunisia"


def get_weather(city="Tunis"):
    try:
        query = city.split(",")[0].strip()
        url = f"https://wttr.in/{query}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        current = data["current_condition"][0]
        temp_c = current["temp_C"]
        feels = current["FeelsLikeC"]
        desc = current["weatherDesc"][0]["value"]
        humidity = current["humidity"]
        wind = current["windspeedKmph"]

        today = data["weather"][0]
        max_t = today["maxtempC"]
        min_t = today["mintempC"]

        return (
            f"{desc}, {temp_c} degrees, feels like {feels}. "
            f"High {max_t}, low {min_t}. "
            f"Humidity {humidity}%, wind {wind} km/h."
        )
    except Exception as e:
        return f"Weather unavailable: {e}"


def get_news(count=3):
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.news("Tunisia today", max_results=count))
        if not results:
            return "No Tunisia news available right now."
        headlines = [r["title"] for r in results if r.get("title")]
        if not headlines:
            return "No Tunisia news available right now."
        return ". ".join(headlines[:count])
    except Exception as e:
        return f"News unavailable: {e}"


def get_today_reminders():
    try:
        from core.scheduler import list_reminders
        result = list_reminders()
        if "No pending" in result:
            return ""
        return f"Reminders: {result}"
    except Exception:
        return ""


def build_briefing():
    now = datetime.now()
    greeting = _time_greeting()
    date_str = now.strftime("%A, %B %d")

    city = detect_city()
    weather = get_weather(city)
    news = get_news(3)
    reminders = get_today_reminders()

    parts = [
        f"{greeting}. Today is {date_str}.",
        f"Weather in {city}: {weather}",
        f"Tunisia news: {news}",
    ]
    if reminders:
        parts.append(reminders)

    return " ".join(parts)
