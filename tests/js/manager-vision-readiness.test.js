const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../../static/js/manager.js'), 'utf8');

function managerHarness() {
  const elements = new Map();
  const intervalCallbacks = [];
  const mapListeners = {};
  const requested = [];
  const uploadResolvers = [];
  const impactPreviewResolvers = [];
  let visionPostCount = 0;
  let visionJobs = [];
  let roadEvents = [];
  const calls = [];
  let inferenceReady = false;
  let visionStatusFailure = false;
  let sessionExpired = false;
  let deferImpactPreviews = false;
  let createdNodeCount = 0;

  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const listeners = {};
    const classes = new Set();
    let textContent = '';
    const node = {
      id, hidden: false, value: '', innerHTML: '', disabled: false,
      checked: false, files: [], style: {}, listeners, children: [], attributes: {},
      setAttribute(name, value) { this.attributes[name] = String(value); },
      classList: {
        add: (value) => classes.add(value),
        remove: (value) => classes.delete(value),
        contains: (value) => classes.has(value) || String(node.className || '').split(/\s+/).includes(value),
        toggle: (value, force) => force ? classes.add(value) : classes.delete(value),
      },
      addEventListener: (name, callback) => { listeners[name] = callback; },
      appendChild(child) { this.children.push(child); return child; },
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
    };
    Object.defineProperty(node, 'textContent', {
      get: () => textContent,
      set(value) {
        textContent = value;
        if (value === '') this.children = [];
      },
    });
    elements.set(id, node);
    return node;
  }

  const map = {
    setView() { return this; },
    invalidateSize() {},
    on(name, callback) { mapListeners[name] = callback; },
    removeLayer() {},
  };
  const layer = { addTo() { return this; }, clearLayers() {} };
  const L = {
    map: () => map,
    tileLayer: () => layer,
    layerGroup: () => layer,
    circleMarker: () => layer,
    polyline: () => layer,
  };
  const window = {
    WHU_WALKER_CONFIG: { mapCenter: [114.363, 30.5365], mapZoom: 15 },
    setTimeout: (fn) => fn(),
    setInterval: (fn) => { intervalCallbacks.push(fn); return intervalCallbacks.length; },
    clearInterval() {},
    alert() {}, confirm: () => true,
  };
  const document = { hidden: false, activeElement: null, getElementById: element, createElement: (tag) => {
      const node = element('created-' + tag + '-' + createdNodeCount++);
      node.tagName = tag;
      return node;
    }, createElementNS: (_namespace, tag) => {
      const node = element('created-' + tag + '-' + createdNodeCount++);
      node.tagName = tag;
      return node;
    }, createTextNode: (text) => ({ text }) };
  const context = {
    document,
    window, L, FormData: class FormData { append() {} }, URLSearchParams, Date, Math, Promise, setTimeout,
    fetch: async (url, options = {}) => {
      requested.push(url);
      calls.push({ url, options });
      if (url.startsWith('/api/road-conditions/snap') && deferImpactPreviews) {
        const match = url.match(/[?&]type=([^&]+)/);
        const type = match ? decodeURIComponent(match[1]) : '';
        return new Promise((resolve) => impactPreviewResolvers.push({ type, resolve }));
      }
      if (url === '/api/manager/vision-jobs' && options.method === 'POST') {
        visionPostCount += 1;
        return new Promise((resolve) => uploadResolvers.push(resolve));
      }
      if (url.startsWith('/api/manager/vision-status') && (visionStatusFailure || sessionExpired)) {
        return { ok: false, status: sessionExpired ? 401 : 503,
          json: async () => ({ message: sessionExpired ? '需要管理员权限' : '状态读取失败' }) };
      }
      let data = {};
      if (url === '/api/admin/status') data = { is_admin: true, csrf_token: 'csrf' };
      else if (url.startsWith('/api/road-conditions/snap')) data = {
        snap: { u: 1, v: 2, key: 0, road_name: '测试路', dist_m: 2,
          chain_length_m: 100, snap_lng_gcj: 114.36, snap_lat_gcj: 30.53,
          geometry_gcj: [[114.36, 30.53], [114.361, 30.53]] },
        impact_preview: {
          road_name: '测试路', affected_road_segments: 1, affected_length_m: 100,
          active_event_count: 2, event_scope_note: '基于当前有效路况快照',
          route_sample_scope: '仅为所选路段两端的示例路线',
          effects_by_mode: {}, sample_routes: {},
        },
      };
      else if (url.startsWith('/api/manager/vision-status')) data = {
        inference_ready: inferenceReady, max_media_bytes: 1024, notice: inferenceReady ? '已就绪' : '尚未就绪',
      };
      else if (url.startsWith('/api/manager/vision-jobs')) data = { jobs: visionJobs };
      else if (url.startsWith('/api/road-conditions')) data = { conditions: roadEvents };
      return { ok: true, json: async () => ({ data }) };
    },
  };
  vm.runInNewContext(source, context);
  return {
    elements, intervalCallbacks, mapListeners, requested, calls, document,
    setInferenceReady: (value) => { inferenceReady = value; },
    setVisionJobs: (items) => { visionJobs = items; },
    setRoadEvents: (items) => { roadEvents = items; },
    setVisionStatusFailure: (value) => { visionStatusFailure = value; },
    setSessionExpired: (value) => { sessionExpired = value; },
    deferImpactPreviews: () => { deferImpactPreviews = true; },
    resolveImpactPreview: (type, roadName) => {
      const index = impactPreviewResolvers.findIndex((item) => item.type === type);
      if (index < 0) throw new Error('No pending impact preview for ' + type);
      const item = impactPreviewResolvers.splice(index, 1)[0];
      const preview = {
        road_name: roadName, affected_road_segments: 1, affected_length_m: 80,
        event_scope_note: '预览快照', route_sample_scope: '局部示例',
        effects_by_mode: {}, sample_routes: {},
      };
      item.resolve({ ok: true, json: async () => ({ data: {
        snap: { road_name: roadName, chain_length_m: 80, edges: [] },
        impact_preview: preview,
      } }) });
    },
    getVisionPostCount: () => visionPostCount,
    releaseVisionUploads: () => uploadResolvers.splice(0).forEach((resolve) => resolve({
      ok: true, json: async () => ({ data: { job: { job_id: 'job-1' } } }),
    })),
  };
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
}

