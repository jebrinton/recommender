// sidebar.js — State management, API calls, autocomplete, draft persistence
// Loaded as an ES module by content.js

const QUALITY_LABELS = ['', 'Poor', 'Weak', 'Fair', 'Good', 'Strong', 'Great', 'Superb'];
const INTEREST_LABELS = ['', 'Boring', 'Dull', 'Meh', 'Decent', 'Good', 'Hooked', 'Riveting'];
const DRAFT_TTL = 7 * 24 * 60 * 60 * 1000; // 7 days
const SAVE_DEBOUNCE = 500;

export function initSidebar(shadowRoot, { extractMetadata, apiBase, onClose }) {

  // ── State ───────────────────────────────────────────────────────────────────

  const state = {
    articleId: null,
    isExisting: false,
    isDirty: false,
    serverOnline: false,
    sources: [],
    fields: {
      title: '',
      url: '',
      source: '',
      quality_rating: null,
      interest_rating: null,
      notes: '',
      status: 'unread',
    },
  };

  // ── DOM refs ────────────────────────────────────────────────────────────────

  const $ = (sel) => shadowRoot.querySelector(sel);
  const statusDot    = $('#status-dot');
  const closeBtn     = $('#close-btn');
  const titleInput   = $('#field-title');
  const urlLink      = $('#field-url');
  const sourceInput  = $('#field-source');
  const sourceDD     = $('#source-dropdown');
  const qualityPills = $('#quality-pills');
  const interestPills = $('#interest-pills');
  const qualityDesc  = $('#quality-desc');
  const interestDesc = $('#interest-desc');
  const notesArea    = $('#field-notes');
  const btnSkip      = $('#btn-skip');
  const btnSave      = $('#btn-save');
  const btnMarkRead  = $('#btn-mark-read');
  const toast        = $('#toast');

  // ── Rating pills ────────────────────────────────────────────────────────────

  function buildPills(container, descEl, labels, ratingKey) {
    container.innerHTML = '';
    for (let i = 1; i <= 7; i++) {
      const pill = document.createElement('div');
      pill.className = 'rpill';
      pill.textContent = i;
      pill.dataset.value = i;

      pill.addEventListener('mouseenter', () => {
        previewRating(container, descEl, labels, i);
      });

      pill.addEventListener('mouseleave', () => {
        resetRatingDisplay(container, descEl, labels, state.fields[ratingKey]);
      });

      pill.addEventListener('click', () => {
        state.fields[ratingKey] = i;
        state.isDirty = true;
        resetRatingDisplay(container, descEl, labels, i);
        debouncedSaveDraft();
      });

      container.appendChild(pill);
    }
  }

  function previewRating(container, descEl, labels, value) {
    container.querySelectorAll('.rpill').forEach((p) => {
      const v = parseInt(p.dataset.value);
      p.classList.toggle('filled', v <= value && v !== value);
      p.classList.toggle('active', v === value);
    });
    descEl.textContent = labels[value] || '';
  }

  function resetRatingDisplay(container, descEl, labels, value) {
    container.querySelectorAll('.rpill').forEach((p) => {
      const v = parseInt(p.dataset.value);
      if (value) {
        p.classList.toggle('filled', v < value);
        p.classList.toggle('active', v === value);
      } else {
        p.classList.remove('filled', 'active');
      }
    });
    descEl.textContent = value ? (labels[value] || '') : '';
  }

  buildPills(qualityPills, qualityDesc, QUALITY_LABELS, 'quality_rating');
  buildPills(interestPills, interestDesc, INTEREST_LABELS, 'interest_rating');

  // ── Source autocomplete ─────────────────────────────────────────────────────

  sourceInput.addEventListener('input', () => {
    state.fields.source = sourceInput.value;
    state.isDirty = true;
    debouncedSaveDraft();

    const val = sourceInput.value.toLowerCase();
    if (val.length < 1) { sourceDD.hidden = true; return; }
    const matches = state.sources.filter(s =>
      s.toLowerCase().includes(val)
    ).slice(0, 8);
    if (matches.length === 0) { sourceDD.hidden = true; return; }

    sourceDD.innerHTML = matches.map(s =>
      `<div class="ac-option">${escapeHtml(s)}</div>`
    ).join('');
    sourceDD.hidden = false;
  });

  sourceDD.addEventListener('click', (e) => {
    const opt = e.target.closest('.ac-option');
    if (opt) {
      sourceInput.value = opt.textContent;
      sourceDD.hidden = true;
      state.fields.source = opt.textContent;
      state.isDirty = true;
      debouncedSaveDraft();
    }
  });

  sourceInput.addEventListener('blur', () => {
    setTimeout(() => { sourceDD.hidden = true; }, 150);
  });

  // ── Field change listeners ──────────────────────────────────────────────────

  titleInput.addEventListener('input', () => {
    state.fields.title = titleInput.value;
    state.isDirty = true;
    debouncedSaveDraft();
  });

  notesArea.addEventListener('input', () => {
    state.fields.notes = notesArea.value;
    state.isDirty = true;
    debouncedSaveDraft();
  });

  // ── Button handlers ─────────────────────────────────────────────────────────

  closeBtn.addEventListener('click', onClose);

  btnSave.addEventListener('click', async () => {
    await saveArticle(false);
  });

  btnMarkRead.addEventListener('click', async () => {
    await saveArticle(true);
  });

  btnSkip.addEventListener('click', async () => {
    state.fields.status = 'skipped';
    await saveArticle(false);
    onClose();
  });

  // ── API helpers ─────────────────────────────────────────────────────────────

  async function apiFetch(path, options = {}) {
    const resp = await fetch(`${apiBase}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    return resp;
  }

  function buildRequestBody(markRead) {
    if (state.isExisting && state.articleId) {
      const body = {
        title: state.fields.title,
        source: state.fields.source,
        quality_rating: state.fields.quality_rating,
        interest_rating: state.fields.interest_rating,
        notes: state.fields.notes || null,
        status: state.fields.status,
      };
      if (markRead) body.date_read = new Date().toISOString().split('T')[0];
      return { method: 'PATCH', path: `/api/articles/${state.articleId}`, body };
    } else {
      const body = {
        title: state.fields.title,
        url: state.fields.url,
        source: state.fields.source,
        quality_rating: state.fields.quality_rating,
        interest_rating: state.fields.interest_rating,
        notes: state.fields.notes || null,
        status: state.fields.status,
      };
      if (markRead) body.date_read = new Date().toISOString().split('T')[0];
      return { method: 'POST', path: '/api/articles', body };
    }
  }

  async function saveArticle(markRead) {
    if (markRead) {
      state.fields.status = 'read';
    }

    const req = buildRequestBody(markRead);

    // If offline, queue to outbox
    if (!state.serverOnline) {
      return queueToOutbox(req, markRead);
    }

    // Try to save online
    try {
      const resp = await apiFetch(req.path, {
        method: req.method,
        body: JSON.stringify(req.body),
      });

      if (resp.ok || resp.status === 201) {
        const data = await resp.json();
        state.articleId = data.id;
        state.isExisting = true;
        state.isDirty = false;
        clearDraft(state.fields.url);
        showToast(markRead ? 'Marked as read' : 'Saved');
        if (markRead) {
          setTimeout(onClose, 600);
        }
      } else if (resp.status === 409) {
        const data = await resp.json();
        state.articleId = data.id;
        state.isExisting = true;
        showToast('Article exists — switching to update mode');
      } else {
        showToast(`Error: ${resp.status}`);
      }
    } catch (e) {
      // Network failed mid-request — queue to outbox as fallback
      console.warn('Save failed, queueing offline:', e);
      state.serverOnline = false;
      updateServerUI();
      return queueToOutbox(req, markRead);
    }
  }

  async function queueToOutbox(req, markRead) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({
        action: 'queue-outbox',
        item: { ...req, meta: { url: state.fields.url }, queuedAt: Date.now() },
      }, (resp) => {
        state.isDirty = false;
        clearDraft(state.fields.url);
        updateOutboxBadge(resp?.outboxCount || 0);
        showToast(markRead ? 'Queued — will sync when online' : 'Saved offline');
        if (markRead) {
          setTimeout(onClose, 600);
        }
        resolve();
      });
    });
  }

  // ── Draft persistence (chrome.storage.local) ──────────────────────────────

  function draftKey(url) {
    return `recommender-draft:${url}`;
  }

  let saveTimer = null;
  function debouncedSaveDraft() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveDraft(), SAVE_DEBOUNCE);
  }

  function saveDraft() {
    const key = draftKey(state.fields.url);
    const draft = { ...state.fields, timestamp: Date.now() };
    chrome.storage.local.set({ [key]: draft });
  }

  function loadDraft(url) {
    return new Promise((resolve) => {
      const key = draftKey(url);
      chrome.storage.local.get(key, (result) => {
        const draft = result[key];
        if (!draft) { resolve(null); return; }
        if (Date.now() - draft.timestamp > DRAFT_TTL) {
          chrome.storage.local.remove(key);
          resolve(null);
          return;
        }
        resolve(draft);
      });
    });
  }

  function clearDraft(url) {
    chrome.storage.local.remove(draftKey(url));
  }

  // ── Server status ─────────────────────────────────────────────────────────

  async function checkServerStatus() {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ action: 'get-server-status' }, (resp) => {
        state.serverOnline = resp?.online ?? false;
        updateServerUI();
        updateOutboxBadge(resp?.outboxCount || 0);
        resolve(state.serverOnline);
      });
    });
  }

  function updateServerUI() {
    statusDot.classList.toggle('online', state.serverOnline);
    statusDot.title = state.serverOnline ? 'Server online' : 'Offline — saves will queue locally';
    // Buttons always enabled — offline saves go to outbox
  }

  function updateOutboxBadge(count) {
    const badge = $('#outbox-badge');
    if (!badge) return;
    if (count > 0) {
      badge.textContent = `${count} queued`;
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
  }

  // ── Toast ─────────────────────────────────────────────────────────────────

  let toastTimer = null;
  function showToast(msg) {
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2000);
  }

  // ── Render state → DOM ────────────────────────────────────────────────────

  function renderFields() {
    titleInput.value = state.fields.title;
    urlLink.href = state.fields.url;
    urlLink.textContent = state.fields.url.length > 60
      ? state.fields.url.slice(0, 60) + '...'
      : state.fields.url;
    sourceInput.value = state.fields.source;
    notesArea.value = state.fields.notes;

    resetRatingDisplay(qualityPills, qualityDesc, QUALITY_LABELS, state.fields.quality_rating);
    resetRatingDisplay(interestPills, interestDesc, INTEREST_LABELS, state.fields.interest_rating);
  }

  // ── Open sidebar (called by content.js) ───────────────────────────────────

  async function open(metadata) {
    // Reset state
    state.articleId = null;
    state.isExisting = false;
    state.isDirty = false;
    state.fields = {
      title: metadata.title || '',
      url: metadata.url || '',
      source: metadata.source || '',
      quality_rating: null,
      interest_rating: null,
      notes: '',
      status: 'unread',
    };

    // Layer 1: page metadata (already set above)
    // Layer 2: draft (user's unsaved edits from a previous session)
    const draft = await loadDraft(metadata.url);
    if (draft) {
      // Draft overrides metadata for user-edited fields
      if (draft.title) state.fields.title = draft.title;
      if (draft.source) state.fields.source = draft.source;
      if (draft.quality_rating) state.fields.quality_rating = draft.quality_rating;
      if (draft.interest_rating) state.fields.interest_rating = draft.interest_rating;
      if (draft.notes) state.fields.notes = draft.notes;
    }

    renderFields();

    // Layer 3: DB lookup (authoritative — overwrites draft + metadata)
    const online = await checkServerStatus();
    if (online) {
      try {
        const resp = await apiFetch(`/api/articles/by-url?url=${encodeURIComponent(metadata.url)}`);
        if (resp.ok) {
          const article = await resp.json();
          state.articleId = article.id;
          state.isExisting = true;
          state.fields.title = article.title || state.fields.title;
          state.fields.source = article.source || state.fields.source;
          state.fields.notes = article.notes || state.fields.notes;
          state.fields.quality_rating = article.quality_rating ?? state.fields.quality_rating;
          state.fields.interest_rating = article.interest_rating ?? state.fields.interest_rating;
          state.fields.status = article.status || state.fields.status;
          renderFields();
        }
        // 404 = new article, that's fine
      } catch (e) {
        console.error('DB lookup failed:', e);
      }

      // Fetch sources for autocomplete
      try {
        const resp = await apiFetch('/api/sources');
        if (resp.ok) {
          state.sources = await resp.json();
        }
      } catch {}
    }
  }

  // ── Utilities ─────────────────────────────────────────────────────────────

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Listen for outbox sync from background ────────────────────────────────

  function onOutboxSynced(remaining) {
    updateOutboxBadge(remaining);
    if (remaining === 0) {
      showToast('Outbox synced');
      // Refresh server status
      checkServerStatus();
    }
  }

  // ── Public API ────────────────────────────────────────────────────────────

  return { open, onOutboxSynced };
}
