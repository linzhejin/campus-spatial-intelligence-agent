import agents.tools as tools


def test_get_place_details_returns_verifiable_fields_and_explicit_unknowns(monkeypatch):
    poi = {
        "id": "gym-1",
        "name": "卓尔体育馆",
        "aliases": ["体育馆"],
        "coordinates": {"lng": 114.36, "lat": 30.53},
        "type": "sports",
        "subcategory": "gym",
        "campus": "文理学部",
        "description": "校内体育馆",
        "source_refs": [{"source": "OpenStreetMap", "id": "way/123"}],
        "verification_status": "source_only",
        "coordinate_verification_status": "source_only",
    }
    monkeypatch.setattr(tools, "find_poi_ambiguous", lambda _name: (poi, []))

    result, artifact = tools.execute_tool("get_place_details", {"name": "卓尔体育馆"})

    assert result["found"] is True
    place = result["place"]
    assert place["poi_id"] == "gym-1"
    assert place["aliases"] == ["体育馆"]
    assert place["source_refs"] == poi["source_refs"]
    assert place["verification_status"] == "source_only"
    assert place["coordinate_verification_status"] == "source_only"
    assert place["opening_hours"] == {"status": "unknown", "value": None}
    assert artifact is None


def test_get_place_details_preserves_not_found_candidates(monkeypatch):
    monkeypatch.setattr(tools, "find_poi_ambiguous", lambda _name: (None, []))

    result, artifact = tools.execute_tool("get_place_details", {"name": "不存在地点"})

    assert result["found"] is False
    assert result["opening_hours"] == "unknown"
    assert artifact is None


def test_get_place_details_schema_and_executor_are_registered():
    schema = next((item for item in tools.TOOL_SCHEMAS
                   if item["function"]["name"] == "get_place_details"), None)

    assert schema is not None
    assert schema["function"]["parameters"]["required"] == ["name"]
    assert "get_place_details" in tools._EXECUTORS
