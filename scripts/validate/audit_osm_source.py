"""Reproduce the original 82-row diagnosis and source-vs-pipeline evidence."""
import json
from pathlib import Path
from collections import Counter
import sys
from shapely.geometry import shape, Point

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts/fetch'))
from fetch_pois_osm import haversine_m


def read(path):
    return json.loads((ROOT/path).read_text(encoding='utf-8'))


def main():
    source = read('scripts/audit_output/osm_local_evidence.json')
    before = read('scripts/audit_output/pois_osm_candidates_before_boundary_audit.json')
    current = read('data/pois_osm_candidates.json')
    pois = read('data/pois.json')
    decisions = {r['source_id']:r for r in read('data/campus_review_decisions.json')['poi_decisions']
                 if r.get('source')=='OpenStreetMap'}
    elements = {r['osm_id']:r for r in source['elements']}
    areas = [(r['osm_id'],r['tags']['name'],shape(r['geometry'])) for r in source['education_areas']]
    whu_names = {'武汉大学文理学部','武汉大学信息学部','武汉大学工学部'}
    rows = []
    for row in before['missing_from_production']:
        element = elements[row['osm_id']]
        point = Point(element['lon'],element['lat'])
        containing = [(oid,name) for oid,name,geometry in areas if geometry.covers(point)]
        group = 'whu_campus' if any(name in whu_names for _,name in containing) else 'other_education_area' if containing else 'outside_or_boundary_review'
        decision = decisions.get(row['osm_id'], {})
        rows.append({**row, 'scope_diagnosis':group,'containing_areas':containing,
                     'review_decision':decision.get('decision'), 'poi_id':decision.get('poi_id'),
                     'evidence':decision.get('evidence'),
                     'source_url':f"https://www.openstreetmap.org/{row['osm_id']}"})
    before_excluded = {r['osm_id']:r for r in before['excluded']}
    recovered = [{**r,'old_exclusion_reason':before_excluded[r['osm_id']]['reason']}
                 for r in current['candidates'] if r['osm_id'] in before_excluded]
    previous_ids = {r['osm_id'] for r in before['candidates']} | set(before_excluded)
    additional_query_features = [r for r in current['candidates'] if r['osm_id'] not in previous_ids]
    coordinate_changes = []
    previous_candidates = {r['osm_id']:r for r in before['candidates']}
    for row in current['candidates']:
        previous = previous_candidates.get(row['osm_id'])
        if previous and row['osm_id'].startswith('relation/'):
            distance = haversine_m(row['lng'],row['lat'],previous['lng'],previous['lat'])
            if distance > 1:
                coordinate_changes.append({'osm_id':row['osm_id'],'name':row['name'],
                    'change_m':round(distance,1),'before':[previous['lng'],previous['lat']],
                    'after':[row['lng'],row['lat']]})
    report = {
        'source_file':'hubei-260924.osm.pbf', 'source_license':'ODbL-1.0',
        'raw_named_objects':len(source['elements']), 'assembled_education_areas':len(areas),
        'original_candidates':before['count'], 'original_name_missing_count':len(rows),
        'original_name_missing_by_scope':dict(Counter(r['scope_diagnosis'] for r in rows)),
        'original_whu_missing_by_decision':dict(Counter(r['review_decision'] for r in rows if r['scope_diagnosis']=='whu_campus')),
        'current_candidates':current['count'], 'current_name_matched':sum(r['already_exists'] for r in current['candidates']),
        'production_count':pois['count'], 'recovered_previous_exclusions_count':len(recovered),
        'additional_query_features_count':len(additional_query_features),
        'verification_status_meaning':'source_only means no independent on-site verification; it is not a quality score',
        'conclusions':[
            '39/82 original name-missing candidates lie in named neighboring institutions: campus filtering and affiliation inference were defective.',
            '40/82 lie in WHU campus areas; these include actual missing destinations, aliases, building/tenant relationships and unresolved naming conflicts.',
            'The original keyword filter excluded legitimate in-campus clinics, gates, sports facilities and cultural landmarks.',
            'The original feature query omitted office, shop and healthcare-only tagged entities, including WHU-operated departments.',
            'Relations are now assembled from complete geometry; candidates no longer average only way centers inside the search box.',
        ],
        'original_82_reviews':rows,
        'recovered_previous_exclusions':recovered,
        'additional_query_features':additional_query_features,
        'relation_coordinate_corrections':coordinate_changes,
    }
    output = ROOT/'scripts/audit_output/osm_poi_source_audit.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if not isinstance(v,list)},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