test('manager refreshes vision readiness and updates submission availability while open', async () => {
  const harness = managerHarness();
  await flush();
  const { elements, intervalCallbacks, mapListeners } = harness;
  assert.equal(intervalCallbacks.length, 1);

  elements.get('media-file').files = [{ type: 'image/png', name: 'campus.png', size: 10 }];
  elements.get('media-file').listeners.change();
  elements.get('pick-anchor').listeners.click();
  mapListeners.click({ latlng: { lng: 114.36, lat: 30.53 } });
  assert.equal(elements.get('submit-vision').disabled, true);

  harness.setInferenceReady(true);
  intervalCallbacks[0]();
  await flush();
  assert.equal(elements.get('submit-vision').disabled, false);

  harness.setVisionStatusFailure(true);
  intervalCallbacks[0]();
  await flush();
  assert.equal(elements.get('submit-vision').disabled, true);
  assert.equal(elements.get('vision-status').classList.contains('is-ready'), false);

  harness.setVisionStatusFailure(false);
  harness.setInferenceReady(false);
  intervalCallbacks[0]();
  await flush();
  assert.equal(elements.get('submit-vision').disabled, true);
});

test('readiness polling cannot re-enable or duplicate an in-flight media upload', async () => {
  const harness = managerHarness();
  await flush();
  const { elements, intervalCallbacks, mapListeners } = harness;
  harness.setInferenceReady(true);
  intervalCallbacks[0]();
  await flush();

  elements.get('media-file').files = [{ type: 'image/png', name: 'campus.png', size: 10 }];
  elements.get('media-file').listeners.change();
  elements.get('pick-anchor').listeners.click();
  mapListeners.click({ latlng: { lng: 114.36, lat: 30.53 } });
  assert.equal(elements.get('submit-vision').disabled, false);

  const upload = elements.get('vision-form').listeners.submit({ preventDefault() {} });
  await flush();
  try {
    intervalCallbacks[0]();
    await flush();
    assert.equal(elements.get('submit-vision').disabled, true);
    await elements.get('vision-form').listeners.submit({ preventDefault() {} });
    assert.equal(harness.getVisionPostCount(), 1);
  } finally {
    harness.releaseVisionUploads();
    await upload;
  }
});

test('manager impact preview discloses its active-condition snapshot and local scope', async () => {
  const harness = managerHarness();
  await flush();
  const { elements, mapListeners } = harness;
  elements.get('event-type').value = 'closure';
  elements.get('pick-road').listeners.click();
  await mapListeners.click({ latlng: { lng: 114.36, lat: 30.53 } });

  const preview = elements.get('impact-preview');
  assert.equal(preview.hidden, false);
  const note = preview.children.find((child) => child.tagName === 'small');
  const noteText = note ? note.textContent : preview.children.at(-1).textContent;
  assert.match(noteText, /基于当前有效路况快照/);
  assert.match(noteText, /2 条当前生效路况/);
  assert.match(noteText, /所选路段两端/);
});

