/* Dashboard — runtime status polling + config form + action buttons */

'use strict';

const POLL_MS = 2000;

async function refresh() {
  try {
    const s = await api('GET', '/api/runtime/status');
    fillStatus(s);
    fillStats(s.stats);
    fillRejected(s.stats.rejected_per_station || {});
    fillConfig(s.config);
    $('#st-cycle').textContent      = 'cycle: ' + s.cycle;
    $('#st-station').textContent    = 'station: ' + (s.station_name || '-');
    $('#st-counters').textContent   = `sent: ${s.stats.files_sent} / rej: ${s.stats.files_rejected}`;
    $('#st-refreshed').textContent  = 'refreshed: ' + clockNow();
    $('#btn-pause').textContent     = s.is_paused ? '재개' : '일시정지';
  } catch (e) {
    toast('상태 로드 실패: ' + e.message, 'error');
  }
}

function fillStatus(s) {
  $('#s-cycle').textContent       = s.cycle;
  $('#s-station').textContent     = s.station_name || '-';
  $('#s-station-idx').textContent = `${s.station_idx} / ${s.station_count}`;
  $('#s-file').textContent        = s.current_filename
    ? `${s.file_idx_in_station} / ${s.files_in_current_stay} (${s.current_filename})`
    : '-';
  $('#s-last').textContent        = fmtTs(s.last_file_at);
  $('#s-paused').textContent      = s.is_paused ? 'PAUSED' : 'RUNNING';
}

function fillStats(stats) {
  $('#t-cycles').textContent   = stats.cycles_completed;
  $('#t-sent').textContent     = stats.files_sent;
  $('#t-rejected').textContent = stats.files_rejected;
  $('#t-chunks').textContent   = stats.chunks_sent;
  $('#t-ack').textContent      = stats.ack_count;
  $('#t-error').textContent    = stats.error_count;
  $('#last-error').textContent = stats.last_error || '(없음)';
}

function fillRejected(m) {
  const ul = $('#rejected-by-station');
  ul.innerHTML = '';
  const entries = Object.entries(m).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = '(없음)';
    ul.appendChild(li);
    return;
  }
  for (const [name, count] of entries) {
    const li = document.createElement('li');
    const left = document.createElement('span');
    left.textContent = name;
    const right = document.createElement('span');
    right.style.float = 'right';
    right.className = 'warn-count';
    right.textContent = count;
    li.appendChild(left);
    li.appendChild(right);
    ul.appendChild(li);
  }
}

let _formDirty = false;
function fillConfig(c) {
  if (_formDirty) return;
  $('#cf-mode').value               = c.mode;
  $('#cf-output_format').value      = c.output_format;
  $('#cf-out_fps').value            = c.out_fps ?? '';
  $('#cf-jpeg_quality').value       = c.jpeg_quality ?? '';
  $('#cf-resize_w').value           = c.resize_w ?? '';
  $('#cf-resize_h').value           = c.resize_h ?? '';
  $('#cf-chunk_size_kb').value      = c.chunk_size_kb ?? '';
  $('#cf-interval_sec').value       = c.interval_sec ?? '';
  $('#cf-cycle_interval_sec').value = c.cycle_interval_sec ?? '';
  $('#cf-file_order').value         = c.file_order;
  $('#cf-stay_mode').value          = c.stay_mode;
  $('#cf-files_per_stay').value     = c.files_per_stay ?? '';
  $('#cf-stay_seconds').value       = c.stay_seconds ?? '';
  $('#cf-amr_id').value             = c.amr_id || '';
  $('#cf-loop').checked             = !!c.loop;
}

$$('input, select').forEach((el) => {
  el.addEventListener('input', () => { _formDirty = true; });
});

$('#btn-apply-config').addEventListener('click', async () => {
  const body = {
    mode:               $('#cf-mode').value,
    output_format:      $('#cf-output_format').value,
    out_fps:            parseInt($('#cf-out_fps').value, 10) || 1,
    jpeg_quality:       parseInt($('#cf-jpeg_quality').value, 10) || 85,
    resize_w:           $('#cf-resize_w').value || null,
    resize_h:           $('#cf-resize_h').value || null,
    chunk_size_kb:      parseInt($('#cf-chunk_size_kb').value, 10) || 512,
    interval_sec:       parseFloat($('#cf-interval_sec').value) || 0,
    cycle_interval_sec: parseFloat($('#cf-cycle_interval_sec').value) || 0,
    file_order:         $('#cf-file_order').value,
    stay_mode:          $('#cf-stay_mode').value,
    files_per_stay:     $('#cf-files_per_stay').value || null,
    stay_seconds:       $('#cf-stay_seconds').value || null,
    amr_id:             $('#cf-amr_id').value || 'mock-amr-01',
    loop:               $('#cf-loop').checked,
  };
  try {
    await api('PATCH', '/api/runtime/config', body);
    _formDirty = false;
    toast('적용 완료 (다음 파일부터 반영)', 'success');
    await refresh();
  } catch (e) { toast('적용 실패: ' + e.message, 'error'); }
});

$('#btn-pause').addEventListener('click', async () => {
  try {
    const s = await api('GET', '/api/runtime/status');
    if (s.is_paused) await api('POST', '/api/runtime/resume');
    else await api('POST', '/api/runtime/pause');
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
});

$('#btn-skip-file').addEventListener('click', async () => {
  try { await api('POST', '/api/runtime/skip-file'); toast('파일 스킵 요청', 'success'); }
  catch (e) { toast(e.message, 'error'); }
});

$('#btn-skip-station').addEventListener('click', async () => {
  try { await api('POST', '/api/runtime/skip-station'); toast('개소 스킵 요청', 'success'); }
  catch (e) { toast(e.message, 'error'); }
});

$('#btn-restart').addEventListener('click', async () => {
  if (!confirm('현재 사이클을 중단하고 처음부터 다시 시작합니다. 계속?')) return;
  try { await api('POST', '/api/runtime/restart-cycle'); toast('사이클 재시작 요청', 'success'); }
  catch (e) { toast(e.message, 'error'); }
});

$('#btn-refresh').addEventListener('click', () => refresh());

(async function start() {
  await refresh();
  setInterval(refresh, POLL_MS);
})();
