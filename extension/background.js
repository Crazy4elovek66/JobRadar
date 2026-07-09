// background.js — fetch-прокси для content script
// Content scripts на https://hh.ru не могут делать запросы к http://127.0.0.1
// из-за ограничений mixed-content/CORS. Service worker таких ограничений не имеет.

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type !== 'analyzeVacancy') return false;

  (async () => {
    try {
      const settings = await chrome.storage.local.get(['endpoint', 'secret']);
      const endpoint = (settings.endpoint || 'http://127.0.0.1:8080').replace(/\/+$/, '');
      const secret = settings.secret || '';

      const response = await fetch(`${endpoint}/api/extension/analyze`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Extension-Secret': secret,
        },
        body: JSON.stringify(message.payload),
      });

      const data = await response.json();
      if (!response.ok) {
        sendResponse({ success: false, error: data.error || `HTTP ${response.status}` });
      } else {
        sendResponse({ success: true, data });
      }
    } catch (err) {
      sendResponse({ success: false, error: `Ошибка сети: ${err.message}` });
    }
  })();

  return true; // Держим канал открытым для асинхронного ответа
});
