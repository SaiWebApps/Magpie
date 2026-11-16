// ==UserScript==
// @name         Magpie (from disk)
// @namespace    com.magpie.stash.disk
// @version      1.0.0
// @description  Runs /Applications/Magpie/magpie.user.js straight from disk, so an edit there is live after a page reload.
// @match        https://www.youtube.com/*
// @match        https://youtube.com/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @run-at       document-idle
// @require      file:///Applications/Magpie/magpie.user.js
// ==/UserScript==

// Development install of Magpie. Tampermonkey ignores the header of a
// @require'd file, so everything the real script needs — the pages it runs on,
// GM_xmlhttpRequest, permission to reach 127.0.0.1 — is declared above and
// must be kept in step with magpie.user.js by hand.
//
// Needs "Allow access to file URLs" switched on for Tampermonkey in the
// browser's extension settings; without it the @require fails to load.
//
// The namespace differs from the real script's on purpose: Tampermonkey
// identifies a script by name and namespace, and this must not replace the
// URL-installed "Magpie". Disable that one while this is on, or every
// YouTube page gets two Stash buttons.
