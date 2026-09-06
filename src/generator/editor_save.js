/* editor_save.js — Shared save layer (window.EditorSave). Ctrl+S → save in place
   via File System Access API; fallback = download. The editor passes a clean,
   re-editable HTML string to saveHtml(); this module just decides where bytes go. */
(function () {
  'use strict';
  if (window.EditorSave) return; /* idempotent — injected in both outputs */

  var _handle    = null; /* FileSystemFileHandle kept for the session       */
  var _supported = (typeof window !== 'undefined' &&
                    typeof window.showSaveFilePicker === 'function');

  /* ── low-level writers ─────────────────────────────────────────────── */
  function _download(html, name) {
    var blob = new Blob([html], { type: 'text/html' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }

  function _writeHandle(handle, html) {
    return handle.createWritable().then(function (w) {
      return w.write(new Blob([html], { type: 'text/html' })).then(function () {
        return w.close();
      });
    });
  }

  /* ── public: saveHtml(html, defaultName, opts) → Promise<{method,name}> ─
   *   opts.forceNew : true → always show the picker (i.e. "Save As…")
   */
  function saveHtml(html, defaultName, opts) {
    opts = opts || {};
    defaultName = defaultName || 'slides_edited.html';

    if (_supported) {
      var pick = (!_handle || opts.forceNew)
        ? window.showSaveFilePicker({
            suggestedName: defaultName,
            types: [{ description: 'HTML slide deck', accept: { 'text/html': ['.html'] } }],
          }).then(function (h) { _handle = h; return h; })
        : Promise.resolve(_handle);

      return pick
        .then(function (h) { return _writeHandle(h, html).then(function () { return h; }); })
        .then(function (h) {
          toast('Saved · ' + (h.name || defaultName));
          return { method: 'fsapi', name: h.name || defaultName };
        })
        .catch(function (err) {
          if (err && err.name === 'AbortError') return { method: 'cancelled' };
          /* Any other failure (e.g. picker unavailable on file://): fall back */
          _handle = null;
          _download(html, defaultName);
          toast('Downloaded · ' + defaultName);
          return { method: 'download', name: defaultName };
        });
    }

    _download(html, defaultName);
    toast('Downloaded · ' + defaultName);
    return Promise.resolve({ method: 'download', name: defaultName });
  }

  function hasHandle()  { return !!_handle; }
  function handleName() { return _handle ? (_handle.name || '') : ''; }

  /* ── tiny non-blocking toast ───────────────────────────────────────── */
  var _toastEl = null, _toastT = null;
  function toast(msg) {
    if (!_toastEl) {
      _toastEl = document.createElement('div');
      _toastEl.setAttribute('data-editor-toast', '');
      _toastEl.style.cssText =
        'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);' +
        'background:rgba(20,22,32,.96);color:#fff;padding:10px 18px;border-radius:8px;' +
        'font:14px/1.4 system-ui,-apple-system,Segoe UI,sans-serif;z-index:2147483647;' +
        'box-shadow:0 6px 24px rgba(0,0,0,.45);opacity:0;transition:opacity .2s;pointer-events:none';
      document.body.appendChild(_toastEl);
    }
    _toastEl.textContent = msg;
    _toastEl.style.opacity = '1';
    clearTimeout(_toastT);
    _toastT = setTimeout(function () { if (_toastEl) _toastEl.style.opacity = '0'; }, 1800);
  }

  /* ── localStorage autosave manager ─────────────────────────────────── *
   *   makeAutosave(key, getState[, delayMs]) → { schedule, flush, load, clear }
   *   getState() must return a JSON-serialisable snapshot.
   */
  function makeAutosave(key, getState, delayMs) {
    delayMs = delayMs || 2000;
    var t = null;
    function _persist() {
      try {
        localStorage.setItem(key, JSON.stringify({ ts: Date.now(), state: getState() }));
        return true;
      } catch (e) { return false; /* quota / private mode */ }
    }
    return {
      schedule: function () { clearTimeout(t); t = setTimeout(_persist, delayMs); },
      flush:    function () { clearTimeout(t); return _persist(); },
      load: function (maxAgeMs) {
        try {
          var raw = localStorage.getItem(key);
          if (!raw) return null;
          var d = JSON.parse(raw);
          if (maxAgeMs && Date.now() - (d.ts || 0) > maxAgeMs) { localStorage.removeItem(key); return null; }
          return d.state;
        } catch (e) { return null; }
      },
      clear: function () { try { localStorage.removeItem(key); } catch (e) {} },
    };
  }

  window.EditorSave = {
    saveHtml: saveHtml,
    hasHandle: hasHandle,
    handleName: handleName,
    toast: toast,
    makeAutosave: makeAutosave,
    supported: _supported,
  };
})();
