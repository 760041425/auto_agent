import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const API = '/api';
let currentImageId = null;

document.addEventListener('DOMContentLoaded', () => {
  // 恢复上次停留的 tab
  const savedTab = localStorage.getItem('activeTab');
  if (savedTab) {
    switchTab(savedTab);
  }

  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      localStorage.setItem('activeTab', tab);
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab-' + tab).classList.add('active');
      if (tab === 'list') loadImages();
      if (tab === 'tasks') loadTasks();
      if (tab === 'localize') loadLocalizeImages();
      if (tab === 'pointcloud') {
        try {
          initPointCloud();
        } catch(e) {
          console.error('initPointCloud error:', e);
        }
      }
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
  localStorage.setItem('activeTab', name);
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

async function startPreprocessBuild() {
  await startPreprocessFlow('build', 'build-btn', '下采样 + octree_build');
}

async function startPreprocessRender() {
  await startPreprocessFlow('render', 'render-btn', '仅渲染投影图');
}

async function startPreprocessFeature() {
  await startPreprocessFlow('feature', 'feature-btn', '仅重建 SALAD 特征');
}

async function startPreprocessAce() {
  await startPreprocessFlow('ace', 'ace-btn', '训练 ACE 模型');
}

async function startPreprocess() {
  await startPreprocessFlow('full', 'full-btn', '完整预处理');
}

async function startPreprocessFlow(mode, buttonId, buttonLabel) {
  var btn = document.getElementById(buttonId);
  var statusEl = document.getElementById('preprocess-status');
  var progressWrap = document.getElementById('preprocess-progress-wrap');
  var progressBar = document.getElementById('preprocess-progress-bar');

  btn.disabled = true;
  btn.textContent = '处理中...';
  statusEl.classList.remove('hidden');
  statusEl.className = 'status loading';
  statusEl.textContent = '启动' + buttonLabel + '...';
  progressWrap.classList.remove('hidden');
  progressBar.style.width = '0%';

  try {
    var endpoint = mode === 'build' ? '/las/preprocess/build' 
                 : mode === 'render' ? '/las/preprocess/render'
                 : mode === 'feature' ? '/las/preprocess/feature'
                 : mode === 'ace' ? '/las/preprocess/ace'
                 : '/las/preprocess';
    var resp = await fetch(API + endpoint, { method: 'POST' });
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

    pollPreprocessStatus(btn, statusEl, progressWrap, progressBar, buttonLabel);
  } catch(e) {
    btn.disabled = false;
    btn.textContent = buttonLabel;
    statusEl.textContent = '启动失败: ' + e.message;
    statusEl.className = 'status error';
  }
}

async function pollPreprocessStatus(btn, statusEl, progressWrap, progressBar, buttonLabel) {
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
      btn.textContent = buttonLabel;
      statusEl.textContent = '❌ ' + s.error;
      statusEl.className = 'status error';
      return;
    }

    if (s.running) {
      setTimeout(function() { pollPreprocessStatus(btn, statusEl, progressWrap, progressBar, buttonLabel); }, 1000);
    } else {
      btn.disabled = false;
      btn.textContent = buttonLabel;
      if (s.progress >= 100) {
        statusEl.textContent = '✅ ' + (s.step || '预处理完成');
        statusEl.className = 'status success';
      }
    }
  } catch(e) {
    btn.disabled = false;
    btn.textContent = buttonLabel;
    statusEl.textContent = '查询状态失败: ' + e.message;
    statusEl.className = 'status error';
  }
}

// ====== 视觉定位 ======

var localizeSelectedId = null;
var localizeAlgorithmNames = {
  'flann': 'SIFT + FLANN kd-tree',
  'salad_roma': 'SALAD+RoMa (原版)',
  'salad_roma_v2': 'SALAD v2 (DISK+LightGlue)',
  'salad_roma_v2_loftr': 'SALAD v2 + LoFTR',
  'hybrid': 'Hybrid (DISK+LightGlue + LoFTR)',
  'ace_las': 'ACE + LAS 验证',
  'multi_strategy': 'Multi-Strategy 融合',
  'salad_lightglue': 'SALAD+LightGlue',
  'ace': 'ACE 场景坐标回归',
  // 009 加速方案（原方案不动，新增对比项）
  'salad_v2_loftr_fast': 'SALAD v2 + LoFTR [加速]',
  'salad_v2_hybrid_fast': 'Hybrid (DISK+LG + LoFTR) [加速]',
  'salad_v2_xfeat': 'SALAD v2 + XFeat'
};

function escapeLocalizeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function localizeErrorText(error) {
  if (!error) return '定位无解';
  if (typeof error === 'string') return error;
  return error.message || error.detail || error.code || '定位失败';
}

function localizeStatusBadge(result) {
  if (!result.success) {
    return '<span class="status-badge failed">✗ 失败</span>';
  }
  var coordinate = result.coordinate_transform || (result.validations || {}).coordinate_crosscheck;
  var consistency = coordinate && coordinate.consistency;
  var reliable = !!(coordinate && coordinate.status === 'ready'
    && consistency && consistency.status === 'available' && consistency.passed);
  if (reliable) {
    return '<span class="status-badge completed">✓ 可信</span>';
  }
  if (consistency && consistency.status === 'available') {
    return '<span class="status-badge" style="background:#fff3e0;color:#e65100">⚠ 低可信</span>';
  }
  return '<span class="status-badge" style="background:#fce4ec;color:#c62828">⚠ 无法判定</span>';
}

const LOCALIZE_REASON_MESSAGES = {
  'coordinate_transform_context_not_ready': '坐标变换上下文未就绪',
  'coordinate_transform_artifact_unavailable': '坐标变换产物不可用',
  'insufficient_valid_projection_pixels': '有效投影像素不足',
  'insufficient_valid_homography_samples': '有效单应样本不足'
};

function renderCoordinateReliabilityDecision(result) {
  var coordinate = result.coordinate_transform || (result.validations || {}).coordinate_crosscheck || {};
  var consistency = coordinate.consistency || {};
  var available = consistency.status === 'available';
  var passed = available && consistency.passed;
  var background = passed ? '#e8f5e9' : '#ffebee';
  var border = passed ? '#2e7d32' : '#c62828';
  var html = '<div style="margin:0.6rem 0;padding:0.65rem;background:' + background + ';border-radius:4px;border-left:4px solid ' + border + '">';
  html += '<p style="font-size:0.86rem;font-weight:bold;margin:0 0 0.35rem">坐标差最终判定</p>';
  if (available) {
    html += '<p style="font-size:0.8rem;margin:0.2rem 0">中位坐标差: <b>' + Number(consistency.median_m).toFixed(3) + ' m</b></p>';
    html += '<p style="font-size:0.78rem;margin:0.2rem 0">P95: <b>' + Number(consistency.p95_m).toFixed(3) + ' m</b>；样本: <b>' + consistency.sample_count + '</b></p>';
    html += '<p style="font-size:0.78rem;margin:0.2rem 0">判定门槛: <b>&lt; ' + Number(consistency.threshold_m).toFixed(3) + ' m</b>；结论: <b>' + (passed ? '通过 / 可信' : '未通过 / 不准') + '</b></p>';
  } else {
    var reason = consistency.reason || coordinate.reason;
    var causeText = (reason && LOCALIZE_REASON_MESSAGES[reason])
      ? '：' + LOCALIZE_REASON_MESSAGES[reason]
      : '：该算法未生成多点坐标差产物，需重新定位';
    html += '<p style="font-size:0.78rem;margin:0.2rem 0">未生成可用的多点坐标差' + causeText + '，按最终标准判定为低可信；请重新定位。</p>';
  }
  html += '<p style="font-size:0.7rem;color:#666;margin:0.3rem 0 0">最终可信状态只由本地 H→SLAM 与最终位姿 NPY 的多点中位坐标差决定；内点数和相似度仅作辅助诊断。</p></div>';
  return html;
}

function localizeDiagnosticLine(r) {
  var quality = r.quality || {};
  var inliers = r.inliers != null
    ? r.inliers
    : (quality.inlier_count != null ? quality.inlier_count : '—');
  var total3d = r.total_3d_points != null
    ? r.total_3d_points
    : (quality.match_count != null ? quality.match_count : '—');
  return '<p style="font-size:0.78rem;color:#777;margin:0.3rem 0">辅助诊断：内点 ' + inliers + ' | 3D点数 ' + total3d;
}

