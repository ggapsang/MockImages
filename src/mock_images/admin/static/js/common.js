/* MockImages — common helpers (api, toast, modal, formatting) */

'use strict';

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* may be empty */ }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

function toast(message, kind) {
  const div = document.createElement('div');
  div.className = 'toast' + (kind ? ' ' + kind : '');
  div.textContent = message;
  const box = $('#toast-container');
  if (box) box.appendChild(div);
  setTimeout(() => div.remove(), 3000);
}

function openModal(id) { const el = $('#' + id); if (el) el.classList.add('open'); }
function closeModal(id) { const el = $('#' + id); if (el) el.classList.remove('open'); }

document.addEventListener('click', (e) => {
  const close = e.target.closest('[data-modal-close]');
  if (close) closeModal(close.getAttribute('data-modal-close'));
});

function fmtTs(s) { return s ? String(s).replace('T', ' ').replace('Z', '').slice(0, 19) : ''; }
function id8(s) { return s ? String(s).slice(0, 8) : ''; }
function clockNow() { return new Date().toTimeString().slice(0, 8); }

function fmtBytes(n) {
  if (n === null || n === undefined) return '';
  n = Number(n);
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB';
}
