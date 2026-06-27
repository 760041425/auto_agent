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
      <img src="/images/${img.filename}" alt="${img.original_name}" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22150%22><rect fill=%22%23ddd%22 width=%22200%22 height=%22150%22/><text x=%2250%25%22 y=%2250%25%22 text-anchor=%22middle%22>No Image</text></svg>'">
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
    statusEl.textContent = '匹配完成!';
    statusEl.className = 'status success';
    const reportResp = await fetch(`${API}/tasks/${taskId}/report`);
    const report = await reportResp.json();
    showReport(report);
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

function showReport(report) {
  const el = document.getElementById('compare-result');
  const content = document.getElementById('result-content');

  let regionsHtml = '';
  if (report.regions && report.regions.length > 0) {
    regionsHtml = `
      <h5>匹配区域 (${report.regions.length})</h5>
      <table>
        <tr><th>参考图像</th><th>匹配点数</th><th>3D 中心坐标</th></tr>
        ${report.regions.map(r => `
          <tr>
            <td>${r.ref_image.split('/').pop()}</td>
            <td>${r.num_matches}</td>
            <td class="coord">(${r.center_3d.map(v => v.toFixed(3)).join(', ')})</td>
          </tr>
        `).join('')}
      </table>`;
  }

  content.innerHTML = `
    <p>总匹配点数: <strong>${report.total_matches}</strong></p>
    <p>置信度: <strong>${(report.confidence * 100).toFixed(1)}%</strong></p>
    ${report.center_3d ? `
      <p>整体中心 3D 坐标:
        <span class="coord">X: ${report.center_3d.x.toFixed(3)}</span>
        <span class="coord">Y: ${report.center_3d.y.toFixed(3)}</span>
        <span class="coord">Z: ${report.center_3d.z.toFixed(3)}</span>
      </p>` : ''}
    ${regionsHtml}
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
