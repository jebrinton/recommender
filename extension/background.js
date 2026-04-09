// background.js — Service worker for Reading Recommender extension
// Handles: keyboard shortcut relay, server health polling, outbox sync

const API_BASE = 'http://localhost:7432';
let serverOnline = false;
let syncing = false;

// ── Server health check ─────────────────────────────────────────────────────

async function checkServer() {
  const wasOffline = !serverOnline;
  try {
    const r = await fetch(`${API_BASE}/api/stats`, {
      signal: AbortSignal.timeout(3000),
    });
    serverOnline = r.ok;
  } catch {
    serverOnline = false;
  }
  // Just came back online — flush the outbox
  if (wasOffline && serverOnline) {
    flushOutbox();
  }
}

// Poll every 15 seconds
setInterval(checkServer, 15000);
checkServer();

// ── Outbox: queued saves for when server is offline ─────────────────────────

async function getOutbox() {
  return new Promise((resolve) => {
    chrome.storage.local.get('recommender-outbox', (result) => {
      resolve(result['recommender-outbox'] || []);
    });
  });
}

async function setOutbox(items) {
  return new Promise((resolve) => {
    chrome.storage.local.set({ 'recommender-outbox': items }, resolve);
  });
}

async function flushOutbox() {
  if (syncing) return;
  syncing = true;

  try {
    const items = await getOutbox();
    if (items.length === 0) { syncing = false; return; }

    const remaining = [];
    for (const item of items) {
      try {
        const resp = await fetch(`${API_BASE}${item.path}`, {
          method: item.method,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(item.body),
          signal: AbortSignal.timeout(5000),
        });
        if (resp.ok || resp.status === 201) {
          // Success — also clear the draft for this URL
          const url = item.body.url || item.meta?.url;
          if (url) {
            chrome.storage.local.remove(`recommender-draft:${url}`);
          }
        } else if (resp.status === 409 && item.method === 'POST') {
          // Duplicate URL — try as PATCH instead
          const existing = await resp.json();
          if (existing.id) {
            const patchResp = await fetch(`${API_BASE}/api/articles/${existing.id}`, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(item.body),
              signal: AbortSignal.timeout(5000),
            });
            if (!patchResp.ok) {
              remaining.push(item);
            }
          }
        } else {
          remaining.push(item); // retry later
        }
      } catch {
        remaining.push(item); // network error, retry later
        break; // server likely went down again, stop trying
      }
    }

    await setOutbox(remaining);
    // Notify any open sidebar about the sync result
    notifyAllTabs({ action: 'outbox-synced', remaining: remaining.length });
  } finally {
    syncing = false;
  }
}

function notifyAllTabs(msg) {
  chrome.tabs.query({}, (tabs) => {
    for (const tab of tabs) {
      if (tab.id) {
        chrome.tabs.sendMessage(tab.id, msg).catch(() => {});
      }
    }
  });
}

// ── Keyboard shortcut → content script ──────────────────────────────────────

chrome.commands.onCommand.addListener((command) => {
  if (command === 'toggle-sidebar') {
    chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
      if (tab?.id) {
        chrome.tabs.sendMessage(tab.id, { action: 'toggle-sidebar' });
      }
    });
  }
});

// ── Message handler for content script queries ──────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'get-server-status') {
    getOutbox().then((outbox) => {
      sendResponse({ online: serverOnline, outboxCount: outbox.length });
    });
    return true; // async
  }
  if (msg.action === 'check-server-now') {
    checkServer().then(() => {
      getOutbox().then((outbox) => {
        sendResponse({ online: serverOnline, outboxCount: outbox.length });
      });
    });
    return true;
  }
  if (msg.action === 'queue-outbox') {
    getOutbox().then(async (outbox) => {
      outbox.push(msg.item);
      await setOutbox(outbox);
      sendResponse({ queued: true, outboxCount: outbox.length });
      // Try to flush immediately in case server just came back
      if (serverOnline) flushOutbox();
    });
    return true;
  }
  if (msg.action === 'get-outbox-count') {
    getOutbox().then((outbox) => {
      sendResponse({ outboxCount: outbox.length });
    });
    return true;
  }
  if (msg.action === 'flush-outbox') {
    flushOutbox().then(() => {
      getOutbox().then((outbox) => {
        sendResponse({ outboxCount: outbox.length });
      });
    });
    return true;
  }
});
