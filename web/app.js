const API = '/api';

let currentView = 'list';
let currentImageId = null;
let pollTimer = null;

document.addEventListener('DOMContentLoaded', () => {
  setupTabs();
  setupUpload();
  document.getElementById('refresh-btn').addEventListener('click', loadImageList);
  document.getElementById('back-btn').addEventListener('click', () => showTab('list'));
  loadImageList();
});

function setupTabs() {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      currentView = tab;
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      document.getElementById(`tab-${tab}`).classList.add('active');
      if (tab === 'list') loadImageList();
      if (tab === 'tasks') loadTaskList();
    });
  });
}

function showTab(name) {
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  document.querySelector(`.tab[data-tab="${name}"]`).classList.add('active');
  currentView = name;
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.getElementById(`tab-${name}`).classList.add('active');
  if (name === 'list') loadImageList();
  if (name === 'tasks') loadTaskList();
}

function setupUpload() {
  const zone = document.getElementById('upload-zone');
  const input = document.getElementById('file-input');
  zone.addEventListener('click', () => input.click());
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    uploadFiles(e.dataTransfer.files);
  });
  input.addEventListener('change', () => uploadFiles(input.files));
}

async function uploadFiles(files) {
  const progress = document.getElementById('upload-progress');
  const result = document.getElementById('upload-result');
  progress.classList.remove('hidden');
  result.classList.add('hidden');

  for (const file of files) {
    const bar = progress.querySelector('.progress-bar');
    bar.style.width = '50%';
    const form = new FormData();
    form.append('file', file);
    const resp = await fetch(`${API}/images/upload`, { method: 'POST', body: form });
    bar.style.width = '100%';
    if (resp.ok) {
      const data = await resp.json();
      result.innerHTML = `<p>上传成功: ${data.original_name} (ID: ${data.id})</p>`;
      result.classList.remove('hidden');
    } else {
      result.innerHTML = '<p style="color:red">上传失败</p>';
      result.classList.remove('hidden');
    }
    await new Promise(r => setTimeout(r, 300));
  }
  setTimeout(() => {
    progress.classList.add('hidden');
    result.classList.add('hidden');
    loadImageList();
  }, 1500);
}

async function loadImageList() {
  const resp = await fetch(`${API}/images`);
  const images = await resp.json();
  const grid = document.getElementById('image-grid');
  document.getElementById('image-count').textContent = `共 ${images.length} 张`;

  grid.innerHTML = images.map(img => `
    <div class="image-card" onclick="showImageDetail(${img.id})">
      <img src="/images/${img.filename}" alt="${img.original_name}">
      <div class="info">
        <div class="name" title="${img.original_name}">${img.original_name}</div>
        <div class="status ${img.status}">${img.status}</div>
        <div>${new Date(img.created_at).toLocaleString()}</div>
      </div>
    </div>
  `).join('');
}

async function showImageDetail(imageId) {
  currentImageId = imageId;
  const resp = await fetch(`${API}/images/${imageId}`);
  const img = await resp.json();

  showTab('detail');
  document.getElementById('detail-title').textContent = img.original_name;
  document.getElementById('detail-image').src = `/images/${img.filename}`;
  document.getElementById('detail-image').onload = () => {
    const canvas = document.getElementById('overlay-canvas');
    const imgEl = document.getElementById('detail-image');
    canvas.width = imgEl.naturalWidth;
    canvas.height = imgEl.naturalHeight;
  };
  document.getElementById('compare-status').classList.add('hidden');
  document.getElementById('compare-result').classList.add('hidden');

  document.getElementById('compare-btn').onclick = () => startComparison(imageId);
}

async function startComparison(imageId) {
  const statusEl = document.getElementById('compare-status');
  const resultEl = document.getElementById('compare-result');
  statusEl.classList.remove('hidden');
  statusEl.textContent = '正在创建比较任务...';
  statusEl.className = 'status loading';
  resultEl.classList.add('hidden');

  const resp = await fetch(`${API}/tasks/compare`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_id: imageId }),
  });
  if (!resp.ok) {
    statusEl.textContent = '创建任务失败';
    statusEl.className = 'status error';
    return;
  }
  const task = await resp.json();
  pollTask(task.id);
}