test('a late event-type preview cannot overwrite the newer selection', async () => {
  const harness = managerHarness();
  await flush();
  const { elements, mapListeners } = harness;
  elements.get('pick-road').listeners.click();
  await mapListeners.click({ latlng: { lng: 114.36, lat: 30.53 } });

  harness.deferImpactPreviews();
  elements.get('event-type').value = 'accident';
  const older = elements.get('event-type').listeners.change();
  elements.get('event-type').value = 'closure';
  const newer = elements.get('event-type').listeners.change();
  harness.resolveImpactPreview('closure', '新选择的道路');
  await newer;
  harness.resolveImpactPreview('accident', '旧选择的道路');
  await older;

  const previewText = elements.get('impact-preview').children.map((child) => child.textContent).join(' ');
  assert.match(previewText, /新选择的道路/);
  assert.doesNotMatch(previewText, /旧选择的道路/);
});

test('a pending preview safely settles after the selected road is cleared', async () => {
  const harness = managerHarness();
  await flush();
  const { elements, mapListeners } = harness;
  elements.get('pick-road').listeners.click();
  await mapListeners.click({ latlng: { lng: 114.36, lat: 30.53 } });

  harness.deferImpactPreviews();
  elements.get('event-type').value = 'closure';
  const pending = elements.get('event-type').listeners.change();
  const submit = elements.get('condition-form').listeners.submit({ preventDefault() {} });
  await submit;
  harness.resolveImpactPreview('closure', '已清除路段');

  await assert.doesNotReject(pending);
  assert.equal(elements.get('impact-preview').hidden, true);
});

test('completed image jobs show vehicle counts and safely render detector boxes', async () => {
  const harness = managerHarness();
  await flush();
  harness.setVisionJobs([{
    job_id: 'vision-1', status: 'completed', media_kind: 'image', media_url: '/private/media',
    original_name: 'drone.png', result: {
      media: { width: 640, height: 480 },
      model: { id: 'visdrone-rtdetrv4-s', version: 'abc123' },
      metrics: { peak_vehicle_count: 2, peak_class_counts: { car: 2 }, frames_analyzed: 1 },
      candidates: [],
      preview_detections: [
        { label: 'car', confidence: 0.91, box: [10, 20, 100, 90] },
        { label: '<script>alert(1)</script>', confidence: 0.4, box: [120, 40, 200, 110] },
      ],
    },
  }]);
  const { elements, intervalCallbacks } = harness;
  intervalCallbacks[0]();
  await flush();
  const card = elements.get('vision-jobs').children[0];
  const stage = card.children.find((node) => node.className === 'vision-image-stage');
  const svg = stage.children.find((node) => node.tagName === 'svg');
  assert.equal(svg.attributes.viewBox, '0 0 640 480');
  assert.equal(svg.children.filter((node) => node.tagName === 'rect').length, 1);
  assert.equal(svg.children.find((node) => node.tagName === 'text').textContent, 'car 91%');
  assert.doesNotMatch(svg.children.map((node) => node.textContent).join(' '), /script|alert/);
  const summary = card.children.find((node) => node.className === 'vision-metrics');
  const summaryText = summary.children.map((node) => node.textContent).join(' ');
  assert.match(summaryText, /检出车辆 2/);
  assert.match(summaryText, /本版不支持事故识别/);
});

function descendants(node) {
  return [node, ...(node.children || []).flatMap(descendants)];
}

test('confirmed visual evidence transfers to an explicitly verified road event', async () => {
  const harness = managerHarness();
  await flush();
  harness.setVisionJobs([{
    job_id: 'a6f6c6b1-d237-43fa-8ea2-b01ae48a3e8e',
    status: 'needs_review', review_status: 'confirmed', media_kind: 'image',
    original_name: '巡查影像.png', anchor_gcj: { lng: 114.36, lat: 30.53 },
    result: { candidates: [{ kind: 'vehicle_cluster_review', confidence: 0.8 }] },
  }]);
  harness.intervalCallbacks[0]();
  await flush();
  const { elements, mapListeners, calls } = harness;
  const card = elements.get('vision-jobs').children[0];
  const transfer = descendants(card).find((node) => node.tagName === 'button' && node.textContent === '转入道路事件');
  assert.ok(transfer);
  transfer.listeners.click();

  assert.equal(elements.get('vision-source-banner').hidden, false);
  assert.equal(elements.get('publish-event').disabled, true);
  assert.equal(elements.get('event-type').value, '');
  elements.get('event-type').value = 'event';
  elements.get('event-type').listeners.change();
  await mapListeners.click({ latlng: { lng: 114.36, lat: 30.53 } });
  assert.equal(elements.get('publish-event').disabled, true);

  elements.get('field-confirmation').value = '已联系现场负责人核实，该路段确有人流聚集。';
  elements.get('field-confirmation').listeners.input();
  assert.equal(elements.get('publish-event').disabled, false);
  await elements.get('condition-form').listeners.submit({ preventDefault() {} });
  const posted = calls.find((call) => call.url === '/api/road-conditions' && call.options.method === 'POST');
  assert.ok(posted);
  const body = JSON.parse(posted.options.body);
  assert.equal(body.source_vision_job_id, 'a6f6c6b1-d237-43fa-8ea2-b01ae48a3e8e');
  assert.equal(body.field_confirmation, '已联系现场负责人核实，该路段确有人流聚集。');
  assert.equal(elements.get('vision-source-banner').hidden, true);
});

