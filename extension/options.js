// options.js — логика страницы настроек JobRadar

(function () {
  'use strict';

  const endpointInput = document.getElementById('endpoint');
  const secretInput = document.getElementById('secret');
  const saveBtn = document.getElementById('save-btn');
  const status = document.getElementById('status');

  // ── Загрузка сохранённых настроек ──────────────────────────────────

  chrome.storage.local.get(['endpoint', 'secret'], (settings) => {
    if (settings.endpoint) {
      endpointInput.value = settings.endpoint;
    }
    if (settings.secret) {
      secretInput.value = settings.secret;
    }
  });

  // ── Сохранение ─────────────────────────────────────────────────────

  saveBtn.addEventListener('click', () => {
    const endpoint = endpointInput.value.trim();
    const secret = secretInput.value.trim();

    chrome.storage.local.set({ endpoint, secret }, () => {
      status.classList.add('visible');
      setTimeout(() => {
        status.classList.remove('visible');
      }, 2000);
    });
  });
})();
