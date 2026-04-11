#!/usr/bin/env python3
"""
AI Weather Assistant — powered by Claude + OpenWeatherMap.

Setup:
  export ANTHROPIC_API_KEY="your-anthropic-key"
  export OPENWEATHERMAP_API_KEY="your-owm-key"
  pip install anthropic
  python weather_assistant.py

Get a free OpenWeatherMap API key at https://openweathermap.org/api
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

import anthropic
from anthropic import beta_tool

# ---------------------------------------------------------------------------
# API clients
# ---------------------------------------------------------------------------

_client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY from env
_OWM_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "")
_OWM_BASE = "https://api.openweathermap.org/data/2.5"


def _owm_get(endpoint: str, params: dict) -> dict:
    """Make a GET request to the OpenWeatherMap API."""
    params = {**params, "appid": _OWM_KEY}
    url = f"{_OWM_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        raise RuntimeError(f"OpenWeatherMap {exc.code}: {body}") from exc


# ---------------------------------------------------------------------------
# Weather tools (Claude calls these automatically)
# ---------------------------------------------------------------------------

@beta_tool
def get_current_weather(location: str, units: str = "metric") -> str:
    """Get the current weather conditions for a city.

    Args:
        location: City name, optionally with country code (e.g. "London" or "Paris,FR").
        units: Temperature units — "metric" (°C, default), "imperial" (°F), or "standard" (Kelvin).
    """
    try:
        data = _owm_get("weather", {"q": location, "units": units})
        sym = {"metric": "°C", "imperial": "°F"}.get(units, "K")
        return json.dumps({
            "city": f"{data['name']}, {data['sys']['country']}",
            "temperature": f"{data['main']['temp']:.1f}{sym}",
            "feels_like": f"{data['main']['feels_like']:.1f}{sym}",
            "description": data["weather"][0]["description"].capitalize(),
            "humidity": f"{data['main']['humidity']}%",
            "wind_speed": f"{data['wind']['speed']} m/s",
            "cloudiness": f"{data['clouds']['all']}%",
            "visibility_km": round(data.get("visibility", 0) / 1000, 1),
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@beta_tool
def get_forecast(location: str, days: int = 5, units: str = "metric") -> str:
    """Get the weather forecast for a city (up to 5 days).

    Args:
        location: City name, optionally with country code (e.g. "Tokyo" or "Tokyo,JP").
        days: Number of days to forecast (1–5). Defaults to 5.
        units: Temperature units — "metric" (°C, default), "imperial" (°F), or "standard" (Kelvin).
    """
    try:
        data = _owm_get("forecast", {"q": location, "units": units, "cnt": min(days, 5) * 8})
        sym = {"metric": "°C", "imperial": "°F"}.get(units, "K")

        # Group the 3-hourly readings by day
        by_day: dict[str, dict] = {}
        for item in data["list"]:
            day = item["dt_txt"][:10]
            entry = by_day.setdefault(day, {"temps": [], "descs": [], "pop": []})
            entry["temps"].append(item["main"]["temp"])
            entry["descs"].append(item["weather"][0]["description"])
            entry["pop"].append(round(item.get("pop", 0) * 100))

        forecast = [
            {
                "date": day,
                "low": f"{min(v['temps']):.1f}{sym}",
                "high": f"{max(v['temps']):.1f}{sym}",
                "description": max(set(v["descs"]), key=v["descs"].count).capitalize(),
                "rain_chance": f"{max(v['pop'])}%",
            }
            for day, v in list(by_day.items())[:days]
        ]
        return json.dumps({
            "city": f"{data['city']['name']}, {data['city']['country']}",
            "forecast": forecast,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@beta_tool
def get_air_quality(location: str) -> str:
    """Get the current air quality index (AQI) and key pollutant levels for a city.

    Args:
        location: City name, optionally with country code (e.g. "Beijing" or "Delhi,IN").
    """
    try:
        # Geocode to coordinates first, then fetch AQI
        geo = _owm_get("weather", {"q": location})
        lat, lon = geo["coord"]["lat"], geo["coord"]["lon"]

        data = _owm_get("air_pollution", {"lat": lat, "lon": lon})
        entry = data["list"][0]
        aqi = entry["main"]["aqi"]
        c = entry["components"]

        labels = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}
        return json.dumps({
            "city": f"{geo['name']}, {geo['sys']['country']}",
            "aqi": aqi,
            "quality": labels.get(aqi, "Unknown"),
            "pm2_5_µg_m3": c.get("pm2_5"),
            "pm10_µg_m3": c.get("pm10"),
            "no2_µg_m3": c.get("no2"),
            "o3_µg_m3": c.get("o3"),
            "co_µg_m3": c.get("co"),
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# System prompt (cached — stable across all turns)
# ---------------------------------------------------------------------------

_SYSTEM = """You are a friendly, knowledgeable AI weather assistant with access to real-time weather data.

You can:
- Report current weather conditions for any city worldwide
- Provide multi-day forecasts (up to 5 days)
- Check air quality index (AQI) and pollutant levels
- Compare weather across multiple cities side-by-side
- Give clothing, activity, and travel recommendations based on conditions

Guidelines:
- Always confirm the city and country so the user knows you found the right place
- Be conversational — interpret the data rather than dumping raw numbers
- Proactively add useful context ("pack an umbrella", "great day for a run", etc.)
- Default to Celsius (metric) unless the user requests Fahrenheit
- If asked "How's the weather?" without a location, ask which city they mean
- Offer to check air quality or forecast when it seems relevant"""

_TOOLS = [get_current_weather, get_forecast, get_air_quality]


# ---------------------------------------------------------------------------
# Main conversation loop
# ---------------------------------------------------------------------------

def main() -> None:
    if not _OWM_KEY:
        raise SystemExit(
            "Error: OPENWEATHERMAP_API_KEY is not set.\n"
            "Get a free key at https://openweathermap.org/api and run:\n"
            "  export OPENWEATHERMAP_API_KEY='your-key'"
        )

    print("AI Weather Assistant  (type 'quit' to exit)")
    print("─" * 48)

    history: list[dict] = []

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "bye"}:
            print("Goodbye!")
            break

        history.append({"role": "user", "content": user_input})

        # The tool runner handles all weather API calls automatically:
        # Claude decides which tools to call, calls them, gets results,
        # and keeps going until it has a final answer.
        runner = _client.beta.messages.tool_runner(
            model="claude-opus-4-6",
            max_tokens=2048,
            thinking={"type": "adaptive"},
            system=[{
                "type": "text",
                "text": _SYSTEM,
                "cache_control": {"type": "ephemeral"},  # cache system prompt
            }],
            tools=_TOOLS,
            messages=history,
        )

        reply = ""
        for message in runner:
            for block in message.content:
                if block.type == "text":
                    reply = block.text  # keep the last text block

        print(f"\nAssistant: {reply}")
        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