function localizeFailureDiagLine(r) {
  // TL-007-06 (specs/007 AC-007-06)：失败结果含 diagnostics 时展示
  // 「内点 X | 重投影误差 Y px | 预测Z [a, b]」（保留 2 位小数）；
  // 字段缺失/为 None 时对应段渲染 "—"；不含 diagnostics（旧结构兜底）
  // 时沿用原失败文案 localizeErrorText(r.error)，不崩溃。
  if (!r.diagnostics) return localizeErrorText(r.error);
  var pnp = r.diagnostics.pnp || {};
  var pred = r.diagnostics.pred_xyz || {};
  var inliers = pnp.best_inliers != null ? pnp.best_inliers : '—';
  var reproj = pnp.best_reproj_error_px != null
    ? Number(pnp.best_reproj_error_px).toFixed(2) + ' px'
    : '—';
  var z = (pred.z_min != null && pred.z_max != null)
    ? '[' + Number(pred.z_min).toFixed(2) + ', ' + Number(pred.z_max).toFixed(2) + ']'
    : '—';
  return '内点 ' + inliers + ' | 重投影误差 ' + reproj + ' | 预测Z ' + z;
}

function renderProjectionConsistency(result) {
  var validations = result.validations || {};
  var value = validations.projection_consistency || result.projection_verification;
  if (!value) return '';
  var fit = value.homography_fit;
  var html = '<div style="margin:0.5rem 0;padding:0.5rem;background:#e3f2fd;border-radius:4px;border-left:3px solid #1976d2">';
  html += '<p style="font-size:0.82rem;font-weight:bold;margin:0 0 0.3rem">2D 几何拟合诊断（非 Benchmark）</p>';
  if (fit && fit.status === 'available') {
    html += '<p style="font-size:0.78rem;margin:0.2rem 0">单应内点: <b>' + (fit.n_inliers || 0) + '/' + (fit.n_matches || 0) + '</b></p>';
    if (fit.inlier_median_residual_px != null) html += '<p style="font-size:0.78rem;margin:0.2rem 0">内点中位残差: <b>' + Number(fit.inlier_median_residual_px).toFixed(3) + ' px</b></p>';
  } else {
    html += '<p style="font-size:0.78rem;margin:0.2rem 0">未生成：本轮诊断匹配点不足或未执行。</p>';
  }
  html += '<p style="font-size:0.72rem;color:#666;margin:0.25rem 0 0">同源 NPY 不能作为米制验证，已禁止显示同源米制自比较结果。</p></div>';
  return html;
}

function renderGroundTruthBenchmark(result) {
  var truth = (result.validations || {}).ground_truth || {};
  var html = '<div style="margin:0.5rem 0;padding:0.5rem;background:#fff8e1;border-radius:4px;border-left:3px solid #ff9800">';
  html += '<p style="font-size:0.82rem;font-weight:bold;margin:0 0 0.3rem">独立真值 Benchmark</p>';
  if (truth.status === 'available') {
    html += '<p style="font-size:0.78rem;margin:0.2rem 0">平移误差: <b>' + Number(truth.translation_error_m).toFixed(3) + ' m</b>；旋转误差: <b>' + Number(truth.rotation_error_deg).toFixed(3) + '°</b></p>';
  } else {
    html += '<p style="font-size:0.78rem;margin:0.2rem 0">未执行：未提供与地图/算法输入解耦的 holdout 位姿真值（Phase B TODO）。</p>';
  }
  html += '</div>';
  return html;
}

function formatCoordinateXYZ(value, keys) {
  if (!value) return '不可用';
  return '[' + keys.map(function(key) {
    return value[key] == null ? '-' : Number(value[key]).toFixed(3);
  }).join(', ') + ']';
}

async function verifyCoordinatePoint(event, taskId, resultIndex, targetId) {
  event.preventDefault();
  event.stopPropagation();
  var image = event.currentTarget;
  var rect = image.getBoundingClientRect();
  var u = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  var v = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
  var target = document.getElementById(targetId);
  if (!target) return;
  target.innerHTML = '<span style="color:#1976d2">正在查询 (' + u.toFixed(4) + ', ' + v.toFixed(4) + ')...</span>';

  try {
    var response = await fetch(API + '/localize/coordinate-transform', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task_id: taskId, result_index: resultIndex, u: u, v: v}),
    });
    var payload = await response.json();
    if (!response.ok) {
      var detail = payload.detail || payload.error || '本地坐标转换产物不可用';
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    if (payload.status !== 'available') {
      throw new Error(payload.reason || '坐标源不完整');
    }
    let html = '<div style="font-weight:bold;margin-bottom:0.25rem">坐标交叉验证</div>';
    html += '<div>选点: <b>(' + payload.u.toFixed(4) + ', ' + payload.v.toFixed(4) + ')</b></div>';
    if (payload.slam_xy) {
      html += '<div>计算 XY: <b>(' + payload.slam_xy[0].toFixed(2) + ', ' + payload.slam_xy[1].toFixed(2) + ') m</b></div>';
    }
    if (payload.npy_xyz) {
      html += '<div>NPY XYZ: <b>(' + payload.npy_xyz[0].toFixed(2) + ', ' + payload.npy_xyz[1].toFixed(2) + ', ' + payload.npy_xyz[2].toFixed(2) + ')</b></div>';
    }
    if (payload.error_m !== undefined && payload.error_m !== null) {
      html += '<div>坐标误差: <b>' + payload.error_m.toFixed(3) + ' m</b></div>';
    }
    if (payload.error_px !== undefined && payload.error_px !== null) {
      html += '<div style="font-size:0.7rem;color:#666">重投影像素误差: ' + payload.error_px.toFixed(1) + ' px</div>';
    }
    html += '<div style="font-size:0.7rem;color:#666;margin-top:0.2rem">PnP位姿+射线平面求交 vs NPY</div>';
    target.innerHTML = html;
  } catch (error) {
    target.innerHTML = '<span style="color:#c62828">坐标交叉验证失败：' + escapeLocalizeHtml(error.message) + '</span>';
  }
}

