"""Provider data must establish identity before it can move a navigation endpoint."""
from spatial.amap_poi import normalize_poi, choose_match, lookup
from scripts.fetch.fetch_pois_amap_full import keep_poi


def test_campus_names_are_not_rejected_by_ambiguous_substrings():
    assert keep_poi('茶港门', '武汉大学', '190301')
    assert keep_poi('武汉大学学生服务中心', '', '141200')
    assert keep_poi('武汉大学健身中心', '', '080100')
    assert not keep_poi('茶港门外卖柜', '', '070000')


def test_empty_provider_arrays_are_not_coordinates_or_aliases():
    row = normalize_poi({'name': '卓尔体育馆', 'location': '114.36,30.54',
                         'alias': [], 'entr_location': [], 'parent': []})
    assert row['aliases'] == []
    assert row['navigation_coordinates'] is None
    assert row['parent_id'] == ''


def test_wrong_gate_is_never_an_exact_match():
    rows = [normalize_poi({'name': '凌波门', 'location': '114.36,30.54'})]
    assert choose_match('澄波门', rows) is None


def test_parent_building_and_locker_do_not_match_gate():
    rows = [normalize_poi({'name': name, 'location': '114.36,30.54'})
            for name in ['武汉大学', '茶港门外卖柜']]
    assert choose_match('茶港门', rows) is None


def test_exact_identity_preserves_entrance_and_alias_without_moving_center():
    row = normalize_poi({'name': '武汉大学卓尔体育馆', 'location': '114.36,30.54',
                         'entr_location': '114.3601,30.5401', 'alias': '卓尔馆|卓尔体育馆'})
    found = choose_match('卓尔体育馆', [row], {'lon': 114.36, 'lat': 30.54})
    assert found['coordinates'] == {'lng': 114.36, 'lat': 30.54}
    assert found['navigation_coordinates'] == {'lng': 114.3601, 'lat': 30.5401}
    assert found['aliases'] == ['卓尔馆', '卓尔体育馆']


def test_same_name_far_away_or_ambiguous_is_not_selected():
    row = normalize_poi({'name': '体育馆', 'location': '114.38,30.54'})
    assert choose_match('体育馆', [row], {'lon': 114.35, 'lat': 30.54}) is None
    other = normalize_poi({'name': '体育馆', 'location': '114.36,30.54'})
    assert choose_match('体育馆', [row, other]) is None


def test_remote_entrance_does_not_override_local_destination():
    row = normalize_poi({'name': '总馆', 'location': '114.36,30.54',
                         'entr_location': '114.38,30.54'})
    assert row['navigation_coordinates'] is None


def test_online_lookup_searches_all_wuhan_districts(monkeypatch):
    monkeypatch.setenv('AMAP_WEB_KEY', 'test-key')

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'status': '1', 'pois': [
                {'id': '1', 'name': '卓尔体育馆', 'location': '114.36,30.54'}]}

    class Client:
        def __init__(self):
            self.params = None

        def get(self, url, params):
            self.params = params
            return Response()

    client = Client()
    assert lookup('卓尔体育馆', client=client)['name'] == '卓尔体育馆'
    assert client.params['city'] == '420100'
    assert client.params['citylimit'] == 'false'
