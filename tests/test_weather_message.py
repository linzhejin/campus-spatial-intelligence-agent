from agents.workflow import _weather_message


def test_weather_advice_has_only_one_sentence_ending():
    weather = {
        "weather": "雾", "temperature": 17, "windpower": "≤3",
        "advice": "能见度较低，请注意安全。",
    }
    assert _weather_message(weather) == "当前天气：雾，气温 17℃，风力 ≤3，能见度较低，请注意安全。"
