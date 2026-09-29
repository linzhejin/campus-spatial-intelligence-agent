(function () {
  'use strict';

  var state = { csrf: '', map: null, eventLayers: null, visionLayers: null, preview: null, anchorMarker: null, picked: null, anchor: null, picking: false, pickPurpose: null, maxBytes: 24 * 1024 * 1024, inferenceReady: false, visionStatusBusy: false, visionUploadBusy: false, poll: null };
  var byId = function (id) { return document.getElementById(id); };
  var message = function (id, text, good) {
    var node = byId(id);
    if (!node) return;
    node.textContent = text || '';
    node.style.color = good ? '#397653' : '';
  };

  function isVideoFile(file) {
    return !!file && (/^video\//i.test(file.type || '') || /\.(mp4|mov|avi|webm)$/i.test(file.name || ''));
  }

  function canSubmitVision() {
    return !!(state.inferenceReady && !state.visionUploadBusy && state.anchor && byId('media-file').files.length);
  }

  function updateVisionSubmitState() {
    byId('submit-vision').disabled = !canSubmitVision();
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
    if (!response.ok) throw new Error(payload.message || '请求失败，请重试');
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
    refreshVisionJobs();
    if (!state.poll) state.poll = window.setInterval(function () {
      if (!document.hidden) {
        refreshVisionStatus();
        refreshVisionJobs();
      }
    }, 5000);
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
    state.visionLayers = L.layerGroup().addTo(state.map);
    state.map.on('click', handleMapClick);
  }

  function setPicking(active, purpose) {
    state.picking = active;
    state.pickPurpose = active ? (purpose || state.pickPurpose || 'road') : null;
    byId('manager-map').classList.toggle('manager-map-picking', active);
    byId('pick-road').textContent = active && state.pickPurpose === 'road' ? '取消选路' : '⌖ 选取路段';
    byId('pick-state').textContent = active
      ? (state.pickPurpose === 'vision' ? '点击地图标注影像的大致观察区域' : '请在地图道路上点选，系统会吸附到正式路网')
      : '选择路段后再填写事件';
  }

  async function handleMapClick(event) {
    if (!state.picking) return;
    var lng = event.latlng.lng;
    var lat = event.latlng.lat;
    if (state.pickPurpose === 'vision') {
      state.anchor = { lng: lng, lat: lat };
      if (state.anchorMarker) state.map.removeLayer(state.anchorMarker);
      state.anchorMarker = L.circleMarker([lat, lng], {
        radius: 8, color: '#fff', weight: 2, fillColor: '#52786e', fillOpacity: 1,
      }).addTo(state.visionLayers);
      byId('selected-anchor').textContent = '观察区域参考点 · ' + lat.toFixed(5) + ', ' + lng.toFixed(5);
      byId('selected-anchor').classList.add('is-set');
      updateVisionSubmitState();
      message('vision-message', '观察区域已标注。检测框不会自动转换为地面道路坐标。', true);
      setPicking(false);
      return;
    }
    message('form-message', '正在将点位匹配到校园路网…');
    try {
      var snapData = await request('/api/road-conditions/snap?lng=' + encodeURIComponent(lng) + '&lat=' + encodeURIComponent(lat), 'GET');
      var snap = snapData.snap;
      if (!snap) throw new Error('这个位置没有匹配到校园道路，请放大后重选。');
      state.picked = { lng: lng, lat: lat, snap: snap };
      if (state.preview) state.map.removeLayer(state.preview);
      var geometry = (snap.geometry_gcj || []).map(function (point) { return [point[1], point[0]]; });
      if (geometry.length >= 2) {
        state.preview = L.polyline(geometry, { color: '#d48943', weight: 8, opacity: .85, lineCap: 'round' }).addTo(state.map);
      } else {
        state.preview = L.circleMarker([snap.snap_lat_gcj, snap.snap_lng_gcj], {
          radius: 8, color: '#fff', weight: 2, fillColor: '#d48943', fillOpacity: 1,
        }).addTo(state.map);
      }
      var road = snap.road_name || '未命名道路';
      var length = snap.chain_length_m ? Math.round(snap.chain_length_m) + ' 米路段' : '已吸附到一条道路';
      byId('selected-road').textContent = road + ' · 偏移 ' + Math.round(snap.dist_m || 0) + ' 米 · ' + length;
      byId('selected-road').classList.add('is-set');
      byId('publish-event').disabled = false;
      message('form-message', '路段已核对，可以填写并发布事件。', true);
      setPicking(false);
    } catch (error) {
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
    status.appendChild(document.createTextNode({ active: '生效中', scheduled: '待生效', expired: '已结束' }[eventStatus(item)] || '已结束'));
    var name = document.createElement('div');
    var title = document.createElement('div'); title.className = 'event-name'; title.textContent = item.name || '未命名事件';
    var description = document.createElement('div'); description.className = 'event-sub'; description.textContent = item.description || '无补充说明';
    name.append(title, description);
    var type = document.createElement('div'); type.className = 'event-type'; type.textContent = item.type_label || item.type || '管制';
    var road = document.createElement('div'); road.className = 'event-meta';
    var edge = item.edge || {};
    road.textContent = (edge.road_name || '未命名道路') + (edge.chain_length_m ? ' · ' + Math.round(edge.chain_length_m) + ' 米' : '');
    var times = document.createElement('div'); times.className = 'event-meta';
    var end = item.end_time ? new Date(Number(item.end_time) * 1000).toLocaleString('zh-CN', { hour12: false }) : '持续有效';
    times.textContent = eventStatus(item) === 'scheduled' ? '计划开始 ' + new Date(Number(item.start_time) * 1000).toLocaleString('zh-CN', { hour12: false }) : '结束 ' + end;
    var actions = document.createElement('div'); actions.className = 'row-actions';
    if (eventStatus(item) === 'active') {
      var endButton = document.createElement('button'); endButton.type = 'button'; endButton.textContent = '结束事件';
      endButton.addEventListener('click', function () { endEvent(item.id); }); actions.appendChild(endButton);
    }
    var deleteButton = document.createElement('button'); deleteButton.type = 'button'; deleteButton.textContent = '删除';
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
      byId('vision-status').classList.toggle('is-ready', !!data.inference_ready);
    } catch (error) {
      state.inferenceReady = false;
      updateVisionSubmitState();
      byId('vision-status').classList.toggle('is-ready', false);
      byId('vision-status').textContent = error.message || '视觉任务状态暂不可用。';
    } finally { state.visionStatusBusy = false; }
  }

  function candidateName(kind) {
    return {
      possible_congestion: '疑似低速车辆聚集',
      possible_accident: '疑似事故类别目标',
      vehicle_cluster_review: '车辆密集观察',
    }[kind] || '影像候选';
  }

  function reviewJob(job, status) {
    var note = status === 'confirmed'
      ? '管理员确认影像中存在候选迹象；请再通过地图选定具体道路并手动登记通行事件。'
      : '管理员复核后排除该影像候选。';
    request('/api/manager/vision-jobs/' + encodeURIComponent(job.job_id) + '/review', 'POST', { status: status, note: note })
      .then(refreshVisionJobs)
      .catch(function (error) { window.alert(error.message); });
  }

  function renderVisionJob(job) {
    var card = document.createElement('article'); card.className = 'vision-job';
    var head = document.createElement('div'); head.className = 'vision-job-head';
    var title = document.createElement('div'); title.className = 'vision-job-title'; title.textContent = job.original_name || '巡查影像';
    var meta = document.createElement('div'); meta.className = 'vision-job-meta';
    var when = job.created_at ? new Date(job.created_at).toLocaleString('zh-CN', { hour12: false }) : '';
    meta.textContent = when + (job.anchor_gcj ? ' · 观察区域已标注' : '')
      + (job.media_kind === 'video' ? (job.camera_stabilized ? ' · 管理员声明固定/已稳像' : ' · 镜头运动未校正') : '');
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
    var result = job.result || {};
    if (job.status === 'needs_review' || job.review_status) {
      var preview;
      if (job.media_kind === 'video') {
        preview = document.createElement('video'); preview.controls = true; preview.preload = 'metadata';
      } else { preview = document.createElement('img'); preview.alt = '管理员上传的校园巡查影像'; }
      preview.className = 'vision-preview'; preview.src = job.media_url; card.appendChild(preview);
      var candidates = document.createElement('div'); candidates.className = 'candidate-list';
      (result.candidates || []).forEach(function (item) {
        var candidate = document.createElement('div'); candidate.className = 'candidate-card';
        var titleNode = document.createElement('strong');
        titleNode.textContent = candidateName(item.kind) + ' · 置信度 ' + Math.round((item.confidence || 0) * 100) + '%';
        var why = document.createElement('p'); why.textContent = item.reason || '请结合原始影像复核。';
        candidate.append(titleNode, why);
        if (job.status === 'needs_review' && !job.review_status) {
          var actions = document.createElement('div'); actions.className = 'candidate-actions';
          var confirmButton = document.createElement('button'); confirmButton.type = 'button'; confirmButton.textContent = '确认候选';
          confirmButton.addEventListener('click', function () { reviewJob(job, 'confirmed'); });
          var dismissButton = document.createElement('button'); dismissButton.type = 'button'; dismissButton.textContent = '排除候选';
          dismissButton.addEventListener('click', function () { reviewJob(job, 'dismissed'); });
          actions.append(confirmButton, dismissButton); candidate.appendChild(actions);
        }
        candidates.appendChild(candidate);
      });
      card.appendChild(candidates);
      if (job.review_status === 'confirmed') {
        var next = document.createElement('p'); next.className = 'vision-job-meta';
        next.textContent = '已确认影像迹象。若需影响导航，请在上方路况表单中手工选定具体道路并发布事件。';
        card.appendChild(next);
      }
    }
    return card;
  }

  async function refreshVisionJobs() {
    if (!byId('workspace') || byId('workspace').hidden) return;
    var root = byId('vision-jobs');
    try {
      var data = await request('/api/manager/vision-jobs?limit=20', 'GET');
      var jobs = data.jobs || [];
      root.replaceChildren();
      if (!jobs.length) {
        var empty = document.createElement('div'); empty.className = 'empty-state'; empty.textContent = '尚无巡查任务。提交影像后，分析进度和候选都会显示在这里。'; root.appendChild(empty);
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
    if (!state.anchor) { message('vision-message', '请先在地图上标注影像所在校园区域。'); return; }
    if (file.size > state.maxBytes) {
      message('vision-message', '文件过大，当前上限为 ' + Math.round(state.maxBytes / (1024 * 1024)) + ' MB。'); return;
    }
    var form = new FormData(); form.append('media', file); form.append('lng', state.anchor.lng); form.append('lat', state.anchor.lat);
    form.append('camera_stabilized', String(isVideoFile(file) && byId('camera-stabilized').checked));
    var button = byId('submit-vision');
    state.visionUploadBusy = true;
    updateVisionSubmitState();
    button.textContent = '正在提交…';
    try {
      var data = await request('/api/manager/vision-jobs', 'POST', form);
      message('vision-message', '影像任务已入队。分析服务独立运行，不会阻塞路线规划。', true);
      byId('media-file').value = ''; refreshVisionJobs();
    } catch (error) { message('vision-message', error.message || '任务提交失败。'); }
    finally {
      state.visionUploadBusy = false;
      button.innerHTML = '提交分析任务 <span>→</span>';
      updateVisionSubmitState();
    }
  }

  async function endEvent(id) {
    if (!window.confirm('结束这条管制事件？记录会保留在历史中。')) return;
    try { await request('/api/road-conditions/' + encodeURIComponent(id), 'PATCH', { action: 'end' }); refreshEvents(); }
    catch (error) { window.alert(error.message); }
  }

  async function deleteEvent(id) {
    if (!window.confirm('删除这条事件记录？此操作不能撤销。')) return;
    try { await request('/api/road-conditions/' + encodeURIComponent(id), 'DELETE'); refreshEvents(); }
    catch (error) { window.alert(error.message); }
  }

  async function submitEvent(event) {
    event.preventDefault();
    if (!state.picked) { message('form-message', '请先在地图上选取一条道路。'); return; }
    var start = byId('start-time').value;
    var end = byId('end-time').value;
    if (start && end && end <= start) { message('form-message', '结束时间必须晚于开始时间。'); return; }
    var button = byId('publish-event'); button.disabled = true; button.textContent = '正在发布…';
    var body = {
      type: byId('event-type').value,
      name: byId('event-name').value.trim(),
      description: byId('event-description').value.trim(),
      lng: state.picked.lng, lat: state.picked.lat,
    };
    if (start) body.start_time = start;
    if (end) body.end_time = end;
    try {
      await request('/api/road-conditions', 'POST', body);
      message('form-message', '事件已发布，路线规划会按该路段状态处理。', true);
      byId('event-name').value = ''; byId('event-description').value = '';
      byId('start-time').value = ''; byId('end-time').value = '';
      byId('selected-road').textContent = '尚未选择道路'; byId('selected-road').classList.remove('is-set');
      if (state.preview) state.map.removeLayer(state.preview);
      state.preview = null; state.picked = null;
      refreshEvents();
    } catch (error) { message('form-message', error.message || '事件发布失败。'); }
    finally { button.innerHTML = '发布事件 <span>→</span>'; button.disabled = !state.picked; }
  }

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
  byId('pick-anchor').addEventListener('click', function () { setPicking(true, 'vision'); });
  byId('media-file').addEventListener('change', function () {
    var file = byId('media-file').files[0];
    updateVisionSubmitState();
    byId('camera-stabilized').disabled = !isVideoFile(file);
    if (!isVideoFile(file)) byId('camera-stabilized').checked = false;
    if (file && file.size > state.maxBytes) message('vision-message', '文件超过当前大小上限。');
  });
  byId('vision-form').addEventListener('submit', submitVision);
  byId('condition-form').addEventListener('submit', submitEvent);
  byId('refresh-events').addEventListener('click', refreshEvents);
  byId('logout').addEventListener('click', async function () {
    try { await request('/api/admin/logout', 'POST', {}); } catch (_) {}
    if (state.poll) { window.clearInterval(state.poll); state.poll = null; }
    state.csrf = ''; byId('workspace').hidden = true; byId('logout').hidden = true; byId('login-panel').hidden = false;
  });

  request('/api/admin/status', 'GET').then(function (data) {
    if (data.is_admin) setLoggedIn(data);
    else if (!data.login_enabled) message('login-message', '管理功能尚未配置，请联系系统管理员。');
  }).catch(function () { message('login-message', '管理服务暂时不可用，请稍后刷新。'); });
}());
