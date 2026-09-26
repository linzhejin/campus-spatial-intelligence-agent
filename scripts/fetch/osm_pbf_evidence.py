"""Extract complete OSM areas and named source objects without campus heuristics."""
import json
from pathlib import Path
import osmium
from shapely import wkb
from shapely.geometry import box, mapping

ROOT = Path(__file__).resolve().parents[2]
BBOX = box(114.3460, 30.5150, 114.3900, 30.5510)
factory = osmium.geom.WKBFactory()

class Evidence(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.elements = {}
        self.areas = []

    def node(self, obj):
        if not obj.location.valid():
            return
        x, y = obj.location.lon, obj.location.lat
        if 114.3460 <= x <= 114.3900 and 30.5150 <= y <= 30.5510:
            tags = dict(obj.tags)
            if tags.get('name') or tags.get('name:zh'):
                key = f'node/{obj.id}'
                self.elements[key] = dict(osm_id=key, lon=x, lat=y, tags=tags)

    def way(self, obj):
        tags = dict(obj.tags)
        if not (tags.get('name') or tags.get('name:zh')):
            return
        coords = [(p.location.lon, p.location.lat) for p in obj.nodes if p.location.valid()]
        if not coords or len(coords) != len(obj.nodes):
            return
        xmin, xmax = min(p[0] for p in coords), max(p[0] for p in coords)
        ymin, ymax = min(p[1] for p in coords), max(p[1] for p in coords)
        if not BBOX.intersects(box(xmin, ymin, xmax, ymax)):
            return
        key = f'way/{obj.id}'
        self.elements[key] = dict(osm_id=key, lon=(xmin+xmax)/2, lat=(ymin+ymax)/2, tags=tags,
                                  geometry_complete=True)

    def area(self, obj):
        tags = dict(obj.tags)
        if not (tags.get('name') or tags.get('name:zh')):
            return
        try:
            geom = wkb.loads(factory.create_multipolygon(obj), hex=True)
        except Exception:
            return
        if not geom.intersects(BBOX):
            return
        key = f'{"way" if obj.from_way() else "relation"}/{obj.orig_id()}'
        xmin, ymin, xmax, ymax = geom.bounds
        point = geom.representative_point()
        record = dict(osm_id=key, lon=(xmin+xmax)/2, lat=(ymin+ymax)/2, tags=tags,
                      point_on_surface=[point.x, point.y],
                      geometry=mapping(geom), bounds=list(geom.bounds), geometry_complete=True)
        self.elements[key] = record
        if tags.get('amenity') in ('university','college','school') or tags.get('landuse') == 'education':
            self.areas.append(record)

def extract_evidence(path):
    handler = Evidence()
    handler.apply_file(str(path), locations=True)
    return dict(elements=list(handler.elements.values()), education_areas=handler.areas,
                source_file=Path(path).name, geometry_method='libosmium_complete_area_assembly')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pbf')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = extract_evidence(args.pbf)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"{len(result['elements'])} named objects; {len(result['education_areas'])} education areas")
