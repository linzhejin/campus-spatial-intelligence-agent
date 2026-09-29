import json
from pathlib import Path

from app import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_course_reference_asset_contains_every_normalized_source_feature():
    path = ROOT / "data" / "course_spatial_reference.geojson"
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["type"] == "FeatureCollection"
    assert document["source_crs"] == "EPSG:4547"
    assert document["coordinate_system"] == "EPSG:4326"
    roads = [feature for feature in document["features"]
             if feature["geometry"]["type"] == "LineString"]
    spots = [feature for feature in document["features"]
             if feature["geometry"]["type"] == "Point"]
    assert len(roads) == 647
    assert len(spots) == 5

    by_row = {feature["properties"]["source_row"]: feature for feature in roads}
    for row in (210, 270):
        feature = by_row[row]
        assert feature["properties"]["name"]
        assert feature["properties"]["routing_disposition"] == "active_in_route"
    assert all(feature.get("properties", {}).get("source_id") for feature in roads + spots)
    assert all(feature.get("properties", {}).get("source_ids") for feature in spots)


def test_course_reference_api_serves_full_source_and_routing_dispositions():
    client = create_app().test_client()

    response = client.get("/api/course-spatial-reference")

    assert response.status_code == 200
    body = response.get_json()
    assert body["data"]["counts"] == {"roads": 647, "spots": 5}
    assert body["data"]["source_crs"] == "EPSG:4547"
    assert body["data"]["coordinate_system"] == "EPSG:4326"
    rows = {feature["properties"]["source_row"]: feature
            for feature in body["data"]["features"]
            if feature["geometry"]["type"] == "LineString"}
    assert rows[270]["properties"]["routing_disposition"] == "active_in_route"
    assert "network_coverage" in rows[270]["properties"]


def test_runtime_graph_and_spatial_reference_share_release_fingerprint():
    import networkx as nx

    reference = json.loads((ROOT / "data" / "course_spatial_reference.geojson")
                           .read_text(encoding="utf-8"))
    graph = nx.read_graphml(ROOT / "data" / "whu_road_network.graphml",
                            node_type=int, force_multigraph=True)

    assert graph.graph["course_release_fingerprint"] == reference["release_fingerprint"]


def test_course_coverage_audit_accounts_for_every_road_without_claiming_access():
    path = ROOT / "data" / "course_road_coverage_audit.json"
    audit = json.loads(path.read_text(encoding="utf-8"))

    assert audit["course_road_count"] == 647
    assert len(audit["roads"]) == 647
    assert len({row["source_id"] for row in audit["roads"]}) == 647
    assert "does not establish pedestrian access" in audit["interpretation"]
    by_row = {row["source_row"]: row for row in audit["roads"]}
    assert by_row[210]["release_within_3m_pct"] >= 95
    assert by_row[270]["release_within_3m_pct"] >= 95


def test_every_course_road_has_an_explicit_runtime_status():
    path = ROOT / "data" / "course_spatial_reference.geojson"
    document = json.loads(path.read_text(encoding="utf-8"))
    roads = [feature for feature in document["features"]
             if feature["geometry"]["type"] == "LineString"]

    assert len(roads) == 647
    by_row = {feature["properties"]["source_row"]: feature["properties"]
              for feature in roads}
    active_rows = {row for row, properties in by_row.items()
                   if properties["routing_disposition"] == "active_in_route"}
    assert len(active_rows) == 645
    assert {row for row, properties in by_row.items()
            if properties["routing_disposition"] == "ambiguous_not_changed"} == {393, 394}
    assert by_row[265]["routing_disposition"] == "active_in_route"
    assert by_row[265]["active_in_routing_graph"] is True
    assert by_row[265]["routing_override"]["id"] == "walkable-construction-source-row-265"


def test_authoritative_course_roads_are_materialized_in_walk_graph():
    import ast
    import networkx as nx

    reference = json.loads((ROOT / "data" / "course_spatial_reference.geojson")
                           .read_text(encoding="utf-8"))
    features = [feature for feature in reference["features"]
                if feature["geometry"]["type"] == "LineString"]
    protected_rows = {393, 394}
    policy = json.loads((ROOT / "data" / "course_routing_policy.json")
                        .read_text(encoding="utf-8"))
    walkable_construction_rows = set(policy["walkable_construction_source_rows"])
    expected_active_ids = {
        feature["properties"]["source_id"] for feature in features
        if feature["properties"].get("source_row") not in protected_rows
        and (feature["properties"].get("road_class") != "construction"
             or feature["properties"].get("source_row") in walkable_construction_rows)
    }
    graph = nx.read_graphml(ROOT / "data" / "whu_road_network.graphml",
                            node_type=int, force_multigraph=True)
    materialized_ids = set()
    for _, _, _, edge in graph.edges(keys=True, data=True):
        source_ids = edge.get("course_source_ids") or []
        if isinstance(source_ids, str):
            try:
                source_ids = json.loads(source_ids)
            except json.JSONDecodeError:
                try:
                    source_ids = ast.literal_eval(source_ids)
                except (ValueError, SyntaxError):
                    source_ids = [source_ids]
        if edge.get("course_source_id"):
            source_ids = [*source_ids, edge["course_source_id"]]
        materialized_ids.update(map(str, source_ids))

    assert len(expected_active_ids) == 645
    assert expected_active_ids <= materialized_ids
    rows_by_id = {feature["properties"]["source_id"]: feature
                  for feature in features}
    assert all(rows_by_id[source_id]["properties"]["active_in_routing_graph"]
               for source_id in expected_active_ids)
    assert {feature["properties"]["source_row"] for feature in features
            if not feature["properties"]["active_in_routing_graph"]} == {
                *protected_rows,
                *(feature["properties"]["source_row"] for feature in features
                  if feature["properties"].get("road_class") == "construction"
                  and feature["properties"].get("source_row") not in walkable_construction_rows),
            }


