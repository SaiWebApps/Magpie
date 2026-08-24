// ==UserScript==
// @name         Magpie
// @namespace    com.magpie.stash
// @version      2.3.0
// @description  One-click YouTube audio/video stash via local server
// @match        https://www.youtube.com/*
// @match        https://youtube.com/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @run-at       document-idle
// @updateURL    http://127.0.0.1:7865/magpie.user.js
// @downloadURL  http://127.0.0.1:7865/magpie.user.js
// ==/UserScript==

// Install this from http://127.0.0.1:7865/magpie.user.js, not by pasting it.
// Installed that way, Tampermonkey re-reads this file from the Magpie server,
// so an edit here actually reaches the browser. Tampermonkey only applies an
// update when @version increases — bump it on every change to this file.

(function () {
  'use strict';

  const CLASS = 'magpie-stash-btn';
  const STATUS_ID = 'magpie-status';
  const PLAYLIST_CLASS = 'magpie-playlist-btn';
  const SERVER = 'http://127.0.0.1:7865';
  const LOG = (...args) => console.log('[Magpie]', ...args);

  LOG('userscript loaded on', location.href);

  const FORMATS = { mp3: 'Audio (MP3)', mp4: 'Video (MP4)' };
  function getFormat(key) { try { return localStorage.getItem(key || 'magpie-format') || 'mp3'; } catch (e) { return 'mp3'; } }
  function setFormat(fmt, key) { try { localStorage.setItem(key || 'magpie-format', fmt); } catch (e) {} }

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
    attachErrorTip(el);
    return el;
  }

  function showStashed(message, opts = {}) {
    const el = ensureStatusEl();
    el.innerHTML = '';
    el.title = '';
    el._error = '';
    el.style.cursor = '';
    hideErrorTip();

    const failed = !!opts.failed;

    const mark = document.createElement('span');
    mark.textContent = failed ? '⚠' : '✓';
    mark.style.cssText =
      'font-weight: 700; margin-right: 8px; color: ' + (failed ? '#f87171' : '#4ade80') + ';';

    const label = document.createElement('span');
    label.textContent = message || (failed ? 'Stash failed' : 'Stashed');
    label.style.cssText = 'color: rgba(255,255,255,0.85); font-weight: 500;';

    el.appendChild(mark);
    el.appendChild(label);

    if (opts.detail) {
      // Hovering anywhere on the row reveals the full error, including
      // multi-line output from yt-dlp.
      el._error = String(opts.detail);
      el.title = el._error;
      el.style.cursor = 'help';
      label.style.textDecoration = 'underline dotted rgba(255,255,255,0.45)';
      label.style.textUnderlineOffset = '3px';

      const hint = document.createElement('span');
      hint.textContent = ' — hover for details';
      hint.style.cssText = 'color: rgba(255,255,255,0.45); font-size: 12px;';
      el.appendChild(hint);
    }

    el.style.display = 'block';
  }

  // ---- Button label helpers -------------------------------------------------

  const BASE_LABEL = 'Stash';

  // Download-arrow SVG path (Material Design file_download)
  const ICON_PATH = 'M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z';

  function setLabel(btn, text, opts = {}) {
    const span = btn.querySelector('.magpie-label');
    const svg = btn.querySelector('svg');
    const divider = btn.querySelector('.magpie-divider');
    const chev = btn.querySelector('.magpie-chevron');
    const main = btn.querySelector('.magpie-main');
    if (span) span.textContent = text;
    const isDefault = text === (btn._defaultLabel || BASE_LABEL);
    // A stuck failure keeps the format dropdown reachable, so the next attempt
    // can use a different format without waiting for the label to reset.
    const showChevron = isDefault || !!opts.keepChevron;
    if (svg) svg.style.display = isDefault ? '' : 'none';
    if (divider) divider.style.display = showChevron ? '' : 'none';
    if (chev) chev.style.display = showChevron ? '' : 'none';
    if (main) main.style.paddingRight = showChevron ? '12px' : '16px';
    btn._disabled = !!opts.disabled;
    btn.style.opacity = opts.disabled ? '0.85' : '1';
    btn.style.cursor = opts.disabled ? 'wait' : 'pointer';
    if (opts.color) btn.style.background = opts.color;
  }

  function flash(btn, text, color, ms = 2500) {
    setLabel(btn, text, { color });
    setTimeout(() => setLabel(btn, btn._defaultLabel || BASE_LABEL, { color: 'rgba(255,255,255,0.1)' }), ms);
  }

  // ---- Error tooltip --------------------------------------------------------
  //
  // A native title= tooltip waits about a second before appearing and is easy
  // to miss, which made the error look like it was never there. This one shows
  // on the first hover with no delay.

  const TIP_ID = 'magpie-error-tip';

  function hideErrorTip() {
    const tip = document.getElementById(TIP_ID);
    if (tip) tip.remove();
  }

  function showErrorTip(anchor, text) {
    hideErrorTip();
    if (!text || !anchor || !anchor.isConnected) return;

    const tip = document.createElement('div');
    tip.id = TIP_ID;
    tip.textContent = String(text);
    tip.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      max-width: 460px;
      background: #1c1c1c;
      color: rgba(255,255,255,0.92);
      border: 1px solid rgba(255,255,255,0.16);
      border-left: 3px solid #f87171;
      border-radius: 8px;
      padding: 10px 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      z-index: 2147483647;
      box-shadow: 0 8px 24px rgba(0,0,0,0.55);
      pointer-events: none;
    `;
    document.body.appendChild(tip);

    // Measure first, then place, so a tall error near the bottom of the window
    // flips above the button instead of running off screen.
    const a = anchor.getBoundingClientRect();
    const t = tip.getBoundingClientRect();
    let top = a.bottom + 8;
    if (top + t.height > window.innerHeight - 8) top = a.top - t.height - 8;
    let left = a.left;
    if (left + t.width > window.innerWidth - 8) left = window.innerWidth - t.width - 8;
    tip.style.top = Math.max(8, top) + 'px';
    tip.style.left = Math.max(8, left) + 'px';
    return tip;
  }

  function attachErrorTip(el) {
    el.addEventListener('mouseenter', () => {
      if (el._error) showErrorTip(el, el._error);
    });
    el.addEventListener('mouseleave', hideErrorTip);
  }

  // Failures stay put. A message that clears itself after a few seconds takes
  // the reason with it, so the button holds the failed state until the next
  // stash and keeps the full error one hover away.
  function setFailure(btn, text, detail, color = '#7a2a2a') {
    setLabel(btn, text, { color, keepChevron: true });
    btn._failed = true;
    btn._error = detail ? String(detail) : '';
    btn.title = btn._error;  // fallback if the custom tip fails to mount
    if (detail) console.error('[Magpie]', detail);
  }

  function clearFailure(btn) {
    btn._failed = false;
    btn._error = '';
    btn.title = '';
    hideErrorTip();
  }

  function progressLabel(msg) {
    const phase = msg.phase;
    const pct = msg.percent;
    if (phase === 'starting') return '⌛ Starting…';
    if (phase === 'loading') return '⌛ Loading…';
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

  function stashViaServer(btn, url, name, format, playlist) {
    clearFailure(btn);
    setLabel(btn, '⌛ Starting…', { disabled: true, color: '#3f3f3f' });
    LOG('sending stash request to local server', { url, name });

    fetchToken(function (token) {
      if (!token) {
        LOG('no auth token available');
        const why = 'No response from ' + SERVER +
                    '\n\nStart it with:   make start' +
                    '\nCheck it with:   make status';
        setFailure(btn, '⚠️ No server', why);
        showStashed('Magpie server not reachable', { failed: true, detail: why });
        return;
      }

      let parsedUpTo = 0;
      let finished = false;
      let playlistInfo = '';

      function handleMsg(msg) {
        if (msg.type === 'progress') {
          if (msg.phase === 'playlist_item') {
            playlistInfo = msg.current + '/' + msg.total + ' — ';
            setLabel(btn, '⌛ ' + msg.current + '/' + msg.total, { disabled: true, color: '#3f3f3f' });
          } else if (msg.phase === 'backing_up') {
            // Downloading has finished by this point, so drop the "12/12 — "
            // download prefix instead of stacking two counters on the button.
            playlistInfo = '';
            const many = typeof msg.current === 'number' && msg.total > 1;
            const count = many ? ' ' + msg.current + '/' + msg.total : '';
            setLabel(btn, '⌛ Backing up' + count + '…', { disabled: true, color: '#3f3f3f' });
          } else {
            const label = progressLabel(msg);
            if (playlistInfo) {
              setLabel(btn, '⌛ ' + playlistInfo + label.replace(/^⌛ /, ''), { disabled: true, color: '#3f3f3f' });
            } else {
              setLabel(btn, label, { disabled: true, color: '#3f3f3f' });
            }
          }
        } else if (msg.type === 'done') {
          finished = true;
          if (msg.ok && msg.backup_status === 'failed') {
            // The local file saved fine — only the iBroadcast copy failed, so
            // this stays a warning about the backup, not about the stash.
            LOG('saved, but backup failed:', msg.path);
            setFailure(btn, '⚠️ Backup failed', msg.backup_error);
            showStashed(msg.message, { failed: true, detail: msg.backup_error });
          } else if (msg.ok) {
            LOG('saved:', msg.path);
            flash(btn, '✅ Stashed', '#2a6a2a');
            showStashed(msg.message);
          } else {
            LOG('server error:', msg.error);
            setFailure(btn, '⚠️ Failed', msg.error);
            showStashed('Stash failed', { failed: true, detail: msg.error });
          }
        }
      }

      GM_xmlhttpRequest({
        method: 'POST',
        url: SERVER + '/stash',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + token
        },
        data: JSON.stringify({ url: url, name: name, format: format, playlist: !!playlist }),
        responseType: 'text',

      onprogress: function (response) {
        if (finished) return;
        const result = parseNdjsonChunk(response.responseText, parsedUpTo);
        parsedUpTo = result.consumedLength;
        for (const msg of result.messages) handleMsg(msg);
      },

      onload: function (response) {
        if (!finished) {
          const result = parseNdjsonChunk(response.responseText, parsedUpTo);
          for (const msg of result.messages) handleMsg(msg);
          if (!finished) {
            if (response.status >= 200 && response.status < 300) {
              LOG('stream ended without explicit done message');
              const why = 'The server closed the connection without reporting a ' +
                          'result.\nThe file may or may not have saved.' +
                          '\n\nCheck with:   make logs';
              setFailure(btn, '⚠️ Unknown', why, '#7a5a2a');
              showStashed('Stash result unknown', { failed: true, detail: why });
            } else {
              LOG('server returned status', response.status);
              const why = 'Server returned HTTP ' + response.status +
                          '\n\n' + (response.responseText || '(no body)');
              setFailure(btn, '⚠️ Failed', why);
              showStashed('Stash failed', { failed: true, detail: why });
            }
          }
        }
      },

      onerror: function (response) {
        if (finished) return;
        const why = 'Could not reach ' + SERVER + '\n' +
                    (response && response.statusText ? response.statusText : 'connection failed') +
                    '\n\nCheck it with:   make status';
        LOG('request error:', why);
        setFailure(btn, '⚠️ Error', why);
        showStashed('Stash failed', { failed: true, detail: why });
      },

      ontimeout: function () {
        if (finished) return;
        const limit = playlist ? '60 minutes' : '10 minutes';
        const why = 'The server did not finish within ' + limit + '.' +
                    '\n\nCheck what it was doing:   make logs';
        LOG('request timed out');
        setFailure(btn, '⚠️ Timeout', why);
        showStashed('Stash timed out', { failed: true, detail: why });
      },

      timeout: playlist ? 3600000 : 600000
    });
    });
  }

  // ---- Format popover -------------------------------------------------------

  function showFormatPopover(btn, anchor, formatKey) {
    const existing = document.getElementById('magpie-format-popover');
    if (existing) { existing.remove(); return; }

    const rect = anchor.getBoundingClientRect();
    const popover = document.createElement('div');
    popover.id = 'magpie-format-popover';
    popover.style.cssText = `
      position: fixed;
      top: ${rect.bottom + 4}px;
      left: ${rect.left + rect.width / 2}px;
      transform: translateX(-50%);
      background: #282828;
      border: 1px solid rgba(255,255,255,0.15);
      border-radius: 8px;
      padding: 4px 0;
      z-index: 2147483647;
      box-shadow: 0 4px 16px rgba(0,0,0,0.4);
      font-family: "Roboto", "Arial", sans-serif;
      font-size: 14px;
      min-width: 140px;
    `;

    const current = getFormat(formatKey);

    for (const [fmt, label] of Object.entries(FORMATS)) {
      const item = document.createElement('div');
      item.textContent = label;
      item.style.cssText = `
        padding: 8px 16px;
        color: ${fmt === current ? '#3ea6ff' : 'rgba(255,255,255,0.85)'};
        cursor: pointer;
        white-space: nowrap;
      `;
      item.addEventListener('mouseenter', () => { item.style.background = 'rgba(255,255,255,0.1)'; });
      item.addEventListener('mouseleave', () => { item.style.background = 'transparent'; });
      item.addEventListener('click', () => {
        setFormat(fmt, formatKey);
        popover.remove();
        LOG('format set to', fmt);
      });
      popover.appendChild(item);
    }

    document.body.appendChild(popover);

    const dismiss = (e) => {
      if (!popover.contains(e.target) && !anchor.contains(e.target)) {
        popover.remove();
        document.removeEventListener('click', dismiss, true);
      }
    };
    setTimeout(() => document.addEventListener('click', dismiss, true), 0);
  }

  // ---- Build button ---------------------------------------------------------

  function buildButton() {
    const wrap = document.createElement('div');
    wrap.className = CLASS;
    wrap.style.cssText = `
      margin: 0 0 0 8px;
      height: 36px;
      border-radius: 18px;
      border: none;
      background: rgba(255,255,255,0.1);
      color: #fff;
      font-size: 14px;
      font-weight: 500;
      font-family: "Roboto", "Arial", sans-serif;
      transition: background 120ms ease;
      display: inline-flex !important;
      align-items: center;
      align-self: center;
      justify-content: center;
      visibility: hidden;
      opacity: 1 !important;
      position: relative;
      flex-shrink: 0;
      white-space: nowrap;
      box-sizing: border-box;
      overflow: hidden;
    `;

    const main = document.createElement('div');
    main.className = 'magpie-main';
    main.style.cssText = `
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 0 12px 0 16px;
      height: 100%;
      cursor: pointer;
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

    main.appendChild(svg);
    main.appendChild(span);

    const divider = document.createElement('div');
    divider.className = 'magpie-divider';
    divider.style.cssText = 'width: 1px; height: 16px; background: rgba(255,255,255,0.2); flex-shrink: 0;';

    const chevron = document.createElement('div');
    chevron.className = 'magpie-chevron';
    chevron.style.cssText = `
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0 10px;
      height: 100%;
      cursor: pointer;
    `;

    const chevSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    chevSvg.setAttribute('viewBox', '0 0 24 24');
    chevSvg.setAttribute('width', '16');
    chevSvg.setAttribute('height', '16');
    chevSvg.style.cssText = 'fill: currentColor; pointer-events: none;';
    const chevPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    chevPath.setAttribute('d', 'M7 10l5 5 5-5z');
    chevSvg.appendChild(chevPath);
    chevron.appendChild(chevSvg);

    wrap.appendChild(main);
    wrap.appendChild(divider);
    wrap.appendChild(chevron);

    main.addEventListener('mouseenter', () => {
      if (!wrap._disabled) main.style.background = 'rgba(255,255,255,0.1)';
    });
    main.addEventListener('mouseleave', () => {
      main.style.background = 'transparent';
    });

    chevron.addEventListener('mouseenter', () => {
      if (!wrap._disabled) chevron.style.background = 'rgba(255,255,255,0.1)';
    });
    chevron.addEventListener('mouseleave', () => {
      chevron.style.background = 'transparent';
    });

    main.addEventListener('click', () => {
      if (wrap._disabled) return;
      const existing = document.getElementById('magpie-format-popover');
      if (existing) existing.remove();
      const format = getFormat(wrap._formatKey);
      const ext = '.' + format;
      const url = window.location.href;
      const rawTitle = document.title.replace(/ - YouTube$/, '').trim();
      const safeDefault = rawTitle.replace(/[\/\\:*?"<>|]/g, '').trim();
      const filename = window.prompt('Save as (without ' + ext + '):', safeDefault);
      if (!filename) return;
      const cleaned = filename.trim();
      if (!cleaned) return;
      stashViaServer(wrap, url, cleaned, format);
    });

    chevron.addEventListener('click', (e) => {
      e.stopPropagation();
      if (wrap._disabled) return;
      showFormatPopover(wrap, chevron, wrap._formatKey);
    });

    wrap._formatKey = 'magpie-format';
    wrap._defaultLabel = BASE_LABEL;
    attachErrorTip(wrap);

    return wrap;
  }

  // ---- Playlist button ------------------------------------------------------

  function buildPlaylistButton() {
    const wrap = document.createElement('div');
    wrap.className = PLAYLIST_CLASS;
    wrap.style.cssText = `
      display: inline-flex;
      align-items: center;
      height: 32px;
      border-radius: 16px;
      background: rgba(255,255,255,0.1);
      color: #fff;
      font-size: 13px;
      font-weight: 500;
      font-family: "Roboto", "Arial", sans-serif;
      transition: background 120ms ease;
      white-space: nowrap;
      margin-left: 8px;
      box-sizing: border-box;
      user-select: none;
      flex-shrink: 0;
      overflow: hidden;
    `;

    const main = document.createElement('div');
    main.className = 'magpie-main';
    main.style.cssText = `
      display: flex;
      align-items: center;
      gap: 4px;
      padding: 0 10px 0 12px;
      height: 100%;
      cursor: pointer;
    `;

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '16');
    svg.setAttribute('height', '16');
    svg.style.cssText = 'fill: currentColor; flex-shrink: 0; pointer-events: none;';
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', ICON_PATH);
    svg.appendChild(path);

    const span = document.createElement('span');
    span.className = 'magpie-label';
    span.textContent = 'Stash Playlist';

    main.appendChild(svg);
    main.appendChild(span);

    const divider = document.createElement('div');
    divider.className = 'magpie-divider';
    divider.style.cssText = 'width: 1px; height: 14px; background: rgba(255,255,255,0.2); flex-shrink: 0;';

    const chevron = document.createElement('div');
    chevron.className = 'magpie-chevron';
    chevron.style.cssText = `
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0 8px;
      height: 100%;
      cursor: pointer;
    `;

    const chevSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    chevSvg.setAttribute('viewBox', '0 0 24 24');
    chevSvg.setAttribute('width', '14');
    chevSvg.setAttribute('height', '14');
    chevSvg.style.cssText = 'fill: currentColor; pointer-events: none;';
    const chevPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    chevPath.setAttribute('d', 'M7 10l5 5 5-5z');
    chevSvg.appendChild(chevPath);
    chevron.appendChild(chevSvg);

    wrap.appendChild(main);
    wrap.appendChild(divider);
    wrap.appendChild(chevron);

    main.addEventListener('mouseenter', () => {
      if (!wrap._disabled) main.style.background = 'rgba(255,255,255,0.1)';
    });
    main.addEventListener('mouseleave', () => {
      main.style.background = 'transparent';
    });

    chevron.addEventListener('mouseenter', () => {
      if (!wrap._disabled) chevron.style.background = 'rgba(255,255,255,0.1)';
    });
    chevron.addEventListener('mouseleave', () => {
      chevron.style.background = 'transparent';
    });

    main.addEventListener('click', () => {
      if (wrap._disabled) return;
      const existing = document.getElementById('magpie-format-popover');
      if (existing) existing.remove();
      const params = new URLSearchParams(window.location.search);
      const listId = params.get('list');
      if (!listId) {
        flash(wrap, '⚠️ No playlist', '#7a2a2a', 3000);
        return;
      }
      const playlistUrl = 'https://www.youtube.com/playlist?list=' + listId;
      const panel = document.querySelector('ytd-playlist-panel-renderer');
      const titleEl = panel && (panel.querySelector('#title') || panel.querySelector('h3'));
      const playlistTitle = (titleEl && titleEl.textContent.trim()) || 'playlist';
      const safeTitle = playlistTitle.replace(/[\/\\:*?"<>|]/g, '').trim();
      const format = getFormat(wrap._formatKey);
      const ext = '.' + format;
      const folderName = window.prompt('Save playlist as folder (format: ' + format.toUpperCase() + '):', safeTitle);
      if (!folderName) return;
      const cleaned = folderName.trim();
      if (!cleaned) return;
      stashViaServer(wrap, playlistUrl, cleaned, format, true);
    });

    chevron.addEventListener('click', (e) => {
      e.stopPropagation();
      if (wrap._disabled) return;
      showFormatPopover(wrap, chevron, wrap._formatKey);
    });

    wrap._formatKey = 'magpie-playlist-format';
    wrap._defaultLabel = 'Stash Playlist';
    attachErrorTip(wrap);

    return wrap;
  }

  function injectPlaylistButton() {
    if (document.querySelector('.' + PLAYLIST_CLASS)) return;
    const params = new URLSearchParams(location.search);
    if (!params.get('list')) return;

    const panel = document.querySelector('ytd-playlist-panel-renderer');
    if (!panel) return;

    const controls =
      panel.querySelector('#playlist-action-menu') ||
      panel.querySelector('#top-level-buttons-computed');

    if (controls) {
      const overflow =
        controls.querySelector(':scope > yt-button-shape:last-of-type') ||
        controls.querySelector(':scope > yt-icon-button:last-of-type');
      const btn = buildPlaylistButton();
      if (overflow) {
        controls.insertBefore(btn, overflow);
      } else {
        controls.appendChild(btn);
      }
      LOG('playlist button injected into controls');
      return;
    }

    const header = panel.querySelector('#header-description, #header, .header');
    if (header) {
      const btn = buildPlaylistButton();
      header.appendChild(btn);
      LOG('playlist button injected into header');
    }
  }

  // ---- Injection strategies (tried in order) --------------------------------
  //
  // The action bar containers (#top-level-buttons-computed, ytd-menu-renderer,
  // #actions) all hard-clip their contents via overflow + fixed sizing.
  // If a strategy produces a clipped button, the visibility check removes it
  // and the retry loop (setInterval + MutationObserver) will try again.
  //
  // Strategy A: inside ytd-menu-renderer, before the overflow "..." button.
  // Strategy B: sibling of #actions in its parent flex row.
  // Strategy C: after #subscribe-button inside #owner.
  // Strategy D: append to #owner.

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
      // C — after subscribe button
      function () {
        const sub =
          document.querySelector('#owner #subscribe-button') ||
          document.querySelector('#owner ytd-subscribe-button-renderer');
        if (!sub) return null;
        return { insert: (btn) => sub.insertAdjacentElement('afterend', btn), label: 'after #subscribe-button' };
      },
      // D — append to #owner
      function () {
        const owner =
          document.querySelector('ytd-watch-metadata #owner') ||
          document.querySelector('#owner');
        if (!owner) return null;
        return { insert: (btn) => owner.appendChild(btn), label: 'appended to #owner' };
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
      const popover = document.getElementById('magpie-format-popover');
      if (popover) popover.remove();
      document.querySelectorAll('.' + PLAYLIST_CLASS).forEach((b) => b.remove());
      hideErrorTip();
    }

    injectPlaylistButton();

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

    // Verify visibility after layout settles — if clipped, remove and let retry loop handle it
    requestAnimationFrame(() => { requestAnimationFrame(() => {
      if (!btn.isConnected) return;
      const r = btn.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        btn.style.visibility = 'visible';
        LOG('button visible at', Math.round(r.x) + ',' + Math.round(r.y),
            Math.round(r.width) + 'x' + Math.round(r.height));
        return;
      }
      LOG('button clipped via', strategy.label, '— removing, will retry');
      btn.remove();
    }); });
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
