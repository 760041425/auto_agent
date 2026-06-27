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
      '<div class="image-card" onclick="showDetail(' + img.id + ')">' +
      '<img src="/images/' + img.filename + '">' +
      '<div class="info"><div class="name">' + img.original_name + '</div>' +
      '<div class="status ' + img.status + '">' + img.status + '</div></div></div>'
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

function renderReport(report, task) {
  var el = document.getElementById('compare-result');
  var content = document.getElementById('result-content');
  var html = '';

  html += '<div class="report-summary">';
  html += '<p>匹配: <strong style="color:' + (report.matched ? '#4caf50' : '#f44336') + '">' + (report.matched ? '成功' : '失败') + '</strong></p>';
  html += '<p>匹配点数: <strong>' + (report.total_matches || 0) + '</strong></p>';
  if (report.confidence != null) {
    html += '<p>置信度: <strong>' + (report.confidence * 100).toFixed(1) + '%</strong></p>';
  }
  if (report.center_3d && report.center_3d.x != null) {
    html += '<p>3D 中心: X=' + report.center_3d.x.toFixed(3) + ' Y=' + report.center_3d.y.toFixed(3) + ' Z=' + report.center_3d.z.toFixed(3) + '</p>';
  }
  html += '</div>';

  if (report.regions && report.regions.length > 0) {
    report.regions.forEach(function(r) {
      html += '<div class="region-card">';
      html += '<h5>' + (r.name || 'region') + '</h5>';
      html += '<p>匹配点: ' + r.num_matches + ' | 高置信度: ' + (r.num_high_conf || '-') + '</p>';
      if (r.center_3d && r.center_3d.length) {
        html += '<p>3D: (' + r.center_3d.map(function(v) { return v.toFixed(3); }).join(', ') + ')</p>';
      }
      html += '</div>';
    });
  }

  if (report.verification && !report.verification.error) {
    var v = report.verification;
    html += '<div class="verification-section"><h4>LAS 点云验证</h4>';
    html += '<table>';
    html += '<tr><td>匹配点总数</td><td>' + v.total_checked + '</td></tr>';
    html += '<tr><td>已验证</td><td><strong>' + v.total_verified + '</strong> (' + (v.verification_rate * 100).toFixed(1) + '%)</td></tr>';
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
    list.innerHTML = tasks.map(function(t) {
      return '<div class="task-item"><div><strong>任务#' + t.id + '</strong> 图像#' + t.image_id + '<br><small>' + new Date(t.created_at).toLocaleString() + '</small></div><span class="status-badge ' + t.status + '">' + t.status + '</span></div>';
    }).join('');
  } catch(e) { console.error(e); }
}
