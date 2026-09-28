from shapely.geometry import box

from scripts.fetch.osm_building_footprints import (
    extract_building_features,
    format_map_bbox,
    parse_osm_map_xml,
)


def test_building_extraction_keeps_only_valid_footprints_inside_campus():
    response = {"elements": [
        {"type": "way", "id": 1, "tags": {"building": "yes", "name": "inside"},
         "geometry": [
             {"lon": 114.001, "lat": 30.001},
             {"lon": 114.002, "lat": 30.001},
             {"lon": 114.002, "lat": 30.002},
             {"lon": 114.001, "lat": 30.001},
         ]},
        {"type": "way", "id": 2, "tags": {"building": "yes"},
         "geometry": [
             {"lon": 115.0, "lat": 31.0}, {"lon": 115.01, "lat": 31.0},
             {"lon": 115.01, "lat": 31.01}, {"lon": 115.0, "lat": 31.0},
         ]},
        {"type": "way", "id": 3, "tags": {"highway": "footway"},
         "geometry": [
             {"lon": 114.001, "lat": 30.001}, {"lon": 114.002, "lat": 30.001},
             {"lon": 114.002, "lat": 30.002}, {"lon": 114.001, "lat": 30.001},
         ]},
    ]}

    features, summary = extract_building_features(response, box(114.0, 30.0, 114.1, 30.1))

    assert [feature["id"] for feature in features] == ["way/1"]
    assert features[0]["properties"]["name"] == "inside"
    assert summary == {"elements": 3, "building_ways": 2, "valid_footprints": 1,
                       "outside_campus": 1, "invalid_footprints": 0}


def test_osm_map_bbox_is_bounded_and_in_api_axis_order():
    assert format_map_bbox((114.0, 30.0, 114.1, 30.1)) == "114.0,30.0,114.1,30.1"


def test_osm_map_xml_parser_reconstructs_building_way_from_nodes():
    xml = """<osm version="0.6">
      <node id="1" lon="114.001" lat="30.001" />
      <node id="2" lon="114.002" lat="30.001" />
      <node id="3" lon="114.002" lat="30.002" />
      <node id="4" lon="114.001" lat="30.001" />
      <way id="99"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/>
        <tag k="building" v="yes"/><tag k="name" v="Library"/></way>
    </osm>"""

    parsed = parse_osm_map_xml(xml)

    assert parsed["elements"][0]["id"] == 99
    assert parsed["elements"][0]["geometry"][0] == {"lon": 114.001, "lat": 30.001}
    assert parsed["elements"][0]["tags"] == {"building": "yes", "name": "Library"}