test('revoked road events stay in manager history without map impact or destructive delete', async () => {
  const harness = managerHarness();
  await flush();
  harness.setRoadEvents([{
    id: 'event-1', name: '已撤销事件', type: 'event', status: 'revoked',
    source: { kind: 'vision_job', job_id: 'a6f6c6b1-d237-43fa-8ea2-b01ae48a3e8e', field_confirmation: '现场核查' },
    audit: [{ action: 'revoked', actor: 'web', at: 1791000000 }],
    edge: { road_name: '测试路', geometry_gcj: [[114.36, 30.53], [114.361, 30.53]] },
  }]);
  harness.elements.get('refresh-events').listeners.click();
  await flush();
  const row = harness.elements.get('event-list').children[0];
  const text = descendants(row).map((node) => node.textContent || '').join(' ');
  assert.match(text, /已撤销/);
  assert.match(text, /影像来源/);
  assert.doesNotMatch(text, /删除/);
});

test('expired manager session returns to login while polling', async () => {
  const harness = managerHarness();
  await flush();
  assert.equal(harness.elements.get('workspace').hidden, false);
  harness.setSessionExpired(true);
  harness.intervalCallbacks[0]();
  await flush();
  assert.equal(harness.elements.get('workspace').hidden, true);
  assert.equal(harness.elements.get('login-panel').hidden, false);
});

test('review of multiple visual candidates records the selected item and human note', async () => {
  const harness = managerHarness();
  await flush();
  harness.setVisionJobs([{
    job_id: 'job-multi', status: 'needs_review', media_kind: 'video', original_name: '巡查视频.mp4',
    result: { candidates: [
      { kind: 'possible_congestion', confidence: 0.72 },
      { kind: 'possible_accident', confidence: 0.81 },
    ] },
  }]);
  harness.intervalCallbacks[0]();
  await flush();
  const card = harness.elements.get('vision-jobs').children[0];
  const note = descendants(card).find((node) => node.tagName === 'textarea');
  const confirms = descendants(card).filter((node) => node.tagName === 'button' && node.textContent === '确认此候选');
  assert.ok(note);
  assert.equal(confirms.length, 2);
  confirms[1].listeners.click();
  assert.equal(harness.calls.filter((call) => call.url.endsWith('/review')).length, 0);
  note.value = '逐帧核对发现明确的事故迹象，需现场继续确认。';
  confirms[1].listeners.click();
  await flush();
  const posted = harness.calls.find((call) => call.url.endsWith('/review'));
  assert.ok(posted);
  assert.equal(JSON.parse(posted.options.body).candidate_index, 1);
  assert.equal(JSON.parse(posted.options.body).note, note.value);
});

test('periodic updates preserve an unfinished vision review note and its focus', async () => {
  const harness = managerHarness();
  await flush();
  harness.setVisionJobs([{
    job_id: 'job-draft', status: 'needs_review', media_kind: 'image', original_name: '巡查图片.png',
    result: { candidates: [{ kind: 'vehicle_cluster_review', confidence: 0.7 }] },
  }]);
  harness.intervalCallbacks[0]();
  await flush();
  const root = harness.elements.get('vision-jobs');
  const note = descendants(root).find((node) => node.tagName === 'textarea');
  note.value = '刚才看到很多车辆，正在核对具体的路段';
  note.listeners.input();
  harness.document.activeElement = note;
  harness.intervalCallbacks[0]();
  await flush();
  assert.equal(descendants(root).find((node) => node.tagName === 'textarea'), note);
  assert.equal(note.value, '刚才看到很多车辆，正在核对具体的路段');

  harness.document.activeElement = null;
  harness.intervalCallbacks[0]();
  await flush();
  assert.equal(descendants(root).find((node) => node.tagName === 'textarea').value,
    '刚才看到很多车辆，正在核对具体的路段');
});