def test_technology_gate_course_path_is_connected_walk_only():
    import networkx as nx
    from spatial.routing import filter_graph_for_mode

    graph = nx.read_graphml(ROOT / "data" / "whu_road_network.graphml",
                            node_type=int, force_multigraph=True)
    def source_edges(source_id):
        result = []
        for u, v, key, edge in graph.edges(keys=True, data=True):
            raw_ids = edge.get("course_source_ids") or []
            if isinstance(raw_ids, str):
                try:
                    raw_ids = json.loads(raw_ids)
                except json.JSONDecodeError:
                    raw_ids = [raw_ids]
            if edge.get("course_source_id"):
                raw_ids = [*raw_ids, edge["course_source_id"]]
            if source_id in set(map(str, raw_ids)):
                result.append((u, v, key, edge))
        return result

    row = "whu_course_4392126ae0613239:road:0265"
    row_edges = source_edges(row)

    assert len(row_edges) >= 2
    assert all(json.loads(edge["allowed_modes"]) == ["walk"]
               for _, _, _, edge in row_edges)
    walk_graph, _, _ = filter_graph_for_mode(graph, "walk")
    bike_graph, _, _ = filter_graph_for_mode(graph, "bike")
    drive_graph, _, _ = filter_graph_for_mode(graph, "drive")
    assert all(walk_graph.has_edge(u, v, key) for u, v, key, _ in row_edges)
    assert all(not bike_graph.has_edge(u, v, key) for u, v, key, _ in row_edges)
    assert all(not drive_graph.has_edge(u, v, key) for u, v, key, _ in row_edges)

    physical_endpoints = {node for u, v, _, _ in row_edges for node in (u, v)}
    assert len(physical_endpoints) >= 2
    adjacent_path_nodes = {
        node for u, v, _, _ in source_edges("whu_course_4392126ae0613239:road:0266")
        for node in (u, v)
    }
    gate_side_nodes = {
        node
        for source_id in (
            "whu_course_4392126ae0613239:road:0222",
            "whu_course_4392126ae0613239:road:0223",
            "whu_course_4392126ae0613239:road:0286",
        )
        for u, v, _, _ in source_edges(source_id)
        for node in (u, v)
    }
    assert physical_endpoints & adjacent_path_nodes
    assert physical_endpoints & gate_side_nodes
    assert all(nx.has_path(walk_graph, node, 1196701909)
               for node in physical_endpoints)
    base_graph = nx.read_graphml(ROOT / "data" / "whu_road_network_osm_base.graphml",
                                 node_type=int)
    assert base_graph.has_node(1196701909)


def test_course_walk_activation_policy_keeps_user_designated_topology_rows():
    policy = json.loads((ROOT / "data" / "course_routing_policy.json")
                        .read_text(encoding="utf-8"))
    topology = json.loads((ROOT / "data" / "course_topology_exceptions.json")
                          .read_text(encoding="utf-8"))

    assert policy["source_layer"] == "whu_road"
    assert policy["allowed_modes"] == ["walk"]
    assert policy["include_unclassified"] is True
    assert policy["walkable_construction_source_rows"] == [265]
    assert policy["excluded_source_rows"] == sorted({
        row for exception in topology["exceptions"]
        for row in exception["source_rows"]
    })


def test_runtime_poi_master_uses_canonical_star_lake_canteen_name():
    data = json.loads((ROOT / "data" / "pois.json").read_text(encoding="utf-8"))
    poi = next(row for row in data["pois"] if row["id"] == "poi_307")

    assert poi["name"] == "星湖园食堂"
    assert "星湖园餐厅" in poi["aliases"]


def test_star_lake_canteen_is_returned_by_the_live_poi_api():
    response = create_app().test_client().get("/api/pois?keyword=星湖园食堂")

    assert response.status_code == 200
    result = response.get_json()["data"]["pois"][0]
    assert result["id"] == "poi_307"
    assert result["name"] == "星湖园食堂"


def test_raw_course_data_layers_are_not_exposed_as_user_map_controls():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    for control_id in (
        "toggle-campus-roads",
        "toggle-campus-pois",
        "toggle-course-spots",
    ):
        assert f'id="{control_id}"' not in html
        assert control_id not in javascript
    assert "loadCampusSpatialLayers" not in javascript
    assert "玉兰二门连接小路" not in javascript
