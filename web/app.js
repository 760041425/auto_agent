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

  // ====== 标注的匹配点列表 ======
  if (report.matched_points && report.matched_points.length > 0) {
      html += '<div class="annotated-section"><h4>📍 图像标注点（' + report.matched_points.length + '个）</h4>';
    report.matched_points.forEach(function(p, idx) {
      var c3 = p.point3d;
      html += '<div class="annotated-point">';
      html += '<span class="point-label">' + (idx + 1) + '</span>';
      html += '<span class="point-pixel">像素 (' + p.query_pt[0].toFixed(0) + ', ' + p.query_pt[1].toFixed(0) + ')</span>';
      html += '<span class="point-coord">3D X=' + c3[0].toFixed(3) + ' Y=' + c3[1].toFixed(3) + ' Z=' + c3[2].toFixed(3) + '</span>';
      html += '</div>';
    });
    html += '</div>';

    // 在图像上绘制匹配点
    drawMatchedPoints(report.matched_points);
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
  try {
    var resp = await fetch(API + '/tasks');
    var tasks = await resp.json();
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

    list.innerHTML = tasks.map(function(t) {
      var html = '<div class="task-item">';
      html += '<div class="task-item-main" onclick="toggleTaskDetail(' + t.id + ')">';
      html += '<div><strong>任务#' + t.id + '</strong> 图像#' + t.image_id + '<br><small>' + new Date(t.created_at).toLocaleString() + '</small></div>';
      html += '<span class="status-badge ' + t.status + '">' + t.status + '</span>';
      html += '</div>';

      var r = reports[t.id];
      var img = images[t.image_id];
      if (r && r.matched) {
        var filename = img ? img.filename : '';
        html += '<div class="task-detail" id="task-detail-' + t.id + '" style="display:none">';
        // 图像 + canvas
        html += '<div class="task-detail-image-wrap">';
        html += '<img class="task-detail-image" id="task-img-' + t.id + '" src="/images/' + filename + '" data-task-id="' + t.id + '" style="display:none">';
        html += '<canvas class="task-detail-canvas" id="task-canvas-' + t.id + '" width="0" height="0"></canvas>';
        html += '</div>';
        // 摘要
        html += '<div class="task-detail-summary">';
        html += '<span>匹配 ' + r.total_matches + ' 点</span>';
        if (r.center_3d && r.center_3d.x != null) {
          html += '<span class="task-detail-3d">3D: X=' + r.center_3d.x.toFixed(2) + ' Y=' + r.center_3d.y.toFixed(2) + ' Z=' + r.center_3d.z.toFixed(2) + '</span>';
        }
        html += '</div>';
        // 标注点列表
        if (r.matched_points && r.matched_points.length > 0) {
          html += '<div class="task-detail-points">';
          r.matched_points.forEach(function(p, idx) {
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
  var el = document.getElementById('task-detail-' + id);
  if (el) {
    var isOpen = el.style.display === 'block';
    el.style.display = isOpen ? 'none' : 'block';

    // 展开时绘制标注点
    if (!isOpen) {
      var img = document.getElementById('task-img-' + id);
      if (img) {
        var filename = img.src;
        // 如果图片还没加载，等待加载完成后绘制
        if (img.complete && img.naturalWidth > 0) {
          drawTaskPoints(id);
        } else {
          img.onload = function() { drawTaskPoints(id); };
        }
      }
    }
  }
}

function drawTaskPoints(taskId) {
  var img = document.getElementById('task-img-' + taskId);
  var canvas = document.getElementById('task-canvas-' + taskId);
  if (!img || !canvas || !img.naturalWidth) return;

  canvas.width = Math.min(img.naturalWidth, 600);
  canvas.height = Math.min(img.naturalHeight, 400);
  var ctx = canvas.getContext('2d');

  // 从 report 获取匹配点
  fetch(API + '/tasks/' + taskId + '/report').then(function(r) { return r.json(); }).then(function(report) {
    var points = report.matched_points;
    if (!points) return;
    var scaleX = canvas.width / img.naturalWidth;
    var scaleY = canvas.height / img.naturalHeight;
    var colors = ['#e94560', '#ff6b35', '#ffc107', '#4caf50', '#2196f3',
                  '#9c27b0', '#00bcd4', '#ff4081', '#7c4dff', '#00e676'];

    points.forEach(function(p, idx) {
      var x = p.query_pt[0] * scaleX;
      var y = p.query_pt[1] * scaleY;
      var color = colors[idx % colors.length];
      ctx.beginPath();
      ctx.arc(x, y, 5, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 8px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(idx + 1, x, y);
    });
  }).catch(function() {});
}
