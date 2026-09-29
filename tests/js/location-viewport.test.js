const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function loadLocationHelpers() {
  const source = fs.readFileSync(require.resolve('../../static/js/app.js'), 'utf8');
  const marker = '    if (document.readyState === \'loading\') {';
  const end = source.indexOf(marker);
  assert.ok(end > 0, 'app.js boot boundary exists');
  const status = { style: {}, className: '', textContent: '' };
  const body = {
    className: '',
    classList: { add() {}, remove() {}, contains() { return false; } },
    appendChild() {},
  };
  const layers = {
    divIcon(options) { return options; },
    marker() {
      return {
        setLatLng() {}, bindTooltip() { return this; }, addTo() { return this; },
        getElement() { return { classList: { add() {} } }; },
      };
    },
    circle() {
      return { setLatLng() {}, setRadius() {}, setStyle() {}, addTo() { return this; } };
    },
  };
  const context = vm.createContext({
    window: { WHU_WALKER_CONFIG: { DEFAULT_CENTER: [114.3630, 30.5365], MAP_ZOOM: 16 } },
    document: {
      readyState: 'loading', body, addEventListener() {},
      getElementById(id) { return id === 'loc-status' ? status : null; },
      createElement() { return { style: {}, addEventListener() {}, remove() {} }; },
    },
    navigator: { userAgent: 'Android Mobile', platform: 'Android', maxTouchPoints: 1 },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    L: layers,
    locationStatus: status,
    console, setTimeout() { return 1; }, clearTimeout() {}, setInterval() { return 1; }, clearInterval() {}, AbortController,
  });
  vm.runInContext(source.slice(0, end) + `
    globalThis.locationViewport = {
      consumeFirstUserLocationFix: consumeFirstUserLocationFix,
      locationViewAction: locationViewAction,
      isWithinCampusView: isWithinCampusView,
      bindUserMapInteraction: bindUserMapInteraction,
      clampMobileMapHeight: clampMobileMapHeight,
      shouldFollowChatUpdates: shouldFollowChatUpdates,
      renderUserLocation: renderUserLocation,
      status: locationStatus,
      state: state,
    };
  ` + '\n})();', context);
  return context.locationViewport;
}

const helpers = loadLocationHelpers();

test('a cached location never consumes the first fresh GPS recenter opportunity', () => {
  helpers.state._hadUserLocation = false;
  helpers.state._hasCenteredOnLiveLocation = false;
  helpers.state._outsideCampusLocationNoticeShown = false;
  helpers.state._userInteractedWithMap = false;
  assert.equal(helpers.consumeFirstUserLocationFix(true, true), false);
  assert.equal(helpers.state._hadUserLocation, false);
  assert.equal(helpers.consumeFirstUserLocationFix(false, true), true);
  assert.equal(helpers.state._hadUserLocation, true);
  assert.equal(helpers.consumeFirstUserLocationFix(false, true), false);
  helpers.state._hadUserLocation = false;
  assert.equal(helpers.consumeFirstUserLocationFix(false, false), false);
  assert.equal(helpers.state._hadUserLocation, false);
});

function resetLocationMap() {
  const views = [];
  helpers.state.map = {
    setView(center, zoom) { views.push({ center, zoom }); },
    removeLayer() {},
    getContainer() { return { style: {} }; },
  };
  helpers.state.productMode = 'navigation';
  helpers.state._hadUserLocation = false;
  helpers.state._hasCenteredOnLiveLocation = false;
  helpers.state._outsideCampusLocationNoticeShown = false;
  helpers.state._dispFix = null;
  helpers.state._rejects = 0;
  helpers.state.userLocation = null;
  helpers.state.userMarker = null;
  helpers.state.userMarkerRaw = null;
  helpers.state.userAccuracyCircle = null;
  helpers.state.locSubscribers = [];
  return views;
}

