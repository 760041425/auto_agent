const API = '/api';
let currentImageId = null;

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      document.getElementById('tab-' + tab).classList.add('active');
      if (tab === 'list') loadImages();
      if (tab === 'tasks') loadTasks();
      if (tab === 'localize') loadLocalizeImages();
    });
  });

  const zone = document.getElementById('upload-zone');
  const input = document.getElementById('file-input');
  zone.addEventListener('click', () => input.click());
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-over'); uploadFiles(e.dataTransfer.files); });
  input.addEventListener('change', () => uploadFiles(input.files));

  document.getElementById('refresh-btn').addEventListener('click', loadImages);
  document.getElementById('back-btn').addEventListener('click', () => switchTab('list'));
  loadImages();
});

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  document.querySelector('.tab[data-tab="' + name + '"]').classList.add('active');
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'list') loadImages();
  if (name === 'tasks') loadTasks();
  if (name === 'localize') loadLocalizeImages();
}

async function uploadFiles(files) {
  for (const file of files) {
    const form = new FormData();
    form.append('file', file);
    try {
      await fetch(API + '/images/upload', { method: 'POST', body: form });
    } catch(e) {}
  }
  loadImages();
}

async function loadImages() {
  try {
    const resp = await fetch(API + '/images');
    const images = await resp.json();
    const grid = document.getElementById('image-grid');
    document.getElementById('image-count').textContent = '共 ' + images.length + ' 张';
    grid.innerHTML = images.map(img =>
      '<div class="image-card">' +
      '<img src="/images/' + img.filename + '" onclick="showDetail(' + img.id + ')">' +
      '<div class="info"><div class="name" onclick="showDetail(' + img.id + ')">' + img.original_name + '</div>' +
      '<div class="status ' + img.status + '">' + img.status + '</div>' +
      '<button class="match-btn" onclick="startCompareFromList(' + img.id + ', this)">匹配</button></div></div>'
    ).join('');
  } catch(e) { console.error(e); }
}

async function showDetail(id) {
  currentImageId = id;
  try {
    const resp = await fetch(API + '/images/' + id);
    const img = await resp.json();
    switchTab('detail');
    document.getElementById('detail-title').textContent = img.original_name;
    document.getElementById('detail-image').src = '/images/' + img.filename;
    document.getElementById('detail-image').onload = function() {
      var c = document.getElementById('overlay-canvas');
      c.width = this.naturalWidth;
      c.height = this.naturalHeight;
    };
    document.getElementById('compare-status').classList.add('hidden');
    document.getElementById('compare-result').classList.add('hidden');
    document.getElementById('compare-btn').onclick = function() { startCompare(id); };
  } catch(e) { console.error(e); }
}

async function startCompare(imageId) {
  var statusEl = document.getElementById('compare-status');
  var resultEl = document.getElementById('compare-result');
  statusEl.classList.remove('hidden');
  statusEl.textContent = '创建匹配任务...';
  statusEl.className = 'status loading';
  resultEl.classList.add('hidden');
  clearAnnotations();

  try {
    var resp = await fetch(API + '/tasks/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_id: imageId }),
    });
    var task = await resp.json();
    pollTask(task.id);
  } catch(e) {
    statusEl.textContent = '请求失败: ' + e.message;
    statusEl.className = 'status error';
  }
}

async function pollTask(id) {
  var statusEl = document.getElementById('compare-status');
  try {
    var resp = await fetch(API + '/tasks/' + id);
    var task = await resp.json();
    if (task.status === 'completed') {
      statusEl.textContent = '';
      statusEl.className = 'status success';
      var reportResp = await fetch(API + '/tasks/' + id + '/report');
      var report = await reportResp.json();
      renderReport(report, task);
      return;
    }
    if (task.status === 'failed') {
      statusEl.textContent = '匹配失败: ' + (task.error_message || '');
      statusEl.className = 'status error';
      return;
    }
    statusEl.textContent = '匹配中... (' + task.status + ')';
    setTimeout(function() { pollTask(id); }, 2000);
  } catch(e) {
    statusEl.textContent = '轮询失败: ' + e.message;
    statusEl.className = 'status error';
  }
}

async function startCompareFromList(imageId, btn) {
  btn.disabled = true;
  btn.textContent = '匹配中...';
  btn.classList.add('loading');

  try {
    var resp = await fetch(API + '/tasks/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_id: imageId }),
    });
    var task = await resp.json();
    // 轮询等待完成
    await pollTaskFromList(task.id, btn);
  } catch(e) {
    btn.textContent = '请求失败';
    btn.classList.add('failed');
  }
}

