/* Stations page */

'use strict';

let _editing = null;
let _deleting = null;

async function refresh() {
  try {
    const rows = await api('GET', '/api/stations');
    render(rows);
    $('#st-count').textContent = 'stations: ' + rows.length;
    $('#st-refreshed').textContent = 'refreshed: ' + clockNow();
  } catch (e) { toast('로드 실패: ' + e.message, 'error'); }
}

function render(rows) {
  const tbody = $('#tbl-stations tbody');
  tbody.innerHTML = '';
  if (!rows.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 8;
    td.style.textAlign = 'center';
    td.className = 'muted';
    td.textContent = '등록된 개소가 없습니다. [+ 개소 등록] 버튼으로 추가하세요.';
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    const a = document.createElement('a');
    a.href = '/stations/' + encodeURIComponent(r.station_name);
    a.textContent = r.station_name;
    a.style.color = '#000080';
    const tdName = document.createElement('td');
    tdName.appendChild(a);
    tr.appendChild(tdName);

    addCell(tr, r.label || '');
    addStatusBadge(tr, r.status);
    addCell(tr, r.file_count, 'mono');
    addCell(tr, fmtTs(r.last_sent_at), 'mono');

    const tdRej = document.createElement('td');
    tdRej.className = 'mono';
    if (r.rejected_count > 0) tdRej.classList.add('warn-count');
    tdRej.textContent = r.rejected_count;
    tr.appendChild(tdRej);

    addActions(tr, [
      { label: '수정', cls: 'btn',           onClick: () => onEdit(r) },
      { label: '파일', cls: 'btn',           onClick: () => location.href = '/stations/' + encodeURIComponent(r.station_name) },
      { label: '삭제', cls: 'btn btn-danger', onClick: () => onDelete(r) },
    ]);

    tbody.appendChild(tr);
  });
}

function addCell(tr, value, cls) {
  const td = document.createElement('td');
  if (cls) td.className = cls;
  td.textContent = (value === null || value === undefined) ? '' : String(value);
  tr.appendChild(td);
  return td;
}

function addStatusBadge(tr, value) {
  const td = document.createElement('td');
  const sp = document.createElement('span');
  sp.className = 'status-badge';
  if (value === 'paused') sp.classList.add('warn');
  else sp.classList.add('ok');
  sp.textContent = value;
  td.appendChild(sp);
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

// Add modal
$('#btn-add').addEventListener('click', () => {
  $('#add-name').value  = '';
  $('#add-uuid').value  = '';
  $('#add-label').value = '';
  openModal('modal-add');
});

$('#add-save').addEventListener('click', async () => {
  const body = {
    station_name: $('#add-name').value.trim(),
    label:        $('#add-label').value.trim() || null,
  };
  if (!body.station_name) { toast('station_name은 필수', 'error'); return; }
  try {
    await api('POST', '/api/stations', body);
    closeModal('modal-add');
    toast('등록 완료', 'success');
    await refresh();
  } catch (e) { toast('등록 실패: ' + e.message, 'error'); }
});

// Edit modal
function onEdit(r) {
  _editing = r;
  $('#edit-name').textContent = r.station_name;
  $('#edit-label').value      = r.label || '';
  $('#edit-status').value     = r.status;
  openModal('modal-edit');
}

$('#edit-save').addEventListener('click', async () => {
  if (!_editing) return;
  const body = {
    label:      $('#edit-label').value.trim() || null,
    status:     $('#edit-status').value,
  };
  try {
    await api('PATCH', '/api/stations/' + encodeURIComponent(_editing.station_name), body);
    closeModal('modal-edit');
    toast('수정 완료', 'success');
    _editing = null;
    await refresh();
  } catch (e) { toast('수정 실패: ' + e.message, 'error'); }
});

// Delete modal
function onDelete(r) {
  _deleting = r;
  $('#del-name').textContent = r.station_name;
  openModal('modal-delete');
}

$('#del-save').addEventListener('click', async () => {
  if (!_deleting) return;
  try {
    await api('DELETE', '/api/stations/' + encodeURIComponent(_deleting.station_name));
    closeModal('modal-delete');
    toast('삭제 완료', 'success');
    _deleting = null;
    await refresh();
  } catch (e) { toast('삭제 실패: ' + e.message, 'error'); }
});

$('#btn-refresh').addEventListener('click', () => refresh());

(async function start() {
  await refresh();
  setInterval(refresh, 4000);
})();
