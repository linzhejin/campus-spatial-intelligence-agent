import pytest
from unittest.mock import patch, MagicMock

import spatial.poi as poi_module


@pytest.fixture(autouse=True)
def reset_poi_cache():
    poi_module._POIS_CACHE = []
    poi_module._LOADED = False
    yield
    poi_module._POIS_CACHE = []
    poi_module._LOADED = False


MOCK_POIS = [
    {
        "id": "poi_001",
        "name": "牌坊",
        "aliases": ["珞珈门", "武大正门", "国立武汉大学牌坊"],
        "coordinates": {"lng": 114.35806, "lat": 30.53318},
        "type": "landmark",
        "description": "武大正门牌坊",
        "scenery_score": 4,
    },
    {
        "id": "poi_002",
        "name": "樱花大道",
        "aliases": ["樱园", "樱园路"],
        "coordinates": {"lng": 114.36480, "lat": 30.53850},
        "type": "scenery",
        "description": "狮子山南坡樱花主干道",
        "scenery_score": 5,
    },
    {
        "id": "poi_003",
        "name": "樱顶",
        "aliases": ["樱花城堡", "樱园屋顶"],
        "coordinates": {"lng": 114.36400, "lat": 30.53950},
        "type": "scenery",
        "description": "老斋舍顶层平台",
        "scenery_score": 5,
    },
    {
        "id": "poi_004",
        "name": "老图书馆",
        "aliases": ["老图", "樱顶老图"],
        "coordinates": {"lng": 114.36450, "lat": 30.53900},
        "type": "scenery",
        "description": "狮子山顶老图书馆",
        "scenery_score": 5,
    },
    {
        "id": "poi_005",
        "name": "梅园",
        "aliases": ["梅操", "梅园小操场"],
        "coordinates": {"lng": 114.36200, "lat": 30.53450},
        "type": "scenery",
        "description": "梅园宿舍区",
        "scenery_score": 4,
    },
    {
        "id": "poi_006",
        "name": "桂园",
        "aliases": ["桂操", "桂园操场"],
        "coordinates": {"lng": 114.36300, "lat": 30.53800},
        "type": "scenery",
        "description": "桂园宿舍区",
        "scenery_score": 4,
    },
]


@pytest.fixture
def mock_pois():
    with patch.object(poi_module, "_load_from_json", return_value=MOCK_POIS), \
         patch.object(poi_module, "_load_from_config", return_value=[]):
        yield


class TestFindPoi:
    @pytest.mark.parametrize("query,expected_name", [
        ("牌坊", "牌坊"),
        ("老图书馆", "老图书馆"),
    ])
    def test_find_poi_exact_name(self, mock_pois, query, expected_name):
        result = poi_module.find_poi(query)
        assert result is not None
        assert result["name"] == expected_name

    @pytest.mark.parametrize("alias,expected_name", [
        ("珞珈门", "牌坊"),
        ("老图", "老图书馆"),
        ("樱园", "樱花大道"),
        ("梅操", "梅园"),
    ])
    def test_find_poi_by_alias(self, mock_pois, alias, expected_name):
        result = poi_module.find_poi(alias)
        assert result is not None
        assert result["name"] == expected_name

    @pytest.mark.parametrize("query,expected_name", [
        pytest.param("PAIFANG", "牌坊",
                     marks=pytest.mark.xfail(reason="拼音支持为 V1.1 功能，当前仅支持中文输入")),
        ("牌坊", "牌坊"),
        ("老图書館", "老图书馆"),
    ])
    def test_find_poi_case_insensitive(self, mock_pois, query, expected_name):
        result = poi_module.find_poi(query)
        assert result is not None
        assert result["name"] == expected_name

    def test_find_poi_ambiguity_returns_best_match(self, mock_pois):
        result = poi_module.find_poi("樱")
        assert result is not None
        assert result["name"] in ("樱花大道", "樱顶")

    def test_find_poi_not_found_returns_none(self, mock_pois):
        result = poi_module.find_poi("不存在的地方xyz123", min_score=0.9)
        assert result is None


class TestGetPoi:
    def test_get_poi_by_correct_id(self, mock_pois):
        result = poi_module.get_poi("牌坊")
        assert result is not None
        assert result["id"] == "poi_001"
        assert result["name"] == "牌坊"
        assert "lat" in result
        assert "lon" in result

    def test_get_poi_wrong_name_returns_none(self, mock_pois):
        result = poi_module.get_poi("某某某不存在的地点", fuzzy=False)
        assert result is None


MOCK_POIS_WITH_SUB = [
    {
        "id": "c1", "name": "桂园食堂", "aliases": [],
        "coordinates": {"lng": 114.36, "lat": 30.53},
        "type": "dining", "subcategory": "canteen", "is_minor": False,
        "scenery_score": 2,
    },
    {
        "id": "c2", "name": "瑞幸咖啡(梅园店)", "aliases": [],
        "coordinates": {"lng": 114.361, "lat": 30.531},
        "type": "dining", "subcategory": "coffee", "is_minor": True,
        "scenery_score": 1,
    },
    {
        "id": "c3", "name": "WHU1893咖啡馆", "aliases": [],
        "coordinates": {"lng": 114.362, "lat": 30.532},
        "type": "dining", "subcategory": "coffee", "is_minor": True,
        "scenery_score": 3,
    },
    {
        "id": "c4", "name": "珞珈自强超市", "aliases": [],
        "coordinates": {"lng": 114.363, "lat": 30.533},
        "type": "service", "subcategory": "supermarket", "is_minor": False,
        "scenery_score": 1,
    },
]


@pytest.fixture
def mock_sub_pois():
    with patch.object(poi_module, "_load_from_json", return_value=MOCK_POIS_WITH_SUB), \
         patch.object(poi_module, "_load_from_config", return_value=[]):
        yield


class TestSubcategory:
    def test_flatten_poi_passthrough(self, mock_sub_pois):
        flat = poi_module.get_poi("桂园食堂", fuzzy=False)
        assert flat["subcategory"] == "canteen"
        assert flat["is_minor"] is False
        assert flat["category"] == "dining"

    def test_flatten_poi_defaults_when_missing(self, mock_pois):
        """旧数据无 subcategory/is_minor 字段时给默认值，不报错。"""
        flat = poi_module.get_poi("牌坊")
        assert flat["subcategory"] == ""
        assert flat["is_minor"] is False

    def test_search_by_category_hits(self, mock_sub_pois):
        results = poi_module.search_by_category(subcategory="coffee")
        names = [p["name"] for p in results]
        assert set(names) == {"瑞幸咖啡(梅园店)", "WHU1893咖啡馆"}

    def test_search_by_category_include_minor_false(self, mock_sub_pois):
        """泛推荐场景过滤小店铺：coffee 全是 is_minor，应返回空。"""
        assert poi_module.search_by_category(
            subcategory="coffee", include_minor=False
        ) == []
        results = poi_module.search_by_category(poi_type="dining", include_minor=False)
        assert [p["name"] for p in results] == ["桂园食堂"]

    def test_search_pois_subcategory_filter(self, mock_sub_pois):
        results = poi_module.search_pois("咖啡", subcategory="coffee")
        assert results and all(p["subcategory"] == "coffee" for p in results)