function renderLocalizationArtifacts(result, maxHeight, taskId, resultIndex) {
  var artifacts = result.artifacts || {};
  var queryImage = _fixImagePath(result.query_image || artifacts.query_image);
  var reprojectionImage = _fixImagePath(result.reprojection_image || artifacts.reprojection_image);
  var comparisonImage = _fixImagePath(result.comparison_image || artifacts.comparison_image);
  var height = maxHeight || 300;
  var coordinateTransform = result.coordinate_transform || (result.validations || {}).coordinate_crosscheck || {};
  var coordinateReady = coordinateTransform.status === 'ready';
  var verificationId = 'coordinate-crosscheck-' + taskId + '-' + resultIndex;
  var html = '';

  if (queryImage && reprojectionImage) {
    html += '<div class="localization-artifacts" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0.7rem;margin-top:0.7rem">';
    html += '<figure style="margin:0;text-align:center"><figcaption style="font-size:0.78rem;font-weight:bold;margin-bottom:0.25rem">查询图像</figcaption>';
    if (coordinateReady && taskId != null) {
      html += '<img src="' + queryImage + '" alt="查询图像（点击取点）" title="点击图像执行本地坐标交叉验证" onclick="verifyCoordinatePoint(event,' + taskId + ',' + resultIndex + ',\'' + verificationId + '\')" style="max-width:100%;max-height:' + height + 'px;border-radius:4px;border:2px solid #1976d2;cursor:crosshair">';
      html += '<div style="font-size:0.72rem;color:#1976d2;margin-top:0.2rem">点击查询图取点，比较 H→SLAM XYZ 与 NPY XYZ</div>';
    } else {
      html += '<a href="' + queryImage + '" target="_blank"><img src="' + queryImage + '" alt="查询图像" style="max-width:100%;max-height:' + height + 'px;border-radius:4px;border:1px solid #ddd"></a>';
    }
    html += '</figure>';
    html += '<figure style="margin:0;text-align:center"><figcaption style="font-size:0.78rem;font-weight:bold;margin-bottom:0.25rem">最终位姿投影</figcaption>';
    html += '<a href="' + reprojectionImage + '" target="_blank"><img src="' + reprojectionImage + '" alt="最终位姿投影" style="max-width:100%;max-height:' + height + 'px;border-radius:4px;border:1px solid #ddd"></a></figure></div>';
    if (comparisonImage) {
      html += '<details style="margin-top:0.45rem"><summary style="cursor:pointer;font-size:0.78rem;color:#1976d2">查看双图对比</summary>';
      html += '<a href="' + comparisonImage + '" target="_blank"><img src="' + comparisonImage + '" alt="查询图与最终投影对比" style="max-width:100%;max-height:' + height + 'px;margin-top:0.35rem;border-radius:4px;border:1px solid #ddd"></a></details>';
    }
    if (coordinateReady && taskId != null) {
      html += '<div id="' + verificationId + '" style="margin-top:0.5rem;padding:0.55rem;background:#eef7ee;border-left:3px solid #43a047;border-radius:4px;font-size:0.78rem">坐标交叉验证（非绝对精度）：本地 H 内点 <b>' + (coordinateTransform.n_inliers || 0) + '/' + (coordinateTransform.n_matches || 0) + '</b>，请点击查询图像选点。</div>';
    } else {
      html += '<div style="margin-top:0.5rem;padding:0.55rem;background:#fff3e0;border-left:3px solid #fb8c00;border-radius:4px;font-size:0.78rem;color:#e65100">本地坐标转换产物不可用：历史结果需重新定位后生成。</div>';
    }
    return html;
  }

  if (comparisonImage) {
    return '<div style="margin-top:0.5rem;text-align:center"><img src="' + comparisonImage + '" alt="查询图与最终投影对比" style="max-width:100%;max-height:' + height + 'px;border-radius:4px;border:1px solid #ddd"><p style="font-size:0.75rem;color:#888;margin-top:0.2rem">左：查询图像｜右：最终位姿投影</p></div>';
  }

  var generation = (result.validations || {}).artifact_generation || {};
  var reason = generation.error || generation.reason || '该结果未返回视觉产物';
  return '<p class="artifact-missing" style="font-size:0.78rem;color:#e65100;background:#fff3e0;padding:0.45rem;border-radius:4px">视觉产物未生成：' + escapeLocalizeHtml(reason) + '。历史结果需重新定位后生成。</p>';
}

