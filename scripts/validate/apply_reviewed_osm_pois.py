"""Apply the explicitly reviewed OSM POI decisions; never bulk-promote candidates.

python scripts/fetch/fetch_pois_osm.py --evidence scripts/audit_output/osm_local_evidence.json
python scripts/validate/apply_reviewed_osm_pois.py

The allowlists record a review of name, full footprint, campus boundary and nearby
production POIs. Nearby unrelated facilities remain distinct. Re-running is safe:
source IDs find prior additions and existing fields are preserved.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts' / 'fetch'))
from fetch_pois_osm import haversine_m

# Each item was inspected against the PBF geometry and current production names.
# Source-only describes verification depth, not data quality.
ADDITIONS = {
    'node/1197839325': ('澄波门', 'gate', 'gate', '武大工学部与水生所共边界校门；保留 foot=permit，不扩大学校范围。'),
    'node/1196703578': ('珞东二门', 'gate', 'gate', '具名校门节点、文理学部边界和通行标签一致；正式库无同名校门。'),
    'node/3631666752': ('珞南二门', 'gate', 'gate', 'OSM 明确别名明贤门；与正式库珞南门相距269米，保留独立校门。'),
    'node/13613667172': ('珞南三门', 'gate', 'gate', '具名边界门，距正式库珞南门213米，保留独立校门。'),
    'way/1445906649': ('梅园快递服务中心', 'service', 'service', 'OSM official_name 与梅园完整建筑面一致，正式库没有相同服务设施。'),
    'node/13266231641': ('信息学部快递服务中心', 'service', 'service', 'OSM official_name 标明信息学部学生区快递服务中心；独立于附近门店和宿舍。'),
    'node/9555974805': ('武汉大学医院二门诊部', 'service', 'hospital', 'operator=武汉大学医院，loc_name=信息学部校医院；此前被门诊部黑名单误排。'),
    'way/306379133': ('武汉大学医院一门诊部', 'service', 'hospital', 'operator=武汉大学医院，loc_name=工学部校医院；与文理学部医院不同院区。'),
    'way/1408203762': ('武汉大学水电服务大厅', 'service', 'service', '具名完整建筑面位于文理学部，现有POI点不在该建筑面内。'),
    'way/1445901385': ('武汉大学邮政室', 'service', 'service', '梅园具名独立建筑；与工学部已有樱花邮政所不同位置。'),
    'way/303368388': ('武汉大学一站式学生服务中心', 'service', 'service', '工学部具名完整建筑面；附近机械实验设施与该楼不同。'),
    'way/306232321': ('武汉大学信息中心', 'service', 'service', '工学部独立办公建筑，现有POI不在该建筑面内。'),
    'way/869323038': ('卓尔附馆', 'sports', 'gym', '与卓尔主馆为两个独立建筑面，中心相距102米，保留独立附馆。'),
    'way/1477325043': ('工学部排球场', 'sports', 'court', '独立运动场面，距现有工学部篮球场63米，球场类型不同。'),
    'relation/19149478': ('星湖运动场', 'sports', 'sports_field', '完整运动场关系面位于信息学部，距游泳池39米，为不同运动设施；候选图贴路29米。'),
    'way/1382564211': ('竹园网球场', 'sports', 'court', '具名网球场面位于信息学部，距现有体育馆49米，设施类型不同；候选图贴路20米。'),
    'way/380915238': ('露天羽毛球场', 'sports', 'court', '具名羽毛球场面位于工学部，附近只有宿舍类POI；候选图贴路15米。'),
    'node/9655586490': ('大学生体育活动中心', 'sports', 'sports_centre', '具名体育活动中心节点位于文理学部，距卓尔体育馆54米，名称与实体不同；候选图贴路13米。'),
    'way/104219268': ('测量仪器陈列馆', 'study', 'museum', '信息学部具名完整建筑面；附近大地测量研究室与本馆名称和建筑不同。'),
    'way/1337114129': ('人文社科楼', 'study', 'building', '完整楼面内已有爱平音乐厅，楼宇和内部音乐厅是不同层级目的地。'),
    'way/103749311': ('博观楼', 'study', 'building', '文理学部具名完整建筑面，与生命科学学院及其附楼点位不同。'),
    'way/173419309': ('水工大厅', 'study', 'laboratory', '工学部具名独立建筑面，现有POI点不在该面内。'),
    'relation/20206556': ('泥沙实验楼', 'study', 'laboratory', 'libosmium完整关系面；工学部具名独立实验建筑。'),
    'way/153752703': ('教材中心', 'service', 'service', '工学部具名独立建筑面，现有POI点不在该面内。'),
    'way/153752702': ('高压大厅', 'study', 'laboratory', '工学部具名独立建筑面，距工学部7教44米，不能仅凭距离合并。'),
    'relation/20205019': ('尖端科技楼', 'study', 'building', 'libosmium完整关系面；工学部具名独立建筑。'),
    'way/306407472': ('机电排灌楼', 'study', 'laboratory', '工学部具名独立建筑面，现有POI点不在该面内。'),
    'way/153751900': ('水工实验楼', 'study', 'laboratory', '工学部具名独立建筑面，现有POI点不在该面内。'),
    'way/1155391715': ('星空广场', 'scenery', 'square', '采用完整广场面；同名纪念石作为广场内要素，不另建重名广场。'),
    'way/1444077815': ('博望亭', 'scenery', 'pavilion', 'OSM明确别名星湖亭，亭子与星湖水体是不同目的地。'),
}

# Explicit semantic equivalence plus source geometry/proximity, not proximity alone.
ALIASES = {
    'way/104372405': ('poi_282', ['信息学部3舍', '信部3舍'], '同学部同宿舍号，距离4米。'),
    'way/104372401': ('poi_283', ['信息学部4舍', '信部4舍'], '同学部同宿舍号，距离4米。'),
    'way/236801236': ('poi_284', ['信息学部5舍', '信部5舍'], '同学部同宿舍号，距离2米。'),
    'way/103940881': ('poi_099', ['枫园三舍'], '中文三与数字3一致，距离3米；实际归属文理学部。'),
    'way/103752729': ('poi_025', ['文理学部第一教学楼'], '第一教学楼与教1楼一致，距离2米。'),
    'relation/19725659': ('poi_015', ['武汉大学图书馆'], '完整总馆面和现有总馆位置一致。'),
    'way/85985416': ('poi_261', ['竹园游泳池'], 'OSM明确alt_name=武汉大学信息学部游泳池，距离28米。'),
    'node/13613667143': ('poi_297', ['珞喻门'], '珞喻/珞瑜字形别名，南侧步行校门位置相距22米。'),
    'node/13613667147': ('poi_297', ['珞喻门'], '同一校门另一侧步行入口，保留两源节点。'),
    'node/3148705291': ('poi_296', ['南二门'], 'OSM明确alt_name=南二门，现有位置与来源节点重合。'),
    'node/13613667196': ('poi_295', ['文澜门'], '同名校门位置相距20米，附加实际入口标签。'),
    'node/13613667184': ('poi_137', [], '凌波门同名且25米内，附加实际入口标签。'),
    'node/1358787382': ('poi_303', [], '扬波门同名且13米内，附加实际入口标签。'),
    'node/13613667157': ('poi_301', [], '弘毅门同名且30米内，附加实际入口标签。'),
    'node/1663906468': ('poi_226', [], '茶港门同名且7米内；共边界门保留既有学部归属。'),
    'node/3186610000': ('poi_139', [], '科技门同名且位置重合，附加入口标签。'),
    'node/1199819000': ('poi_227', [], '洪波门同名；保留正式库坐标，将59米外来源入口单独记录。'),
}

PENDING = {
    'node/12824851047': '餐饮聚集区，不能把附近一品豆花或Manner当作其别名；待确定是否提供片区目的地。',
    'way/286543789': '同一建筑包含一、二食堂；不得作为两家食堂任一家的等价别名。',
    'way/104372393': '同一建筑包含三、四食堂；保留建筑与设施层级差异。',
    'node/13513425336': 'name=二食堂、old_name=四食堂；现库一食堂在14米内，需核对食堂改名/楼层。',
    'way/104218610': '信息学部22舍靠近B4教学楼，需核对宿舍改号或用途变化。',
    'way/104218586': '23舍与现库国际软件学院C7舍相距9米，但无明确改名证据。',
    'way/104373483': '星园食堂与星湖园餐厅相距9米；近距离不足以确认名称等价。',
    'way/306232324': '力学实验楼与力学实验教学中心相距33米，待核对是建筑/机构层级还是别名。',
    'way/104451054': '青年宿舍楼在校园面外，附近青年楼宿舍存在编号差异，保留待核。',
    'way/851856784': '弘博公寓食堂在三学部校园面外，不能纳入本轮校内目的地。',
    'way/98965430': '珞珈胡同建筑在三学部校园面外，校园名称词不能代替边界证据。',
    'way/1389860246': '牌楼与现有珞珈门点近，但景物与通行校门是不同实体，不并作别名。',
    'node/13700720842': '星空广场纪念石位于广场内；已采用way/1155391715表示广场，不另建同名广场。',
}

CAMPUS_CORRECTIONS = {
    'poi_397': ('node/13550957476', '工学部'),
    'poi_395': ('node/10743552429', '工学部'),
    'poi_415': ('way/153741407', '工学部'),
}

ALIAS_CONFLICT_RESOLUTIONS = {
    'node/13266231641': ('poi_270', '信息学部快递服务中心',
                         'OSM 将快递服务中心作为独立具名 POI；它与信息学部六舍相距约 27 米，不能继续作为六舍别名。'),
}


def source_ref(source_id):
    return {'source':'OpenStreetMap', 'id':source_id, 'license':'ODbL-1.0',
            'url':f'https://www.openstreetmap.org/{source_id}'}


def apply_review(pois_doc, candidates_doc, decisions_doc):
    rows = {p['osm_id']:p for p in candidates_doc['candidates']}
    pois = pois_doc['pois']
    by_id = {p['id']:p for p in pois}
    source_pois = {ref['id']:p for p in pois for ref in p.get('source_refs', [])
                   if ref.get('source') == 'OpenStreetMap' and ref.get('id')}
    next_id = max(int(p['id'][4:]) for p in pois if p['id'].startswith('poi_')) + 1
    decisions = {d.get('source_id'):d for d in decisions_doc.get('poi_decisions', [])
                 if d.get('source') == 'OpenStreetMap'}
    untouched = [d for d in decisions_doc.get('poi_decisions', []) if d.get('source') != 'OpenStreetMap']
    added = []
    for source_id, (name, category, subcategory, rationale) in ADDITIONS.items():
        row = rows[source_id]  # refuse silent partial application after a source change
        if row['name'] != name:
            raise ValueError(f'Name changed for reviewed source {source_id}')
        poi = source_pois.get(source_id)
        if poi is None:
            poi = {'id':f'poi_{next_id:03d}', 'name':name, 'aliases':list(row['aliases']),
                   'coordinates':{'lng':row['lng'], 'lat':row['lat']},
                   'type':category,'campus':row['campus'],'category':category,
                   'description':f"武汉大学{row['campus']}{'校门' if category == 'gate' else '校内地点'}。",
                   'season_tags':['all'],'scenery_score':1,'rating':None,
                   'subcategory':subcategory,'is_minor':False,
                   'source_refs':[source_ref(source_id)],'verification_status':'source_only',
                   'review_basis':'osm_name_full_geometry_campus_and_existing_poi_review',
                   'campus_boundary_refs':row['campus_boundary_refs']}
            if category == 'gate':
                poi['access'] = {k:row['osm_tags'][k] for k in ('access','foot','bicycle','motor_vehicle','opening_hours','locked') if k in row['osm_tags']}
            pois.append(poi); by_id[poi['id']] = poi; source_pois[source_id] = poi
            added.append(poi['id']); next_id += 1
        else:
            # Reconcile the reviewed category when an earlier run already
            # created the same source-linked row with an invalid legacy type.
            poi['type'] = category
            poi['category'] = category
        decisions[source_id] = dict(source='OpenStreetMap',source_id=source_id,decision='included',
            poi_id=poi['id'],verification_status='source_only',evidence=rationale,
            boundary_refs=row['campus_boundary_refs'],source_ref=source_ref(source_id))
    aliases_added = 0
    for source_id, (poi_id, labels, rationale) in ALIASES.items():
        row = rows[source_id]; poi = by_id[poi_id]
        distance = haversine_m(row['lng'],row['lat'],poi['coordinates']['lng'],poi['coordinates']['lat'])
        if distance > 80:
            raise ValueError(f'Reviewed match moved by {distance:.1f}m: {source_id}')
        for label in labels:
            if label != poi['name'] and label not in poi.setdefault('aliases', []):
                poi['aliases'].append(label); aliases_added += 1
        if not any(ref.get('source')=='OpenStreetMap' and ref.get('id')==source_id for ref in poi.setdefault('source_refs', [])):
            poi['source_refs'].append(source_ref(source_id))
        if poi.get('verification_status') in (None, 'legacy_unverified'):
            poi['verification_status'] = 'source_only'
        if poi['type'] == 'gate':
            entrances = poi.setdefault('source_entrances', [])
            if not any(e['source_id']==source_id for e in entrances):
                entrances.append({'source_id':source_id,'coordinates':{'lng':row['lng'],'lat':row['lat']},
                    'coordinate_system':'GCJ-02','tags':row['osm_tags']})
        decisions[source_id] = dict(source='OpenStreetMap',source_id=source_id,decision='alias_or_source_enrichment',
            poi_id=poi_id,aliases=labels,evidence=rationale,distance_m=round(distance,1),source_ref=source_ref(source_id))
    for poi_id, (source_id, campus) in CAMPUS_CORRECTIONS.items():
        row = rows[source_id]; poi = by_id[poi_id]
        if row['campus'] != campus or poi_id not in row['matched_poi_ids']:
            raise ValueError(f'Campus evidence changed: {source_id}')
        poi['campus'] = campus
        poi['campus_boundary_refs'] = row['campus_boundary_refs']
        decisions[source_id] = dict(source='OpenStreetMap',source_id=source_id,decision='campus_corrected',
            poi_id=poi_id,campus=campus,evidence='同名来源点80米内且位于完整工学部面内。',boundary_refs=row['campus_boundary_refs'])
    for source_id, (poi_id, alias, rationale) in ALIAS_CONFLICT_RESOLUTIONS.items():
        poi = by_id[poi_id]
        poi['aliases'] = [value for value in poi.get('aliases', []) if value != alias]
        current = decisions.get(source_id, {})
        decisions[source_id] = {**current, 'source':'OpenStreetMap', 'source_id':source_id,
            'decision':'included', 'alias_conflict_removed_from_poi_id':poi_id,
            'alias_conflict_removed':alias, 'evidence':rationale}
    for source_id, rationale in PENDING.items():
        decisions[source_id] = dict(source='OpenStreetMap',source_id=source_id,decision='pending_review',evidence=rationale)
    # Every excluded source gets a traceable reason; no assertion of low quality.
    for row in candidates_doc['excluded']:
        if row['osm_id'] not in decisions:
            decisions[row['osm_id']] = dict(source='OpenStreetMap', source_id=row['osm_id'],decision='excluded_from_scope',
                name=row.get('name'), evidence=row['reason'], boundary_refs=row.get('boundary_refs', []))
    for row in candidates_doc['candidates']:
        if row['osm_id'] not in decisions:
            decisions[row['osm_id']] = dict(source='OpenStreetMap',source_id=row['osm_id'],name=row['name'],
                decision='existing_name_match' if row['already_exists'] else 'pending_review',
                matched_poi_ids=row['matched_poi_ids'],
                evidence='同名或已有别名匹配，坐标80米内；未改动正式记录。' if row['already_exists'] else '已按完整校园面采集，尚未逐项确认名称、层级或用途。')
    pois_doc['count'] = len(pois)
    decisions_doc['poi_decisions'] = untouched + list(decisions.values())
    return {'added_poi_ids':added,'aliases_added':aliases_added,'production_count':len(pois),
            'candidate_count':len(rows),'reviewed_additions':len(ADDITIONS),'reviewed_alias_or_enrichment_sources':len(ALIASES)}


def main():
    pois_path = ROOT/'data/pois.json'
    candidate_path = ROOT/'data/pois_osm_candidates.json'
    decision_path = ROOT/'data/campus_review_decisions.json'
    docs = [json.loads(p.read_text(encoding='utf-8')) for p in (pois_path,candidate_path,decision_path)]
    report = apply_review(*docs)
    pois_path.write_text(json.dumps(docs[0],ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    decision_path.write_text(json.dumps(docs[2],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