async function pollTaskFromList(id, btn) {
  try {
    var resp = await fetch(API + '/tasks/' + id);
    var task = await resp.json();
    if (task.status === 'completed') {
      btn.textContent = '已完成';
      btn.classList.remove('loading');
      btn.classList.add('done');
      return;
    }
    if (task.status === 'failed') {
      btn.textContent = '失败';
      btn.classList.remove('loading');
      btn.classList.add('failed');
      return;
    }
    btn.textContent = '匹配中...';
    setTimeout(function() { pollTaskFromList(id, btn); }, 2000);
  } catch(e) {
    btn.textContent = '轮询失败';
    btn.classList.add('failed');
  }
}

function clearAnnotations() {
  var canvas = document.getElementById('overlay-canvas');
  if (canvas) {
    var ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

function drawMatchedPoints(points) {
  if (!points || points.length === 0) return;
  var canvas = document.getElementById('overlay-canvas');
  var img = document.getElementById('detail-image');
  if (!canvas || !img) return;
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  var ctx = canvas.getContext('2d');

  var colors = ['#e94560', '#ff6b35', '#ffc107', '#4caf50', '#2196f3',
                '#9c27b0', '#00bcd4', '#ff4081', '#7c4dff', '#00e676'];

  points.forEach(function(p, idx) {
    var x = p.query_pt[0];
    var y = p.query_pt[1];
    var color = colors[idx % colors.length];

    // 画圆点
    ctx.beginPath();
    ctx.arc(x, y, 6, 0, 2 * Math.PI);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 2;
    ctx.stroke();

    // 标序号
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 9px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(idx + 1, x, y);
  });
}

function renderReport(report, task) {
  console.log('renderReport called, matched_points:', report.matched_points ? report.matched_points.length : 0);
  var el = document.getElementById('compare-result');
  var content = document.getElementById('result-content');
  var html = '';

  // ====== 摘要 ======
  html += '<div class="report-summary">';
  html += '<p>匹配: <strong style="color:' + (report.matched ? '#4caf50' : '#f44336') + '">' + (report.matched ? '成功' : '失败') + '</strong></p>';
  html += '<p>匹配点数: <strong>' + (report.total_matches || 0) + '</strong></p>';
  if (report.confidence != null) {
    html += '<p>置信度: <strong>' + (report.confidence * 100).toFixed(1) + '%</strong></p>';
  }
  if (report.center_3d && report.center_3d.x != null) {
    html += '<p>区域 3D 中心: X=' + report.center_3d.x.toFixed(3) + ' Y=' + report.center_3d.y.toFixed(3) + ' Z=' + report.center_3d.z.toFixed(3) + '</p>';
  }
  html += '</div>';

  // ====== 标注的匹配点列表（前5个） ======
  var displayPoints = report.matched_points ? report.matched_points.slice(0, 5) : [];
  if (displayPoints.length > 0) {
    html += '<div class="annotated-section"><h4>📍 图像标注点（前' + displayPoints.length + '个）</h4>';
    displayPoints.forEach(function(p, idx) {
      var c3 = p.point3d;
      html += '<div class="annotated-point">';
      html += '<span class="point-label">' + (idx + 1) + '</span>';
      html += '<span class="point-pixel">像素 (' + p.query_pt[0].toFixed(0) + ', ' + p.query_pt[1].toFixed(0) + ')</span>';
      html += '<span class="point-coord">3D X=' + c3[0].toFixed(3) + ' Y=' + c3[1].toFixed(3) + ' Z=' + c3[2].toFixed(3) + '</span>';
      html += '</div>';
    });
    html += '</div>';

    // 在图像上绘制匹配点（前5个）
    drawMatchedPoints(displayPoints);
  }

  // ====== 所有匹配点表格（可展开/折叠） ======
  if (report.all_matched_points && report.all_matched_points.length > 0) {
    html += '<details class="all-points-details">';
    html += '<summary>查看全部 ' + report.all_matched_points.length + ' 个匹配点</summary>';
    html += '<div class="all-points-table-wrap"><table class="points-table">';
    html += '<thead><tr><th>#</th><th>图像X</th><th>图像Y</th><th>3D X</th><th>3D Y</th><th>3D Z</th><th>距离</th></tr></thead><tbody>';
    report.all_matched_points.forEach(function(p, idx) {
      var c3 = p.point3d;
      html += '<tr>';
      html += '<td>' + (idx + 1) + '</td>';
      html += '<td>' + p.query_pt[0].toFixed(0) + '</td>';
      html += '<td>' + p.query_pt[1].toFixed(0) + '</td>';
      html += '<td>' + c3[0].toFixed(3) + '</td>';
      html += '<td>' + c3[1].toFixed(3) + '</td>';
      html += '<td>' + c3[2].toFixed(3) + '</td>';
      html += '<td>' + p.distance.toFixed(1) + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table></div></details>';
  }

  // ====== LAS 点云验证 ======
  if (report.verification && !report.verification.error) {
    var v = report.verification;
    html += '<div class="verification-section"><h4>LAS 点云验证</h4>';
    html += '<table>';
    html += '<tr><td>匹配点总数</td><td>' + v.total_checked + '</td></tr>';
    html += '<tr><td>已验证（偏差<' + (v.details && v.details[0] ? 3 : 3) + 'm）</td><td><strong>' + v.total_verified + '</strong> (' + (v.verification_rate * 100).toFixed(1) + '%)</td></tr>';
    html += '<tr><td>平均偏差</td><td>' + v.mean_distance_m.toFixed(3) + 'm</td></tr>';
    html += '</table>';
    if (v.details) {
      html += '<div style="margin-top:0.5em">';
      v.details.slice(0, 6).forEach(function(d) {
        html += '<div class="coord-row">';
        html += '<span>匹配(' + d.matched_xyz.map(function(x) { return x.toFixed(1); }).join(',') + ')</span>';
        html += '<span>→ LAS(' + d.nearest_las_xyz.map(function(x) { return x.toFixed(1); }).join(',') + ')</span>';
        html += '<span class="dist-badge ' + (d.verified ? 'ok' : 'fail') + '">' + d.distance_m.toFixed(2) + 'm ' + (d.verified ? 'OK' : 'FAIL') + '</span>';
        html += '</div>';
      });
      html += '</div>';
    }
    html += '</div>';
  }

  content.innerHTML = html;
  el.classList.remove('hidden');
}

async function loadTasks() {
  console.log('loadTasks called');
  try {
    var resp = await fetch(API + '/tasks');
    var tasks = await resp.json();
    console.log('tasks loaded:', tasks.length);
    var list = document.getElementById('task-list');
    if (tasks.length === 0) {
      list.innerHTML = '<p>暂无任务</p>';
      return;
    }

    // 并行拉取所有已完成任务的 report 和对应图像信息
    var reports = {};
    var images = {};
    var completedTasks = tasks.filter(function(t) { return t.status === 'completed'; });
    var dataPromises = completedTasks.map(async function(t) {
      try {
        var [rResp, imgResp] = await Promise.all([
          fetch(API + '/tasks/' + t.id + '/report'),
          fetch(API + '/images/' + t.image_id),
        ]);
        reports[t.id] = await rResp.json();
        images[t.image_id] = await imgResp.json();
      } catch(e) {}
    });
    await Promise.all(dataPromises);
    console.log('reports keys:', Object.keys(reports));
    console.log('images keys:', Object.keys(images));

    list.innerHTML = tasks.map(function(t) {
      var html = '<div class="task-item">';
      html += '<div class="task-item-main" onclick="toggleTaskDetail(' + t.id + ')">';
      html += '<div><strong>任务#' + t.id + '</strong> 图像#' + t.image_id + '<br><small>' + new Date(t.created_at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'}) + '</small></div>';
      html += '<span class="status-badge ' + t.status + '">' + t.status + '</span>';
      html += '</div>';

      var r = reports[t.id];
      var img = images[t.image_id];
      console.log('task', t.id, 'r:', !!r, 'img:', !!img, 'matched:', r ? r.matched : 'N/A');
      if (r && r.matched) {
        var filename = img ? img.filename : '';
        html += '<div class="task-detail" id="task-detail-' + t.id + '" style="display:none">';
        // 图像 + canvas
        html += '<div class="task-detail-image-wrap">';
        html += '<img class="task-detail-image" id="task-img-' + t.id + '" src="/images/' + filename + '" data-task-id="' + t.id + '">';
        html += '<canvas class="task-detail-canvas" id="task-canvas-' + t.id + '" width="0" height="0"></canvas>';
        html += '</div>';
        // 摘要
        html += '<div class="task-detail-summary">';
        html += '<span>匹配 ' + r.total_matches + ' 点</span>';
        if (r.center_3d && r.center_3d.x != null) {
          html += '<span class="task-detail-3d">3D: X=' + r.center_3d.x.toFixed(2) + ' Y=' + r.center_3d.y.toFixed(2) + ' Z=' + r.center_3d.z.toFixed(2) + '</span>';
        }
        html += '</div>';
        // LAS 验证结果
        if (r.verification && !r.verification.error) {
          var v = r.verification;
          html += '<div class="task-detail-verification">';
          html += '<span class="verif-label">LAS验证</span>';
          html += '<span class="verif-rate">' + (v.verification_rate * 100).toFixed(0) + '%通过</span>';
          html += '<span class="verif-detail">' + v.total_verified + '/' + v.total_checked + ' 偏差' + v.mean_distance_m.toFixed(2) + 'm</span>';
          html += '</div>';
        }
        // 标注点列表（前5个）
        var displayPoints = r.matched_points ? r.matched_points.slice(0, 5) : [];
        if (displayPoints.length > 0) {
          html += '<div class="task-detail-points">';
          displayPoints.forEach(function(p, idx) {
            var c3 = p.point3d;
            html += '<div class="annotated-point">';
            html += '<span class="point-label">' + (idx + 1) + '</span>';
            html += '<span class="point-pixel">像素 (' + p.query_pt[0].toFixed(0) + ', ' + p.query_pt[1].toFixed(0) + ')</span>';
            html += '<span class="point-coord">3D X=' + c3[0].toFixed(2) + ' Y=' + c3[1].toFixed(2) + ' Z=' + c3[2].toFixed(2) + '</span>';
            html += '</div>';
          });
          html += '</div>';
        }
        html += '</div>';
      } else if (r && !r.matched) {
        html += '<div class="task-detail" id="task-detail-' + t.id + '" style="display:none">';
        html += '<div class="task-detail-row"><span class="task-detail-label" style="color:#f44336">匹配失败</span></div>';
        html += '</div>';
      }

      html += '</div>';
      return html;
    }).join('');
  } catch(e) { console.error(e); }
}

function toggleTaskDetail(id) {
  console.log('toggleTaskDetail', id);
  var el = document.getElementById('task-detail-' + id);
  if (el) {
    console.log('  element found, display:', el.style.display);
    var isOpen = el.style.display === 'block';
    el.style.display = isOpen ? 'none' : 'block';

    // 展开时绘制标注点
    if (!isOpen) {
      var img = document.getElementById('task-img-' + id);
      console.log('  task-img found:', !!img, 'src:', img ? img.src : 'N/A');
      if (img) {
        if (img.complete && img.naturalWidth > 0) {
          console.log('  image already loaded, drawing');
          drawTaskPoints(id);
        } else {
          console.log('  waiting for image onload');
          img.onload = function() { console.log('  image loaded, drawing'); drawTaskPoints(id); };
        }
      }
    }
  }
}

function drawTaskPoints(taskId) {
  console.log('drawTaskPoints', taskId);
  var img = document.getElementById('task-img-' + taskId);
  var canvas = document.getElementById('task-canvas-' + taskId);
  console.log('  img:', !!img, 'canvas:', !!canvas, 'naturalWidth:', img ? img.naturalWidth : 0);
  if (!img || !canvas || !img.naturalWidth) return;

  // canvas 的实际像素尺寸 = 图片原始尺寸
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  console.log('  canvas size:', canvas.width, 'x', canvas.height);
  var ctx = canvas.getContext('2d');

  // 从 report 获取匹配点（只画前5个）
  fetch(API + '/tasks/' + taskId + '/report').then(function(r) { return r.json(); }).then(function(report) {
    var points = (report.matched_points || []).slice(0, 5);
    console.log('  points to draw:', points.length);
    if (!points.length) return;
    var colors = ['#e94560', '#ff6b35', '#ffc107', '#4caf50', '#2196f3'];

    points.forEach(function(p, idx) {
      var x = p.query_pt[0];
      var y = p.query_pt[1];
      var color = colors[idx % colors.length];
      ctx.beginPath();
      ctx.arc(x, y, 6, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 9px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(idx + 1, x, y);
    });
  }).catch(function() {});
}

// ====== LAS 预处理 ======

async function startPreprocess() {
  var btn = document.getElementById('preprocess-btn');
  var statusEl = document.getElementById('preprocess-status');
  var progressWrap = document.getElementById('preprocess-progress-wrap');
  var progressBar = document.getElementById('preprocess-progress-bar');

  btn.disabled = true;
  btn.textContent = '处理中...';
  statusEl.classList.remove('hidden');
  statusEl.className = 'status loading';
  statusEl.textContent = '启动预处理...';
  progressWrap.classList.remove('hidden');
  progressBar.style.width = '0%';

  try {
    var resp = await fetch(API + '/las/preprocess', { method: 'POST' });
    var text = await resp.text();
    var data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (parseErr) {
      console.error('preprocess start response is not valid json:', text);
      data = { error: text || 'unknown error' };
    }
    console.log('preprocess started:', data);

    if (!resp.ok || data.error) {
      throw new Error(data.error || '预处理启动失败');
    }

    // 轮询进度
    pollPreprocessStatus(btn, statusEl, progressWrap, progressBar);
  } catch(e) {
    btn.disabled = false;
    btn.textContent = '开始预处理';
    statusEl.textContent = '启动失败: ' + e.message;
    statusEl.className = 'status error';
  }
}

async function pollPreprocessStatus(btn, statusEl, progressWrap, progressBar) {
  try {
    var resp = await fetch(API + '/las/preprocess/status');
    var text = await resp.text();
    var s = {};
    try {
      s = text ? JSON.parse(text) : {};
    } catch (parseErr) {
      console.error('preprocess status response is not valid json:', text);
      s = { error: text || 'unknown error' };
    }

    statusEl.textContent = s.step || '';
    progressBar.style.width = s.progress + '%';

    if (s.error) {
      btn.disabled = false;
      btn.textContent = '开始预处理';
      statusEl.textContent = '❌ ' + s.error;
      statusEl.className = 'status error';
      return;
    }

    if (s.running) {
      setTimeout(function() { pollPreprocessStatus(btn, statusEl, progressWrap, progressBar); }, 1000);
    } else {
      // 完成
      btn.disabled = false;
      btn.textContent = '重新预处理';
      if (s.progress >= 100) {
        statusEl.textContent = '✅ ' + (s.step || '预处理完成');
        statusEl.className = 'status success';
      }
    }
  } catch(e) {
    btn.disabled = false;
    btn.textContent = '开始预处理';
    statusEl.textContent = '查询状态失败: ' + e.message;
    statusEl.className = 'status error';
  }
}

// ====== 视觉定位 ======

var localizeSelectedId = null;

// 定位页面上传
document.addEventListener('DOMContentLoaded', function() {
  var zone = document.getElementById('localize-upload-zone');
  var input = document.getElementById('localize-file-input');
  if (zone && input) {
    zone.addEventListener('click', function() { input.click(); });
    zone.addEventListener('dragover', function(e) { e.preventDefault(); zone.style.borderColor = '#e94560'; });
    zone.addEventListener('dragleave', function() { zone.style.borderColor = '#ccc'; });
    zone.addEventListener('drop', function(e) {
      e.preventDefault();
      zone.style.borderColor = '#ccc';
      uploadLocalizeFiles(e.dataTransfer.files);
    });
    input.addEventListener('change', function() { uploadLocalizeFiles(input.files); });
  }
});

async function uploadLocalizeFiles(files) {
  for (var file of files) {
    var form = new FormData();
    form.append('file', file);
    try {
      await fetch(API + '/images/upload', { method: 'POST', body: form });
    } catch(e) {}
  }
  refreshLocalizeImages();
}

async function refreshLocalizeImages() {
  await loadLocalizeImages();
}

async function deleteLocalizeImage(imageId) {
  try {
    await fetch(API + '/images/' + imageId, { method: 'DELETE' });
    loadLocalizeImages();
  } catch(e) {
    console.error('删除失败:', e);
  }
}

async function loadLocalizeImages() {
  try {
    var resp = await fetch(API + '/images');
    var images = await resp.json();
    var grid = document.getElementById('localize-image-grid');
    if (images.length === 0) {
      grid.innerHTML = '<p style="color:#888;font-size:0.85rem;padding:1rem;text-align:center">暂无图像，请上传</p>';
      return;
    }
    grid.innerHTML = images.map(function(img) {
      return '<div class="image-card" data-id="' + img.id + '" onclick="selectLocalizeImage(' + img.id + ', \'' + img.filename + '\', \'' + img.original_name + '\')">' +
        '<img src="/images/' + img.filename + '">' +
        '<button class="delete-btn" onclick="event.stopPropagation(); deleteLocalizeImage(' + img.id + ')">&#10006;</button>' +
        '<div class="info"><div class="name">' + img.original_name + '</div></div></div>';
      }).join('');
    } catch(e) { console.error(e); }
    loadLatestLocalizeResult();
  }

  function _fixImagePath(path) {
    if (!path) return null;
    var fixed = path;
    if (!path.startsWith('/')) {
      fixed = path.replace('projections/', '');
      fixed = '/projections/' + fixed;
    }
    console.log('[DEBUG] _fixImagePath:', path, '->', fixed);
    return fixed;
  }

  async function loadLatestLocalizeResult() {
    try {
      var resp = await fetch(API + '/tasks?limit=5');
      var tasks = await resp.json();
      console.log('[DEBUG] loadLatestLocalizeResult tasks:', tasks.length);
      if (!tasks || tasks.length === 0) return;
      var latest = tasks.find(function(t) { return t.status === 'completed'; });
      console.log('[DEBUG] latest task:', latest ? latest.id : null, latest ? latest.status : null);
      if (!latest) return;
      var resultData = latest.result_json;
      console.log('[DEBUG] resultData exists:', !!resultData, 'has results:', resultData && !!resultData.results);
      if (!resultData || !resultData.results) return;
      var results = resultData.results;
      console.log('[DEBUG] results count:', results.length);
      var resultsEl = document.getElementById('localize-results');
      var html = '<div style="margin-top:1rem"><h4>📋 上次定位结果 (task #' + latest.id + ')</h4></div>';
      var matchNames = { 'flann': 'FLANN kd-tree', 'bf': 'BruteForce', 'flann_lowes': 'FLANN严格(0.6)', 'bf_cross': 'BF交叉验证', 'knn_rank': 'KNN Top-50', 'lightglue': 'LightGlue (深度学习)', 'loftr': 'LoFTR (深度学习)', 'salad_roma': 'SALAD+RoMa (v3)' };
      results.forEach(function(r, idx) {
        var compImg = _fixImagePath(r.comparison_image);
        html += '<div class="localize-card" style="background:#fff;border-radius:8px;padding:1rem;margin-top:0.8rem;box-shadow:0 1px 3px rgba(0,0,0,0.1)">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">';
        html += '<h4 style="margin:0">SIFT + ' + (matchNames[r.match_method] || r.match_method) + '</h4>';
        html += '<span class="status-badge ' + (r.success ? 'completed' : 'failed') + '">' + (r.success ? '✓ 成功' : '✗ 失败') + '</span>';
        html += '</div>';
        if (r.success) {
          html += '<p style="font-size:0.85rem;margin:0.3rem 0">内点: ' + r.inliers + '</p>';
           if (r.match_method === 'salad_roma' && r.all_candidates && r.all_candidates.length > 1) {
           html += '<details open style="margin:0.3rem 0"><summary style="cursor:pointer;color:#1976d2;font-weight:bold">迭代 ' + r.total_rounds + ' 轮 (点击展开)</summary>';
           html += '<table style="font-size:0.78rem;width:100%;border-collapse:collapse;margin-top:0.3rem">';
           html += '<tr style="background:#f5f5f5"><th>轮</th><th>相似度</th><th>内点</th><th>位姿</th><th>对比图</th></tr>';
           r.all_candidates.forEach(function(c, ci) {
             var isBest = c.is_best || ci === 0;
             var candCompImg = _fixImagePath(c.comparison_image);
             html += '<tr style="' + (isBest ? 'background:#e8f5e9' : '') + '">';
             html += '<td>' + c.round + (isBest ? '🏆' : '') + '</td>';
             html += '<td>' + c.salad_similarity.toFixed(4) + '</td>';
             html += '<td>' + c.pnp_inliers + '</td>';
             html += '<td style="font-size:0.65rem;font-family:monospace">[' + (c.translation || []).map(function(v) { return v.toFixed(1); }).join(',') + ']</td>';
             if (candCompImg) {
               html += '<td><a href="' + candCompImg + '" target="_blank"><img src="' + candCompImg + '" style="width:60px;height:auto;border-radius:2px;border:1px solid #ddd"></a></td>';
             } else {
               html += '<td style="font-size:0.7rem;color:#999">-</td>';
             }
             html += '</tr>';
           });
           html += '</table></details>';
        } else if (r.match_method === 'salad_roma' && r.total_rounds) {
            html += '<p style="font-size:0.82rem;color:#1976d2;font-weight:bold">迭代 ' + r.total_rounds + ' 轮';
            if (r.iter_history) {
              html += ' (';
              r.iter_history.forEach(function(h, i) { html += (i > 0 ? ' → ' : '') + h.salad_similarity.toFixed(2); });
              html += ')';
            }
            html += '</p>';
          }
          if (r.pose && r.pose.translation) {
            html += '<p style="font-size:0.82rem;color:#666;font-family:monospace">位姿: [' + r.pose.translation.map(function(v) { return v.toFixed(2); }).join(', ') + ']</p>';
          }
          if (compImg) {
            html += '<div style="margin-top:0.5rem;text-align:center">';
            html += '<img src="' + compImg + '" style="max-width:100%;max-height:300px;border-radius:4px;border:1px solid #ddd" onerror="this.style.display=\'none\'">';
            html += '<p style="font-size:0.75rem;color:#888;margin-top:0.2rem">左: 原图 | 右: 重投影 | 彩色连线: 匹配点</p>';
            html += '</div>';
          }
          html += '<button class="btn-refine" style="margin-top:0.5rem;padding:4px 12px;font-size:0.8rem;background:#ff9800;color:#fff;border:none;border-radius:4px;cursor:pointer" onclick="refinePose(this, ' + latest.id + ', ' + idx + ')">🔄 RoMa 优化</button>';
        } else {
          html += '<p style="font-size:0.85rem;color:#f44336">' + (r.error || '失败') + '</p>';
        }
        html += '</div>';
      });
      resultsEl.innerHTML = html;
    } catch(e) { console.error(e); }
  }

  function selectLocalizeImage(id, filename, name) {
  localizeSelectedId = id;
  document.getElementById('localize-selected').style.display = 'block';
  document.getElementById('localize-selected-thumb').src = '/images/' + filename;
  document.getElementById('localize-selected-name').textContent = name;
  // 高亮选中
  document.querySelectorAll('#localize-image-grid .image-card').forEach(function(c) {
    c.style.border = c.dataset.id == id ? '2px solid #e94560' : 'none';
  });
}

async function startLocalize() {
  var imageId = localizeSelectedId;
  if (!imageId) { alert('请先选择图像'); return; }
  if (!document.getElementById('localize-btn').dataset.colmapOk) {
    // 先检查是否有 COLMAP 数据
    try {
      var chk = await fetch(API + '/localize/check');
      var chkData = await chk.json();
      if (!chkData.available) {
        alert('定位不可用：缺少 COLMAP 数据（las/images.txt）。\n请先准备 COLMAP 数据后再使用此功能。');
        return;
      }
      document.getElementById('localize-btn').dataset.colmapOk = '1';
    } catch(e) {
      alert('检查 COLMAP 数据失败: ' + e.message);
      return;
    }
  }

  var btn = document.getElementById('localize-btn');
  var statusEl = document.getElementById('localize-status');
  var resultsEl = document.getElementById('localize-results');

  btn.disabled = true;
  btn.textContent = '定位中...';
  statusEl.classList.remove('hidden');
  statusEl.className = 'status loading';
  statusEl.textContent = '启动定位...';
  resultsEl.innerHTML = '';

  try {
    var resp = await fetch(API + '/localize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_id: imageId,
        feature_methods: ['sift'],
        match_methods: ['flann', 'bf', 'flann_lowes', 'bf_cross', 'knn_rank', 'lightglue', 'loftr', 'salad_roma'],
      }),
    });
    var text = await resp.text();
    var data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (parseErr) {
      console.error('localize start response is not valid json:', text);
      data = { error: text || 'unknown error' };
    }
    if (!resp.ok || data.error) {
      throw new Error(data.error || '定位启动失败');
    }
    if (!data.task_id) {
      throw new Error('未返回 task_id');
    }
    pollLocalize(data.task_id, btn, statusEl, resultsEl);
  } catch(e) {
    btn.disabled = false;
    btn.textContent = '开始定位';
    statusEl.textContent = '请求失败: ' + e.message;
    statusEl.className = 'status error';
  }
}

async function pollLocalize(taskId, btn, statusEl, resultsEl) {
  try {
    var resp = await fetch(API + '/localize/' + taskId);
    var text = await resp.text();
    var data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (parseErr) {
      console.error('localize status response is not valid json:', text);
      data = { status: 'failed', error: text || 'unknown error' };
    }

    if (data.status === 'running') {
      statusEl.textContent = '定位中... (' + (data.results || []).filter(function(r) { return r.success; }).length + '/6 完成)';
      setTimeout(function() { pollLocalize(taskId, btn, statusEl, resultsEl); }, 2000);
      return;
    }

    btn.disabled = false;
    btn.textContent = '开始定位';

    if (data.status === 'failed') {
      statusEl.textContent = '定位失败: ' + (data.error || '未知错误');
      statusEl.className = 'status error';
      return;
    }

    statusEl.textContent = '定位完成';
    statusEl.className = 'status success';

    // 渲染结果
    var html = '';
    var results = data.results || [];

    results.forEach(function(r, idx) {
      var compImg = _fixImagePath(r.comparison_image);
      html += '<div class="localize-card" style="background:#fff;border-radius:8px;padding:1rem;margin-top:0.8rem;box-shadow:0 1px 3px rgba(0,0,0,0.1)">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">';
      var matchNames = { 'flann': 'FLANN kd-tree', 'bf': 'BruteForce', 'flann_lowes': 'FLANN严格(0.6)', 'bf_cross': 'BF交叉验证', 'knn_rank': 'KNN Top-50', 'lightglue': 'LightGlue (深度学习)', 'loftr': 'LoFTR (深度学习)', 'salad_roma': 'SALAD+RoMa (v3)' };
      var matchName = matchNames[r.match_method] || r.match_method;
      html += '<h4 style="margin:0">SIFT + ' + matchName + '</h4>';
      html += '<span class="status-badge ' + (r.success ? 'completed' : 'failed') + '">' + (r.success ? '✓ 成功' : '✗ 失败') + '</span>';
      html += '</div>';

      if (r.success) {
        html += '<p style="font-size:0.85rem;margin:0.3rem 0">内点: ' + r.inliers + ' | 3D点数: ' + (r.total_3d_points || 0) + '</p>';
        // SALAD+RoMa 显示轮数
        if (r.match_method === 'salad_roma' && r.all_candidates && r.all_candidates.length > 1) {
          html += '<details open style="margin:0.3rem 0"><summary style="cursor:pointer;color:#1976d2;font-weight:bold">迭代 ' + r.total_rounds + ' 轮 (点击展开)</summary>';
          html += '<table style="font-size:0.78rem;width:100%;border-collapse:collapse;margin-top:0.3rem">';
          html += '<tr style="background:#f5f5f5"><th>轮</th><th>相似度</th><th>内点</th><th>位姿</th><th>对比图</th></tr>';
          r.all_candidates.forEach(function(c, ci) {
            var isBest = c.is_best || ci === 0;
            var candCompImg = _fixImagePath(c.comparison_image);
            html += '<tr style="' + (isBest ? 'background:#e8f5e9' : '') + '">';
            html += '<td>' + c.round + (isBest ? '🏆' : '') + '</td>';
            html += '<td>' + c.salad_similarity.toFixed(4) + '</td>';
            html += '<td>' + c.pnp_inliers + '</td>';
            html += '<td style="font-size:0.65rem;font-family:monospace">[' + (c.translation || []).map(function(v) { return v.toFixed(1); }).join(',') + ']</td>';
            if (candCompImg) {
              html += '<td><a href="' + candCompImg + '" target="_blank"><img src="' + candCompImg + '" style="width:60px;height:auto;border-radius:2px;border:1px solid #ddd"></a></td>';
            } else {
              html += '<td style="font-size:0.7rem;color:#999">-</td>';
            }
            html += '</tr>';
          });
          html += '</table></details>';
        } else if (r.match_method === 'salad_roma' && r.total_rounds) {
          html += '<p style="font-size:0.82rem;color:#1976d2;font-weight:bold">迭代 ' + r.total_rounds + ' 轮';
          if (r.iter_history) {
            html += ' (';
            r.iter_history.forEach(function(h, idx) {
              html += (idx > 0 ? ' → ' : '') + h.salad_similarity.toFixed(2);
            });
            html += ')';
          }
          html += '</p>';
        }
        if (r.pose && r.pose.translation) {
          html += '<p style="font-size:0.82rem;color:#666;font-family:monospace">位姿: [' + r.pose.translation.map(function(v) { return v.toFixed(2); }).join(', ') + ']</p>';
        }
        if (compImg) {
          html += '<div style="margin-top:0.5rem;text-align:center">';
          html += '<img src="' + compImg + '" style="max-width:100%;max-height:400px;border-radius:4px;border:1px solid #ddd" onerror="this.style.display=\'none\'">';
          html += '<p style="font-size:0.75rem;color:#888;margin-top:0.2rem">左: 原图 | 右: 重投影 | 彩色连线: 匹配点</p>';
          html += '</div>';
        }
        // 优化按钮（所有成功结果都显示）
        html += '<button class="btn-refine" data-task="' + taskId + '" data-idx="' + idx + '" style="margin-top:0.5rem;padding:4px 12px;font-size:0.8rem;background:#ff9800;color:#fff;border:none;border-radius:4px;cursor:pointer" onclick="refinePose(this, ' + taskId + ', ' + idx + ')">🔄 RoMa 优化</button>';
      } else {
        html += '<p style="font-size:0.85rem;color:#f44336">' + (r.error || '失败') + '</p>';
      }

      html += '</div>';
    });

    resultsEl.innerHTML = html;

  } catch(e) {
    btn.disabled = false;
    btn.textContent = '开始定位';
    statusEl.textContent = '查询失败: ' + e.message;
    statusEl.className = 'status error';
  }
}

async function refinePose(btn, taskId, methodIdx) {
  btn.disabled = true;
  btn.textContent = '优化中...';
  try {
    var resp = await fetch(API + '/localize/refine', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task_id: taskId, method_index: methodIdx}),
    });
    var text = await resp.text();
    var result = {};
    try {
      result = text ? JSON.parse(text) : {};
    } catch (parseErr) {
      console.error('refine response is not valid json:', text);
      result = { success: false, error: text || 'unknown error' };
    }
    if (!resp.ok || result.error) {
      throw new Error(result.error || '优化请求失败');
    }
    if (result.success) {
      var msg = '✅ 优化完成';
      if (result.improved) {
        msg += ' SALAD 相似度: ' + result.salad_sim_before.toFixed(3) + ' → ' + result.salad_sim_after.toFixed(3);
      } else {
        msg += ' 未提升 (相似度: ' + result.salad_sim_after.toFixed(3) + ')';
      }
      btn.textContent = msg;
      btn.style.background = result.improved ? '#4caf50' : '#888';
      setTimeout(function() { location.reload(); }, 1500);
    } else {
      btn.textContent = '❌ 优化失败: ' + (result.error || '未知错误');
      btn.style.background = '#f44336';
      btn.disabled = false;
    }
  } catch(e) {
    btn.textContent = '❌ 请求失败: ' + e.message;
    btn.style.background = '#f44336';
    btn.disabled = false;
  }
}