test('cached marker does not pan the map, then the fresh nearby GPS fix centers it', () => {
  const views = resetLocationMap();
  helpers.renderUserLocation(114.50, 30.60, 20, false, true);
  assert.equal(views.length, 0);
  assert.equal(helpers.state._hadUserLocation, false);

  helpers.renderUserLocation(114.3630, 30.5365, 18, false, false);
  assert.equal(views.length, 1);
  assert.equal(views[0].zoom, 17);
  assert.equal(helpers.state._hadUserLocation, true);
});

test('a distant first GPS fix keeps Wuhan University centered and displays a notice', () => {
  const views = resetLocationMap();
  helpers.renderUserLocation(114.50, 30.60, 20, false, false);
  assert.deepEqual(Array.from(views[0].center), [30.5365, 114.363]);
  assert.equal(views[0].zoom, 16);
  assert.match(helpers.status.textContent, /不在武大校园附近/);
  assert.equal(helpers.state._hasCenteredOnLiveLocation, false);

  helpers.state._dispFix = null; // 模拟后续 GPS 修正，不把两个测试点当成同秒移动。
  helpers.renderUserLocation(114.3630, 30.5365, 18, false, false);
  assert.equal(views.length, 2);
  assert.equal(views[1].zoom, 17);
  assert.equal(helpers.state._hasCenteredOnLiveLocation, true);
});

test('fresh fixes choose the user, campus, or preserve view without cached jumps', () => {
  const center = [114.3630, 30.5365];
  assert.equal(helpers.locationViewAction(center[0], center[1], true, false, false), 'user');
  assert.equal(helpers.locationViewAction(center[0], center[1], false, false, false), 'preserve');
  assert.equal(helpers.locationViewAction(center[0], center[1], true, true, false), 'preserve');
  assert.equal(helpers.locationViewAction(114.50, 30.60, true, false, false, false), 'campus');
  assert.equal(helpers.locationViewAction(114.50, 30.60, true, false, false, true), 'preserve');
  assert.equal(helpers.locationViewAction(114.50, 30.60, false, false, true, false), 'user');
  assert.equal(helpers.locationViewAction(center[0], center[1], true, false, false, false, true), 'preserve');
});

test('campus vicinity accepts a campus edge but rejects a city-scale distant fix', () => {
  assert.equal(helpers.isWithinCampusView(114.3630, 30.5365), true);
  assert.equal(helpers.isWithinCampusView(114.378, 30.5365), true);
  assert.equal(helpers.isWithinCampusView(114.50, 30.60), false);
});

test('manual map gestures prevent the first delayed GPS fix from stealing the viewport', () => {
  const handlers = {};
  const container = { addEventListener(name, callback) { handlers[name] = callback; } };
  helpers.state._userInteractedWithMap = false;
  helpers.bindUserMapInteraction({ getContainer() { return container; } });
  handlers.pointerdown({ isTrusted: true });
  assert.equal(helpers.state._userInteractedWithMap, true);
  assert.equal(helpers.locationViewAction(114.3630, 30.5365, true, false, false, false,
    helpers.state._userInteractedWithMap), 'preserve');
  assert.equal(helpers.locationViewAction(114.3630, 30.5365, true, false, true, false,
    helpers.state._userInteractedWithMap), 'user');
});

test('mobile map pane height stays within usable map and chat limits', () => {
  assert.equal(helpers.clampMobileMapHeight(600, 330), 330);
  assert.equal(helpers.clampMobileMapHeight(600, 20), 168);
  assert.equal(helpers.clampMobileMapHeight(600, 900), 360);
  assert.equal(helpers.clampMobileMapHeight(300, 250), 100);
});

test('chat only auto-follows new replies while the reader is already near the latest message', () => {
  assert.equal(helpers.shouldFollowChatUpdates(600, 300, 920), true);
  assert.equal(helpers.shouldFollowChatUpdates(300, 300, 920), false);
});
