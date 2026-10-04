(function () {
  'use strict';

  var legacyDismissedVisionStorageKey = 'managerDismissedVisionJobs';
  var state = { csrf: '', map: null, eventLayers: null, preview: null, pickFeedbackLayers: [], pickRequestId: 0, picked: null, sourceJobId: null, reviewDrafts: {}, legacyDismissedVisionJobs: loadLegacyDismissedVisionJobs(), deletedVisionJobs: new Set(), visionDeleteBusy: new Set(), legacyDeleteBusy: false, picking: false, pickPurpose: null, maxBytes: 24 * 1024 * 1024, inferenceReady: false, visionStatusBusy: false, visionUploadBusy: false, mediaPreviewUrl: '', mediaInvalidReason: '', impactPreviewRequestId: 0, poll: null };
  var byId = function (id) { return document.getElementById(id); };
  var message = function (id, text, good) {
    var node = byId(id);
    if (!node) return;
    node.textContent = text || '';
    node.style.color = good ? '#397653' : '';
  };

  function loadLegacyDismissedVisionJobs() {
    try {
      var stored = JSON.parse(window.sessionStorage.getItem(legacyDismissedVisionStorageKey) || '[]');
      return new Set(Array.isArray(stored) ? stored.filter(function (id) { return typeof id === 'string'; }) : []);
    } catch (_) {
      return new Set();
    }
  }

  function persistLegacyDismissedVisionJobs() {
    try {
      if (state.legacyDismissedVisionJobs.size) {
        window.sessionStorage.setItem(legacyDismissedVisionStorageKey, JSON.stringify(Array.from(state.legacyDismissedVisionJobs)));
      } else {
        window.sessionStorage.removeItem(legacyDismissedVisionStorageKey);
      }
    } catch (_) {}
  }

  function isVisionJobHidden(jobId) {
    return state.deletedVisionJobs.has(jobId) || state.legacyDismissedVisionJobs.has(jobId);
  }

  async function migrateLegacyDismissedVisionJobs() {
    if (state.legacyDeleteBusy || !state.legacyDismissedVisionJobs.size) return;
    state.legacyDeleteBusy = true;
    var legacyIds = Array.from(state.legacyDismissedVisionJobs);
    for (var i = 0; i < legacyIds.length; i += 1) {
      var jobId = legacyIds[i];
      try {
        await request('/api/manager/vision-jobs/' + encodeURIComponent(jobId), 'DELETE');
        state.legacyDismissedVisionJobs.delete(jobId);
      } catch (_) {
        // Keep the old local hide marker and retry on the next manager poll.
      }
    }
    persistLegacyDismissedVisionJobs();
    state.legacyDeleteBusy = false;
  }

  function retryVisionSelection() {
    var uploadCard = byId('vision-upload-card');
    if (uploadCard && uploadCard.scrollIntoView) uploadCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
    byId('media-file').click();
  }

  async function deleteVisionJob(jobId) {
    if (state.visionDeleteBusy.has(jobId)) return;
    if (!window.confirm('永久删除这条影像记录及上传文件？删除后无法恢复。')) return;
    state.visionDeleteBusy.add(jobId);
    state.deletedVisionJobs.add(jobId);
    refreshVisionJobs(true);
    try {
      await request('/api/manager/vision-jobs/' + encodeURIComponent(jobId), 'DELETE');
      state.legacyDismissedVisionJobs.delete(jobId);
      persistLegacyDismissedVisionJobs();
      refreshVisionJobs(true);
    } catch (error) {
      state.deletedVisionJobs.delete(jobId);
      window.alert(error.message || '影像记录删除失败，请重试。');
      refreshVisionJobs(true);
    } finally {
      state.visionDeleteBusy.delete(jobId);
    }
  }

  function blockedModeInputs() {
    return [byId('block-walk'), byId('block-bike'), byId('block-drive')];
  }

  function resetBlockedModes() {
    blockedModeInputs().forEach(function (input) { input.checked = true; });
  }

  function selectedBlockedModes() {
    return blockedModeInputs().filter(function (input) { return input.checked; })
      .map(function (input) { return input.id.replace('block-', ''); });
  }

  function blockedModesQuery(modes) {
    return (modes || selectedBlockedModes()).map(function (mode) {
      return '&blocked_modes=' + encodeURIComponent(mode);
    }).join('');
  }

  function sameModes(left, right) {
    return left.length === right.length && left.every(function (mode, index) { return mode === right[index]; });
  }

  function isVideoFile(file) {
    return !!file && (/^video\//i.test(file.type || '') || /\.(mp4|mov|avi|webm)$/i.test(file.name || ''));
  }

  function isImageFile(file) {
    return !!file && (/^image\/(jpeg|png|webp)$/i.test(file.type || '') || /\.(jpe?g|png|webp)$/i.test(file.name || ''));
  }

  function formatBytes(value) {
    if (value < 1024) return value + ' B';
    if (value < 1024 * 1024) return (value / 1024).toFixed(1) + ' KB';
    return (value / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function clearMediaPreview() {
    if (state.mediaPreviewUrl) URL.revokeObjectURL(state.mediaPreviewUrl);
    state.mediaPreviewUrl = '';
    var preview = byId('selected-media-preview');
    preview.removeAttribute('src');
    preview.hidden = true;
  }

  function clearSelectedMedia() {
    clearMediaPreview();
    state.mediaInvalidReason = '';
    byId('media-file').value = '';
    byId('selected-media').hidden = true;
    byId('selected-media-name').textContent = '';
    byId('selected-media-meta').textContent = '';
  }

  function renderSelectedMedia(file) {
    clearMediaPreview();
    state.mediaInvalidReason = '';
    if (!file) {
      byId('selected-media').hidden = true;
      return;
    }
    var mediaKind = isImageFile(file) ? '图片' : isVideoFile(file) ? '视频' : '不支持的文件';
    if (mediaKind === '不支持的文件') {
      state.mediaInvalidReason = '文件格式不受支持，请选择 JPG、PNG、WebP 图片或 MP4、MOV、AVI、WebM 视频。';
    } else if (file.size > state.maxBytes) {
      state.mediaInvalidReason = '文件大小为 ' + formatBytes(file.size) + '，超过当前 ' + formatBytes(state.maxBytes) + ' 上限。';
    }
    byId('selected-media-name').textContent = file.name || '未命名影像';
    byId('selected-media-meta').textContent = formatBytes(file.size || 0) + ' · ' + mediaKind;
    byId('selected-media').hidden = false;
    if (isImageFile(file)) {
      var preview = byId('selected-media-preview');
      state.mediaPreviewUrl = URL.createObjectURL(file);
      preview.src = state.mediaPreviewUrl;
      preview.hidden = false;
      preview.onerror = function () {
        preview.hidden = true;
        if (!state.mediaInvalidReason) {
          message('vision-message', '图片已选择，但无法生成本地预览；仍可尝试识别。');
        }
      };
    }
  }

  function updateVisionGuidance() {
    var file = byId('media-file').files[0];
    if (!state.inferenceReady) {
      message('vision-message', file ? '视觉服务暂未就绪，已保留所选文件，请等待服务恢复。' : '视觉服务暂未就绪，请等待服务恢复。');
    } else if (!file) {
      message('vision-message', '先选择一张图片或一段短视频。');
    } else if (state.mediaInvalidReason) {
      message('vision-message', state.mediaInvalidReason);
    } else {
      message('vision-message', '影像已准备好，可以开始识别；如有有效候选，将在复核后选择具体道路。', true);
    }
  }

  function canSubmitVision() {
    return !!(state.inferenceReady && !state.visionUploadBusy && !state.mediaInvalidReason && byId('media-file').files.length);
  }

  function updateVisionSubmitState() {
    byId('submit-vision').disabled = !canSubmitVision();
  }

  function updatePublishState() {
    var hasFieldEvidence = !state.sourceJobId || byId('field-confirmation').value.trim().length >= 8;
    byId('publish-event').disabled = !state.picked || !state.picked.confirmed || state.picking ||
      !byId('event-type').value || !selectedBlockedModes().length || !hasFieldEvidence;
  }

  function clearPickedRoad() {
    state.pickRequestId += 1;
    setPicking(false);
    state.picked = null;
    state.impactPreviewRequestId += 1;
    if (state.preview) state.map.removeLayer(state.preview);
    state.preview = null;
    state.pickFeedbackLayers.forEach(function (layer) { state.map.removeLayer(layer); });
    state.pickFeedbackLayers = [];
    byId('selected-road').textContent = '尚未选择道路';
    byId('selected-road').classList.remove('is-set');
    byId('confirm-picked-road').hidden = true;
    byId('clear-picked-road').hidden = true;
    renderImpactPreview(null);
    updatePublishState();
  }

  function clearVisionSource() {
    state.sourceJobId = null;
    byId('vision-source-banner').hidden = true;
    byId('field-confirmation-wrap').hidden = true;
    byId('field-confirmation').required = false;
    byId('field-confirmation').value = '';
    updatePublishState();
  }

  function prepareVisionEvent(job) {
    if (job.review_status !== 'confirmed') return;
    clearPickedRoad();
    state.sourceJobId = job.job_id;
    byId('vision-source-banner').hidden = false;
    byId('vision-source-label').textContent = '已复核影像：' + (job.original_name || job.job_id) + '。请重新选择具体道路、事件类型，并填写现场核实依据。';
    byId('field-confirmation-wrap').hidden = false;
    byId('field-confirmation').required = true;
    byId('event-type').value = '';
    byId('event-name').value = '';
    byId('event-description').value = '';
    byId('start-time').value = '';
    byId('end-time').value = '';
    byId('field-confirmation').value = '';
    resetBlockedModes();
    if (job.anchor_gcj) state.map.setView([job.anchor_gcj.lat, job.anchor_gcj.lng], 17);
    setPicking(true, 'road');
    updatePublishState();
    if (byId('road-workspace') && byId('road-workspace').scrollIntoView) {
      byId('road-workspace').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  async function request(path, method, body) {
    var headers = {};
    if (state.csrf && method !== 'GET') headers['X-CSRF-Token'] = state.csrf;
    if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
    var response = await fetch(path, {
      method: method || 'GET', headers: headers,
      body: body === undefined ? undefined : (body instanceof FormData ? body : JSON.stringify(body)),
      credentials: 'same-origin',
    });
    var payload = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      if (response.status === 401 && path !== '/api/admin/login') {
        setLoggedOut('管理登录已过期，请重新登录。');
      }
      throw new Error(payload.message || '请求失败，请重试');
    }
    return payload.data || payload;
  }

  function setLoggedIn(data) {
    state.csrf = data.csrf_token || state.csrf;
    byId('login-panel').hidden = true;
    byId('workspace').hidden = false;
    byId('logout').hidden = false;
    if (!state.map) initMap();
    setTimeout(function () { state.map.invalidateSize(); }, 80);
    refreshEvents();
    refreshVisionStatus();
    migrateLegacyDismissedVisionJobs().then(function () { refreshVisionJobs(true); });
    if (!state.poll) state.poll = window.setInterval(function () {
      if (!document.hidden) {
        refreshVisionStatus();
        migrateLegacyDismissedVisionJobs();
        refreshVisionJobs();
      }
    }, 5000);
  }

  function setLoggedOut(notice) {
    if (state.poll) { window.clearInterval(state.poll); state.poll = null; }
    state.csrf = '';
    state.reviewDrafts = {};
    clearVisionSource();
    if (state.map) clearPickedRoad();
    byId('event-type').value = '';
    byId('event-name').value = '';
    byId('event-description').value = '';
    byId('start-time').value = '';
    byId('end-time').value = '';
    clearSelectedMedia();
    byId('workspace').hidden = true;
    byId('logout').hidden = true;
    byId('login-panel').hidden = false;
    byId('password').value = '';
    message('login-message', notice || '已退出管理台。', true);
  }

  function initMap() {
    var cfg = window.WHU_WALKER_CONFIG || {};
    var center = cfg.mapCenter || [114.363, 30.5365];
    state.map = L.map('manager-map', { zoomControl: true, preferCanvas: true })
      .setView([center[1], center[0]], cfg.mapZoom || 15);
    L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}', {
      subdomains: ['1', '2', '3', '4'], maxZoom: 18,
      attribution: '© <a href="https://ditu.amap.com/" target="_blank" rel="noopener">高德地图</a>',
    }).addTo(state.map);
    state.eventLayers = L.layerGroup().addTo(state.map);
    state.map.on('click', handleMapClick);
  }

  function setPicking(active, purpose) {
    var nextPurpose = active ? (purpose || state.pickPurpose || 'road') : null;
    if (!active || nextPurpose !== state.pickPurpose) state.pickRequestId += 1;
    state.picking = active;
    state.pickPurpose = nextPurpose;
    byId('manager-map').classList.toggle('manager-map-picking', active);
    byId('pick-road').textContent = active && state.pickPurpose === 'road' ? '取消选路' : '⌖ 选取路段';
    byId('pick-state').textContent = active
      ? '请在地图道路上点选，系统会吸附到正式路网'
      : '选择路段后再填写事件';
    updatePublishState();
  }

  function renderImpactPreview(preview) {
    var box = byId('impact-preview');
    box.textContent = '';
    if (!preview) { box.hidden = true; return; }
    box.hidden = false;
    var heading = document.createElement('strong');
    heading.textContent = '发布影响预览';
    box.appendChild(heading);
    var extent = document.createElement('p');
    extent.textContent = (preview.road_name || '所选道路') + ' · ' +
      (preview.affected_road_segments || 0) + ' 个路段 · 约 ' +
      (preview.affected_length_m || 0) + ' 米';
    box.appendChild(extent);
    var modes = document.createElement('p');
    var labels = { walk: '步行', bike: '骑行', drive: '驾车' };
    modes.textContent = Object.keys(preview.effects_by_mode || {}).map(function (mode) {
      var effect = preview.effects_by_mode[mode] || {};
      return labels[mode] + '：' + (effect.label || '影响未知');
    }).join('；');
    box.appendChild(modes);
    var examples = Object.keys(preview.sample_routes || {}).map(function (mode) {
      var sample = preview.sample_routes[mode] || {};
      if (sample.status !== 'available') return null;
      return labels[mode] + '示例：' + sample.before_distance_m + ' 米 → ' +
        sample.after_distance_m + ' 米（' + (sample.detour_m >= 0 ? '+' : '') + sample.detour_m + ' 米）';
    }).filter(Boolean);
    if (examples.length) {
      var routeSamples = document.createElement('p');
      routeSamples.textContent = examples.join('；');
      box.appendChild(routeSamples);
    }
    var note = document.createElement('small');
    note.textContent = [
      preview.event_scope_note,
      typeof preview.active_event_count === 'number' ? '预览使用 ' + preview.active_event_count + ' 条当前生效路况' : '',
      preview.route_sample_scope || '示例路线只反映所选路段附近的局部影响，不代表全校总影响。',
    ].filter(Boolean).join('；');
    box.appendChild(note);
  }

  async function refreshImpactPreview() {
    if (!state.picked) return;
    var picked = state.picked;
    var type = byId('event-type').value;
    var blockedModes = selectedBlockedModes();
    var requestId = ++state.impactPreviewRequestId;
    var query = '?lng=' + encodeURIComponent(picked.lng) +
      '&lat=' + encodeURIComponent(picked.lat) +
      '&type=' + encodeURIComponent(type) + blockedModesQuery(blockedModes);
    try {
      var data = await request('/api/road-conditions/snap' + query, 'GET');
      if (requestId !== state.impactPreviewRequestId || state.picked !== picked ||
          byId('event-type').value !== type || !sameModes(blockedModes, selectedBlockedModes())) return;
      picked.snap = data.snap;
      picked.impactPreview = data.impact_preview;
      renderImpactPreview(data.impact_preview);
      updatePublishState();
    } catch (error) {
      if (requestId !== state.impactPreviewRequestId || state.picked !== picked ||
          byId('event-type').value !== type || !sameModes(blockedModes, selectedBlockedModes())) return;
      renderImpactPreview({
        road_name: picked.snap && picked.snap.road_name,
        affected_length_m: picked.snap && picked.snap.chain_length_m,
        affected_road_segments: picked.snap && picked.snap.edges && picked.snap.edges.length,
        effects_by_mode: {}, sample_routes: {},
        route_sample_scope: error.message || '路线影响预览暂时不可用，请核对所选路段和事件类型。',
      });
    }
  }

  async function handleMapClick(event) {
    if (!state.picking) return;
    var pickRequestId = ++state.pickRequestId;
    var lng = event.latlng.lng;
    var lat = event.latlng.lat;
    message('form-message', '正在将点位匹配到校园路网…');
    var requestedEventType = byId('event-type').value;
    var requestedBlockedModes = selectedBlockedModes();
    try {
      var snapData = await request('/api/road-conditions/snap?lng=' + encodeURIComponent(lng) +
        '&lat=' + encodeURIComponent(lat) + '&type=' + encodeURIComponent(requestedEventType) +
        blockedModesQuery(requestedBlockedModes), 'GET');
      if (pickRequestId !== state.pickRequestId || !state.picking || state.pickPurpose !== 'road') return;
      var snap = snapData.snap;
      if (!snap) throw new Error('这个位置没有匹配到校园道路，请放大后重选。');
      var currentEventType = byId('event-type').value;
      var previewMatchesCurrentType = currentEventType === requestedEventType &&
        sameModes(requestedBlockedModes, selectedBlockedModes());
      state.picked = { lng: lng, lat: lat, snap: snap, impactPreview: previewMatchesCurrentType ? snapData.impact_preview : null, confirmed: false };
      renderImpactPreview(previewMatchesCurrentType ? snapData.impact_preview : null);
      if (state.preview) state.map.removeLayer(state.preview);
      state.pickFeedbackLayers.forEach(function (layer) { state.map.removeLayer(layer); });
      state.pickFeedbackLayers = [];
      var geometry = (snap.geometry_gcj || []).map(function (point) { return [point[1], point[0]]; });
      if (geometry.length >= 2) {
        state.preview = L.polyline(geometry, { color: '#d48943', weight: 8, opacity: .85, lineCap: 'round' }).addTo(state.map);
      } else {
        state.preview = L.circleMarker([snap.snap_lat_gcj, snap.snap_lng_gcj], {
          radius: 8, color: '#fff', weight: 2, fillColor: '#d48943', fillOpacity: 1,
        }).addTo(state.map);
      }
      var snapLng = Number(snap.snap_lng_gcj);
      var snapLat = Number(snap.snap_lat_gcj);
      if (Number.isFinite(snapLng) && Number.isFinite(snapLat)) {
        if ((snap.dist_m || 0) > .5) {
          state.pickFeedbackLayers.push(L.polyline([[lat, lng], [snapLat, snapLng]], {
            color: '#365f50', weight: 2, opacity: .9, dashArray: '4 5', interactive: false,
          }).addTo(state.map));
        }
        state.pickFeedbackLayers.push(L.circleMarker([lat, lng], {
          radius: 5, color: '#fff', weight: 2, fillColor: '#365f50', fillOpacity: 1,
        }).addTo(state.map));
        state.pickFeedbackLayers.push(L.circleMarker([snapLat, snapLng], {
          radius: 6, color: '#fff', weight: 2, fillColor: '#d48943', fillOpacity: 1,
        }).addTo(state.map));
      }
      var road = snap.road_name || '未命名道路';
      var length = snap.chain_length_m ? Math.round(snap.chain_length_m) + ' 米路段' : '已吸附到一条道路';
      byId('selected-road').textContent = road + ' · 吸附距离 ' + Number(snap.dist_m || 0).toFixed(1) + ' 米 · ' + length;
      byId('selected-road').classList.add('is-set');
      byId('confirm-picked-road').hidden = false;
      byId('clear-picked-road').hidden = false;
      updatePublishState();
      message('form-message', '请核对地图高亮路段和吸附距离，确认后才能发布。');
      setPicking(false);
      if (!previewMatchesCurrentType && currentEventType) refreshImpactPreview();
    } catch (error) {
      if (pickRequestId !== state.pickRequestId) return;
      message('form-message', error.message || '路段匹配失败，请重试。');
    }
  }

  function eventStatus(item) {
    return item.status || 'active';
  }

  function renderEventRow(item) {
    var row = document.createElement('article');
    row.className = 'event-row';
    var status = document.createElement('span');
    status.className = 'event-status ' + eventStatus(item);
    var dot = document.createElement('i'); status.appendChild(dot);
    status.appendChild(document.createTextNode({ active: '生效中', scheduled: '待生效', expired: '已结束', revoked: '已撤销' }[eventStatus(item)] || '已结束'));
    var name = document.createElement('div');
    var title = document.createElement('div'); title.className = 'event-name'; title.textContent = item.name || '未命名事件';
    var description = document.createElement('div'); description.className = 'event-sub'; description.textContent = item.description || '无补充说明';
    name.append(title, description);
    if (item.source && item.source.kind === 'vision_job') {
      var source = document.createElement('div'); source.className = 'event-sub';
      source.textContent = '影像来源 ' + String(item.source.job_id || '').slice(0, 8) + ' · ' + (item.source.field_confirmation || '未记录核实依据');
      name.appendChild(source);
    }
    if (item.audit && item.audit.length) {
      var last = item.audit[item.audit.length - 1];
      var audit = document.createElement('div'); audit.className = 'event-sub';
      audit.textContent = '最近操作：' + ({ created: '发布', updated: '修改', ended: '结束', revoked: '撤销' }[last.action] || last.action) +
        ' · ' + (last.actor || '未知') + ' · ' + (last.at ? new Date(Number(last.at) * 1000).toLocaleString('zh-CN', { hour12: false }) : '时间未知');
      name.appendChild(audit);
    }
    var type = document.createElement('div'); type.className = 'event-type'; type.textContent = item.type_label || item.type || '管制';
    var road = document.createElement('div'); road.className = 'event-meta';
    var edge = item.edge || {};
    road.textContent = (edge.road_name || '未命名道路') + (edge.chain_length_m ? ' · ' + Math.round(edge.chain_length_m) + ' 米' : '');
    var times = document.createElement('div'); times.className = 'event-meta';
    var end = item.end_time ? new Date(Number(item.end_time) * 1000).toLocaleString('zh-CN', { hour12: false }) : '持续有效';
    times.textContent = eventStatus(item) === 'revoked' ? '撤销 ' + new Date(Number(item.revoked_at || item.updated_at) * 1000).toLocaleString('zh-CN', { hour12: false }) :
      (eventStatus(item) === 'scheduled' ? '计划开始 ' + new Date(Number(item.start_time) * 1000).toLocaleString('zh-CN', { hour12: false }) : '结束 ' + end);
    var actions = document.createElement('div'); actions.className = 'row-actions';
    if (eventStatus(item) === 'active') {
      var endButton = document.createElement('button'); endButton.type = 'button'; endButton.textContent = '结束事件';
      endButton.addEventListener('click', function () { endEvent(item.id); }); actions.appendChild(endButton);
    }
    var deleteButton = document.createElement('button'); deleteButton.type = 'button'; deleteButton.textContent = '永久删除';
    deleteButton.addEventListener('click', function () { deleteEvent(item.id); }); actions.appendChild(deleteButton);
    row.append(status, name, type, road, times, actions);
    return row;
  }

  async function refreshEvents() {
    var list = byId('event-list');
    list.replaceChildren();
    var loading = document.createElement('div'); loading.className = 'empty-state'; loading.textContent = '正在读取事件记录…'; list.appendChild(loading);
    try {
      var result = await request('/api/road-conditions?all=1', 'GET');
      var items = result.conditions || [];
      byId('active-count').textContent = items.filter(function (item) { return item.status === 'active'; }).length;
      state.eventLayers.clearLayers();
      items.forEach(function (item) {
        if (item.status === 'revoked' || item.status === 'expired') return;
        var edge = item.edge || {};
        var geometry = (edge.geometry_gcj || []).map(function (point) { return [point[1], point[0]]; });
        if (geometry.length >= 2) {
          L.polyline(geometry, { color: item.status === 'active' ? '#d48943' : '#9da79c', weight: 5, opacity: .76 }).addTo(state.eventLayers);
        } else if (edge.snap && edge.snap.lat && edge.snap.lng) {
          L.circleMarker([edge.snap.lat, edge.snap.lng], { radius: 6, color: '#fff', weight: 2, fillColor: '#d48943', fillOpacity: .95 }).addTo(state.eventLayers);
        }
      });
      list.replaceChildren();
      if (!items.length) {
        var empty = document.createElement('div'); empty.className = 'empty-state'; empty.textContent = '目前没有路况记录。可在上方地图选路后发布第一条事件。'; list.appendChild(empty);
      } else {
        items.forEach(function (item) { list.appendChild(renderEventRow(item)); });
      }
    } catch (error) {
      list.replaceChildren();
      var failed = document.createElement('div'); failed.className = 'empty-state'; failed.textContent = error.message || '事件记录暂时无法读取。'; list.appendChild(failed);
    }
  }

  async function refreshVisionStatus() {
    if (state.visionStatusBusy) return;
    state.visionStatusBusy = true;
    try {
      var data = await request('/api/manager/vision-status', 'GET');
      byId('vision-status').textContent = data.notice;
      state.maxBytes = data.max_media_bytes || state.maxBytes;
      state.inferenceReady = !!data.inference_ready;
      updateVisionSubmitState();
      updateVisionGuidance();
      byId('vision-status').classList.toggle('is-ready', !!data.inference_ready);
    } catch (error) {
      state.inferenceReady = false;
      updateVisionSubmitState();
      updateVisionGuidance();
      byId('vision-status').classList.toggle('is-ready', false);
      byId('vision-status').textContent = error.message || '视觉任务状态暂不可用。';
    } finally { state.visionStatusBusy = false; }
  }

  function candidateName(kind) {
    return {
      possible_congestion: '可能存在车辆排队/低速聚集',
      possible_accident: '疑似事故类别目标',
      vehicle_cluster_review: '车辆密集观察',
    }[kind] || '影像候选';
  }

  function appendVisionPreview(card, job, result) {
    if (job.media_kind === 'video') {
      var video = document.createElement('video');
      video.controls = true; video.preload = 'metadata'; video.className = 'vision-preview'; video.src = job.media_url;
      card.appendChild(video);
      return;
    }
    var imageInfo = result.media || {};
    var width = Number(imageInfo.width), height = Number(imageInfo.height);
    var detections = Array.isArray(result.preview_detections) ? result.preview_detections.slice(0, 250) : [];
    var stage = document.createElement('div'); stage.className = 'vision-image-stage';
    var image = document.createElement('img');
    image.alt = '管理员上传的校园巡查影像'; image.className = 'vision-preview'; image.src = job.media_url;
    stage.appendChild(image);
    if (width > 0 && height > 0 && width * height <= 12500000 && detections.length) {
      var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
      svg.setAttribute('preserveAspectRatio', 'none');
      svg.setAttribute('aria-label', '车辆检测框');
      var allowedLabels = {
        car: 'car', van: 'van', truck: 'truck', bus: 'bus', motor: 'motor',
        tricycle: 'tricycle', 'awning-tricycle': 'awning-tricycle', bicycle: 'bicycle',
      };
      detections.forEach(function (item) {
        if (!item || !Array.isArray(item.box) || item.box.length !== 4) return;
        var sourceLabel = String(item.label || '').trim().toLowerCase();
        if (!Object.prototype.hasOwnProperty.call(allowedLabels, sourceLabel)) return;
        var values = item.box.map(Number);
        if (!values.every(Number.isFinite)) return;
        var x1 = Math.max(0, Math.min(width, values[0]));
        var y1 = Math.max(0, Math.min(height, values[1]));
        var x2 = Math.max(0, Math.min(width, values[2]));
        var y2 = Math.max(0, Math.min(height, values[3]));
        if (x2 <= x1 || y2 <= y1) return;
        var rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', x1); rect.setAttribute('y', y1);
        rect.setAttribute('width', x2 - x1); rect.setAttribute('height', y2 - y1);
        svg.appendChild(rect);
        var label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', x1); label.setAttribute('y', Math.max(13, y1 - 3));
        var confidence = Number(item.confidence);
        confidence = Number.isFinite(confidence) ? Math.max(0, Math.min(1, confidence)) : 0;
        label.textContent = allowedLabels[sourceLabel] + ' ' + Math.round(confidence * 100) + '%';
        svg.appendChild(label);
      });
      stage.appendChild(svg);
    }
    card.appendChild(stage);
  }

  function appendVisionMetrics(card, result, mediaKind) {
    var metrics = result.metrics || {};
    var summary = document.createElement('div'); summary.className = 'vision-metrics';
    var count = document.createElement('strong');
    count.textContent = mediaKind === 'video'
      ? '每帧平均检出车辆 ' + Number(metrics.mean_vehicle_count || 0)
      : '检出车辆 ' + Number(metrics.peak_vehicle_count || 0);
    summary.appendChild(count);
    if (mediaKind === 'video') {
      var peak = document.createElement('span'); peak.textContent = '抽样帧 ' + Number(metrics.frames_analyzed || 0) + ' · 单帧最多 ' + Number(metrics.peak_vehicle_count || 0);
      summary.appendChild(peak);
      var motion = document.createElement('span');
      motion.textContent = {
        camera_motion_compensated: '镜头运动校正通过',
        operator_declared_stabilized: '按管理员声明使用固定/已稳像视角',
        camera_motion_uncompensated: '镜头运动校正不足，仅供查看车辆数量',
        insufficient_single_frame: '画面数量不足',
      }[metrics.motion_assessment] || '镜头运动状态未知';
      summary.appendChild(motion);
    }
    var counts = metrics.peak_class_counts || {};
    var classes = Object.keys(counts).filter(function (label) { return Number(counts[label]) > 0; }).slice(0, 8);
    if (classes.length) {
      var list = document.createElement('span');
      list.textContent = '类别峰值：' + classes.map(function (label) { return label + ' ' + counts[label]; }).join('、');
      summary.appendChild(list);
    }
    var model = result.model || {};
    var version = document.createElement('small');
    version.textContent = '模型 ' + String(model.id || '未记录') + ' · 版本 ' + String(model.version || '未记录') + ' · 识别框不是地面坐标；本版不支持事故识别。';
    summary.appendChild(version);
    card.appendChild(summary);
  }

  function reviewJob(job, status, candidateIndex, noteField, feedback) {
    var note = noteField.value.trim();
    if (note.length < 8) {
      feedback.textContent = '请先填写至少 8 字的影像复核依据。';
      return;
    }
    feedback.textContent = '正在保存审核…';
    var body = { status: status, note: note };
    if (status === 'confirmed') body.candidate_index = candidateIndex;
    request('/api/manager/vision-jobs/' + encodeURIComponent(job.job_id) + '/review', 'POST', body)
      .then(function () {
        delete state.reviewDrafts[job.job_id];
        return refreshVisionJobs(true);
      })
      .catch(function (error) { feedback.textContent = error.message || '审核保存失败。'; });
  }

  function renderVisionJob(job) {
    var card = document.createElement('article'); card.className = 'vision-job';
    var head = document.createElement('div'); head.className = 'vision-job-head';
    var title = document.createElement('div'); title.className = 'vision-job-title'; title.textContent = job.original_name || '巡查影像';
    var meta = document.createElement('div'); meta.className = 'vision-job-meta';
    var when = job.created_at ? new Date(job.created_at).toLocaleString('zh-CN', { hour12: false }) : '';
    meta.textContent = when + (job.anchor_gcj ? ' · 观察区域已标注' : '')
      + (job.media_kind === 'video' ? (job.camera_stabilized ? ' · 管理员声明固定/已稳像' : ' · 自动检查镜头运动') : '');
    title.appendChild(meta);
    var stateLabel = document.createElement('span'); stateLabel.className = 'vision-job-state ' + job.status;
    stateLabel.textContent = {
      queued: '排队中', running: '分析中', needs_review: job.review_status ? '已复核' : '待复核',
      completed: '完成', failed: '分析失败',
    }[job.status] || job.status;
    head.append(title, stateLabel); card.appendChild(head);
    if (job.status === 'failed' && job.error) {
      var failure = document.createElement('p'); failure.className = 'vision-job-meta';
      failure.textContent = job.error.message || '分析失败'; card.appendChild(failure);
    }
    if (job.review_status) {
      var reviewed = document.createElement('p'); reviewed.className = 'vision-job-meta';
      reviewed.textContent = (job.review_status === 'confirmed' ? '已确认' : '已排除') +
        ' · ' + (job.reviewed_by || '管理者') +
        (job.reviewed_at ? ' · ' + new Date(job.reviewed_at).toLocaleString('zh-CN', { hour12: false }) : '') +
        (job.review_note ? ' · ' + job.review_note : '');
      card.appendChild(reviewed);
    }
    var result = job.result || {};
    if (job.result && (job.status === 'needs_review' || job.status === 'completed' || job.review_status)) {
      appendVisionPreview(card, job, result);
      appendVisionMetrics(card, result, job.media_kind);
      var reviewNote = null;
      var reviewFeedback = null;
      if (job.status === 'needs_review' && !job.review_status && (result.candidates || []).length) {
        var noteLabel = document.createElement('label'); noteLabel.className = 'vision-review-label';
        noteLabel.textContent = '影像复核依据';
        reviewNote = document.createElement('textarea'); reviewNote.maxLength = 1000;
        reviewNote.className = 'vision-review-note';
        reviewNote.value = state.reviewDrafts[job.job_id] || '';
        reviewNote.addEventListener('input', function () {
          state.reviewDrafts[job.job_id] = reviewNote.value;
        });
        reviewNote.placeholder = '说明查看了哪些影像证据，以及确认或排除的理由（至少 8 字）';
        noteLabel.appendChild(reviewNote); card.appendChild(noteLabel);
        reviewFeedback = document.createElement('p'); reviewFeedback.className = 'form-message';
        card.appendChild(reviewFeedback);
      }
      var candidates = document.createElement('div'); candidates.className = 'candidate-list';
      (result.candidates || []).forEach(function (item, index) {
        var candidate = document.createElement('div'); candidate.className = 'candidate-card';
        var titleNode = document.createElement('strong');
        titleNode.textContent = candidateName(item.kind) + ' · 置信度 ' + Math.round((item.confidence || 0) * 100) + '%';
        if (job.review_status === 'confirmed' && job.review_candidate_index === index) {
          titleNode.textContent += ' · 已确认';
        }
        var why = document.createElement('p'); why.textContent = item.reason || '请结合原始影像复核。';
        candidate.append(titleNode, why);
        if (job.status === 'needs_review' && !job.review_status) {
          var actions = document.createElement('div'); actions.className = 'candidate-actions';
          var confirmButton = document.createElement('button'); confirmButton.type = 'button'; confirmButton.textContent = '确认此候选';
          confirmButton.addEventListener('click', function () {
            reviewJob(job, 'confirmed', index, reviewNote, reviewFeedback);
          });
          actions.append(confirmButton); candidate.appendChild(actions);
        }
        candidates.appendChild(candidate);
      });
      if (candidates.children.length) card.appendChild(candidates);
      if (reviewNote) {
        var dismissButton = document.createElement('button'); dismissButton.type = 'button';
        dismissButton.className = 'outline-button dismiss-vision';
        dismissButton.textContent = '排除整条影像任务';
        dismissButton.addEventListener('click', function () {
          reviewJob(job, 'dismissed', null, reviewNote, reviewFeedback);
        });
        card.appendChild(dismissButton);
      }
      if (job.review_status === 'confirmed') {
        var next = document.createElement('p'); next.className = 'vision-job-meta';
        next.textContent = '已确认影像迹象。若需影响导航，请在上方路况表单中手工选定具体道路并发布事件。';
        card.appendChild(next);
        if (Number.isInteger(job.review_candidate_index) || (result.candidates || []).length === 1) {
          var transfer = document.createElement('button'); transfer.type = 'button';
          transfer.className = 'outline-button transfer-button'; transfer.textContent = '转入道路事件';
          transfer.addEventListener('click', function () { prepareVisionEvent(job); });
          card.appendChild(transfer);
        } else {
          var ambiguous = document.createElement('p'); ambiguous.className = 'vision-job-meta';
          ambiguous.textContent = '旧审核未记录具体候选，无法直接作为道路事件来源。';
          card.appendChild(ambiguous);
        }
      }
      if (job.status === 'completed' && !(result.candidates || []).length) {
        var terminal = document.createElement('div'); terminal.className = 'vision-terminal';
        var terminalTitle = document.createElement('strong'); terminalTitle.textContent = '分析已结束';
        var terminalCopy = document.createElement('p');
        terminalCopy.textContent = '未生成可复核的路况候选。车辆数量只代表画面检测结果，不能单独判断拥堵。';
        terminal.append(terminalTitle, terminalCopy); card.appendChild(terminal);
        appendVisionRecoveryActions(card, job);
      }
    }
    if (job.status === 'failed') appendVisionRecoveryActions(card, job);
    appendVisionDeleteAction(card, job);
    return card;
  }

  function appendVisionRecoveryActions(card, job) {
    var actions = document.createElement('div'); actions.className = 'vision-recovery-actions';
    var retry = document.createElement('button'); retry.type = 'button'; retry.textContent = '重新选择影像';
    retry.addEventListener('click', retryVisionSelection);
    actions.appendChild(retry); card.appendChild(actions);
  }

  function appendVisionDeleteAction(card, job) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'outline-button delete-vision';
    button.textContent = '删除影像记录';
    button.disabled = state.visionDeleteBusy.has(job.job_id);
    button.addEventListener('click', function () { deleteVisionJob(job.job_id); });
    card.appendChild(button);
  }

  async function refreshVisionJobs(force) {
    if (!byId('workspace') || byId('workspace').hidden) return;
    if (!force && document.activeElement && document.activeElement.classList &&
        document.activeElement.classList.contains('vision-review-note')) return;
    var root = byId('vision-jobs');
    try {
      var data = await request('/api/manager/vision-jobs?limit=20', 'GET');
      if (!force && document.activeElement && document.activeElement.classList &&
          document.activeElement.classList.contains('vision-review-note')) return;
      var jobs = (data.jobs || []).filter(function (job) { return !isVisionJobHidden(job.job_id); });
      root.replaceChildren();
      if (!jobs.length) {
        var empty = document.createElement('div'); empty.className = 'empty-state';
        empty.textContent = state.legacyDismissedVisionJobs.size
          ? '正在清理旧版标记为已清除的影像记录。'
          : '尚无巡查任务。提交影像后，分析进度和候选都会显示在这里。';
        root.appendChild(empty);
      } else { jobs.forEach(function (job) { root.appendChild(renderVisionJob(job)); }); }
    } catch (error) {
      root.replaceChildren(); var failed = document.createElement('div'); failed.className = 'empty-state';
      failed.textContent = error.message || '视觉任务记录暂时无法读取。'; root.appendChild(failed);
    }
  }

  async function submitVision(event) {
    event.preventDefault();
    if (state.visionUploadBusy) return;
    if (!state.inferenceReady) { message('vision-message', '视觉分析当前尚未就绪，请稍后重试。'); return; }
    var file = byId('media-file').files[0];
    if (!file) { message('vision-message', '请先选择一张图片或一段视频。'); return; }
    if (file.size > state.maxBytes) {
      message('vision-message', '文件过大，当前上限为 ' + Math.round(state.maxBytes / (1024 * 1024)) + ' MB。'); return;
    }
    var form = new FormData(); form.append('media', file);
    form.append('camera_stabilized', String(isVideoFile(file) && byId('camera-stabilized').checked));
    var button = byId('submit-vision');
    state.visionUploadBusy = true;
    updateVisionSubmitState();
    button.textContent = '正在提交…';
    try {
      var data = await request('/api/manager/vision-jobs', 'POST', form);
      message('vision-message', '影像任务已入队。分析服务独立运行，不会阻塞路线规划。', true);
      clearSelectedMedia(); refreshVisionJobs();
    } catch (error) { message('vision-message', error.message || '任务提交失败。'); }
    finally {
      state.visionUploadBusy = false;
      button.innerHTML = '开始识别 <span>→</span>';
      updateVisionSubmitState();
    }
  }

  async function endEvent(id) {
    if (!window.confirm('结束这条管制事件？记录会保留在历史中。')) return;
    try { await request('/api/road-conditions/' + encodeURIComponent(id), 'PATCH', { action: 'end' }); refreshEvents(); }
    catch (error) { window.alert(error.message); }
  }

  async function deleteEvent(id) {
    if (!window.confirm('永久删除这条路段管制记录？删除后无法恢复。')) return;
    try { await request('/api/road-conditions/' + encodeURIComponent(id), 'DELETE'); refreshEvents(); }
    catch (error) { window.alert(error.message); }
  }

  async function submitEvent(event) {
    event.preventDefault();
    if (!state.picked) { message('form-message', '请先在地图上选取一条道路。'); return; }
    if (!state.picked.confirmed) { message('form-message', '请先核对并确认地图高亮的路段。'); return; }
    if (!byId('event-type').value) { message('form-message', '请选择现场确认的事件类型。'); return; }
    var blockedModes = selectedBlockedModes();
    if (!blockedModes.length) { message('form-message', '请至少选择一种需要禁止通行的方式。'); return; }
    var fieldConfirmation = byId('field-confirmation').value.trim();
    if (state.sourceJobId && fieldConfirmation.length < 8) {
      message('form-message', '请填写具体道路的现场核实依据（至少 8 字）。'); return;
    }
    var start = byId('start-time').value;
    var end = byId('end-time').value;
    if (start && end && end <= start) { message('form-message', '结束时间必须晚于开始时间。'); return; }
    var button = byId('publish-event'); button.disabled = true; button.textContent = '正在发布…';
    var body = {
      type: byId('event-type').value,
      name: byId('event-name').value.trim(),
      description: byId('event-description').value.trim(),
      lng: state.picked.lng, lat: state.picked.lat,
      blocked_modes: blockedModes,
    };
    if (start) body.start_time = start;
    if (end) body.end_time = end;
    if (state.sourceJobId) {
      body.source_vision_job_id = state.sourceJobId;
      body.field_confirmation = fieldConfirmation;
    }
    try {
      await request('/api/road-conditions', 'POST', body);
      message('form-message', '事件已发布，路线规划会按该路段状态处理。', true);
      byId('event-name').value = ''; byId('event-description').value = '';
      byId('start-time').value = ''; byId('end-time').value = '';
      resetBlockedModes();
      clearPickedRoad(); clearVisionSource();
      refreshEvents();
    } catch (error) { message('form-message', error.message || '事件发布失败。'); }
    finally { button.innerHTML = '发布事件 <span>→</span>'; updatePublishState(); }
  }

  resetBlockedModes();
  byId('login-form').addEventListener('submit', async function (event) {
    event.preventDefault();
    message('login-message', '正在验证…');
    try {
      var data = await request('/api/admin/login', 'POST', { password: byId('password').value });
      state.csrf = data.csrf_token || '';
      setLoggedIn(data);
    } catch (error) { message('login-message', error.message || '无法登录管理台。'); }
  });
  byId('pick-road').addEventListener('click', function () { setPicking(!(state.picking && state.pickPurpose === 'road'), 'road'); });
  byId('confirm-picked-road').addEventListener('click', function () {
    if (!state.picked) return;
    state.picked.confirmed = true;
    byId('confirm-picked-road').hidden = true;
    message('form-message', '已确认高亮路段，可以填写并发布事件。', true);
    updatePublishState();
  });
  byId('clear-picked-road').addEventListener('click', function () {
    clearPickedRoad();
    message('form-message', '已清除所选路段。');
  });
  byId('event-type').addEventListener('change', function () { updatePublishState(); return refreshImpactPreview(); });
  blockedModeInputs().forEach(function (input) {
    input.addEventListener('change', function () {
      updatePublishState();
      refreshImpactPreview();
    });
  });
  byId('field-confirmation').addEventListener('input', updatePublishState);
  byId('clear-vision-source').addEventListener('click', clearVisionSource);
  byId('media-file').addEventListener('change', function () {
    var file = byId('media-file').files[0];
    renderSelectedMedia(file);
    updateVisionSubmitState();
    byId('camera-stabilized').disabled = !isVideoFile(file);
    if (!isVideoFile(file)) byId('camera-stabilized').checked = false;
    updateVisionGuidance();
  });
  byId('vision-form').addEventListener('submit', submitVision);
  byId('condition-form').addEventListener('submit', submitEvent);
  byId('refresh-events').addEventListener('click', refreshEvents);
  byId('logout').addEventListener('click', async function () {
    try { await request('/api/admin/logout', 'POST', {}); } catch (_) {}
    setLoggedOut('已退出管理台。');
  });

  request('/api/admin/status', 'GET').then(function (data) {
    if (data.is_admin) setLoggedIn(data);
    else if (!data.login_enabled) message('login-message', '管理功能尚未配置，请联系系统管理员。');
  }).catch(function () { message('login-message', '管理服务暂时不可用，请稍后刷新。'); });
}());