async function runE2ETest() {
  var btn = document.getElementById('e2e-test-btn');
  var status = document.getElementById('e2e-test-status');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = '⏳ 运行中...';
  status.textContent = '';
  try {
    var r = await fetch(API + '/localize/verify-e2e', { method: 'POST' });
    var data = await r.json();
    if (data.success) {
      btn.textContent = '✅ 通过';
      status.innerHTML = '<span style="color:#2e7d32">端到端回归测试全部通过（' +
        (data.stdout.match(/(\d+) passed/)?.[1] || '?') + ' tests）</span>';
    } else {
      btn.textContent = '❌ 失败';
      var failInfo = (data.stdout.match(/FAILED.*/g) || []).join('<br>');
      status.innerHTML = '<span style="color:#c62828">测试失败：</span><br><code style="font-size:0.72rem">' +
        escapeLocalizeHtml(failInfo || data.error || data.stdout.slice(-500)) + '</code>';
    }
  } catch (e) {
    btn.textContent = '❌ 错误';
    status.textContent = '调用失败: ' + e.message;
  } finally {
    setTimeout(function () {
      btn.disabled = false;
      btn.textContent = '🧪 运行回归测试';
    }, 3000);
  }
}

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
      results.forEach(function(r, idx) {
        html += '<div class="localize-card" style="background:#fff;border-radius:8px;padding:1rem;margin-top:0.8rem;box-shadow:0 1px 3px rgba(0,0,0,0.1)">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">';
        html += '<h4 style="margin:0">' + escapeLocalizeHtml(localizeAlgorithmNames[r.algorithm_id || r.match_method] || r.algorithm_id || r.match_method) + '</h4>';
        html += localizeStatusBadge(r);
        html += '</div>';
        if (r.success) {
          html += localizeDiagnosticLine(r);
          if (r.timings && r.timings.total_s != null) {
            html += ' &nbsp;·&nbsp; ⏱ ' + r.timings.total_s.toFixed(2) + 's';
          }
          html += '</p>';
          if (r.quality_passed === true) {
            html += '<span style="font-size:0.75rem;color:#2e7d32;font-weight:bold">✓ 质量通过 (score=' + (r.quality_score || r.score || 0).toFixed(1) + ')</span>';
          } else if (r.quality_passed === false) {
            var qReasons = (r.quality_reasons && r.quality_reasons.length) ? r.quality_reasons.join(', ') : '未达标';
            html += '<span style="font-size:0.75rem;color:#e65100;font-weight:bold">✗ 质量不通过: ' + escapeLocalizeHtml(qReasons) + '</span>';
          }
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
          html += renderCoordinateReliabilityDecision(r);
          html += '<details style="margin:0.4rem 0"><summary style="cursor:pointer;color:#666;font-size:0.78rem">辅助几何诊断与 Benchmark（不作为最终判定）</summary>';
          html += renderProjectionConsistency(r);
          html += renderGroundTruthBenchmark(r);
          // LAS 验证结果
          if (r.las_verification && r.las_verification.total > 0) {
            var lv = r.las_verification;
            html += '<p style="font-size:0.78rem;margin:0.2rem 0">LAS 验证: <b>' + lv.verified + '/' + lv.total + '</b> 通过 (mean ' + (lv.mean_distance_m || 0).toFixed(2) + 'm)</p>';
          }
          html += '</details>';
          html += renderLocalizationArtifacts(r, 300, latest.id, idx);
          html += '<button class="btn-refine" style="margin-top:0.5rem;padding:4px 12px;font-size:0.8rem;background:#ff9800;color:#fff;border:none;border-radius:4px;cursor:pointer" onclick="refinePose(this, ' + latest.id + ', ' + idx + ')">🔄 RoMa 优化</button>';
          html += ' <button style="margin-top:0.5rem;padding:4px 12px;font-size:0.8rem;background:#1976d2;color:#fff;border:none;border-radius:4px;cursor:pointer" onclick="generateVerifyReport(' + latest.image_id + ')">📐 生成 2D 拟合报告</button>';
        } else {
          html += '<p style="font-size:0.85rem;color:#f44336">' + escapeLocalizeHtml(localizeFailureDiagLine(r)) + '</p>';
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
  var algorithms = Array.prototype.map.call(document.querySelectorAll('#localize-algorithms input[type="checkbox"]:checked:not(#localize-debug):not(#localize-verify)'), function(input) { return input.value; });
  if (!algorithms.length) { alert('请至少选择一种定位算法'); return; }

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
        algorithms: algorithms,
        max_iterations: 2,
        debug_visualizations: document.getElementById('localize-debug').checked,
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
      statusEl.textContent = '定位中... (' + (data.results || []).length + '/' + (data.total || '?') + ' 完成)';
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
      html += '<div class="localize-card" style="background:#fff;border-radius:8px;padding:1rem;margin-top:0.8rem;box-shadow:0 1px 3px rgba(0,0,0,0.1)">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">';
      var algorithmId = r.algorithm_id || r.match_method;
      var matchName = localizeAlgorithmNames[algorithmId] || algorithmId;
      html += '<h4 style="margin:0">' + escapeLocalizeHtml(matchName) + '</h4>';
      html += localizeStatusBadge(r);
      html += '</div>';

      if (r.success) {
        html += localizeDiagnosticLine(r);
        if (r.timings && r.timings.total_s != null) {
          html += ' &nbsp;·&nbsp; ⏱ ' + r.timings.total_s.toFixed(2) + 's';
        }
        html += '</p>';
        if (r.quality_passed === true) {
          html += '<span style="font-size:0.75rem;color:#2e7d32;font-weight:bold">✓ 质量通过 (score=' + (r.quality_score || r.score || 0).toFixed(1) + ')</span>';
        } else if (r.quality_passed === false) {
          var qReasons = (r.quality_reasons && r.quality_reasons.length) ? r.quality_reasons.join(', ') : '未达标';
          html += '<span style="font-size:0.75rem;color:#e65100;font-weight:bold">✗ 质量不通过: ' + escapeLocalizeHtml(qReasons) + '</span>';
        }
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
        if (!r.reliable) {
          html += '<p style="font-size:0.78rem;color:#e65100">求解已返回位姿，但质量门槛未满足，请勿作为可信定位结果使用。</p>';
        }
        html += renderCoordinateReliabilityDecision(r);
        html += '<details style="margin:0.4rem 0"><summary style="cursor:pointer;color:#666;font-size:0.78rem">辅助几何诊断与 Benchmark（不作为最终判定）</summary>';
        html += renderProjectionConsistency(r);
        html += renderGroundTruthBenchmark(r);
        html += '</details>';
        html += renderLocalizationArtifacts(r, 400, taskId, idx);
        // 优化按钮（所有成功结果都显示）
        html += '<button class="btn-refine" data-task="' + taskId + '" data-idx="' + idx + '" style="margin-top:0.5rem;padding:4px 12px;font-size:0.8rem;background:#ff9800;color:#fff;border:none;border-radius:4px;cursor:pointer" onclick="refinePose(this, ' + taskId + ', ' + idx + ')">🔄 RoMa 优化</button>';
      } else {
        html += '<p style="font-size:0.85rem;color:#f44336">' + escapeLocalizeHtml(localizeFailureDiagLine(r)) + '</p>';
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

async function generateVerifyReport(imageId) {
  if (!imageId) {
    alert('请先选择一张图像');
    return;
  }
  var statusEl = document.getElementById('localize-status');
  statusEl.classList.remove('hidden');
  statusEl.className = 'status loading';
  statusEl.textContent = '生成 2D 拟合报告（约 30s）...';
  try {
    // 调用后端 API 生成报告
    var resp = await fetch(API + '/localize/verify-report', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({image_id: imageId}),
    });
    var result = await resp.json();
    if (result.success) {
      statusEl.className = 'status completed';
      statusEl.textContent = '✅ 报告已生成';
      // 打开报告
      window.open('/' + result.report_path, '_blank');
    } else {
      statusEl.className = 'status failed';
      statusEl.textContent = '❌ ' + (result.error || result.detail || '生成失败');
    }
  } catch(e) {
    statusEl.className = 'status failed';
    statusEl.textContent = '❌ 请求失败: ' + e.message;
  }
}

// =====================================================================
// 3D 点云瓦片加载
// =====================================================================


// =====================================================================
// 3D 点云（简化版，全自动加载）
// =====================================================================
let pcScene, pcCamera, pcRenderer, pcControls, pcTileCache = {}, pcTileMeta = [], pcTileSize = 50, pcInitialized = false, pcLoading = false;
let pcLoadingTiles = false;       // 防止并发加载瓦片
let pcLoadTimer = null;           // 防抖定时器

function initPointCloud() {
  console.log('[PC] initPointCloud called, pcInitialized=', pcInitialized);
  if (pcInitialized) {
    // 已初始化但可能没加载数据（首次 tab 切换）
    if (!Object.keys(pcTileCache).length && !pcLoading) {
      console.log('[PC] already initialized but no tiles, loading...');
      loadPointCloud();
    }
    return;
  }
  pcInitialized = true;

  // 等待容器可见后初始化（tab 切换后 display: none → block）
  let retries = 0;
  const tryInit = () => {
    const c = document.getElementById('pc-canvas-container');
    console.log('[PC] tryInit retry=' + retries + ', clientWidth=' + (c ? c.clientWidth : 'null'));
    if (!c || c.clientWidth === 0) {
      if (++retries < 50) return setTimeout(tryInit, 100);
      console.warn('[PC] container not visible after 5s');
      return;
    }
    initScene(c);
  };

  function initScene(c) {
    console.log('[PC] initScene, container size=' + c.clientWidth + 'x' + c.clientHeight);
    pcScene = new THREE.Scene();
    pcScene.background = new THREE.Color(0x1a1a2e);
    pcCamera = new THREE.PerspectiveCamera(60, c.clientWidth / c.clientHeight, 0.1, 10000);
    pcRenderer = new THREE.WebGLRenderer({ antialias: true });
    pcRenderer.setSize(c.clientWidth, c.clientHeight);
    pcRenderer.setPixelRatio(window.devicePixelRatio);
    c.appendChild(pcRenderer.domElement);
    pcControls = new OrbitControls(pcCamera, pcRenderer.domElement);
    pcControls.enableDamping = true;
    pcScene.add(new THREE.AxesHelper(30));
    pcScene.add(new THREE.GridHelper(300, 30, 0x444466, 0x333355));
    console.log('[PC] scene initialized');

    loadPointCloud();

    addEventListener('resize', () => {
      if (!pcCamera) return;
      pcCamera.aspect = c.clientWidth / c.clientHeight;
      pcCamera.updateProjectionMatrix();
      pcRenderer.setSize(c.clientWidth, c.clientHeight);
    });
    // 防抖：OrbitControls change 事件在高频触发时，延迟 300ms 才真正加载
    pcControls.addEventListener('change', () => {
      if (pcLoadTimer) clearTimeout(pcLoadTimer);
      pcLoadTimer = setTimeout(() => {
        pcLoadTimer = null;
        loadNearbyTiles();
      }, 300);
    });
    (function animate(){ requestAnimationFrame(animate); if(pcControls) pcControls.update(); if(pcRenderer) pcRenderer.render(pcScene, pcCamera); })();
  }

  // 启动：延迟 50ms 后尝试初始化
  setTimeout(tryInit, 50);
  console.log('[PC] initPointCloud done, waiting for tryInit');
}

async function loadPointCloud() {
  console.log('[PC] loadPointCloud start');
  if (pcLoading) { console.log('[PC] already loading, skip'); return; }
  pcLoading = true;
  document.getElementById('pc-loading').style.display = 'block';
  for (const k in pcTileCache) { pcScene.remove(pcTileCache[k]); pcTileCache[k].geometry.dispose(); pcTileCache[k].material.dispose(); }
  pcTileCache = {};
  pcTileMeta = [];
  try {
    const url = `/api/point-cloud/tiles?tile_size=${pcTileSize}`;
    console.log('[PC] fetching:', url);
    const d = await fetch(url).then(r => r.json());
    console.log('[PC] tiles response:', d.tiles ? d.tiles.length : 'no tiles', 'error:', d.error);
    if (d.error) { document.getElementById('pc-loading').textContent = '错误: ' + d.error; pcLoading = false; return; }
    pcTileMeta = d.tiles;
    document.getElementById('pc-total').textContent = d.total_points.toLocaleString();
    document.getElementById('pc-tiles').textContent = pcTileMeta.length;
    document.getElementById('pc-stats').style.display = 'block';
    document.getElementById('pc-loading').style.display = 'none';
    const xs = pcTileMeta.flatMap(t => [t.min[0], t.max[0]]);
    const ys = pcTileMeta.flatMap(t => [t.min[1], t.max[1]]);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const cz = (Math.min(...pcTileMeta.flatMap(t=>[t.min[2],t.max[2]])) + Math.max(...pcTileMeta.flatMap(t=>[t.min[2],t.max[2]]))) / 2;
    const sz = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
    console.log('[PC] center:', cx.toFixed(1), cy.toFixed(1), cz.toFixed(1), 'size:', sz.toFixed(1));
    pcControls.target.set(cx, cy, cz);
    pcCamera.position.set(cx + sz * 0.8, cy + sz * 0.8, cz + sz * 0.8);
    pcControls.update();
    await loadNearbyTiles();
  } catch(e) {
    document.getElementById('pc-loading').textContent = '失败: ' + e.message;
  }
  pcLoading = false;
}

async function loadNearbyTiles() {
  if (!pcTileMeta.length || !pcControls || pcLoading) { console.log('[PC] loadNearbyTiles skip: meta=' + pcTileMeta.length + ' loading=' + pcLoading); return; }
  if (pcLoadingTiles) { console.log('[PC] loadNearbyTiles already running, skip'); return; }
  pcLoadingTiles = true;
  const camX = pcControls.target.x, camY = pcControls.target.y;
  const cix = Math.floor(camX / pcTileSize), ciy = Math.floor(camY / pcTileSize);
  console.log('[PC] loadNearbyTiles: cam=' + camX.toFixed(1) + ',' + camY.toFixed(1) + ' tile=' + cix + ',' + ciy);
  const wanted = new Set();
  for (let dx = -2; dx <= 2; dx++) for (let dy = -2; dy <= 2; dy++) wanted.add(`${cix+dx},${ciy+dy}`);
  for (const k of Object.keys(pcTileCache)) if (!wanted.has(k)) {
    pcScene.remove(pcTileCache[k]); pcTileCache[k].geometry.dispose(); pcTileCache[k].material.dispose(); delete pcTileCache[k];
  }
  let loaded = 0;
  for (const k of wanted) {
    if (pcTileCache[k]) continue;
    const [ix, iy] = k.split(',').map(Number);
    if (!pcTileMeta.some(t => t.ix === ix && t.iy === iy)) continue;
    try {
      const d = await fetch(`/api/point-cloud/tile/${ix}/${iy}?tile_size=${pcTileSize}&max_points=50000`).then(r=>r.json());
      if (d.error || !d.points.length) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(d.points.flat()), 3));
      g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(d.colors.flat()), 3));
      const pts = new THREE.Points(g, new THREE.PointsMaterial({ size: 0.8, vertexColors: true, sizeAttenuation: true }));
      pcScene.add(pts);
      pcTileCache[k] = pts;
      loaded++;
    } catch(e) { console.warn('[PC] tile ' + k + ' error:', e); }
  }
  console.log('[PC] loadNearbyTiles done, loaded=' + loaded + ' total cached=' + Object.keys(pcTileCache).length);
  document.getElementById('pc-loaded-tiles').textContent = Object.keys(pcTileCache).length;
  document.getElementById('pc-loaded-pts').textContent = Object.values(pcTileCache).reduce((s,p)=>s+p.geometry.attributes.position.count,0).toLocaleString();
  pcLoadingTiles = false;
}

// 暴露给 inline onclick 处理程序（模块作用域内全局不可见）
window.showDetail = showDetail;
window.startCompare = startCompare;
window.startCompareFromList = startCompareFromList;
window.toggleTaskDetail = toggleTaskDetail;
window.selectLocalizeImage = selectLocalizeImage;
window.startPreprocessBuild = startPreprocessBuild;
window.startPreprocessRender = startPreprocessRender;
window.startPreprocessFeature = startPreprocessFeature;
window.startPreprocessAce = startPreprocessAce;
window.startPreprocess = startPreprocess;
window.refreshLocalizeImages = refreshLocalizeImages;
window.startLocalize = startLocalize;
window.runE2ETest = runE2ETest;
window.verifyCoordinatePoint = verifyCoordinatePoint;
window.refinePose = refinePose;
window.generateVerifyReport = generateVerifyReport;