async function pollTask(taskId) {
  const statusEl = document.getElementById('compare-status');
  const resultEl = document.getElementById('compare-result');

  const resp = await fetch(`${API}/tasks/${taskId}`);
  const task = await resp.json();

  if (task.status === 'completed') {
    statusEl.textContent = '';
    statusEl.className = 'status success';
    const reportResp = await fetch(`${API}/tasks/${taskId}/report`);
    const report = await reportResp.json();
    showReport(report, task);
    return;
  }
  if (task.status === 'failed') {
    statusEl.textContent = `匹配失败: ${task.error_message || '未知错误'}`;
    statusEl.className = 'status error';
    return;
  }
  statusEl.textContent = `处理中... (${task.status})`;
  setTimeout(() => pollTask(taskId), 2000);
}

function showReport(report, task) {
  const el = document.getElementById('compare-result');
  const content = document.getElementById('result-content');

  let regionsHtml = '';
  if (report.regions && report.regions.length > 0) {
    regionsHtml = report.regions.map(r => {
      const name = r.name || r.ref_image || 'unknown';
      return `
        <div class="region-card">
          <h5>${name}</h5>
          <p>匹配点数: ${r.num_matches} | 高置信度: ${r.num_high_conf || '-'}</p>
          ${r.center_3d ? `<p>3D 中心: (${r.center_3d.map(v => v.toFixed(3)).join(', ')})</p>` : ''}
        </div>
      `;
    }).join('');
  }

  let verifyHtml = '';
  if (report.verification && !report.verification.error) {
    const v = report.verification;
    verifyHtml = `
      <div class="verification-section">
        <h4>LAS 点云验证</h4>
        <table>
          <tr><td>采样点数</td><td>${v.sample_size}</td></tr>
          <tr><td>已验证匹配</td><td><strong>${v.total_verified}</strong> / ${v.total_checked}</td></tr>
          <tr><td>验证率</td><td><strong style="color:${v.verification_rate > 0.5 ? '#4caf50' : '#f44336'}">${(v.verification_rate * 100).toFixed(1)}%</strong></td></tr>
          <tr><td>平均偏差</td><td>${v.mean_distance_m.toFixed(3)} m</td></tr>
        </table>
        ${v.details ? v.details.slice(0, 5).map(d =>
          `<p style="font-size:0.8em">
            匹配点: (${d.matched_xyz.map(x => x.toFixed(2)).join(', ')})
            → 最近LAS点: (${d.nearest_las_xyz.map(x => x.toFixed(2)).join(', ')})
            偏差: ${d.distance_m.toFixed(3)}m ${d.verified ? '✅' : '❌'}
          </p>`
        ).join('') : ''}
      </div>`;
  }

  let matchedHtml = '';
  if (task.result_json && task.result_json.matched_3d) {
    const pts = task.result_json.matched_3d.slice(0, 30);
    matchedHtml = `
      <div class="matched-points">
        <h5>匹配点详情 (前30个)</h5>
        ${pts.map(p => `<span class="coord-tag">(${p.query_pt[0].toFixed(0)},${p.query_pt[1].toFixed(0)})→3D</span>`).join(' ')}
      </div>`;
  }

  content.innerHTML = `
    <div class="report-summary">
      <p>匹配状态: <strong style="color:${report.matched ? '#4caf50' : '#f44336'}">${report.matched ? '成功' : '失败'}</strong></p>
      <p>总匹配点数: <strong>${report.total_matches}</strong></p>
      <p>置信度: <strong>${(report.confidence * 100).toFixed(1)}%</strong></p>
      ${report.center_3d ? `
        <p>3D 中心坐标:
          <span class="coord">X: ${report.center_3d.x.toFixed(3)}</span>
          <span class="coord">Y: ${report.center_3d.y.toFixed(3)}</span>
          <span class="coord">Z: ${report.center_3d.z.toFixed(3)}</span>
        </p>` : ''}
    </div>
    ${regionsHtml}
    ${verifyHtml}
    ${matchedHtml}
  `;
  el.classList.remove('hidden');
}

async function loadTaskList() {
  const resp = await fetch(`${API}/tasks`);
  const tasks = await resp.json();
  const list = document.getElementById('task-list');

  list.innerHTML = tasks.length === 0 ? '<p>暂无任务记录</p>' : tasks.map(t => `
    <div class="task-item">
      <div>
        <strong>任务 #${t.id}</strong> — 图像 #${t.image_id}
        <div style="font-size:0.8rem;color:#888">${new Date(t.created_at).toLocaleString()}</div>
      </div>
      <span class="status-badge ${t.status}">${t.status}</span>
    </div>
  `).join('');
}
