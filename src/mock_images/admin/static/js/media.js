/* Media page — drag and drop upload + file CRUD */

'use strict';

const STATION = window.STATION_NAME;

async function refresh() {
  try {
    const rows = await api('GET', `/api/stations/${encodeURIComponent(STATION)}/files`);
    render(rows);
    $('#st-count').textContent = 'files: ' + rows.length;
    $('#st-refreshed').textContent = 'refreshed: ' + clockNow();
  } catch (e) { toast('로드 실패: ' + e.message, 'error'); }
}

function render(rows) {
  const tbody = $('#tbl-files tbody');
  tbody.innerHTML = '';
  if (!rows.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 7;
    td.style.textAlign = 'center';
    td.className = 'muted';
    td.textContent = '파일이 없습니다. 위쪽 영역으로 드래그하거나 클릭하여 업로드하세요.';
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    addCell(tr, r.filename, 'mono');
    addCell(tr, r.file_type);
    addCell(tr, fmtBytes(r.size_bytes), 'mono');
    addCell(tr, fmtTs(r.uploaded_at), 'mono');
    addCell(tr, fmtTs(r.last_sent_at), 'mono');
    addCell(tr, r.send_count, 'mono');
    addActions(tr, [
      { label: 'reset', cls: 'btn',           onClick: () => onReset(r) },
      { label: '삭제',  cls: 'btn btn-danger', onClick: () => onDelete(r) },
    ]);
    tbody.appendChild(tr);
  });
}

function addCell(tr, value, cls) {
  const td = document.createElement('td');
  if (cls) td.className = cls;
  td.textContent = (value === null || value === undefined) ? '' : String(value);
  tr.appendChild(td);
}

function addActions(tr, buttons) {
  const td = document.createElement('td');
  const wrap = document.createElement('span');
  wrap.className = 'row-actions';
  buttons.forEach((b) => {
    const btn = document.createElement('button');
    btn.className = b.cls;
    btn.textContent = b.label;
    btn.addEventListener('click', b.onClick);
    wrap.appendChild(btn);
  });
  td.appendChild(wrap);
  tr.appendChild(td);
}

async function onReset(r) {
  try {
    await api('POST',
      `/api/stations/${encodeURIComponent(STATION)}/files/${encodeURIComponent(r.filename)}/reset`);
    toast('reset 완료', 'success');
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
}

async function onDelete(r) {
  if (!confirm(`${r.filename} 삭제?`)) return;
  try {
    await api('DELETE',
      `/api/stations/${encodeURIComponent(STATION)}/files/${encodeURIComponent(r.filename)}`);
    toast('삭제 완료', 'success');
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
}

// ---------- drag & drop upload ----------

const dz = $('#dropzone');
const input = $('#file-input');
const status = $('#upload-status');

dz.addEventListener('click', () => input.click());

dz.addEventListener('dragover', (e) => {
  e.preventDefault();
  dz.classList.add('over');
});
dz.addEventListener('dragleave', () => dz.classList.remove('over'));
dz.addEventListener('drop', async (e) => {
  e.preventDefault();
  dz.classList.remove('over');
  const files = e.dataTransfer.files;
  if (files && files.length) await uploadFiles(files);
});
input.addEventListener('change', async (e) => {
  if (input.files && input.files.length) await uploadFiles(input.files);
  input.value = '';
});

async function uploadFiles(files) {
  status.textContent = `uploading ${files.length} file(s)...`;
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  try {
    const res = await fetch(`/api/stations/${encodeURIComponent(STATION)}/files`, {
      method: 'POST',
      body: fd,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    status.textContent = `uploaded ${data.count}`;
    toast(`업로드 완료 (${data.count}건)`, 'success');
    await refresh();
  } catch (e) {
    status.textContent = 'error';
    toast('업로드 실패: ' + e.message, 'error');
  }
}

$('#btn-rescan').addEventListener('click', async () => {
  try {
    const r = await api('POST', `/api/stations/${encodeURIComponent(STATION)}/rescan`);
    toast(`rescan: +${r.inserted} ~${r.updated} -${r.deleted}`, 'success');
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
});

$('#btn-refresh').addEventListener('click', () => refresh());

(async function start() {
  await refresh();
  setInterval(refresh, 5000);
})();
