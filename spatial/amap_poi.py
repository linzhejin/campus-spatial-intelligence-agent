"""Bounded online AMap lookup; provider data lives only in the current request.

Open master data is maintained separately. Exact name/alias and local proximity
must agree before a provider entrance can be used for a known destination.
"""
import math
import os
import re

import httpx

def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _coord(value):
    try:
        lng, lat = map(float, _text(value).split(','))
        if math.isfinite(lng) and math.isfinite(lat) and -180 <= lng <= 180 and -90 <= lat <= 90:
            return {'lng': lng, 'lat': lat}
    except (ValueError, TypeError):
        pass
    return None


def _distance(a, b):
    lat = math.radians((a['lat'] + b['lat']) / 2)
    return math.hypot((a['lng'] - b['lng']) * 111320 * math.cos(lat),
                      (a['lat'] - b['lat']) * 110540)


def _label(value):
    from spatial.poi import _normalize_num
    value = re.sub(r'^(武汉大学|武大)[\s\-—]*', '', _text(value))
    return re.sub(r'[\s\-—（）()]', '', _normalize_num(value))


def normalize_poi(raw):
    center = _coord(raw.get('location'))
    if not center or not _text(raw.get('name')):
        return None
    entrance = _coord(raw.get('entr_location'))
    if entrance and _distance(center, entrance) > 150:
        entrance = None
    aliases = list(dict.fromkeys(x.strip() for x in re.split(r'[|;；]', _text(raw.get('alias'))) if x.strip()))
    return {'id': 'amap:' + _text(raw.get('id')), 'name': _text(raw.get('name')),
            'aliases': aliases, 'coordinates': center, 'lon': center['lng'], 'lat': center['lat'],
            'navigation_coordinates': entrance, 'coordinate_system': 'GCJ-02',
            'parent_id': _text(raw.get('parent')), 'navi_poiid': _text(raw.get('navi_poiid')),
            'type_code': _text(raw.get('typecode')), 'type_name': _text(raw.get('type')),
            'description': _text(raw.get('address')), 'source': 'AMap online',
            'verification_status': 'provider_only'}


def choose_match(name, rows, known=None):
    labels = {_label(name)}
    if known:
        labels.update(_label(x) for x in [known.get('name', ''), *known.get('aliases', [])])
    labels.discard('')
    matches = []
    for row in rows:
        if not row or not labels.intersection(_label(x) for x in [row['name'], *row['aliases']]):
            continue
        if known:
            center = known.get('coordinates') or {'lng': known['lon'], 'lat': known['lat']}
            if _distance(center, row['coordinates']) > 100:
                continue
        matches.append(row)
    return matches[0] if len(matches) == 1 else None


def lookup(name, known=None, client=None):
    """One query, no pagination harvesting, disk cache or raw-response logging."""
    key = os.getenv('AMAP_WEB_KEY') or os.getenv('AMAP_KEY')
    if not key or not name:
        return None
    params = {'key': key, 'keywords': name, 'city': '420100', 'citylimit': 'false',
              'extensions': 'all', 'offset': 20, 'page': 1}
    try:
        if client is None:
            with httpx.Client(timeout=httpx.Timeout(3, connect=2)) as request_client:
                response = request_client.get('https://restapi.amap.com/v3/place/text', params=params)
        else:
            response = client.get('https://restapi.amap.com/v3/place/text', params=params)
        response.raise_for_status()
        body = response.json()
        if str(body.get('status')) != '1':
            return None
        rows = [normalize_poi(x) for x in body.get('pois') or [] if isinstance(x, dict)]
        return choose_match(name, rows, known)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        # HTTP exception strings can contain the key-bearing request URL.
        return None


def online_lookup(name, known=None):
    """Request-scoped lookup; offline algorithms and automated tests stay offline."""
    from flask import current_app, g, has_request_context
    if not has_request_context() or current_app.testing:
        return None
    if os.getenv('AMAP_POI_LOOKUP_ENABLED', 'true').lower() in ('false', '0', 'no'):
        return None
    cache = g.setdefault('_amap_poi_request', {})
    token = (name, known.get('id') if known else None)
    if token not in cache:
        if len(cache) >= 4:
            return None
        cache[token] = lookup(name, known)
    return cache[token]


def navigation_wgs(poi, mode='walk'):
    """Keep destination display coordinates intact; select a separate arrival point."""
    from spatial.coord_transform import gcj02_to_wgs84
    center = poi.get('coordinates') or {'lng': poi['lon'], 'lat': poi['lat']}
    # Gate permissions belong to the graph. Never move a restricted gate to a
    # different provider entrance, potentially bypassing its access control.
    if mode == 'walk' and poi.get('type') not in ('gate', 'area') and poi.get('name'):
        source = online_lookup(poi['name'], poi)
        if source and source.get('navigation_coordinates'):
            center = source['navigation_coordinates']
    return gcj02_to_wgs84(center['lng'], center['lat'])
