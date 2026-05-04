// ==UserScript==
// @name         Magpie
// @namespace    com.magpie.stash
// @version      1.8
// @description  One-click YouTube audio stash via local server
// @match        https://www.youtube.com/*
// @match        https://youtube.com/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const CLASS = 'magpie-stash-btn';
  const STATUS_ID = 'magpie-status';
  const SERVER = 'http://127.0.0.1:7865';
  const LOG = (...args) => console.log('[Magpie]', ...args);

  LOG('userscript loaded on', location.href);

  // ---- Persistent stash-result panel ----------------------------------------

  function findStatusParent() {
    return (
      document.querySelector('ytd-watch-metadata') ||
      document.querySelector('#below') ||
      document.querySelector('#info') ||
      document.body
    );
  }

  function ensureStatusEl() {
    let el = document.getElementById(STATUS_ID);
    if (el && el.isConnected) return el;

    el = document.createElement('div');
    el.id = STATUS_ID;
    el.style.cssText = `
      margin: 12px 0 0;
      padding: 10px 14px;
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.10);
      border-radius: 12px;
      color: rgba(255, 255, 255, 0.85);
      font-size: 13px;
      line-height: 1.5;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
      display: none;
      word-break: break-all;
      user-select: text;
      max-width: 720px;
    `;

    const parent = findStatusParent();
    const actions = parent.querySelector('#actions');
    if (actions && actions.parentNode === parent) {
      actions.insertAdjacentElement('afterend', el);
    } else {
      parent.appendChild(el);
    }
    return el;
  }

  function showStashed(path) {
    const el = ensureStatusEl();
    el.innerHTML = '';

    const check = document.createElement('span');
    check.textContent = '✓';
    check.style.cssText = 'color: #4ade80; font-weight: 700; margin-right: 8px;';

    const label = document.createElement('span');
    label.textContent = 'Stashed at';
    label.style.cssText = 'color: rgba(255,255,255,0.55); margin-right: 8px; font-weight: 500;';

    const pathEl = document.createElement('code');
    pathEl.textContent = path;
    pathEl.title = 'Click to copy';
    pathEl.style.cssText = `
      font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
      color: #fff;
      font-size: 12.5px;
      cursor: pointer;
      padding: 1px 4px;
      border-radius: 4px;
      transition: background 120ms ease;
    `;
    pathEl.addEventListener('mouseenter', () => {
      pathEl.style.background = 'rgba(255,255,255,0.08)';
    });
    pathEl.addEventListener('mouseleave', () => {
      pathEl.style.background = 'transparent';
    });
    pathEl.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(path);
        const original = pathEl.textContent;
        pathEl.textContent = 'copied ✓';
        pathEl.style.color = '#4ade80';
        setTimeout(() => {
          pathEl.textContent = original;
          pathEl.style.color = '#fff';
        }, 1200);
      } catch (e) {
        LOG('clipboard copy failed:', e);
      }
    });

    el.appendChild(check);
    el.appendChild(label);
    el.appendChild(pathEl);
    el.style.display = 'block';
  }

  // ---- Button label helpers -------------------------------------------------

  const BASE_LABEL = 'Stash';

  // Download-arrow SVG path (Material Design file_download)
  const ICON_PATH = 'M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z';

  function setLabel(btn, text, opts = {}) {
    const span = btn.querySelector('.magpie-label');
    const svg = btn.querySelector('svg');
    if (span) span.textContent = text;
    if (svg) svg.style.display = (text === BASE_LABEL) ? '' : 'none';
    btn.disabled = !!opts.disabled;
    btn.style.opacity = opts.disabled ? '0.85' : '1';
    btn.style.cursor = opts.disabled ? 'wait' : 'pointer';
    if (opts.color) btn.style.background = opts.color;
  }

  function flash(btn, text, color, ms = 2500) {
    setLabel(btn, text, { color });
    setTimeout(() => setLabel(btn, BASE_LABEL, { color: 'rgba(255,255,255,0.1)' }), ms);
  }

  function progressLabel(msg) {
    const phase = msg.phase;
    const pct = msg.percent;
    if (phase === 'starting') return '⌛ Starting…';
    if (phase === 'downloading' && typeof pct === 'number') {
      return '⌛ ' + Math.round(pct) + '%';
    }
    if (phase === 'extracting') return '⌛ Converting…';
    if (phase === 'merging') return '⌛ Merging…';
    if (phase === 'fixing') return '⌛ Finishing…';
    return '⌛ Stashing…';
  }

  // ---- NDJSON stream parser -------------------------------------------------

  function parseNdjsonChunk(responseText, lastParsedLength) {
    const newText = responseText.slice(lastParsedLength);
    const lines = newText.split('\n');
    const messages = [];

    let consumedLength = lastParsedLength;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (i === lines.length - 1 && !newText.endsWith('\n')) {
        break;
      }
      consumedLength += line.length + 1;
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        messages.push(JSON.parse(trimmed));
      } catch (e) {
        LOG('failed to parse NDJSON line:', trimmed, e);
      }
    }

    return { messages, consumedLength };
  }

  // ---- Stash via local HTTP server ------------------------------------------

  let cachedToken = null;

  function fetchToken(callback) {
    if (cachedToken) {
      callback(cachedToken);
      return;
    }
    GM_xmlhttpRequest({
      method: 'GET',
      url: SERVER + '/token',
      onload: function (response) {
        try {
          const data = JSON.parse(response.responseText);
          cachedToken = data.token;
          callback(cachedToken);
        } catch (e) {
          LOG('failed to parse token response:', e);
          callback(null);
        }
      },
      onerror: function () {
        LOG('failed to fetch token — is the Magpie server running?');
        callback(null);
      }
    });
  }

  function stashViaServer(btn, url, name) {
    setLabel(btn, '⌛ Starting…', { disabled: true, color: '#3f3f3f' });
    LOG('sending stash request to local server', { url, name });

    fetchToken(function (token) {
      if (!token) {
        LOG('no auth token available');
        flash(btn, '⚠️ No server', '#7a2a2a', 4000);
        return;
      }

      let parsedUpTo = 0;
      let finished = false;

      GM_xmlhttpRequest({
        method: 'POST',
        url: SERVER + '/stash',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + token
        },
        data: JSON.stringify({ url: url, name: name }),
        responseType: 'text',

      onprogress: function (response) {
        if (finished) return;
        const result = parseNdjsonChunk(response.responseText, parsedUpTo);
        parsedUpTo = result.consumedLength;

        for (const msg of result.messages) {
          if (msg.type === 'progress') {
            setLabel(btn, progressLabel(msg), { disabled: true, color: '#3f3f3f' });
          } else if (msg.type === 'done') {
            finished = true;
            if (msg.ok) {
              LOG('saved:', msg.path);
              flash(btn, '✅ Stashed', '#2a6a2a');
              if (msg.path) showStashed(msg.path);
            } else {
              LOG('server error:', msg.error);
              flash(btn, '⚠️ Failed', '#7a2a2a', 4000);
              if (msg.error) console.error('[Magpie]', msg.error);
            }
          }
        }
      },

      onload: function (response) {
        if (!finished) {
          const result = parseNdjsonChunk(response.responseText, parsedUpTo);
          for (const msg of result.messages) {
            if (msg.type === 'progress') {
              setLabel(btn, progressLabel(msg), { disabled: true, color: '#3f3f3f' });
            } else if (msg.type === 'done') {
              finished = true;
              if (msg.ok) {
                LOG('saved:', msg.path);
                flash(btn, '✅ Stashed', '#2a6a2a');
                if (msg.path) showStashed(msg.path);
              } else {
                LOG('server error:', msg.error);
                flash(btn, '⚠️ Failed', '#7a2a2a', 4000);
                if (msg.error) console.error('[Magpie]', msg.error);
              }
            }
          }
          if (!finished) {
            if (response.status >= 200 && response.status < 300) {
              LOG('stream ended without explicit done message');
              flash(btn, '⚠️ Unknown', '#7a5a2a', 3000);
            } else {
              LOG('server returned status', response.status);
              flash(btn, '⚠️ Failed', '#7a2a2a', 4000);
            }
          }
        }
      },

      onerror: function (response) {
        if (finished) return;
        LOG('request error:', response.statusText || 'connection failed');
        flash(btn, '⚠️ Error', '#7a2a2a');
      },

      ontimeout: function () {
        if (finished) return;
        LOG('request timed out');
        flash(btn, '⚠️ Timeout', '#7a2a2a');
      },

      timeout: 600000
    });
    });
  }

  // ---- Build button ---------------------------------------------------------

  function buildButton() {
    const btn = document.createElement('button');
    btn.className = CLASS;
    btn.style.cssText = `
      margin: 0 0 0 8px;
      padding: 0 16px;
      height: 36px;
      border-radius: 18px;
      border: none;
      background: rgba(255,255,255,0.1);
      color: #fff;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      font-family: "Roboto", "Arial", sans-serif;
      transition: background 120ms ease;
      display: inline-flex !important;
      align-items: center;
      align-self: center;
      justify-content: center;
      gap: 6px;
      visibility: visible !important;
      opacity: 1 !important;
      position: relative;
      flex-shrink: 0;
      white-space: nowrap;
      box-sizing: border-box;
    `;

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '20');
    svg.setAttribute('height', '20');
    svg.style.cssText = 'fill: currentColor; flex-shrink: 0; pointer-events: none;';
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', ICON_PATH);
    svg.appendChild(path);

    const span = document.createElement('span');
    span.className = 'magpie-label';
    span.textContent = BASE_LABEL;

    btn.appendChild(svg);
    btn.appendChild(span);

    btn.addEventListener('mouseenter', () => {
      if (!btn.disabled) btn.style.background = 'rgba(255,255,255,0.2)';
    });
    btn.addEventListener('mouseleave', () => {
      if (!btn.disabled) btn.style.background = 'rgba(255,255,255,0.1)';
    });

    btn.addEventListener('click', () => {
      if (btn.disabled) return;
      const url = window.location.href;
      const rawTitle = document.title.replace(/ - YouTube$/, '').trim();
      const safeDefault = rawTitle.replace(/[\/\\:*?"<>|]/g, '').trim();
      const filename = window.prompt('Save as (without .mp3):', safeDefault);
      if (!filename) return;
      const cleaned = filename.trim();
      if (!cleaned) return;

      stashViaServer(btn, url, cleaned);
    });

    return btn;
  }

  // ---- Injection strategies (tried in order) --------------------------------
  //
  // The action bar containers (#top-level-buttons-computed, ytd-menu-renderer,
  // #actions) all hard-clip their contents via overflow + fixed sizing.
  // Inserting inside them — even with CSS overrides — produces an invisible
  // button. So we stay OUTSIDE those containers entirely.
  //
  // Strategy A: insert as a sibling of #actions, inside the flex row that
  //   holds both #owner and #actions. The button appears in the same row,
  //   right before the Like/Share buttons. The parent row is a visible flex
  //   container (it renders Subscribe AND the action buttons) so adding one
  //   more flex item is safe.
  //
  // Strategy B: insert after #subscribe-button inside #owner.
  //
  // Strategy C: if ytd-watch-metadata exists but nothing else, append there.

  function tryStrategies() {
    const strategies = [
      // A — inside ytd-menu-renderer, before the "..." overflow button
      //     (between Save and hamburger)
      function () {
        const menu =
          document.querySelector('ytd-watch-metadata ytd-menu-renderer') ||
          document.querySelector('#actions ytd-menu-renderer');
        if (!menu) return null;
        const overflow =
          menu.querySelector(':scope > yt-button-shape:last-of-type') ||
          menu.querySelector(':scope > yt-icon-button');
        if (!overflow) return null;
        return { insert: (btn) => menu.insertBefore(btn, overflow), label: 'before overflow in ytd-menu-renderer' };
      },
      // B — sibling of #actions, inserted right after it in the row
      function () {
        const actions =
          document.querySelector('ytd-watch-metadata #actions') ||
          document.querySelector('#actions');
        if (!actions || !actions.parentNode) return null;
        const row = actions.parentNode;
        if (row === document.body || row === document.documentElement) return null;
        return { insert: (btn) => actions.insertAdjacentElement('afterend', btn), label: 'after #actions in row' };
      },
      // B — after subscribe button
      function () {
        const sub =
          document.querySelector('#owner #subscribe-button') ||
          document.querySelector('#owner ytd-subscribe-button-renderer');
        if (!sub) return null;
        return { insert: (btn) => sub.insertAdjacentElement('afterend', btn), label: 'after #subscribe-button' };
      },
      // C — append to #owner
      function () {
        const owner =
          document.querySelector('ytd-watch-metadata #owner') ||
          document.querySelector('#owner');
        if (!owner) return null;
        return { insert: (btn) => owner.appendChild(btn), label: 'appended to #owner' };
      },
      // D — append to ytd-watch-metadata
      function () {
        const meta = document.querySelector('ytd-watch-metadata');
        if (!meta) return null;
        return {
          insert: (btn) => {
            const anchor = meta.querySelector('#actions') || meta.querySelector('#owner');
            if (anchor) {
              let row = anchor;
              while (row.parentNode !== meta && row.parentNode) row = row.parentNode;
              if (row.parentNode === meta) {
                row.insertAdjacentElement('afterend', btn);
                return;
              }
            }
            meta.appendChild(btn);
          },
          label: 'in ytd-watch-metadata'
        };
      },
    ];

    for (const strat of strategies) {
      const result = strat();
      if (result) return result;
    }
    return null;
  }

  // ---- Injection logic ------------------------------------------------------

  let lastInjectedUrl = null;

  function inject() {
    if (!location.pathname.startsWith('/watch')) return;

    if (lastInjectedUrl !== null && location.href !== lastInjectedUrl) {
      document.querySelectorAll('.' + CLASS).forEach((b) => b.remove());
      const stale = document.getElementById(STATUS_ID);
      if (stale) stale.remove();
    }

    if (document.querySelector('.' + CLASS)) return;

    const strategy = tryStrategies();
    if (!strategy) {
      if (!inject._loggedNoTarget || Date.now() - inject._loggedNoTarget > 5000) {
        LOG('no target yet on', location.pathname + location.search);
        inject._loggedNoTarget = Date.now();
      }
      return;
    }

    const btn = buildButton();
    strategy.insert(btn);
    lastInjectedUrl = location.href;
    LOG('button injected:', strategy.label);

    // Verify visibility — if this strategy got clipped, try the next one
    setTimeout(() => {
      if (!btn.isConnected) return;
      const r = btn.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        LOG('button visible at', Math.round(r.x) + ',' + Math.round(r.y),
            Math.round(r.width) + 'x' + Math.round(r.height));
        return;
      }
      LOG('button NOT visible via', strategy.label, '— trying next strategy');
      // Log what's clipping it
      let el = btn.parentNode;
      while (el && el !== document.body) {
        const cs = getComputedStyle(el);
        LOG('  ', el.tagName + (el.id ? '#' + el.id : ''),
            'overflow=' + cs.overflow, 'display=' + cs.display,
            'w=' + el.offsetWidth, 'h=' + el.offsetHeight);
        el = el.parentNode;
      }
      btn.remove();
      // Brute-force: try remaining strategies
      const allStrats = [
        tryStrategies, // will skip A if #actions not found, etc.
      ];
      // Just go straight to the ytd-watch-metadata fallback
      const meta = document.querySelector('ytd-watch-metadata');
      if (meta) {
        const fallback = buildButton();
        const anchor = meta.querySelector('#actions') || meta.querySelector('#owner');
        if (anchor) {
          let row = anchor;
          while (row.parentNode !== meta && row.parentNode) row = row.parentNode;
          if (row.parentNode === meta) {
            row.insertAdjacentElement('afterend', fallback);
            LOG('fallback: inserted after row in ytd-watch-metadata');
            return;
          }
        }
        meta.appendChild(fallback);
        LOG('fallback: appended to ytd-watch-metadata');
      }
    }, 200);
  }

  inject();
  setInterval(inject, 300);

  // YouTube fires these during SPA navigation at different stages of DOM rebuild.
  for (const evt of ['yt-navigate-finish', 'yt-page-data-updated']) {
    document.addEventListener(evt, () => {
      setTimeout(inject, 0);
      setTimeout(inject, 100);
      setTimeout(inject, 300);
      setTimeout(inject, 700);
      setTimeout(inject, 1500);
      setTimeout(inject, 3000);
    });
  }

  new MutationObserver(inject).observe(document.body, { childList: true, subtree: true });
})();
