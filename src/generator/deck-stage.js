(function () {
  'use strict';

  class DeckStage extends HTMLElement {
    constructor() {
      super();
      this._idx = 0;
      this._slides = [];
      this._total = 0;
      this._onKey = this._onKey.bind(this);
      this._onResize = this._scale.bind(this);
    }

    connectedCallback() {
      this._slides = Array.from(this.querySelectorAll(':scope > section.slide'));
      this._total = this._slides.length;
      if (!this._total) return;

      document.body.style.margin = '0';
      document.body.style.padding = '0';
      document.body.style.overflow = 'hidden';
      document.body.style.background = '#111';

      this._show(0);
      document.addEventListener('keydown', this._onKey);
      window.addEventListener('resize', this._onResize);
      this._scale();
      this.setAttribute('data-ready', '');
      window.addEventListener('message', this._onMessage.bind(this));
    }

    disconnectedCallback() {
      document.removeEventListener('keydown', this._onKey);
      window.removeEventListener('resize', this._onResize);
    }

    _show(idx) {
      this._idx = Math.max(0, Math.min(idx, this._total - 1));

      this._slides.forEach((s, i) => {
        // Clear any inline styles / FX classes that might have been set previously
        s.style.position = '';
        s.style.top = '';
        s.style.left = '';
        s.style.width = '';
        s.style.transition = '';
        s.style.opacity = '';
        s.style.visibility = '';
        s.style.pointerEvents = '';
        s.style.transform = '';
        s.style.zIndex = '';
        s.style.removeProperty('--tr-dur');
        if (/\btr-[\w-]+/.test(s.className)) {
          s.className = s.className.replace(/\btr-[\w-]+/g, '').replace(/\s{2,}/g, ' ').trim();
        }

        if (i === this._idx) {
          s.style.display = '';
          s.setAttribute('data-deck-active', '');
        } else {
          s.style.display = 'none';
          s.removeAttribute('data-deck-active');
        }
      });

      const reduce   = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
      const isEval    = typeof navigator !== 'undefined' && navigator.webdriver;
      const editing   = typeof document !== 'undefined' && document.body && document.body.hasAttribute('data-editing');
      const firstShow = !this._hasStarted;
      this._hasStarted = true;
      if (!isEval) this.setAttribute('data-transitions', 'true');

      this.dispatchEvent(new CustomEvent('slidechange', {
        bubbles: true,
        detail: { index: this._idx, total: this._total }
      }));

      const slide = this._slides[this._idx];
      if (!slide) return;

      // Auto-fit FIRST — measure at natural size, before any transition/animation
      // transform can distort getBoundingClientRect — THEN play the FX.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        slide.querySelectorAll('[data-fit]').forEach(fitText);
        if (slide.classList.contains('toc-cards')) syncCardTitles(slide);
        slide.querySelectorAll('[data-fit-block]').forEach(fitBlock);
        if (slide.classList.contains('toc-cards')) syncCardScales(slide);

        if (!reduce && !isEval && !firstShow) applySlideTransition(slide);
        if (!reduce && !isEval && !editing)   playSlideAnimations(slide);
      }));
    }

    _onKey(e) {
      const fwd = ['ArrowRight', 'ArrowDown', ' ', 'PageDown'];
      const bwd = ['ArrowLeft', 'ArrowUp', 'PageUp'];
      if (fwd.includes(e.key)) {
        e.preventDefault();
        this._show(this._idx + 1);
      } else if (bwd.includes(e.key)) {
        e.preventDefault();
        this._show(this._idx - 1);
      } else if (e.key === 'Home') {
        e.preventDefault();
        this._show(0);
      } else if (e.key === 'End') {
        e.preventDefault();
        this._show(this._total - 1);
      }
    }

    _scale() {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const scale = Math.min(vw / 1920, vh / 1080);
      const ox = (vw - 1920 * scale) / 2;
      const oy = (vh - 1080 * scale) / 2;
      this.style.width = '1920px';
      this.style.height = '1080px';
      this.style.transform = `scale(${scale.toFixed(6)})`;
      this.style.transformOrigin = 'top left';
      this.style.position = 'fixed';
      this.style.left = `${ox.toFixed(2)}px`;
      this.style.top = `${oy.toFixed(2)}px`;
    }

    _onMessage(e) {
      const d = e.data;
      if (!d || !d.type) return;
      const panel = document.getElementById('twPanel');
      if (d.type === '__activate_edit_mode' && panel) panel.classList.add('show');
      else if (d.type === '__deactivate_edit_mode' && panel) panel.classList.remove('show');
    }
  }

  if (!customElements.get('deck-stage')) {
    customElements.define('deck-stage', DeckStage);
  }


  function fitText(el) {
    const lines = parseInt(el.dataset.fitLines || '2', 10);
    const minPx = parseInt(el.dataset.fitMin || '16', 10);
    const maxPx = parseInt(el.dataset.fitMax || '400', 10);
    const scopeSel = el.dataset.fitScope;
    const fillHeight = 'fitFill' in el.dataset;
    const parent = scopeSel
      ? (el.closest(scopeSel) || el.parentElement)
      : (el.closest('.slide') || el.parentElement);
    if (!parent) return;

    const cs = getComputedStyle(parent);
    const padH = parseFloat(cs.paddingLeft || '0') + parseFloat(cs.paddingRight || '0');
    const availW = scopeSel ? parent.offsetWidth - padH : parent.offsetWidth - 240;

    const prevOverflow = el.style.overflow;
    el.style.overflow = 'hidden';

    let lo = minPx, hi = maxPx, best = minPx;
    if (fillHeight) {
      const availH = el.offsetHeight;
      if (availH <= 0) { el.style.overflow = prevOverflow; el.style.fontSize = minPx + 'px'; return; }
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        el.style.fontSize = mid + 'px';
        if (el.scrollHeight + 6 <= availH && el.scrollWidth <= availW) {
          best = mid;
          lo = mid + 1;
        } else {
          hi = mid - 1;
        }
      }
    } else {
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        el.style.fontSize = mid + 'px';
        const rawLH = getComputedStyle(el).lineHeight;
        const lh = rawLH === 'normal' ? mid * 1.2 : parseFloat(rawLH);
        const actualLines = lh > 0 ? Math.ceil(el.scrollHeight / lh) : Infinity;
        if (el.scrollWidth <= availW && actualLines <= lines) {
          best = mid;
          lo = mid + 1;
        } else {
          hi = mid - 1;
        }
      }
    }
    el.style.fontSize = best + 'px';
    el.style.overflow = prevOverflow;
  }

  function fitBlock(el) {
    el.style.transform = '';
    el.style.transformOrigin = '';
    el.style.justifyContent = '';
    const reserve = parseInt(el.dataset.fitReserve || '0', 10);
    const availH = el.offsetHeight - reserve;
    const availW = el.offsetWidth;
    let contentH = el.scrollHeight;
    let contentW = el.scrollWidth;
    // For flex/grid children with overflow:visible, scrollHeight === offsetHeight
    // (browser ignores visible overflow). Fall back to measuring child bounding boxes.
    if (contentH <= availH + 4 && el.children.length > 0) {
      const box = el.getBoundingClientRect();
      const elTop = box.top;
      const elLeft = box.left;
      for (const child of el.children) {
        const cbox = child.getBoundingClientRect();
        contentH = Math.max(contentH, cbox.bottom - elTop);
        contentW = Math.max(contentW, cbox.right - elLeft);
      }
    }
    let scale = 1;
    if (contentH > availH && availH > 0) {
      scale = Math.min(scale, availH / contentH);
    }
    if (contentW > availW && availW > 0) {
      scale = Math.min(scale, availW / contentW);
    }
    if (scale < 1) {
      el.style.justifyContent = 'flex-start';
      el.style.transform = `scale(${Math.max(0.1, scale).toFixed(4)})`;
      el.style.transformOrigin = 'top center';
    }
  }

  function syncCardTitles(slide) {
    const h3s = Array.from(slide.querySelectorAll('.toc-cards .card h3, .card h3'));
    if (h3s.length < 2) return;
    const minPx = Math.min(...h3s.map(h => parseFloat(h.style.fontSize) || parseFloat(getComputedStyle(h).fontSize)));
    h3s.forEach(h => { h.style.fontSize = minPx + 'px'; });
  }

  function syncCardScales(slide) {
    // Some layouts might have data-fit-block on the card, some on card-content.
    const cards = Array.from(slide.querySelectorAll('.toc-cards [data-fit-block]')).filter(el => el.classList.contains('card') || el.classList.contains('card-content'));
    if (cards.length < 2) return;
    let minScale = 1;
    cards.forEach(c => {
      const t = c.style.transform;
      if (t && t.startsWith('scale(')) {
        const s = parseFloat(t.slice(6));
        if (!isNaN(s) && s < minScale) minScale = s;
      }
    });
    if (minScale < 1) {
      cards.forEach(c => {
        c.style.transform = `scale(${minScale.toFixed(4)})`;
        c.style.transformOrigin = 'top center';
        c.style.justifyContent = 'flex-start';
      });
    }
  }

  /* ── Slide transition (per-slide via data-transition) ─────────────────── */
  function applySlideTransition(slide) {
    var tr = slide.dataset.transition || 'fade';
    if (tr === 'none') return;
    var cls = 'tr-' + tr;
    if (slide.dataset.transitionDur) {
      slide.style.setProperty('--tr-dur', (parseFloat(slide.dataset.transitionDur) || 0.45) + 's');
    }
    slide.classList.remove(cls);
    void slide.offsetWidth;               /* restart the animation */
    slide.classList.add(cls);
    var done = function () {
      slide.classList.remove(cls);
      slide.style.removeProperty('--tr-dur');
      slide.removeEventListener('animationend', done);
    };
    slide.addEventListener('animationend', done);
    setTimeout(done, 1600);               /* safety net if animationend is missed */
  }

  /* ── Per-object entrance animations (data-anim + data-anim-order/dur/delay) ─ */
  function playSlideAnimations(slide) {
    var els = Array.prototype.slice.call(slide.querySelectorAll('[data-anim]'))
      .filter(function (el) {
        var a = el.dataset.anim;
        return a && a !== 'none' && a !== 'anim-none';
      });
    if (!els.length) return;
    els.sort(function (a, b) {
      return (parseInt(a.dataset.animOrder || '0', 10) - parseInt(b.dataset.animOrder || '0', 10));
    });
    var STAGGER = 0.18;                    /* seconds between sequential objects */
    els.forEach(function (el, i) {
      var name = el.dataset.anim;
      var cls  = name.indexOf('anim-') === 0 ? name : 'anim-' + name;
      var dur  = parseFloat(el.dataset.animDur || '0.5') || 0.5;
      var delay = (parseFloat(el.dataset.animDelay || '0') || 0) + i * STAGGER;
      el.style.setProperty('--anim-dur', dur + 's');
      el.style.setProperty('--anim-delay', delay + 's');
      el.classList.remove(cls);
      void el.offsetWidth;
      el.classList.add(cls);
      var done = function () {
        /* drop the class so the element reverts to its natural / auto-fit state */
        el.classList.remove(cls);
        el.style.removeProperty('--anim-dur');
        el.style.removeProperty('--anim-delay');
        el.removeEventListener('animationend', done);
      };
      el.addEventListener('animationend', done);
      setTimeout(done, (delay + dur) * 1000 + 500);
    });
  }

  function runAutoFit() {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      document.querySelectorAll('section.slide').forEach(slide => {
        if (slide.style.display === 'none') return;
        slide.querySelectorAll('[data-fit]').forEach(fitText);
        if (slide.classList.contains('toc-cards')) syncCardTitles(slide);
        slide.querySelectorAll('[data-fit-block]').forEach(fitBlock);
        if (slide.classList.contains('toc-cards')) syncCardScales(slide);
      });
    }));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', runAutoFit);
  } else {
    runAutoFit();
  }
  document.fonts.ready.then(() => {
    // Extra RAF pair so font metrics are flushed to layout before measuring
    requestAnimationFrame(() => requestAnimationFrame(runAutoFit));
  });
  // Fallback: some browsers resolve fonts.ready before metrics propagate
  setTimeout(runAutoFit, 400);
  window.addEventListener('resize', runAutoFit);
  window.addEventListener('load', runAutoFit);

  // Monitor late-loading images that might alter layouts
  if (typeof document !== 'undefined') {
    document.querySelectorAll('img').forEach(img => {
      if (!img.complete) {
        img.addEventListener('load', runAutoFit);
      }
    });
  }

})();
