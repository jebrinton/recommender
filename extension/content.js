// content.js — Injected on every page. Creates sidebar host + handles messages.

const API_BASE = 'http://localhost:7432';

// Don't run on chrome:// or extension pages
if (/^(chrome|chrome-extension|edge|about):/.test(window.location.protocol)) {
  // bail silently
} else {
  init();
}

function init() {
  let sidebarHost = null;
  let shadowRoot = null;
  let isOpen = false;
  let sidebarModule = null; // will hold sidebar.js exports

  // ── Metadata extraction ─────────────────────────────────────────────────────

  function extractPageMetadata() {
    const meta = (name) => {
      const el = document.querySelector(
        `meta[property="${name}"], meta[name="${name}"]`
      );
      return el?.content?.trim() || '';
    };

    const url = window.location.href;
    const hostname = window.location.hostname.replace(/^www\./, '');

    // Title: prefer og:title, fall back to document.title
    const title = meta('og:title') || document.title || '';

    // Source: prefer og:site_name, then JSON-LD publisher, then domain heuristic
    let source = meta('og:site_name');
    if (!source) {
      const ldScripts = document.querySelectorAll('script[type="application/ld+json"]');
      for (const s of ldScripts) {
        try {
          const data = JSON.parse(s.textContent);
          const items = Array.isArray(data) ? data : [data];
          for (const item of items) {
            const pub = item.publisher?.name;
            if (pub) { source = pub; break; }
          }
          if (source) break;
        } catch {}
      }
    }
    if (!source) {
      // Domain heuristic: strip TLD, capitalize
      const parts = hostname.split('.');
      const name = parts.length >= 2 ? parts[parts.length - 2] : parts[0];
      source = name.charAt(0).toUpperCase() + name.slice(1);
    }

    // Description for context (not shown in sidebar, but useful for future summary gen)
    const description = meta('og:description') || meta('description') || '';

    return { url, title, source, description };
  }

  // ── Sidebar injection ───────────────────────────────────────────────────────

  async function createSidebar() {
    // Host element
    sidebarHost = document.createElement('div');
    sidebarHost.id = 'recommender-sidebar-host';
    document.body.appendChild(sidebarHost);

    // Shadow DOM for style isolation
    shadowRoot = sidebarHost.attachShadow({ mode: 'closed' });

    // Load sidebar CSS
    const cssUrl = chrome.runtime.getURL('sidebar.css');
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = cssUrl;
    shadowRoot.appendChild(link);

    // Load sidebar HTML
    const htmlUrl = chrome.runtime.getURL('sidebar.html');
    const resp = await fetch(htmlUrl);
    const html = await resp.text();
    const container = document.createElement('div');
    container.className = 'sidebar-container';
    container.innerHTML = html;
    shadowRoot.appendChild(container);

    // Load sidebar.js logic
    const { initSidebar } = await import(chrome.runtime.getURL('sidebar.js'));
    sidebarModule = initSidebar(shadowRoot, {
      extractMetadata: extractPageMetadata,
      apiBase: API_BASE,
      onClose: () => closeSidebar(),
    });
  }

  // ── Host element styles (injected into main document — minimal) ─────────────

  const hostStyle = document.createElement('style');
  hostStyle.id = 'recommender-host-style';
  hostStyle.textContent = `
    #recommender-sidebar-host {
      position: fixed;
      top: 0;
      right: 0;
      width: 420px;
      height: 100vh;
      z-index: 2147483647;
      transform: translateX(100%);
      transition: transform 0.25s ease;
      pointer-events: none;
    }
    #recommender-sidebar-host.open {
      transform: translateX(0);
      pointer-events: auto;
    }
  `;
  document.head.appendChild(hostStyle);

  // ── Open / close logic ──────────────────────────────────────────────────────

  async function openSidebar() {
    if (!sidebarHost) {
      await createSidebar();
    }
    const metadata = extractPageMetadata();
    if (sidebarModule) {
      await sidebarModule.open(metadata);
    }
    sidebarHost.classList.add('open');
    isOpen = true;
  }

  function closeSidebar() {
    if (sidebarHost) {
      sidebarHost.classList.remove('open');
    }
    isOpen = false;
  }

  function toggleSidebar() {
    if (isOpen) {
      closeSidebar();
    } else {
      openSidebar();
    }
  }

  // ── Listen for messages from background ───────────────────────────────────

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === 'toggle-sidebar') {
      toggleSidebar();
    }
    if (msg.action === 'outbox-synced' && sidebarModule) {
      sidebarModule.onOutboxSynced(msg.remaining);
    }
  });
}
