/**
 * fabric_editor.js — Fabric.js Slide Canvas Editor
 * Steps 1-4: Canvas init, auto-scale, object builders, ribbon binding,
 * custom Canva-style bounding box, undo/redo history.
 * Fabric.js v5.x API (callback-based, not Promise).
 * Architecture: Canvas <-> JSON as single source of truth.
 * No DOM elements for slide content — all objects are fabric.js instances.
 */
(function () {
  'use strict';

  /* ─── Constants ─── */
  const SLIDE_W = 1920;
  const SLIDE_H = 1080;
  const CANVAS_MARGIN = 40;

  /* ─── State ─── */
  let _canvas        = null;
  /* Per-slide history is stored in each _slides[i].history / .historyIdx.
   * _globalHistory is kept only as a legacy alias — no longer used for undo. */
  var _batchSave        = false;
  let _isRestoring   = false;
  const MAX_HISTORY  = 30;

  /* ─── Slide State ─── */
  let _slides       = [{ json: null, thumb: null, presImage: null, history: [], historyIdx: -1 }];
  let _currentSlide = 0;
  let _currentSpec  = null;   // last loaded spec — kept for theme re-apply
  let _clipboard    = null;   // copy/paste buffer (Fabric object clone)
  let _blobAnimVer  = 0;     // incremented on each slide load to stop stale RAF loops
  let _pendingImagePlaceholder = null; // set when user dblclicks an image placeholder rect

  /* ════════════════════════════════════════════════════════
   *  SECTION 1 — UI Shell (full 4-zone layout)
   * ════════════════════════════════════════════════════════ */
  function buildShell() {
    /* ── App root ── */
    const root = document.createElement('div');
    root.id = 'ed-root';
    document.body.appendChild(root);

    /* ── Toolbar / Ribbon ── */
    const toolbar = document.createElement('div');
    toolbar.id = 'ed-toolbar';
    toolbar.innerHTML = `
      <!-- Row 1: Quick Access Bar + Tab Strip + Zoom -->
      <div id="ed-topbar">
        <div class="ed-logo">Canvas Editor</div>
        <div class="ed-group">
          <button class="ed-btn icon off" id="btn-undo" title="Undo (Ctrl+Z)">↩</button>
          <button class="ed-btn icon off" id="btn-redo" title="Redo (Ctrl+Y)">↪</button>
        </div>
        <div id="ed-tabs">
          <button class="ed-tab active" data-tab="home">Home</button>
          <button class="ed-tab" data-tab="insert">Insert</button>
          <button class="ed-tab" data-tab="design">Design</button>
          <button class="ed-tab" data-tab="slideshow">Slide Show</button>
          <!-- Contextual tabs — shown when specific object types are selected -->
          <button class="ed-tab ctx-tab ctx-shape"   id="ed-tab-shape"        data-tab="shape-format"   style="display:none">Shape Format</button>
          <button class="ed-tab ctx-tab ctx-picture"  id="ed-tab-picture"      data-tab="picture-format" style="display:none">Picture Format</button>
          <button class="ed-tab ctx-tab ctx-table"    id="ed-tab-table-design" data-tab="table-design"   style="display:none">Table Design</button>
          <button class="ed-tab ctx-tab ctx-table"    id="ed-tab-table-layout" data-tab="table-layout"   style="display:none">Table Layout</button>
          <button class="ed-tab ctx-tab ctx-eq"       id="ed-tab-equation"     data-tab="equation-format" style="display:none">Equation</button>
        </div>
        <div class="ed-group" style="margin-left:auto">
          <button class="ed-btn icon" id="btn-zoom-out" title="Zoom Out">−</button>
          <span class="ed-zoom-label" id="ed-zoom-label">100%</span>
          <button class="ed-btn icon" id="btn-zoom-fit" title="Fit to Window">⤢</button>
          <button class="ed-btn icon" id="btn-zoom-in"  title="Zoom In">+</button>
        </div>
      </div>

      <!-- Row 2: Ribbon panels -->
      <div id="ed-ribbon">

        <!-- ══ HOME TAB ══ -->
        <div class="tab-panel active" data-panel="home">

          <!-- Clipboard -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="btn-cut" title="Cut (Ctrl+X)">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M9.64 7.64A3.96 3.96 0 0 0 10 6a4 4 0 1 0-4 4c.59 0 1.14-.13 1.64-.36L9 11.29 4.35 16A3.96 3.96 0 0 0 4 18a4 4 0 1 0 4-4c-.59 0-1.14.13-1.64.36L4.71 12.7 9.64 7.64zM6 8a2 2 0 1 1 0-4 2 2 0 0 1 0 4zm0 12a2 2 0 1 1 0-4 2 2 0 0 1 0 4zm7-7.5 1.41-1.41L18 14.58V11h2v7h-7v-2h3.59L13 13.5z"/></svg>
                  Cut
                </button>
                <button class="ribbon-btn-sm" id="btn-copy" title="Copy (Ctrl+C)">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
                  Copy
                </button>
                <button class="ribbon-btn-sm" id="btn-paste" title="Paste (Ctrl+V)">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M19 2h-4.18C14.4.84 13.3 0 12 0c-1.3 0-2.4.84-2.82 2H5c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-7 0c.55 0 1 .45 1 1s-.45 1-1 1-1-.45-1-1 .45-1 1-1zm7 18H5V4h2v3h10V4h2v16z"/></svg>
                  Paste
                </button>
              </div>
            </div>
            <div class="rg-label">Clipboard</div>
          </div>

          <!-- Font -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-font-col">
                <div class="rg-font-row">
                  <select class="ribbon-select" id="sel-font-family" style="width:148px">
                    <optgroup label="── Sans-serif ──">
                    <option value="Open Sans, sans-serif">Open Sans</option>
                    <option value="Montserrat, sans-serif">Montserrat</option>
                    <option value="Poppins, sans-serif">Poppins</option>
                    <option value="Raleway, sans-serif">Raleway</option>
                    <option value="Nunito, sans-serif">Nunito</option>
                    <option value="DM Sans, sans-serif">DM Sans</option>
                    <option value="Josefin Sans, sans-serif">Josefin Sans</option>
                    <option value="Oswald, sans-serif">Oswald</option>
                    <option value="Space Grotesk, sans-serif">Space Grotesk</option>
                    <option value="Arial, sans-serif">Arial</option>
                    </optgroup>
                    <optgroup label="── Serif ──">
                    <option value="Lora, serif">Lora</option>
                    <option value="Playfair Display, serif">Playfair Display</option>
                    <option value="Merriweather, serif">Merriweather</option>
                    <option value="Georgia, serif">Georgia</option>
                    </optgroup>
                    <optgroup label="── Display ──">
                    <option value="Bebas Neue, sans-serif">Bebas Neue</option>
                    </optgroup>
                    <optgroup label="── Monospace ──">
                    <option value="IBM Plex Mono, monospace">IBM Plex Mono</option>
                    <option value="Source Code Pro, monospace">Source Code Pro</option>
                    </select>
                  <input class="ribbon-input" id="inp-font-size" type="number" min="6" max="400" value="60" title="Font size" style="width:44px" />
                </div>
                <div class="rg-font-row">
                  <button class="ribbon-btn-sm" id="btn-bold"      title="Bold (Ctrl+B)"><b>B</b></button>
                  <button class="ribbon-btn-sm" id="btn-italic"    title="Italic (Ctrl+I)"><i style="font-family:Georgia">I</i></button>
                  <button class="ribbon-btn-sm" id="btn-underline" title="Underline (Ctrl+U)"><u>U</u></button>
                  <div style="width:1px;background:var(--border);height:14px;margin:0 2px;"></div>
                  <label class="ribbon-color-btn" title="Text Color">
                    <span>A</span>
                    <div class="swatch-bar" id="bar-text-color" style="background:#1a1a2e"></div>
                    <input type="color" id="inp-text-color" value="#1a1a2e" style="display:none" />
                  </label>
                </div>
              </div>
            </div>
            <div class="rg-label">Font</div>
          </div>

          <!-- Paragraph -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-font-col">
                <div class="rg-font-row">
                  <button class="ribbon-btn-sm" id="btn-align-left"    title="Align Left">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M15 15H3v2h12v-2zm0-8H3v2h12V7zM3 13h18v-2H3v2zm0 8h18v-2H3v2zM3 3v2h18V3H3z"/></svg>
                  </button>
                  <button class="ribbon-btn-sm" id="btn-align-center"  title="Align Center">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M7 15v2h10v-2H7zm-4 6h18v-2H3v2zm0-8h18v-2H3v2zm4-6v2h10V7H7zM3 3v2h18V3H3z"/></svg>
                  </button>
                  <button class="ribbon-btn-sm" id="btn-align-right"   title="Align Right">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M3 21h18v-2H3v2zm6-4h12v-2H9v2zm-6-4h18v-2H3v2zm6-4h12V7H9v2zM3 3v2h18V3H3z"/></svg>
                  </button>
                  <button class="ribbon-btn-sm" id="btn-align-justify" title="Justify">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M3 21h18v-2H3v2zm0-4h18v-2H3v2zm0-4h18v-2H3v2zm0-4h18V7H3v2zm0-6v2h18V3H3z"/></svg>
                  </button>
                </div>
                <div class="rg-font-row">
                  <!-- Bullet split-button -->
                  <div class="ed-split-btn" style="position:relative">
                    <button id="btn-bullet-main" class="ed-btn" title="Bullets">•</button>
                    <button id="btn-bullet-dd" class="ed-btn ed-split-arrow" title="Bullet type">▾</button>
                    <div class="ed-dropdown-menu" id="dd-bullet-menu">
                      <div class="ed-dd-item" data-bullet="disc">•&nbsp; Bullet (disc)</div>
                      <div class="ed-dd-item" data-bullet="dash">–&nbsp; Bullet (dash)</div>
                      <div class="ed-dd-item" data-bullet="arrow">→&nbsp; Bullet (arrow)</div>
                      <div class="ed-dd-item" data-bullet="check">✓&nbsp; Bullet (check)</div>
                      <div class="ed-dd-item" data-bullet="diamond">◆&nbsp; Bullet (diamond)</div>
                      <div class="ed-dd-item" data-bullet="circle">○&nbsp; Bullet (circle)</div>
                      <div class="ed-dd-item" data-bullet="star">★&nbsp; Bullet (star)</div>
                    </div>
                  </div>
                  <!-- Numbering split-button -->
                  <div class="ed-split-btn" style="position:relative">
                    <button id="btn-num-main" class="ed-btn" title="Numbering">1.</button>
                    <button id="btn-num-dd" class="ed-btn ed-split-arrow" title="Numbering type">▾</button>
                    <div class="ed-dropdown-menu" id="dd-num-menu">
                      <div class="ed-dd-item" data-num="num">1.&nbsp; Numbered</div>
                      <div class="ed-dd-item" data-num="alpha">a.&nbsp; Alphabetic</div>
                      <div class="ed-dd-item" data-num="roman">i.&nbsp; Roman numeral</div>
                    </div>
                  </div>
                  <!-- Line spacing split-button -->
                  <div class="ed-split-btn" style="position:relative">
                    <button id="btn-ls-main" class="ed-btn" title="Line Spacing" style="font-size:15px;letter-spacing:-1px">&#x2261;</button>
                    <button id="btn-ls-dd" class="ed-btn ed-split-arrow" title="Line Spacing">▾</button>
                    <div class="ed-dropdown-menu" id="dd-ls-menu">
                      <div class="ed-dd-item" data-ls="1.0">1.0</div>
                      <div class="ed-dd-item" data-ls="1.5">1.5</div>
                      <div class="ed-dd-item" data-ls="2.0">2.0</div>
                      <div class="ed-dd-item" data-ls="2.5">2.5</div>
                      <div class="ed-dd-item" data-ls="3.0">3.0</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div class="rg-label">Paragraph</div>
          </div>

          <!-- Drawing: Text + quick shapes -->
          <div class="ribbon-group">
            <div class="rg-body">
              <button class="ribbon-btn-lg" id="btn-add-text" title="Insert Text Box (T)">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M5 4v3h5.5v12h3V7H19V4z"/></svg>
                Text
              </button>
              <div class="rg-sep"></div>
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="btn-home-rect"     title="Rectangle">▭ Rect</button>
                <button class="ribbon-btn-sm" id="btn-home-circle"   title="Circle">◯ Circle</button>
                <button class="ribbon-btn-sm" id="btn-home-triangle" title="Triangle">△ Triangle</button>
              </div>
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="btn-home-line"  title="Line">╱ Line</button>
                <button class="ribbon-btn-sm" id="btn-home-arrow" title="Arrow">→ Arrow</button>
                <div class="ed-dropdown" id="dd-shapes">
                  <button class="ribbon-btn-sm" id="btn-shapes-toggle" title="All shapes">⬡ More ▾</button>
                  <div class="ed-dropdown-menu" id="dd-shapes-menu">
                    <div class="ed-dd-item" id="btn-add-rect">▭&nbsp; Rectangle</div>
                    <div class="ed-dd-item" id="btn-add-rounded">▢&nbsp; Rounded Rect</div>
                    <div class="ed-dd-item" id="btn-add-circle">◯&nbsp; Circle / Ellipse</div>
                    <div class="ed-dd-item" id="btn-add-triangle">△&nbsp; Triangle</div>
                    <div class="ed-dd-item" id="btn-add-diamond">◇&nbsp; Diamond</div>
                    <div class="ed-dd-item" id="btn-add-star">★&nbsp; Star</div>
                    <div class="ed-dd-sep"></div>
                    <div class="ed-dd-item" id="btn-add-line">╱&nbsp; Line</div>
                    <div class="ed-dd-item" id="btn-add-arrow">→&nbsp; Arrow</div>
                  </div>
                </div>
              </div>
            </div>
            <div class="rg-label">Drawing</div>
          </div>

          <!-- Position: center + distribute on slide -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="btn-align-cx" title="Center horizontally on slide">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M11 2H13V22H11zM2 11H7V13H2zM17 11H22V13H17z"/></svg>
                  Ctr H
                </button>
                <button class="ribbon-btn-sm" id="btn-align-cy" title="Center vertically on slide">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M2 11H22V13H2zM11 17H13V22H11zM11 2H13V7H11z"/></svg>
                  Ctr V
                </button>
              </div>
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="btn-dist-h" title="Distribute horizontally">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M3 3H5V21H3zM19 3H21V21H19zM8 10H16V14H8z"/></svg>
                  Dist H
                </button>
                <button class="ribbon-btn-sm" id="btn-dist-v" title="Distribute vertically">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M3 3H21V5H3zM3 19H21V21H3zM10 8H14V16H10z"/></svg>
                  Dist V
                </button>
              </div>
            </div>
            <div class="rg-label">Position</div>
          </div>

          <!-- Arrange: Group + Layer order -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col" style="gap:2px">
                <button class="ribbon-btn-sm" id="btn-home-group"   title="Group selected objects (Ctrl+G)">⊞ Group</button>
                <button class="ribbon-btn-sm" id="btn-home-ungroup" title="Ungroup (Ctrl+Shift+G)">⊟ Ungroup</button>
              </div>
              <div class="rg-col" style="gap:2px">
                <button class="ribbon-btn-sm" id="btn-home-to-front" title="Bring to Front">⬆ Front</button>
                <button class="ribbon-btn-sm" id="btn-home-to-back"  title="Send to Back">⬇ Back</button>
              </div>
              <div class="rg-col" style="gap:2px">
                <button class="ribbon-btn-sm" id="btn-home-fwd" title="Bring Forward">▲ Fwd</button>
                <button class="ribbon-btn-sm" id="btn-home-bk"  title="Send Backward">▼ Bk</button>
              </div>
            </div>
            <div class="rg-label">Arrange</div>
          </div>

          <!-- Format: fill / stroke / stroke-width for selected shape -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col">
                <div style="display:flex;align-items:center;gap:3px;height:22px">
                  <label class="ribbon-color-btn" title="Fill Color">
                    <span style="font-size:11px">■</span>
                    <div class="swatch-bar" id="bar-fill-color" style="background:#4f8ef7"></div>
                    <input type="color" id="inp-fill-color" value="#4f8ef7" style="display:none" />
                  </label>
                  <label class="ribbon-color-btn" title="Stroke Color">
                    <span style="font-size:11px">□</span>
                    <div class="swatch-bar" id="bar-stroke-color" style="background:#4f8ef7"></div>
                    <input type="color" id="inp-stroke-color" value="#4f8ef7" style="display:none" />
                  </label>
                </div>
                <div style="display:flex;align-items:center;gap:4px;height:22px">
                  <span style="font-size:10px;color:var(--text-secondary)">W</span>
                  <input class="ribbon-input" id="inp-stroke-width" type="number" min="0" max="40" value="2" title="Stroke width" style="width:38px" />
                </div>
              </div>
            </div>
            <div class="rg-label">Format</div>
          </div>

        </div><!-- /home -->

        <!-- ══ INSERT TAB ══ -->
        <div class="tab-panel" data-panel="insert">

          <!-- Slides -->
          <div class="ribbon-group">
            <div class="rg-body">
              <button class="ribbon-btn-lg" id="btn-slide-new-ins" title="New Slide">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-2 10h-4v4h-2v-4H7v-2h4V7h2v4h4v2z"/></svg>
                New Slide
              </button>
            </div>
            <div class="rg-label">Slides</div>
          </div>

          <!-- Tables -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="ed-dropdown" id="dd-ins-table">
                <button class="ribbon-btn-lg" id="btn-ins-table" title="Insert Table">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM8 20H4v-4h4v4zm0-6H4v-4h4v4zm0-6H4V4h4v4zm6 12h-4v-4h4v4zm0-6h-4v-4h4v4zm0-6h-4V4h4v4zm6 12h-4v-4h4v4zm0-6h-4v-4h4v4zm0-6h-4V4h4v4z"/></svg>
                  Table
                </button>
                <div class="ed-dropdown-menu ed-tbl-grid-menu" id="dd-ins-table-menu">
                  <div class="ed-tbl-grid-label" id="tbl-grid-label">Insert table</div>
                  <div class="ed-tbl-grid" id="tbl-grid"></div>
                </div>
              </div>
            </div>
            <div class="rg-label">Tables</div>
          </div>

          <!-- Images -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col">
                <button class="ribbon-btn-lg" id="btn-ins-img" title="Insert picture from file">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
                  Pictures
                </button>
              </div>
            </div>
            <div class="rg-label">Images</div>
          </div>

          <!-- Illustrations -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="ed-dropdown" id="dd-ins-shapes">
                <button class="ribbon-btn-lg" id="btn-ins-shapes-toggle" title="Insert Shape">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="5" width="18" height="14" rx="1.5"/></svg>
                  Shapes ▾
                </button>
                <div class="ed-dropdown-menu" id="dd-ins-shapes-menu">
                  <div class="ed-dd-item" data-ins="rect">▭&nbsp; Rectangle</div>
                  <div class="ed-dd-item" data-ins="rounded">▢&nbsp; Rounded Rect</div>
                  <div class="ed-dd-item" data-ins="circle">◯&nbsp; Circle</div>
                  <div class="ed-dd-item" data-ins="triangle">△&nbsp; Triangle</div>
                  <div class="ed-dd-item" data-ins="diamond">◇&nbsp; Diamond</div>
                  <div class="ed-dd-item" data-ins="star">★&nbsp; Star</div>
                  <div class="ed-dd-sep"></div>
                  <div class="ed-dd-item" data-ins="line">╱&nbsp; Line</div>
                  <div class="ed-dd-item" data-ins="arrow">→&nbsp; Arrow</div>
                </div>
              </div>
              <div class="rg-sep"></div>
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="btn-ins-icon" title="Insert icon / emoji">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm3.5-9c.83 0 1.5-.67 1.5-1.5S16.33 8 15.5 8 14 8.67 14 9.5s.67 1.5 1.5 1.5zm-7 0c.83 0 1.5-.67 1.5-1.5S9.33 8 8.5 8 7 8.67 7 9.5 7.67 11 8.5 11zm3.5 6.5c2.33 0 4.31-1.46 5.11-3.5H6.89c.8 2.04 2.78 3.5 5.11 3.5z"/></svg>
                  Icons
                </button>
                <div class="ed-dropdown" id="dd-ins-lists">
                  <button class="ribbon-btn-sm" id="btn-ins-lists-toggle" title="Insert List">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M4 10.5c-.83 0-1.5.67-1.5 1.5s.67 1.5 1.5 1.5 1.5-.67 1.5-1.5-.67-1.5-1.5-1.5zm0-6c-.83 0-1.5.67-1.5 1.5S3.17 7.5 4 7.5 5.5 6.83 5.5 6 4.83 4.5 4 4.5zm0 12c-.83 0-1.5.68-1.5 1.5s.68 1.5 1.5 1.5 1.5-.68 1.5-1.5-.67-1.5-1.5-1.5zM7 19h14v-2H7v2zm0-6h14v-2H7v2zm0-8v2h14V5H7z"/></svg>
                    List ▾
                  </button>
                  <div class="ed-dropdown-menu" id="dd-ins-lists-menu">
                    <div class="ed-dd-item" data-list="disc">•&nbsp; Bullet (disc)</div>
                    <div class="ed-dd-item" data-list="dash">–&nbsp; Bullet (dash)</div>
                    <div class="ed-dd-item" data-list="arrow">→&nbsp; Bullet (arrow)</div>
                    <div class="ed-dd-item" data-list="check">✓&nbsp; Bullet (check)</div>
                    <div class="ed-dd-item" data-list="diamond">◆&nbsp; Bullet (diamond)</div>
                    <div class="ed-dd-sep"></div>
                    <div class="ed-dd-item" data-list="num">1.&nbsp; Numbered</div>
                    <div class="ed-dd-item" data-list="alpha">a.&nbsp; Alphabetic</div>
                    <div class="ed-dd-item" data-list="roman">i.&nbsp; Roman numeral</div>
                  </div>
                </div>
              </div>
            </div>
            <div class="rg-label">Illustrations</div>
          </div>

          <!-- Links -->
          <div class="ribbon-group">
            <div class="rg-body">
              <button class="ribbon-btn-lg" id="btn-ins-link" title="Insert hyperlink text">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z"/></svg>
                Link
              </button>
            </div>
            <div class="rg-label">Links</div>
          </div>

          <!-- Text -->
          <div class="ribbon-group">
            <div class="rg-body">
              <button class="ribbon-btn-lg" id="btn-ins-text" title="Insert Text Box">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M5 4v3h5.5v12h3V7H19V4z"/></svg>
                Text Box
              </button>
              <button class="ribbon-btn-lg" id="btn-ins-hf" title="Insert Header &amp; Footer">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M20 3H4v2h16V3zm0 16H4v2h16v-2zM4 15h16v-2H4v2zm0-4h16V9H4v2z"/></svg>
                Header &amp;<br>Footer
              </button>
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="btn-ins-wordart" title="Insert styled WordArt text">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M5 4v3h5.5v12h3V7H19V4z"/></svg>
                  WordArt
                </button>
                <button class="ribbon-btn-sm" id="btn-ins-date" title="Insert date / time">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M19 3h-1V1h-2v2H8V1H6v2H5c-1.11 0-1.99.9-1.99 2L3 19c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V8h14v11zM7 10h5v5H7z"/></svg>
                  Date &amp; Time
                </button>
                <button class="ribbon-btn-sm" id="btn-ins-slidenum" title="Insert slide number">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 4c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14c-2.67 0-5.01-1.37-6.37-3.44C7.26 13.1 9.53 12 12 12s4.74 1.1 6.37 2.56C17.01 16.63 14.67 18 12 18z"/></svg>
                  Slide #
                </button>
              </div>
            </div>
            <div class="rg-label">Text</div>
          </div>

          <!-- Symbols -->
          <div class="ribbon-group">
            <div class="rg-body">
              <button class="ribbon-btn-lg" id="btn-ins-formula" title="Insert equation / formula">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5.97 14L9 13l-3 4H4l4-5.5L4 6h2l3 4 4.03-4H15l-4 5.5 4.03 5.5h-2z"/></svg>
                Equation
              </button>
              <button class="ribbon-btn-lg" id="btn-ins-symbol" title="Insert special symbol">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zm4.24 16L12 15.45 7.77 18l1.12-4.81-3.73-3.23 4.92-.42L12 5l1.92 4.53 4.92.42-3.73 3.23L16.23 18z"/></svg>
                Symbol
              </button>
            </div>
            <div class="rg-label">Symbols</div>
          </div>

        </div><!-- /insert -->

        <!-- ══ DESIGN TAB ══ -->
        <div class="tab-panel" data-panel="design">

          <!-- Layout: custom dropdown so text is readable -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="ed-dropdown" id="dd-layout">
                <button class="ribbon-btn-sm" id="btn-layout-toggle" style="min-width:160px;justify-content:space-between;padding:2px 8px">
                  <span id="lbl-layout">Bullets</span>
                  <span style="opacity:.6;margin-left:6px">▾</span>
                </button>
                <div class="ed-dropdown-menu" id="dd-layout-menu" style="min-width:180px">
                  <div class="ed-dd-item" data-layout="only_content">Bullets</div>
                  <div class="ed-dd-item" data-layout="two_contents_in_a_slide_layout">Two columns</div>
                  <div class="ed-dd-item" data-layout="two_cols_content_layout">Two cols bullets</div>
                  <div class="ed-dd-item" data-layout="image_left_layout">Image left</div>
                  <div class="ed-dd-item" data-layout="image_right_layout">Image right</div>
                  <div class="ed-dd-item" data-layout="image_above_layout">Image above</div>
                  <div class="ed-dd-item" data-layout="image_below_layout">Image below</div>
                  <div class="ed-dd-item" data-layout="image_fullscreen_overlay_layout">Image full-screen</div>
                  <div class="ed-dd-item" data-layout="two_image_left_layout">Two images left</div>
                  <div class="ed-dd-item" data-layout="two_image_right_layout">Two images right</div>
                  <div class="ed-dd-item" data-layout="two_image_above_layout">Two images above</div>
                  <div class="ed-dd-item" data-layout="two_image_below_layout">Two images below</div>
                  <div class="ed-dd-sep"></div>
                  <div class="ed-dd-item" data-layout="comparison_layout">Table</div>
                  <div class="ed-dd-item" data-layout="table_above_layout">Table + bullets</div>
                  <div class="ed-dd-item" data-layout="data_table_layout">Data table</div>
                  <div class="ed-dd-item" data-layout="key_points_layout">Key points</div>
                  <div class="ed-dd-item" data-layout="steps_horizontal_layout">Steps</div>
                  <div class="ed-dd-item" data-layout="three_cols_content_layout">Three columns</div>
                  <div class="ed-dd-item" data-layout="grid_2x2_layout">2×2 Grid</div>
                  <div class="ed-dd-item" data-layout="nested_bullets_layout">Nested bullets</div>
                  <div class="ed-dd-item" data-layout="conclusion_cards_layout">Conclusions</div>
                  <div class="ed-dd-item" data-layout="numbered_conclusions_layout">Numbered conclusions</div>
                  <div class="ed-dd-item" data-layout="agenda_layout">Agenda</div>
                  <div class="ed-dd-item" data-layout="stats_cards_layout">Stats cards</div>
                  <div class="ed-dd-item" data-layout="pricing_cards_layout">Pricing cards</div>
                  <div class="ed-dd-item" data-layout="research_question_layout">Research question</div>
                  <div class="ed-dd-sep"></div>
                  <div class="ed-dd-item" data-layout="formula_top_layout">Formula (top)</div>
                  <div class="ed-dd-item" data-layout="formula_below_layout">Formula (below)</div>
                  <div class="ed-dd-sep"></div>
                  <div class="ed-dd-item" data-layout="section_divider_layout">Section divider</div>
                  <div class="ed-dd-item" data-layout="quote_layout">Quote</div>
                  <div class="ed-dd-item" data-layout="editorial_layout">Editorial</div>
                  <div class="ed-dd-item" data-layout="config_and_greeting_slide">Cover</div>
                  <div class="ed-dd-item" data-layout="end_layout">End slide</div>
                </div>
              </div>
            </div>
            <div class="rg-label">Layout</div>
          </div>

          <!-- Gradient / Theme Colors -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col">
                <div style="display:flex;align-items:center;gap:4px;height:22px">
                  <label class="ribbon-color-btn" title="Gradient start color (BG1)">
                    <span style="font-size:9px;letter-spacing:.02em">BG1</span>
                    <div class="swatch-bar" id="bar-bg1" style="background:#1a1a2e;width:24px"></div>
                    <input type="color" id="inp-bg1" value="#1a1a2e" style="display:none" />
                  </label>
                  <label class="ribbon-color-btn" title="Gradient end color (BG2)">
                    <span style="font-size:9px;letter-spacing:.02em">BG2</span>
                    <div class="swatch-bar" id="bar-bg2" style="background:#0d3b6e;width:24px"></div>
                    <input type="color" id="inp-bg2" value="#0d3b6e" style="display:none" />
                  </label>
                </div>
                <div style="display:flex;align-items:center;gap:4px;height:22px">
                  <label class="ribbon-color-btn" title="Slide text color">
                    <span style="font-size:9px">Txt</span>
                    <div class="swatch-bar" id="bar-theme-text" style="background:#e8e8f0;width:24px"></div>
                    <input type="color" id="inp-theme-text" value="#e8e8f0" style="display:none" />
                  </label>
                  <label class="ribbon-color-btn" title="Accent color">
                    <span style="font-size:9px">Acc</span>
                    <div class="swatch-bar" id="bar-theme-accent" style="background:#4a9eff;width:24px"></div>
                    <input type="color" id="inp-theme-accent" value="#4a9eff" style="display:none" />
                  </label>
                </div>
              </div>
            </div>
            <div class="rg-label">Gradient</div>
          </div>

          <!-- Slides -->
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="btn-add-slide-tb" title="Add blank slide">+ New Slide</button>
                <button class="ribbon-btn-sm" id="btn-dup-slide-tb" title="Duplicate current slide">⧉ Duplicate</button>
              </div>
            </div>
            <div class="rg-label">Slides</div>
          </div>

        </div><!-- /design -->

        <!-- ══ SLIDE SHOW TAB ══ -->
        <div class="tab-panel" data-panel="slideshow">

          <div class="ribbon-group">
            <div class="rg-body">
              <button class="ribbon-btn-lg" id="btn-present" title="Present (F5)">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                Present
              </button>
            </div>
            <div class="rg-label">Start</div>
          </div>

          <div class="ribbon-group">
            <div class="rg-body">
              <div class="ed-dropdown" id="dd-export">
                <button class="ribbon-btn-lg" id="btn-export-toggle" title="Export options">
                  <svg width="26" height="26" viewBox="0 0 24 24" fill="currentColor"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
                  Export ▾
                </button>
                <div class="ed-dropdown-menu" id="dd-export-menu">
                  <div class="ed-dd-item" id="btn-export-png">Current slide — PNG</div>
                  <div class="ed-dd-item" id="btn-export-all">All slides — PNG (zip)</div>
                  <div class="ed-dd-sep"></div>
                  <div class="ed-dd-item" id="btn-save-html">Save As New HTML</div>
                  <div class="ed-dd-sep"></div>
                  <div class="ed-dd-item" id="btn-export-json">Export JSON</div>
                  <div class="ed-dd-item" id="btn-import-json">Import JSON…</div>
                </div>
              </div>
            </div>
            <div class="rg-label">Export</div>
          </div>

        </div><!-- /slideshow -->

        <!-- ══ SHAPE FORMAT (contextual) ══ -->
        <div class="tab-panel" data-panel="shape-format">

          <!-- ── Insert Shapes ── -->
          <div class="ribbon-group" id="fmt-grp-shape-ins">
            <div class="rg-body">
              <div class="ed-dropdown" id="dd-fmt-shapes">
                <button class="ribbon-btn-lg" id="btn-fmt-shapes-toggle" title="Change shape">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="5" width="18" height="14" rx="1.5"/></svg>
                  Edit Shape ▾
                </button>
                <div class="ed-dropdown-menu" id="dd-fmt-shapes-menu">
                  <div class="ed-dd-item" data-change-shape="rect">▭&nbsp; Rectangle</div>
                  <div class="ed-dd-item" data-change-shape="rounded">▢&nbsp; Rounded Rect</div>
                  <div class="ed-dd-item" data-change-shape="circle">◯&nbsp; Circle</div>
                  <div class="ed-dd-item" data-change-shape="triangle">△&nbsp; Triangle</div>
                  <div class="ed-dd-item" data-change-shape="diamond">◇&nbsp; Diamond</div>
                  <div class="ed-dd-item" data-change-shape="star">★&nbsp; Star</div>
                </div>
              </div>
            </div>
            <div class="rg-label">Insert Shapes</div>
          </div>

          <!-- ── Shape Styles (shapes only, hidden for textboxes) ── -->
          <div class="ribbon-group" id="fmt-grp-shape-style">
            <div class="rg-body">
              <!-- Preset style swatches -->
              <div class="rg-col" style="gap:3px">
                <div style="display:flex;gap:2px">
                  <button class="fmt-shape-preset" id="fmt-preset-0" data-preset="0" title="Filled - Accent"></button>
                  <button class="fmt-shape-preset" id="fmt-preset-1" data-preset="1" title="Filled - Dark"></button>
                  <button class="fmt-shape-preset" id="fmt-preset-2" data-preset="2" title="Outline only"></button>
                </div>
                <div style="display:flex;gap:2px">
                  <button class="fmt-shape-preset" id="fmt-preset-3" data-preset="3" title="Subtle fill"></button>
                  <button class="fmt-shape-preset" id="fmt-preset-4" data-preset="4" title="Intense effect"></button>
                  <button class="fmt-shape-preset" id="fmt-preset-5" data-preset="5" title="No fill - Colored outline"></button>
                </div>
              </div>
              <div class="rg-sep"></div>
              <!-- Shape Fill large button -->
              <label class="ribbon-btn-lg" id="lbl-shape-fill" title="Shape fill color" style="flex-direction:column;align-items:center;gap:1px">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M16.56 8.94L7.62 0 6.21 1.41l2.38 2.38-5.15 5.15a1.49 1.49 0 000 2.12l5.5 5.5c.29.29.68.44 1.06.44s.77-.15 1.06-.44l5.5-5.5c.59-.58.59-1.53 0-2.12zM5.21 10L10 5.21 14.79 10H5.21zM19 11.5s-2 2.17-2 3.5c0 1.1.9 2 2 2s2-.9 2-2c0-1.33-2-3.5-2-3.5z"/></svg>
                <span style="font-size:9px;margin-top:1px">Shape Fill</span>
                <div id="bar-shape-fill" class="swatch-bar" style="width:28px;background:#4472C4"></div>
                <input type="color" id="fmt-shape-fill" value="#4472C4" style="display:none">
              </label>
              <!-- Outline + Stroke W + Opacity stacked -->
              <div class="rg-col" style="gap:3px">
                <label class="ribbon-color-btn" title="Shape outline color">
                  <span style="font-size:9px">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="1"/></svg>
                  </span>
                  <div class="swatch-bar" id="fmt-shape-outline-bar" style="width:22px;background:#4f8ef7"></div>
                  <input type="color" id="fmt-shape-outline" value="#4f8ef7" style="display:none">
                </label>
                <div style="display:flex;align-items:center;gap:2px;font-size:9px">
                  <span title="Stroke width">
                    <svg width="10" height="10" viewBox="0 0 20 20" fill="currentColor"><rect x="0" y="8" width="20" height="4" rx="2"/></svg>
                  </span>
                  <input class="ribbon-input" id="fmt-shape-stroke-w" type="number" min="0" max="40" value="0" style="width:36px" title="Stroke Width (px)">
                </div>
                <div style="display:flex;align-items:center;gap:2px;font-size:9px">
                  <span title="Opacity">⬡</span>
                  <input class="ribbon-input" id="fmt-shape-opacity" type="number" min="0" max="100" value="100" style="width:36px" title="Opacity %">%
                </div>
              </div>
            </div>
            <div class="rg-label">Shape Styles</div>
          </div>

          <!-- ── Text Fill (textboxes only, hidden for shapes) ── -->
          <div class="ribbon-group" id="fmt-grp-text-style" style="display:none">
            <div class="rg-body">
              <div class="rg-col" style="gap:4px">
                <!-- Text Color (Fill) -->
                <label class="ribbon-color-btn" id="lbl-fmt-text-color" title="Text Color">
                  <span style="font-family:Georgia,serif;font-size:15px;font-weight:bold;line-height:1">A</span>
                  <div class="swatch-bar" id="bar-fmt-text-color" style="width:22px;background:#1a1a2e"></div>
                  <input type="color" id="fmt-text-color" value="#1a1a2e" style="display:none">
                </label>
                <!-- Text Outline -->
                <label class="ribbon-color-btn" id="lbl-fmt-text-outline" title="Text Outline Color">
                  <span style="font-family:Georgia,serif;font-size:15px;font-weight:bold;line-height:1;-webkit-text-stroke:1px #fff;paint-order:stroke fill">A</span>
                  <div class="swatch-bar" id="bar-fmt-text-outline" style="width:22px;background:transparent;outline:1px solid rgba(255,255,255,.3)"></div>
                  <input type="color" id="fmt-text-outline" value="#ffffff" style="display:none">
                </label>
                <!-- Text Highlight / Background Color -->
                <label class="ribbon-color-btn" id="lbl-fmt-text-highlight" title="Text Highlight Color">
                  <span style="font-size:11px;padding:0 1px;border-radius:1px;background:#ffff00;color:#222;font-weight:600;line-height:1.4">ab</span>
                  <div class="swatch-bar" id="bar-fmt-text-highlight" style="width:22px;background:#FFFF00"></div>
                  <input type="color" id="fmt-text-highlight" value="#FFFF00" style="display:none">
                </label>
              </div>
              <div class="rg-sep"></div>
              <div class="rg-col" style="gap:3px">
                <div style="display:flex;align-items:center;gap:2px;font-size:9px">
                  <span title="Font size">Sz</span>
                  <input class="ribbon-input" id="fmt-text-size" type="number" min="6" max="400" value="60" style="width:42px">
                </div>
                <div style="display:flex;align-items:center;gap:2px;font-size:9px">
                  <span title="Text outline width">&#x25a1;W</span>
                  <input class="ribbon-input" id="fmt-text-stroke-w" type="number" min="0" max="20" value="0" style="width:36px">
                </div>
                <div style="display:flex;align-items:center;gap:2px;font-size:9px">
                  <span title="Opacity">&#x2B21;</span>
                  <input class="ribbon-input" id="fmt-text-opacity" type="number" min="0" max="100" value="100" style="width:36px">%
                </div>
              </div>
            </div>
            <div class="rg-label">Text Fill &amp; Outline</div>
          </div>

          <!-- ── WordArt / Text Effects (textboxes only) ── -->
          <div class="ribbon-group" id="fmt-grp-wordart" style="display:none">
            <div class="rg-body">
              <div class="rg-col" style="gap:3px">
                <button class="ribbon-btn-sm" id="fmt-txt-bold"      title="Bold"><b>B</b></button>
                <button class="ribbon-btn-sm" id="fmt-txt-italic"    title="Italic"><i>I</i></button>
                <button class="ribbon-btn-sm" id="fmt-txt-underline" title="Underline"><u>U</u></button>
              </div>
              <div class="rg-col" style="gap:3px">
                <button class="ribbon-btn-sm" id="fmt-txt-shadow" title="Text Shadow">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M5 4v3H4a2 2 0 00-2 2v8a2 2 0 002 2h14a2 2 0 002-2v-1h1V7a2 2 0 00-2-2H6V4H5zm1 5h12v8H6V9zm-2 2h1v6H4v-6z" opacity=".5"/><rect x="6" y="2" width="12" height="8" rx="1"/></svg>
                  Shadow
                </button>
                <button class="ribbon-btn-sm" id="fmt-txt-strikethrough" title="Strikethrough">
                  <span style="text-decoration:line-through;font-size:11px">abc</span>
                </button>
              </div>
            </div>
            <div class="rg-label">Text Effects</div>
          </div>

          <!-- ── Arrange ── -->
          <div class="ribbon-group" id="fmt-grp-shape-arrange">
            <div class="rg-body">
              <!-- Layer order: 2 columns × 2 rows (max 3 per col) -->
              <div class="rg-col" style="gap:2px">
                <button class="ribbon-btn-sm" id="fmt-shape-bring-front" title="Bring to Front">⬆ Front</button>
                <button class="ribbon-btn-sm" id="fmt-shape-bring-fwd"   title="Bring Forward">▲ Fwd</button>
              </div>
              <div class="rg-col" style="gap:2px">
                <button class="ribbon-btn-sm" id="fmt-shape-send-back" title="Send to Back">⬇ Back</button>
                <button class="ribbon-btn-sm" id="fmt-shape-send-bk"   title="Send Backward">▼ Bk</button>
              </div>
              <div class="rg-sep"></div>
              <!-- Align: 3+3 icon grid -->
              <div class="rg-col" style="gap:2px">
                <div style="display:flex;gap:2px">
                  <button class="ribbon-btn-sm icon" id="fmt-align-left"    title="Align Left">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="2" y="4" width="13" height="4" rx="1"/><rect x="2" y="10" width="19" height="4" rx="1"/><rect x="2" y="16" width="9" height="4" rx="1"/><rect x="1" y="2" width="2" height="20" rx="1"/></svg>
                  </button>
                  <button class="ribbon-btn-sm icon" id="fmt-align-centerH" title="Center H">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="4" width="14" height="4" rx="1"/><rect x="2" y="10" width="20" height="4" rx="1"/><rect x="7" y="16" width="10" height="4" rx="1"/><rect x="11" y="2" width="2" height="20" rx="1"/></svg>
                  </button>
                  <button class="ribbon-btn-sm icon" id="fmt-align-right"   title="Align Right">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="9" y="4" width="13" height="4" rx="1"/><rect x="3" y="10" width="19" height="4" rx="1"/><rect x="13" y="16" width="9" height="4" rx="1"/><rect x="21" y="2" width="2" height="20" rx="1"/></svg>
                  </button>
                </div>
                <div style="display:flex;gap:2px">
                  <button class="ribbon-btn-sm icon" id="fmt-align-top"     title="Align Top">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="4" height="13" rx="1"/><rect x="10" y="4" width="4" height="19" rx="1"/><rect x="16" y="4" width="4" height="9" rx="1"/><rect x="2" y="1" width="20" height="2" rx="1"/></svg>
                  </button>
                  <button class="ribbon-btn-sm icon" id="fmt-align-middleV" title="Center V">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="3" width="4" height="14" rx="1"/><rect x="10" y="2" width="4" height="20" rx="1"/><rect x="16" y="6" width="4" height="10" rx="1"/><rect x="2" y="11" width="20" height="2" rx="1"/></svg>
                  </button>
                  <button class="ribbon-btn-sm icon" id="fmt-align-bottom"  title="Align Bottom">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="7" width="4" height="13" rx="1"/><rect x="10" y="3" width="4" height="19" rx="1"/><rect x="16" y="11" width="4" height="9" rx="1"/><rect x="2" y="21" width="20" height="2" rx="1"/></svg>
                  </button>
                </div>
              </div>
              <div class="rg-sep"></div>
              <!-- Rotate + Flip: 2×2 -->
              <div class="rg-col" style="gap:2px">
                <button class="ribbon-btn-sm" id="fmt-rotate-cw"  title="Rotate 90° CW">↻ CW</button>
                <button class="ribbon-btn-sm" id="fmt-rotate-ccw" title="Rotate 90° CCW">↺ CCW</button>
              </div>
              <div class="rg-col" style="gap:2px">
                <button class="ribbon-btn-sm" id="fmt-flip-h" title="Flip Horizontal">⇔ Flip H</button>
                <button class="ribbon-btn-sm" id="fmt-flip-v" title="Flip Vertical">⇕ Flip V</button>
              </div>
            </div>
            <div class="rg-label">Arrange</div>
          </div>

          <!-- ── Size ── -->
          <div class="ribbon-group" id="fmt-grp-shape-size">
            <div class="rg-body">
              <div class="rg-col" style="gap:3px;font-size:9px">
                <div style="display:flex;align-items:center;gap:3px">
                  <span style="width:14px" title="Height">H</span>
                  <input class="ribbon-input" id="fmt-inp-shape-h" type="number" min="1" style="width:60px">
                </div>
                <div style="display:flex;align-items:center;gap:3px">
                  <span style="width:14px" title="Width">W</span>
                  <input class="ribbon-input" id="fmt-inp-shape-w" type="number" min="1" style="width:60px">
                </div>
                <div style="display:flex;align-items:center;gap:3px">
                  <span style="width:14px" title="Rotation">∠</span>
                  <input class="ribbon-input" id="fmt-inp-shape-angle" type="number" min="-360" max="360" style="width:60px" title="Rotation angle (degrees)">
                </div>
              </div>
            </div>
            <div class="rg-label">Size</div>
          </div>

        </div><!-- /shape-format -->

        <!-- ══ PICTURE FORMAT (contextual) ══ -->
        <div class="tab-panel" data-panel="picture-format">
          <div class="ribbon-group">
            <div class="rg-body">
              <button class="ribbon-btn-lg" id="fmt-pic-replace" title="Replace image">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
                Replace
              </button>
              <input type="file" id="fmt-pic-file" accept="image/*" style="display:none">
            </div>
            <div class="rg-label">Adjust</div>
          </div>
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="fmt-pic-contain" title="Fit (contain)">◻ Contain</button>
                <button class="ribbon-btn-sm" id="fmt-pic-cover"   title="Fill (cover)">▪ Cover</button>
                <button class="ribbon-btn-sm" id="fmt-pic-stretch" title="Stretch to fill">⟺ Stretch</button>
              </div>
            </div>
            <div class="rg-label">Picture Styles</div>
          </div>
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col">
                <div style="display:flex;align-items:center;gap:2px;font-size:9px">
                  <span>Opacity</span>
                  <input class="ribbon-input" id="fmt-pic-opacity" type="number" min="0" max="100" value="100" style="width:36px">%
                </div>
              </div>
            </div>
            <div class="rg-label">Adjust</div>
          </div>
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="fmt-pic-bring-fwd"  title="Bring Forward">▲ Bring Forward</button>
                <button class="ribbon-btn-sm" id="fmt-pic-send-bk"    title="Send Backward">▼ Send Backward</button>
              </div>
            </div>
            <div class="rg-label">Arrange</div>
          </div>
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col" style="gap:3px;font-size:9px">
                <div style="display:flex;align-items:center;gap:3px">
                  <span style="width:12px">H</span>
                  <input class="ribbon-input" id="fmt-inp-pic-h" type="number" min="1" style="width:58px">
                </div>
                <div style="display:flex;align-items:center;gap:3px">
                  <span style="width:12px">W</span>
                  <input class="ribbon-input" id="fmt-inp-pic-w" type="number" min="1" style="width:58px">
                </div>
              </div>
            </div>
            <div class="rg-label">Size</div>
          </div>
        </div><!-- /picture-format -->

        <!-- ══ TABLE DESIGN (contextual) ══ -->
        <div class="tab-panel" data-panel="table-design">
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col" style="gap:3px">
                <label style="display:flex;align-items:center;gap:4px;font-size:10px;cursor:pointer">
                  <input type="checkbox" id="fmt-tbl-header-row" checked> Header Row
                </label>
                <label style="display:flex;align-items:center;gap:4px;font-size:10px;cursor:pointer">
                  <input type="checkbox" id="fmt-tbl-total-row"> Total Row
                </label>
                <label style="display:flex;align-items:center;gap:4px;font-size:10px;cursor:pointer">
                  <input type="checkbox" id="fmt-tbl-banded-rows" checked> Banded Rows
                </label>
              </div>
              <div class="rg-col" style="gap:3px">
                <label style="display:flex;align-items:center;gap:4px;font-size:10px;cursor:pointer">
                  <input type="checkbox" id="fmt-tbl-first-col"> First Column
                </label>
                <label style="display:flex;align-items:center;gap:4px;font-size:10px;cursor:pointer">
                  <input type="checkbox" id="fmt-tbl-last-col"> Last Column
                </label>
                <label style="display:flex;align-items:center;gap:4px;font-size:10px;cursor:pointer">
                  <input type="checkbox" id="fmt-tbl-banded-cols"> Banded Columns
                </label>
              </div>
            </div>
            <div class="rg-label">Table Style Options</div>
          </div>
          <div class="ribbon-group">
            <div class="rg-body">
              <label class="ribbon-btn-lg" title="Cell shading / background color">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M16.56 8.94L7.62 0 6.21 1.41l2.38 2.38-5.15 5.15a1.49 1.49 0 000 2.12l5.5 5.5c.29.29.68.44 1.06.44s.77-.15 1.06-.44l5.5-5.5c.59-.58.59-1.53 0-2.12zM5.21 10L10 5.21 14.79 10H5.21zM19 11.5s-2 2.17-2 3.5c0 1.1.9 2 2 2s2-.9 2-2c0-1.33-2-3.5-2-3.5z"/></svg>
                Shading
                <input type="color" id="fmt-tbl-shading" value="#1e3a5f" style="display:none">
              </label>
              <div class="rg-col">
                <label class="ribbon-color-btn" title="Border color">
                  <span style="font-size:9px">Border</span>
                  <div class="swatch-bar" id="fmt-tbl-border-bar" style="width:22px;background:#888"></div>
                  <input type="color" id="fmt-tbl-border-color" value="#888888" style="display:none">
                </label>
                <select class="ribbon-select" id="fmt-tbl-border-width" style="width:58px;margin-top:2px">
                  <option value="1">1 px</option>
                  <option value="2">2 px</option>
                  <option value="3">3 px</option>
                  <option value="0">None</option>
                </select>
              </div>
            </div>
            <div class="rg-label">Draw Borders</div>
          </div>
        </div><!-- /table-design -->

        <!-- ══ TABLE LAYOUT (contextual) ══ -->
        <div class="tab-panel" data-panel="table-layout">
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="fmt-tbl-del"     title="Delete selected row">🗑 Del Row</button>
                <button class="ribbon-btn-sm" id="fmt-tbl-del-col" title="Delete selected column">🗑 Del Col</button>
              </div>
              <div class="rg-col">
                <button class="ribbon-btn-sm" id="fmt-tbl-row-above" title="Insert Row Above">⬆ Row Above</button>
                <button class="ribbon-btn-sm" id="fmt-tbl-row-below" title="Insert Row Below">⬇ Row Below</button>
                <button class="ribbon-btn-sm" id="fmt-tbl-col-left"  title="Insert Column Left">← Col Left</button>
                <button class="ribbon-btn-sm" id="fmt-tbl-col-right" title="Insert Column Right">→ Col Right</button>
              </div>
            </div>
            <div class="rg-label">Rows &amp; Columns</div>
          </div>
          <div class="ribbon-group">
            <div class="rg-body">
              <button class="ribbon-btn-lg" id="fmt-tbl-merge" title="Merge selected cells">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM8 20H4v-4h4v4zm0-6H4v-4h4v4zm0-6H4V4h4v4zm6 12h-4v-4h4v4zm0-6h-4v-4h4v4zm0-6h-4V4h4v4zm6 12h-4v-4h4v4zm0-6h-4v-4h4v4zm0-6h-4V4h4v4z"/></svg>
                Merge Cells
              </button>
              <button class="ribbon-btn-lg" id="fmt-tbl-unmerge" title="Unmerge selected cell">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM8 20H4v-4h4v4zm0-6H4v-4h4v4zm0-6H4V4h4v4zm10 12h-4v-4h4v4zm0-6h-4v-4h4v4zm0-6h-4V4h4v4z"/><path d="M11 4h2v16h-2z" opacity=".55"/></svg>
                Unmerge
              </button>
            </div>
            <div class="rg-label">Merge</div>
          </div>
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col" style="gap:3px;font-size:9px">
                <div style="display:flex;align-items:center;gap:3px">
                  <span style="width:12px">H</span>
                  <input class="ribbon-input" id="fmt-tbl-cell-h" type="number" min="1" style="width:54px">
                </div>
                <div style="display:flex;align-items:center;gap:3px">
                  <span style="width:12px">W</span>
                  <input class="ribbon-input" id="fmt-tbl-cell-w" type="number" min="1" style="width:54px">
                </div>
              </div>
            </div>
            <div class="rg-label">Cell Size</div>
          </div>
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col" style="gap:1px">
                <div style="display:flex;gap:1px">
                  <button class="ribbon-btn-sm" id="fmt-tbl-align-tl" title="Top Left"    style="width:24px">↖</button>
                  <button class="ribbon-btn-sm" id="fmt-tbl-align-tc" title="Top Center"  style="width:24px">↑</button>
                  <button class="ribbon-btn-sm" id="fmt-tbl-align-tr" title="Top Right"   style="width:24px">↗</button>
                </div>
                <div style="display:flex;gap:1px">
                  <button class="ribbon-btn-sm" id="fmt-tbl-align-ml" title="Middle Left"  style="width:24px">←</button>
                  <button class="ribbon-btn-sm" id="fmt-tbl-align-mc" title="Middle Center" style="width:24px">·</button>
                  <button class="ribbon-btn-sm" id="fmt-tbl-align-mr" title="Middle Right" style="width:24px">→</button>
                </div>
                <div style="display:flex;gap:1px">
                  <button class="ribbon-btn-sm" id="fmt-tbl-align-bl" title="Bottom Left"  style="width:24px">↙</button>
                  <button class="ribbon-btn-sm" id="fmt-tbl-align-bc" title="Bottom Center" style="width:24px">↓</button>
                  <button class="ribbon-btn-sm" id="fmt-tbl-align-br" title="Bottom Right" style="width:24px">↘</button>
                </div>
              </div>
            </div>
            <div class="rg-label">Alignment</div>
          </div>
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col" style="gap:3px;font-size:9px">
                <div style="display:flex;align-items:center;gap:3px">
                  <span style="width:12px">H</span>
                  <input class="ribbon-input" id="fmt-tbl-size-h" type="number" min="1" style="width:58px">
                </div>
                <div style="display:flex;align-items:center;gap:3px">
                  <span style="width:12px">W</span>
                  <input class="ribbon-input" id="fmt-tbl-size-w" type="number" min="1" style="width:58px">
                </div>
              </div>
            </div>
            <div class="rg-label">Table Size</div>
          </div>
        </div><!-- /table-layout -->

        <!-- ══ EQUATION FORMAT (contextual) ══ -->
        <div class="tab-panel" data-panel="equation-format">
          <div class="ribbon-group">
            <div class="rg-body">
              <button class="ribbon-btn-lg" id="fmt-eq-edit" title="Edit formula source">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5.97 14L9 13l-3 4H4l4-5.5L4 6h2l3 4 4.03-4H15l-4 5.5 4.03 5.5h-2z"/></svg>
                Edit Formula
              </button>
            </div>
            <div class="rg-label">Equation</div>
          </div>
          <div class="ribbon-group">
            <div class="rg-body">
              <div class="rg-col" style="gap:3px;font-size:9px">
                <div style="display:flex;align-items:center;gap:3px">
                  <span>Size</span>
                  <input class="ribbon-input" id="fmt-eq-size" type="number" min="8" max="200" value="60" style="width:48px">
                </div>
                <label class="ribbon-color-btn" title="Formula color">
                  <span style="font-size:9px">Color</span>
                  <div class="swatch-bar" id="fmt-eq-color-bar" style="width:22px;background:#fff"></div>
                  <input type="color" id="fmt-eq-color" value="#ffffff" style="display:none">
                </label>
              </div>
            </div>
            <div class="rg-label">Format</div>
          </div>
        </div><!-- /equation-format -->

        <!-- ══ H&F MODAL ══ -->
        <div id="modal-hf" class="ed-modal" style="display:none">
          <div class="ed-modal-box" style="width:580px">
            <div class="ed-modal-title">Header and Footer</div>
            <div style="display:flex;gap:24px;align-items:flex-start">
              <!-- Left: options -->
              <div style="flex:1;display:flex;flex-direction:column;gap:0">
                <div style="font-size:11px;font-weight:700;color:#333;margin-bottom:8px;padding:4px 6px;background:#f0f0f5;border-radius:4px">Include on slide</div>

                <!-- Date and time -->
                <div style="margin-bottom:10px">
                  <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
                    <input type="checkbox" id="hf-show-date"> <b>Date and time</b>
                  </label>
                  <div id="hf-date-opts" style="margin-left:20px;margin-top:6px;display:flex;flex-direction:column;gap:5px">
                    <label style="display:flex;align-items:center;gap:6px;font-size:11px;cursor:pointer">
                      <input type="radio" name="hf-date-mode" id="hf-date-auto" value="auto" checked>
                      Update automatically
                    </label>
                    <select id="hf-date-format" class="ed-modal-inp" style="width:260px;margin-left:20px">
                      <option value="short">7/12/2026</option>
                      <option value="long">Saturday, July 12, 2026</option>
                      <option value="medium">July 12, 2026</option>
                      <option value="month">July 2026</option>
                    </select>
                    <label style="display:flex;align-items:center;gap:6px;font-size:11px;cursor:pointer;margin-top:2px">
                      <input type="radio" name="hf-date-mode" id="hf-date-fixed" value="fixed">
                      Fixed
                    </label>
                    <input id="hf-date-fixed-val" class="ed-modal-inp" type="text" placeholder="e.g. 7/12/2026" style="margin-left:20px;width:230px">
                  </div>
                </div>

                <!-- Slide number -->
                <div style="margin-bottom:10px">
                  <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
                    <input type="checkbox" id="hf-show-slidenum" checked> Slide number
                  </label>
                </div>

                <!-- Footer -->
                <div style="margin-bottom:10px">
                  <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
                    <input type="checkbox" id="hf-show-footer"> Footer
                  </label>
                  <input id="hf-footer-text" class="ed-modal-inp" type="text" placeholder="Footer text" style="margin-top:4px;margin-left:20px;width:260px">
                </div>

                <!-- Separator -->
                <div style="border-top:1px solid #ddd;margin:6px 0 10px"></div>
                <div style="font-size:11px;font-weight:700;color:#333;margin-bottom:6px">Presentation info</div>

                <!-- Title -->
                <div class="ed-modal-field-label">Slide title (top bar)</div>
                <input id="hf-inp-title"  class="ed-modal-inp" type="text" placeholder="Course / presentation title" style="margin-bottom:6px">

                <!-- Author -->
                <div class="ed-modal-field-label">Author / Presented by</div>
                <input id="hf-inp-author" class="ed-modal-inp" type="text" placeholder="Presenter name" style="margin-bottom:6px">

                <!-- Institution -->
                <div class="ed-modal-field-label">Institution</div>
                <input id="hf-inp-inst"   class="ed-modal-inp" type="text" placeholder="University or organization" style="margin-bottom:6px">

                <!-- Show author -->
                <label style="display:flex;align-items:center;gap:6px;font-size:11px;cursor:pointer;margin-bottom:10px">
                  <input type="checkbox" id="hf-show-author" checked> Show author in footer
                </label>

                <!-- Don't show on title -->
                <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;border-top:1px solid #ddd;padding-top:8px">
                  <input type="checkbox" id="hf-hide-title"> Don't show on title slide
                </label>
              </div>

              <!-- Right: Preview -->
              <div style="width:130px;flex-shrink:0">
                <div style="font-size:11px;font-weight:700;color:#333;margin-bottom:8px">Preview</div>
                <div id="hf-preview-box" style="width:118px;height:80px;border:1px solid #bbb;background:#fff;position:relative;border-radius:2px;overflow:hidden">
                  <!-- slide outline -->
                  <div style="position:absolute;left:6px;right:6px;top:6px;bottom:20px;border:1px dashed #aaa;border-radius:1px"></div>
                  <!-- date bottom-left indicator -->
                  <div id="hf-prev-date"   style="position:absolute;left:6px;bottom:4px;width:28px;height:7px;background:#888;border-radius:1px;display:none"></div>
                  <!-- footer bottom-center indicator -->
                  <div id="hf-prev-footer" style="position:absolute;left:50%;transform:translateX(-50%);bottom:4px;width:32px;height:7px;background:#888;border-radius:1px;display:none"></div>
                  <!-- slide number bottom-right indicator -->
                  <div id="hf-prev-num"    style="position:absolute;right:6px;bottom:4px;width:14px;height:7px;background:#4472C4;border-radius:1px;display:none"></div>
                </div>
                <div style="font-size:9px;color:#888;margin-top:6px;line-height:1.4">
                  <span style="display:inline-block;width:10px;height:5px;background:#888;vertical-align:middle;border-radius:1px"></span> Text<br>
                  <span style="display:inline-block;width:10px;height:5px;background:#4472C4;vertical-align:middle;border-radius:1px"></span> Number
                </div>
              </div>
            </div>

            <div class="ed-modal-actions">
              <button id="btn-hf-apply" class="ed-modal-btn ed-modal-btn-primary">Apply to All Slides</button>
              <button id="btn-hf-close" class="ed-modal-btn">Cancel</button>
            </div>
          </div>
        </div>

        <!-- ══ SYMBOL MODAL ══ -->
        <div id="symbol-modal" class="ed-modal" style="display:none">
          <div class="ed-modal-box" style="width:500px">
            <div class="ed-modal-title">Insert Symbol</div>
            <div style="display:flex;gap:0;border-bottom:1px solid #ddd;margin-bottom:10px">
              <button class="sym-tab active" data-cat="greek">Greek</button>
              <button class="sym-tab" data-cat="math">Math</button>
              <button class="sym-tab" data-cat="arrows">Arrows</button>
              <button class="sym-tab" data-cat="special">Special</button>
            </div>
            <div id="sym-grid" style="display:flex;flex-wrap:wrap;gap:3px;max-height:220px;overflow-y:auto;padding:4px"></div>
            <div style="display:flex;align-items:center;gap:12px;margin-top:10px;border-top:1px solid #eee;padding-top:10px">
              <span style="font-size:12px;color:#666">Selected:</span>
              <span id="sym-preview" style="font-size:28px;line-height:1;min-width:30px;text-align:center;border:1px solid #ddd;padding:2px 6px;border-radius:4px;background:#f9f9f9">—</span>
              <div style="margin-left:auto;display:flex;gap:8px">
                <button id="btn-sym-insert" class="ed-modal-btn ed-modal-btn-primary" disabled>Insert</button>
                <button id="btn-sym-close" class="ed-modal-btn">Close</button>
              </div>
            </div>
          </div>
        </div>

      </div><!-- #ed-ribbon -->

      <input type="file" id="inp-img-file"  accept="image/*"   style="display:none" />
      <input type="file" id="inp-json-file" accept=".json"     style="display:none" />
    `;
    root.appendChild(toolbar);

    /* ── Main area ── */
    const main = document.createElement('div');
    main.id = 'ed-main';
    root.appendChild(main);

    /* ── Slide thumbnail panel ── */
    const slidePanel = document.createElement('div');
    slidePanel.id = 'ed-slide-panel';
    slidePanel.innerHTML = `
      <div class="ed-panel-head">
        <span>Slides</span>
        <button id="btn-dup-slide" title="Duplicate slide" style="margin-right:2px">⧉</button>
        <button id="btn-add-slide" title="Add blank slide">+</button>
      </div>
      <div class="ed-thumb-list" id="ed-thumb-list">
        <div class="ed-thumb active" data-index="0">
          <span class="ed-thumb-num">1</span>
        </div>
      </div>
    `;
    main.appendChild(slidePanel);

    /* ── Canvas viewport ── */
    const vp = document.createElement('div');
    vp.id = 'ed-viewport';
    main.appendChild(vp);

    const wrap = document.createElement('div');
    wrap.id = 'ed-canvas-wrap';
    vp.appendChild(wrap);

    const canvasEl = document.createElement('canvas');
    canvasEl.id = 'slide-canvas';
    wrap.appendChild(canvasEl);

    const tableLayer = document.createElement('div');
    tableLayer.id = 'ed-table-layer';
    wrap.appendChild(tableLayer);

    /* ── Properties panel ── */
    const propsPanel = document.createElement('div');
    propsPanel.id = 'ed-props-panel';
    propsPanel.innerHTML = `
      <div class="ed-panel-head"><span>Properties</span></div>
      <div class="ed-props-scroll">

        <!-- Slide transition (always visible, slide-level) -->
        <div id="props-slide" class="ed-props-section">
          <div class="ed-props-label">Slide Transition</div>
          <select class="ed-num" id="prop-slide-transition" style="width:100%;padding:4px 6px;box-sizing:border-box">
            <option value="none">None</option>
            <option value="fade">Fade</option>
            <option value="slide-left">Slide Left</option>
            <option value="slide-right">Slide Right</option>
            <option value="slide-up">Slide Up</option>
            <option value="slide-down">Slide Down</option>
            <option value="zoom">Zoom</option>
            <option value="flip">Flip</option>
          </select>
          <button class="ed-btn" id="btn-transition-all" style="width:100%;font-size:11px;margin-top:6px">Apply to all slides</button>
        </div>

        <!-- Empty state -->
        <div id="props-empty" class="ed-props-empty">
          <div class="icon">↖</div>
          <p>Select an object<br>to see its properties</p>
        </div>

        <!-- Transform section -->
        <div id="props-transform" class="ed-props-section" style="display:none">
          <div class="ed-props-label">Position</div>
          <div class="ed-props-cols">
            <div class="ed-props-row"><span class="lbl">X</span><input class="ed-num" id="prop-x" type="number" /></div>
            <div class="ed-props-row"><span class="lbl">Y</span><input class="ed-num" id="prop-y" type="number" /></div>
          </div>
          <div class="ed-props-label" style="margin-top:8px">Size</div>
          <div class="ed-props-cols">
            <div class="ed-props-row"><span class="lbl">W</span><input class="ed-num" id="prop-w" type="number" /></div>
            <div class="ed-props-row"><span class="lbl">H</span><input class="ed-num" id="prop-h" type="number" /></div>
          </div>
          <div class="ed-props-label" style="margin-top:8px">Rotation</div>
          <div class="ed-props-row">
            <span class="lbl">°</span>
            <input class="ed-num" id="prop-angle" type="number" min="-360" max="360" style="flex:1" />
          </div>
          <div class="ed-props-label" style="margin-top:8px">Opacity</div>
          <div class="ed-slider-row">
            <input type="range" class="ed-slider" id="prop-opacity" min="0" max="100" value="100" />
            <input class="ed-num" id="prop-opacity-num" type="number" min="0" max="100" value="100" />
          </div>
        </div>

        <!-- Animation section (object entrance animation) -->
        <div id="props-animation" class="ed-props-section" style="display:none">
          <div class="ed-props-label">Entrance Animation</div>
          <select class="ed-num" id="prop-anim" style="width:100%;padding:4px 6px;box-sizing:border-box">
            <option value="none">None</option>
            <option value="fade-in">Fade In</option>
            <option value="fly-left">Fly Left</option>
            <option value="fly-right">Fly Right</option>
            <option value="fly-up">Fly Up</option>
            <option value="fly-down">Fly Down</option>
            <option value="zoom-in">Zoom In</option>
            <option value="zoom-out">Zoom Out</option>
            <option value="rotate">Rotate</option>
            <option value="bounce">Bounce</option>
            <option value="flip-h">Flip H</option>
            <option value="flip-v">Flip V</option>
          </select>
          <div class="ed-props-cols" style="margin-top:6px">
            <div class="ed-props-row"><span class="lbl" title="Duration (s)">⏱</span><input class="ed-num" id="prop-anim-dur" type="number" min="0.1" max="4" step="0.05" value="0.5" style="width:52px" /></div>
            <div class="ed-props-row"><span class="lbl" title="Delay (s)">⏳</span><input class="ed-num" id="prop-anim-delay" type="number" min="0" max="10" step="0.05" value="0" style="width:52px" /></div>
          </div>
          <div class="ed-props-row" style="margin-top:6px"><span class="lbl" title="Build order (lower plays first)">#</span><input class="ed-num" id="prop-anim-order" type="number" min="0" max="99" step="1" value="0" style="flex:1" /></div>
        </div>

        <!-- Typography section (text only) -->
        <div id="props-typography" class="ed-props-section" style="display:none">
          <div class="ed-props-label">Typography</div>
          <div class="ed-props-cols">
            <div class="ed-props-row">
              <span class="lbl" title="Line Height">↕</span>
              <input class="ed-num" id="prop-line-height" type="number" min="0.5" max="5" step="0.05" value="1.2" style="width:52px" title="Line Height" />
            </div>
            <div class="ed-props-row">
              <span class="lbl" title="Letter Spacing">↔</span>
              <input class="ed-num" id="prop-char-spacing" type="number" min="-200" max="800" step="10" value="0" style="width:52px" title="Letter Spacing (1/1000 em)" />
            </div>
          </div>
        </div>

        <!-- Image section (image objects only) -->
        <div id="props-image" class="ed-props-section" style="display:none">
          <div class="ed-props-label">Image</div>
          <div style="margin-bottom:6px">
            <button class="ed-btn" id="btn-replace-img" style="width:100%;font-size:11px">&#8593; Replace Image (file)</button>
            <input type="file" id="inp-replace-img-file" accept="image/*" style="display:none">
          </div>
          <div class="ed-props-label" style="margin-bottom:4px">Load from URL</div>
          <input type="text" id="prop-img-url" class="ed-num" style="width:100%;padding:4px 6px;margin-bottom:4px;box-sizing:border-box;font-size:10px" placeholder="https://...">
          <button class="ed-btn" id="btn-load-img-url" style="width:100%;font-size:11px">Load URL</button>
          <div class="ed-props-label" style="margin-top:8px">Fit Mode</div>
          <div class="ed-group" style="gap:4px">
            <button class="ed-btn" data-imgfit="cover"   style="flex:1;font-size:10px">Cover</button>
            <button class="ed-btn" data-imgfit="contain" style="flex:1;font-size:10px">Contain</button>
            <button class="ed-btn" data-imgfit="fill"    style="flex:1;font-size:10px">Fill</button>
          </div>
        </div>

        <!-- Equation editor (formula textboxes) -->
        <div id="props-equation" class="ed-props-section" style="display:none">
          <div class="ed-props-label">Equation / Formula</div>
          <textarea id="prop-equation-src" style="width:100%;height:80px;font-family:monospace;font-size:10px;background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:4px 6px;border-radius:3px;box-sizing:border-box;resize:vertical" placeholder="LaTeX or formula text..."></textarea>
          <button class="ed-btn" id="btn-apply-equation" style="width:100%;margin-top:4px;font-size:11px">Apply</button>
        </div>

        <!-- Table editor -->
        <div id="props-table" class="ed-props-section" style="display:none">
          <div class="ed-props-label">Table</div>
          <textarea id="prop-table-src" style="width:100%;height:100px;font-family:monospace;font-size:10px;background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:4px 6px;border-radius:3px;box-sizing:border-box;resize:vertical" placeholder="col1 | col2 | col3&#10;val1 | val2 | val3"></textarea>
          <div style="font-size:9px;color:rgba(255,255,255,.4);margin-top:2px">Separate columns with |, rows with newline</div>
          <button class="ed-btn" id="btn-apply-table" style="width:100%;margin-top:4px;font-size:11px">Apply Table</button>
          <div style="display:flex;gap:6px;margin-top:6px">
            <button class="ed-btn" id="btn-tbl-add-row" style="flex:1;font-size:11px" title="Add row at bottom">+ Row</button>
            <button class="ed-btn" id="btn-tbl-del-row" style="flex:1;font-size:11px" title="Delete last row">− Row</button>
          </div>
        </div>

        <!-- Effects section (object selected) -->
        <div id="props-effects" class="ed-props-section" style="display:none">
          <div class="ed-props-label">Shadow</div>
          <div class="ed-props-row" style="align-items:center;gap:6px">
            <input type="checkbox" id="chk-shadow" style="cursor:pointer;width:14px;height:14px">
            <span style="font-size:10px;flex:1;color:rgba(255,255,255,.7)">Enable shadow</span>
            <label class="ed-color" title="Shadow Color" style="margin:0">
              <div class="swatch-bar" id="bar-shadow-color" style="background:rgba(0,0,0,0.5);width:24px;height:12px;border-radius:2px;border:1px solid rgba(255,255,255,.2)"></div>
              <input type="color" id="inp-shadow-color" value="#000000">
            </label>
          </div>
          <div id="shadow-controls" style="display:none">
            <div class="ed-props-cols" style="margin-top:6px">
              <div class="ed-props-row"><span class="lbl">X</span><input class="ed-num" id="prop-shadow-x" type="number" value="5" style="width:46px"></div>
              <div class="ed-props-row"><span class="lbl">Y</span><input class="ed-num" id="prop-shadow-y" type="number" value="5" style="width:46px"></div>
            </div>
            <div class="ed-props-row" style="margin-top:4px">
              <span class="lbl" title="Blur">Blur</span>
              <input type="range" class="ed-slider" id="prop-shadow-blur" min="0" max="80" value="10" style="flex:1">
              <input class="ed-num" id="prop-shadow-blur-num" type="number" min="0" max="80" value="10" style="width:38px">
            </div>
            <div class="ed-props-row" style="margin-top:4px">
              <span class="lbl" title="Opacity">Opac</span>
              <input type="range" class="ed-slider" id="prop-shadow-opacity" min="0" max="100" value="50" style="flex:1">
              <input class="ed-num" id="prop-shadow-opacity-num" type="number" min="0" max="100" value="50" style="width:38px">
            </div>
          </div>
        </div>

        <!-- Layer section -->
        <div id="props-layer" class="ed-props-section" style="display:none">
          <div class="ed-props-label">Layer</div>
          <div class="ed-group" style="gap:4px">
            <button class="ed-btn" id="btn-bring-front" style="flex:1;font-size:11px" title="Bring to Front">▲ Front</button>
            <button class="ed-btn" id="btn-send-back"   style="flex:1;font-size:11px" title="Send to Back">▼ Back</button>
          </div>
          <div class="ed-group" style="gap:4px;margin-top:4px">
            <button class="ed-btn" id="btn-bring-fwd"  style="flex:1;font-size:11px" title="Bring Forward">↑ Forward</button>
            <button class="ed-btn" id="btn-send-bwd"   style="flex:1;font-size:11px" title="Send Backward">↓ Backward</button>
          </div>
          <div class="ed-group" style="gap:4px;margin-top:8px">
            <button class="ed-btn" id="btn-lock-obj"   style="flex:1;font-size:11px" title="Lock / Unlock (Ctrl+L)">🔓 Lock</button>
            <button class="ed-btn" id="btn-group-obj"  style="flex:1;font-size:11px" title="Group (Ctrl+G)">⊞ Group</button>
          </div>
          <div style="margin-top:6px">
            <button class="ed-btn danger" id="btn-delete-obj" style="width:100%;font-size:11px" title="Delete (Del)">
              🗑 Delete Object
            </button>
          </div>
        </div>

        <!-- Theme / Slide section (always visible) -->
        <div id="props-theme-panel" class="ed-props-section">
          <div class="ed-props-label">Theme</div>
          <div id="theme-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:4px">
            <!-- populated by initThemePanel() -->
          </div>
          <div class="ed-props-label" style="margin-top:10px">Slide Background</div>
          <div class="ed-group" style="gap:6px;flex-wrap:wrap;align-items:center">
            <label class="ed-color" title="Solid color">
              <span class="swatch-icon" style="font-size:10px">BG</span>
              <div class="swatch-bar" id="bar-slide-bg-props" style="background:#1a1a2e"></div>
              <input type="color" id="inp-slide-bg-props" value="#1a1a2e">
            </label>
            <button class="ed-btn" id="btn-apply-theme-grad" style="flex:1;font-size:10px" title="Re-apply current theme gradient">↺ Theme Grad</button>
          </div>
        </div>

      </div>
    `;
    main.appendChild(propsPanel);

    /* ── Status bar ── */
    const status = document.createElement('div');
    status.id = 'ed-status';
    status.innerHTML = `
      <span class="ed-status-item">Canvas <b>1920 × 1080</b></span>
      <span class="ed-status-sep"></span>
      <span class="ed-status-item">Zoom <b id="st-zoom">—</b></span>
      <span class="ed-status-sep"></span>
      <span class="ed-status-item">Objects <b id="st-objects">0</b></span>
      <span class="ed-status-sep"></span>
      <span class="ed-status-item" id="st-pos-wrap" style="display:none">
        X <b id="st-x">—</b> &nbsp; Y <b id="st-y">—</b>
      </span>
    `;
    root.appendChild(status);

    /* ── Context menu ── */
    const ctxMenu = document.createElement('div');
    ctxMenu.id = 'ed-context-menu';
    ctxMenu.innerHTML = `
      <div class="ed-ctx-item" data-cmd="duplicate">Duplicate <span class="kbd">Ctrl+D</span></div>
      <div class="ed-ctx-item" data-cmd="group">Group <span class="kbd">Ctrl+G</span></div>
      <div class="ed-ctx-item" data-cmd="ungroup">Ungroup <span class="kbd">Ctrl+Shift+G</span></div>
      <div class="ed-ctx-item" data-cmd="lock">Lock / Unlock <span class="kbd">Ctrl+L</span></div>
      <div class="ed-ctx-sep"></div>
      <div class="ed-ctx-item" data-cmd="bringForward">Bring Forward</div>
      <div class="ed-ctx-item" data-cmd="sendBackward">Send Backward</div>
      <div class="ed-ctx-sep"></div>
      <div class="ed-ctx-item" data-cmd="bringToFront">Bring to Front</div>
      <div class="ed-ctx-item" data-cmd="sendToBack">Send to Back</div>
      <div class="ed-ctx-sep"></div>
      <div class="ed-ctx-item danger" data-cmd="delete">Delete <span class="kbd">Del</span></div>
    `;
    document.body.appendChild(ctxMenu);

    /* ── Formula / Equation popup modal ── */
    const fmlModal = document.createElement('div');
    fmlModal.id = 'formula-modal';
    fmlModal.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.65);z-index:9999;align-items:center;justify-content:center;';
    fmlModal.innerHTML = `
      <div style="background:#1e1e2e;border-radius:12px;padding:28px 32px;width:660px;max-width:92vw;box-shadow:0 24px 64px rgba(0,0,0,.6)">
        <div style="color:#cdd6f4;font-size:14px;font-weight:700;margin-bottom:14px;letter-spacing:.04em">Edit Formula (LaTeX)</div>
        <textarea id="formula-modal-input" rows="6"
          style="width:100%;font-family:monospace;font-size:13px;background:#12121f;color:#cdd6f4;border:1px solid #444;border-radius:6px;padding:10px 12px;resize:vertical;box-sizing:border-box;outline:none;"
          placeholder="e.g.  \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}"></textarea>
        <div style="margin:12px 0 4px;font-size:11px;color:rgba(205,214,244,.5)">Preview (Unicode approximation)</div>
        <div id="formula-modal-preview"
          style="font-family:monospace;font-size:17px;color:#cdd6f4;min-height:36px;padding:6px 10px;background:rgba(255,255,255,.04);border-radius:4px;word-break:break-all;"></div>
        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:18px">
          <button id="formula-modal-cancel"
            style="padding:7px 20px;background:rgba(255,255,255,.08);color:#cdd6f4;border:none;border-radius:6px;cursor:pointer;font-size:13px">Cancel</button>
          <button id="formula-modal-apply"
            style="padding:7px 22px;background:#89b4fa;color:#11111b;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:700">Apply</button>
        </div>
      </div>
    `;
    document.body.appendChild(fmlModal);

    /* ── Table Editor Modal ── */
    const tblModal = document.createElement('div');
    tblModal.id = 'ed-table-modal';
    tblModal.innerHTML = `
      <div id="ed-table-modal-backdrop"></div>
      <div id="ed-table-modal-dialog">
        <div id="ed-table-modal-toolbar">
          <span class="ed-table-modal-title">Edit Table</span>
          <button id="btn-table-add-row">+ Row</button>
          <button id="btn-table-del-row">&#x2212; Row</button>
          <button id="btn-table-add-col">+ Col</button>
          <button id="btn-table-del-col">&#x2212; Col</button>
          <div style="flex:1"></div>
          <button id="btn-table-apply" class="ed-btn-primary">Apply</button>
          <button id="btn-table-cancel">Cancel</button>
        </div>
        <div id="ed-table-modal-content">
          <table id="ed-table-edit"></table>
        </div>
      </div>
    `;
    document.body.appendChild(tblModal);

    /* ── Auto-save toast ── */
    var toast = document.createElement('div');
    toast.id = 'ed-autosave-toast';
    document.body.appendChild(toast);
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 2 — Canvas Initialization
   * ════════════════════════════════════════════════════════ */
  function initCanvas() {
    _canvas = new fabric.Canvas('slide-canvas', {
      width: SLIDE_W,
      height: SLIDE_H,
      backgroundColor: '#ffffff',
      selection: true,
      preserveObjectStacking: true,
      renderOnAddRemove: false,
      /* Match present-mode canvas (line ~5800) so the zoomed-out edit view is as sharp
         on HiDPI screens — without this the backing store matches CSS px 1:1 and gets
         blurred on upscale, while present already renders at devicePixelRatio. */
      enableRetinaScaling: true,
      /* Allow both Ctrl+Click and Shift+Click to add objects to selection */
      selectionKey: ['shiftKey', 'ctrlKey'],
    });

    _canvas.on('object:moving', function (e) {
      e.target.set({
        left: Math.round(e.target.left),
        top:  Math.round(e.target.top),
      });
    });

    return _canvas;
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 3 — Canva-Style Bounding Box
   *
   *  Overrides Fabric.js defaults globally so every object
   *  looks modern: blue border, white-circle handles, 8px
   *  padding, floating delete button, rotate handle 40px above.
   * ════════════════════════════════════════════════════════ */
  function initBoundingBox() {

    /* ── 3a. Global object defaults ── */
    Object.assign(fabric.Object.prototype, {
      borderColor:        '#4f8ef7',       // selection border — blue
      borderScaleFactor:  1.5,             // border line width
      cornerStyle:        'circle',        // round handles
      cornerSize:         12,              // handle diameter
      cornerColor:        '#ffffff',       // handle fill — white
      cornerStrokeColor:  '#4f8ef7',       // handle stroke — blue
      transparentCorners: false,
      padding:            8,               // gap between object and bounding box
    });

    /* ── 3b. Rotate handle — positioned 40px above the top edge ── */
    if (fabric.Object.prototype.controls && fabric.Object.prototype.controls.mtr) {
      fabric.Object.prototype.controls.mtr.offsetY = -40;
      fabric.Object.prototype.controls.mtr.y       = -0.5;
    }

    /* ── 3c. Delete control — top-right corner ── */
    const deleteControl = new fabric.Control({
      x:             0.5,
      y:             -0.5,
      offsetX:       16,
      offsetY:       -16,
      cursorStyle:   'pointer',
      mouseUpHandler: _deleteActiveObject,
      render:        _renderDeleteIcon,
      cornerSize:    24,
      touchSizeX:    30,
      touchSizeY:    30,
    });

    fabric.Object.prototype.controls.deleteControl = deleteControl;

    /* Apply same delete control to text objects */
    if (fabric.IText && fabric.IText.prototype) {
      fabric.IText.prototype.controls = Object.assign(
        {}, fabric.IText.prototype.controls, { deleteControl }
      );
    }
    if (fabric.Textbox && fabric.Textbox.prototype) {
      fabric.Textbox.prototype.controls = Object.assign(
        {}, fabric.Textbox.prototype.controls, { deleteControl }
      );
    }

    /* ── 3d. ActiveSelection (multi-select) — dashed border ── */
    Object.assign(fabric.ActiveSelection.prototype, {
      borderColor:       '#4f8ef7',
      borderDashArray:   [6, 4],
      borderScaleFactor: 1.5,
      cornerStyle:       'circle',
      cornerSize:        10,
      cornerColor:       '#ffffff',
      cornerStrokeColor: '#4f8ef7',
      transparentCorners: false,
      padding:           4,
    });
  }

  /* ── Delete control: mouseUpHandler ── */
  function _deleteActiveObject(_eventData, transform) {
    const canvas = transform.target.canvas;
    const targets = canvas.getActiveObjects();
    canvas.discardActiveObject();
    targets.forEach(function (obj) { canvas.remove(obj); });
    canvas.requestRenderAll();
    return true;
  }

  /* ── Delete control: custom render (red circle + ✕) ── */
  function _renderDeleteIcon(ctx, left, top, _styleOverride, fabricObject) {
    const SIZE = 20;
    const HALF = SIZE / 2;

    ctx.save();
    ctx.translate(left, top);
    ctx.rotate(fabric.util.degreesToRadians(fabricObject.angle || 0));

    /* Red circle background */
    ctx.beginPath();
    ctx.arc(0, 0, HALF, 0, Math.PI * 2);
    ctx.fillStyle   = '#ff4747';
    ctx.shadowColor = 'rgba(0,0,0,0.25)';
    ctx.shadowBlur  = 4;
    ctx.fill();
    ctx.shadowBlur  = 0;

    /* White border ring */
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth   = 1.5;
    ctx.stroke();

    /* ✕ mark */
    const TICK = 4.5;
    ctx.beginPath();
    ctx.moveTo(-TICK, -TICK);  ctx.lineTo( TICK,  TICK);
    ctx.moveTo( TICK, -TICK);  ctx.lineTo(-TICK,  TICK);
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth   = 2;
    ctx.lineCap     = 'round';
    ctx.stroke();

    ctx.restore();
  }

  /* ── Rotate handle: custom render (circle with arrow icon) ── */
  function _renderRotateIcon(ctx, left, top, _styleOverride, fabricObject) {
    const SIZE = 20;
    const HALF = SIZE / 2;

    ctx.save();
    ctx.translate(left, top);
    ctx.rotate(fabric.util.degreesToRadians(fabricObject.angle || 0));

    /* White circle */
    ctx.beginPath();
    ctx.arc(0, 0, HALF, 0, Math.PI * 2);
    ctx.fillStyle   = '#ffffff';
    ctx.shadowColor = 'rgba(0,0,0,0.2)';
    ctx.shadowBlur  = 4;
    ctx.fill();
    ctx.shadowBlur  = 0;
    ctx.strokeStyle = '#4f8ef7';
    ctx.lineWidth   = 1.5;
    ctx.stroke();

    /* Curved arrow (↻) */
    ctx.beginPath();
    ctx.arc(0, 0, 5, -Math.PI * 0.8, Math.PI * 0.8);
    ctx.strokeStyle = '#4f8ef7';
    ctx.lineWidth   = 2;
    ctx.lineCap     = 'round';
    ctx.stroke();

    /* Arrow head */
    const ax =  5 * Math.cos(Math.PI * 0.8);
    const ay =  5 * Math.sin(Math.PI * 0.8);
    ctx.beginPath();
    ctx.moveTo(ax - 3, ay - 1);
    ctx.lineTo(ax,     ay);
    ctx.lineTo(ax + 1, ay - 3);
    ctx.stroke();

    ctx.restore();
  }

  /* Apply custom rotate renderer after canvas exists */
  function _applyRotateRenderer() {
    if (fabric.Object.prototype.controls && fabric.Object.prototype.controls.mtr) {
      fabric.Object.prototype.controls.mtr.render = _renderRotateIcon;
    }
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 3c — Smart Snap Guides
   *
   *  When dragging, show magenta alignment lines and snap
   *  the object to edges/centers of other objects or the slide.
   *  A separate overlay <canvas> draws the guide lines so
   *  Fabric.js rendering is not disturbed.
   * ════════════════════════════════════════════════════════ */
  const SNAP_THRESHOLD = 8;   // canvas-coordinate pixels to trigger snap
  const SNAP_COLOR     = '#e040fb'; // magenta guide lines (Canva-style)
  let   _guideCtx      = null;

  function initSnapGuides() {
    /* Create overlay canvas that sits on top of Fabric canvas */
    const overlay = document.createElement('canvas');
    overlay.id = 'ed-snap-canvas';
    overlay.style.cssText =
      'position:absolute;top:0;left:0;pointer-events:none;z-index:5;';
    document.getElementById('ed-canvas-wrap').appendChild(overlay);
    _guideCtx = overlay.getContext('2d');

    _canvas.on('object:moving',  function (opt) { _snapObject(opt.target); });
    _canvas.on('object:scaling', function (opt) { _clearGuides(); _tbxResizeToWidth(opt.target); _fmlBoxResizing(opt.target); });
    _canvas.on('object:modified',function (opt) { _clearGuides(); _tbxResizeToWidth(opt.target, true); _fmlBoxResizing(opt.target, true); });
    _canvas.on('selection:cleared', function ()  { _clearGuides(); });
    _canvas.on('mouse:up',       function ()     { _clearGuides(); });
  }

  /* Textbox resize = change WIDTH + re-wrap, keep fontSize (Canva/PPT-like) — Fabric's
     default corner-scale would stretch the glyphs. Converts scaleX→width, resets scale. */
  function _tbxResizeToWidth(o, commit) {
    if (!o || (o.type !== 'textbox' && o.type !== 'i-text' && o.type !== 'text')) return;
    if ((o.scaleX === 1 && o.scaleY === 1)) return;
    var newW = Math.max(40, Math.round((o.width || 0) * (o.scaleX || 1)));
    o.set({ width: newW, scaleX: 1, scaleY: 1 });   /* height auto-follows wrapped text */
    if (o.initDimensions) o.initDimensions();
    o.setCoords();
    if (o.editorType === 'formula' && o._formulaBox) _refitFormulaBox(o);
    _canvas.requestRenderAll();
    if (commit) saveState();
  }

  /* Formula-layout box: normalize its own manual resize (scale→w/h, like a plain
     shape) and mark it user-sized so later formula-text edits stop auto-refitting it. */
  function _fmlBoxResizing(o, commit) {
    if (!o || o.editorType !== 'formula-box') return;
    if (o.scaleX !== 1 || o.scaleY !== 1) {
      o.set({
        width:  Math.max(80, Math.round(o.width  * o.scaleX)),
        height: Math.max(40, Math.round(o.height * o.scaleY)),
        scaleX: 1, scaleY: 1,
      });
      o.setCoords();
      _canvas.requestRenderAll();
    }
    o._userSized = true;
    if (commit) saveState();
  }

  /* Recompute a formula box's size/position to hug its linked text after an edit
     (content change or width-resize), unless the user already resized the box by hand. */
  function _refitFormulaBox(textObj) {
    var box = textObj._formulaBox;
    if (!box || box._userSized) return;
    if (textObj.initDimensions) textObj.initDimensions();
    var pad = textObj._formulaPad || 40;
    var maxBoxW = 1680;
    var maxTextW = maxBoxW - pad * 2;
    var contentW = Math.min(maxTextW, Math.ceil(textObj.width || maxTextW));
    var boxW = Math.min(maxBoxW, Math.max(520, contentW + pad * 2));
    var boxX = Math.round((SLIDE_W - boxW) / 2);
    var boxH = Math.ceil(textObj.height || 80) + pad * 2;
    box.set({ left: boxX, width: boxW, height: boxH });
    textObj.set({ left: boxX + pad, top: box.top + pad, width: boxW - pad * 2 });
    box.setCoords();
    textObj.setCoords();
  }

  /* Sync overlay canvas size whenever the main canvas is resized */
  function _syncGuideCanvasSize() {
    const ov = document.getElementById('ed-snap-canvas');
    if (!ov || !_canvas) return;
    ov.width  = _canvas.width;
    ov.height = _canvas.height;
  }

  function _clearGuides() {
    if (!_guideCtx) return;
    _guideCtx.clearRect(0, 0, _guideCtx.canvas.width, _guideCtx.canvas.height);
  }

  /* Collect all candidate snap X/Y values in canvas coordinates */
  function _collectSnapLines(movingObj) {
    const zoom = _canvas.getZoom();
    const snapX = new Set();
    const snapY = new Set();

    /* Slide edges and centre */
    [0, SLIDE_W / 2, SLIDE_W].forEach(x => snapX.add(x * zoom));
    [0, SLIDE_H / 2, SLIDE_H].forEach(y => snapY.add(y * zoom));

    /* All other objects' edges and centres */
    _canvas.getObjects().forEach(function (obj) {
      if (obj === movingObj) return;
      const b = obj.getBoundingRect(true);
      snapX.add(b.left);
      snapX.add(b.left + b.width / 2);
      snapX.add(b.left + b.width);
      snapY.add(b.top);
      snapY.add(b.top + b.height / 2);
      snapY.add(b.top + b.height);
    });

    return { snapX: Array.from(snapX), snapY: Array.from(snapY) };
  }

  function _snapObject(obj) {
    if (!obj || !_guideCtx) return;
    _syncGuideCanvasSize();
    _clearGuides();

    const zoom   = _canvas.getZoom();
    void (_canvas.viewportTransform); /* used implicitly by getBoundingRect */
    const { snapX, snapY } = _collectSnapLines(obj);
    const rect   = obj.getBoundingRect(true); // in canvas display coords

    /* Points on the moving object to test */
    const objXpts = [rect.left, rect.left + rect.width / 2, rect.left + rect.width];
    const objYpts = [rect.top,  rect.top  + rect.height / 2, rect.top  + rect.height];

    let snapDX = null; // delta to apply in display coords
    let snapDY = null;
    let guideXs = [];
    let guideYs = [];

    /* Find closest X snap */
    for (const sx of snapX) {
      for (const ox of objXpts) {
        const d = sx - ox;
        if (Math.abs(d) <= SNAP_THRESHOLD) {
          if (snapDX === null || Math.abs(d) < Math.abs(snapDX)) {
            snapDX = d;
            guideXs = [sx];
          }
        }
      }
    }

    /* Find closest Y snap */
    for (const sy of snapY) {
      for (const oy of objYpts) {
        const d = sy - oy;
        if (Math.abs(d) <= SNAP_THRESHOLD) {
          if (snapDY === null || Math.abs(d) < Math.abs(snapDY)) {
            snapDY = d;
            guideYs = [sy];
          }
        }
      }
    }

    /* Apply snap by adjusting object position */
    if (snapDX !== null) {
      obj.set('left', obj.left + snapDX / zoom);
      obj.setCoords();
    }
    if (snapDY !== null) {
      obj.set('top',  obj.top  + snapDY / zoom);
      obj.setCoords();
    }

    /* Draw guide lines */
    const ctx = _guideCtx;
    ctx.save();
    ctx.strokeStyle = SNAP_COLOR;
    ctx.lineWidth   = 1;
    ctx.setLineDash([]);

    const w = ctx.canvas.width;
    const h = ctx.canvas.height;

    guideXs.forEach(function (x) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    });

    guideYs.forEach(function (y) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    });

    ctx.restore();
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 5 — Auto-Scale (fit-to-window)
   * ════════════════════════════════════════════════════════ */
  function computeScale() {
    /* Measure the ACTUAL available box (the #ed-viewport that holds the canvas),
       instead of subtracting assumed panel widths from window.innerWidth. This is
       correct whether the side panels are shown, hidden, collapsed or overlaid. */
    const vp = document.getElementById('ed-viewport');
    let availW, availH;
    if (vp) {
      const r = vp.getBoundingClientRect();
      availW = r.width  - CANVAS_MARGIN * 2;
      availH = r.height - CANVAS_MARGIN * 2;
    } else {
      /* Fallback before the shell is laid out — only subtract panels that are
         actually rendered (offsetParent !== null means visible & in-flow). */
      const toolbar    = document.getElementById('ed-toolbar');
      const panel      = document.getElementById('ed-slide-panel');
      const propsPanel = document.getElementById('ed-props-panel');
      const toolbarH    = toolbar    ? toolbar.offsetHeight    : 0;
      const panelW      = (panel      && panel.offsetParent)      ? panel.offsetWidth      : 0;
      const propsPanelW = (propsPanel && propsPanel.offsetParent) ? propsPanel.offsetWidth : 0;
      availW = window.innerWidth  - panelW - propsPanelW - CANVAS_MARGIN * 2;
      availH = window.innerHeight - toolbarH - CANVAS_MARGIN * 2;
    }
    if (availW <= 0 || availH <= 0) return 0.1;
    return Math.min(availW / SLIDE_W, availH / SLIDE_H);
  }

  function autoScale(targetScale) {
    if (!_canvas) return;
    const scale    = (typeof targetScale === 'number') ? targetScale : computeScale();
    const displayW = Math.round(SLIDE_W * scale);
    const displayH = Math.round(SLIDE_H * scale);

    _canvas.setZoom(scale);
    _canvas.setDimensions({ width: displayW, height: displayH });

    const wrap = document.getElementById('ed-canvas-wrap');
    if (wrap) { wrap.style.width = displayW + 'px'; wrap.style.height = displayH + 'px'; }

    const pct = Math.round(scale * 100) + '%';
    const label = document.getElementById('ed-zoom-label');
    if (label) label.textContent = pct;
    _setText('st-zoom', pct);

    _syncGuideCanvasSize(); // keep overlay canvas in sync
    document.documentElement.style.setProperty('--ed-zoom', scale);
    _renderTableLayer(_currentSlide);
    _canvas.requestRenderAll();
  }

  const ZOOM_STEPS = [0.1,0.15,0.2,0.25,0.33,0.4,0.5,0.6,0.67,0.75,0.8,0.9,1.0,1.1,1.25,1.5,1.75,2.0];
  function zoomIn()  { autoScale(nextZoom(1));  }
  function zoomOut() { autoScale(nextZoom(-1)); }
  function nextZoom(dir) {
    const cur = _canvas.getZoom();
    if (dir > 0) return ZOOM_STEPS.find(z => z > cur + 0.001) || ZOOM_STEPS[ZOOM_STEPS.length - 1];
    return [...ZOOM_STEPS].reverse().find(z => z < cur - 0.001) || ZOOM_STEPS[0];
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 4 — History & Undo/Redo
   *
   *  JSON snapshot system: every structural change saves a
   *  full canvas.toJSON() string into _history[].
   *
   *  Key design decisions:
   *  • _isRestoring flag prevents event-loops during restore
   *  • text:editing:exited saves AFTER a text session ends,
   *    not on every keystroke
   *  • object:modified saves AFTER mouse-up (final position)
   *  • MAX_HISTORY cap prevents unbounded memory growth
   * ═══════════════════════════════════════════════��════════ */

  /* ====================================================
   *  SECTION 4 — History (per-slide undo/redo)
   *
   *  Each slide has its own independent undo stack:
   *    _slides[i].history   = ['jsonStr0', 'jsonStr1', ...]
   *    _slides[i].historyIdx = current position (int)
   *
   *  Pressing Ctrl+Z on slide 2 ONLY undoes slide 2's
   *  changes. Slide 1 is completely untouched until the
   *  user navigates there and presses Ctrl+Z.
   * ==================================================== */

  /* ── Helpers to get/init current slide's history ── */
  function _slideHist() {
    var sl = _slides && _slides[_currentSlide];
    if (!sl) return null;
    if (!sl.history)   sl.history   = [];
    if (sl.historyIdx == null) sl.historyIdx = -1;
    return sl;
  }

  /* ─── LocalStorage auto-save ─────────────────────────────────────────────
   *  Saves _canvasJsons + _slideTables to localStorage 2 s after the last edit.
   *  On page load, if a record < 7 days old exists for this presentation title,
   *  it is auto-restored so edits survive browser refresh / accidental close.
   * ────────────────────────────────────────────────────────────────────────── */
  var _autoSaveTimer = null;

  function _autoSaveKey() {
    if (!_currentSpec || !_currentSpec.meta) return null;
    return 'FE_as_' + (_currentSpec.meta.title || 'deck').replace(/[^a-z0-9]/gi, '_').substring(0, 40);
  }

  var _TOJSON_KEYS = ['id', 'name', 'locked', 'editorType', 'isTableBg', '_latexSource', 'bulletType', 'tableId', 'tableRow', 'tableCol',
                      'anim', 'animOrder', 'animDur', 'animDelay',
                      /* lock state — custom flag + non-default Fabric lock props (else lost on save) */
                      '_edLocked', 'lockMovementX', 'lockMovementY', 'lockScalingX', 'lockScalingY', 'lockRotation', 'hasControls'];

  function _autoSaveDo() {
    /* Auto-restore is disabled (see loadFromSlideSpec), so writing autosave is
       pointless and only risks quota errors. No-op. Edits persist via Ctrl+S. */
    return;
    /* eslint-disable no-unreachable */
    var key = _autoSaveKey();
    if (!key) return;
    /* Flush live canvas into sl.json for the current slide, but only when NOT
       mid-restore (thumbnail generation cycles the canvas through other slides —
       flushing then would corrupt sl.json with another slide's objects). */
    if (!_isRestoring && _slides[_currentSlide] && _canvas) {
      _slides[_currentSlide].json = _canvas.toJSON(_TOJSON_KEYS);
    }
    var payload = {
      ts:          Date.now(),
      slide:       _currentSlide,
      theme:       (_currentSpec && _currentSpec.meta && _currentSpec.meta.theme) || null,
      canvasJsons: _slides.map(function(s) { return s.json || null; }),
      slideTables: _slides.map(function(s) { return s.tableData || null; }),
    };
    /* Primary attempt: full fidelity */
    try {
      localStorage.setItem(key, JSON.stringify(payload));
      return;
    } catch(e) { /* QuotaExceededError — data too large (images) */ }
    /* Fallback: strip embedded image data (large base64 src) and retry.
       Images are recreated from the original spec on next load. */
    try {
      var stripped = JSON.parse(JSON.stringify(payload));
      stripped.canvasJsons = stripped.canvasJsons.map(function(json) {
        if (!json) return null;
        (json.objects || []).forEach(function(o) {
          if (o.type === 'image' && typeof o.src === 'string' && o.src.length > 500) o.src = '';
        });
        return json;
      });
      localStorage.setItem(key, JSON.stringify(stripped));
    } catch(e2) { /* Completely full — give up */ }
  }

  function _scheduleAutoSave() {
    if (_autoSaveTimer) clearTimeout(_autoSaveTimer);
    _autoSaveTimer = setTimeout(_autoSaveDo, 300);
  }

  /* Returns parsed auto-save data if it exists, is < 7 days old, and matches slide count.
     Returns null if no valid save is found. */
  function _autoSaveLoad(spec) {
    try {
      var title = ((spec.meta && spec.meta.title) || 'deck').replace(/[^a-z0-9]/gi, '_').substring(0, 40);
      var raw = localStorage.getItem('FE_as_' + title);
      if (!raw) return null;
      var d = JSON.parse(raw);
      if (!d || !Array.isArray(d.canvasJsons)) return null;
      if (Date.now() - (d.ts || 0) > 7 * 24 * 3600 * 1000) { localStorage.removeItem('FE_as_' + title); return null; }
      if (d.canvasJsons.length !== (spec.slides || []).length) return null;
      return d;
    } catch(e) { return null; }
  }

  function _showToast(msg, duration) {
    var t = document.getElementById('ed-autosave-toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('ed-toast-visible');
    setTimeout(function() { t.classList.remove('ed-toast-visible'); }, duration || 3000);
  }

  function saveState() {
    if (_isRestoring || _batchSave) return;
    var sl = _slideHist();
    if (!sl) return;
    sl.hasUserContent = true;
    /* Flush current canvas into slide slot */
    var json    = _canvas.toJSON(_TOJSON_KEYS);
    sl.json = json;
    /* Snapshot tableData alongside canvas JSON in a single history entry */
    var entry = JSON.stringify({ _c: json, _t: sl.tableData ? JSON.parse(JSON.stringify(sl.tableData)) : null });
    /* Trim any forward (redo) entries */
    sl.history = sl.history.slice(0, sl.historyIdx + 1);
    /* Deduplicate consecutive identical states */
    if (sl.history.length && sl.history[sl.history.length - 1] === entry) return;
    sl.history.push(entry);
    if (sl.history.length > MAX_HISTORY) sl.history.shift();
    sl.historyIdx = sl.history.length - 1;
    _syncUndoRedoBtns();
    _scheduleAutoSave();
    _scheduleThumbUpdate();   /* live left-panel preview (covers table & non-canvas edits too) */
  }

  function _beginBatch() { _batchSave = true; }
  function _endBatch()   { _batchSave = false; saveState(); }

  function _parseHistoryEntry(raw) {
    /* New format: { _c: canvasJson, _t: tableData }
       Old format: plain canvas JSON object (backwards compat) */
    if (raw && raw._c !== undefined) return { canvas: raw._c, tableData: raw._t };
    return { canvas: raw, tableData: undefined };
  }

  function undo() {
    var sl = _slideHist();
    if (!sl || sl.historyIdx <= 0) return;
    sl.historyIdx--;
    var raw = JSON.parse(sl.history[sl.historyIdx]);
    var parsed = _parseHistoryEntry(raw);
    sl.json = parsed.canvas;
    if (parsed.tableData !== undefined) sl.tableData = parsed.tableData;
    _restoreCurrentSlideCanvas(parsed.canvas);
  }

  function redo() {
    var sl = _slideHist();
    if (!sl || sl.historyIdx >= sl.history.length - 1) return;
    sl.historyIdx++;
    var raw = JSON.parse(sl.history[sl.historyIdx]);
    var parsed = _parseHistoryEntry(raw);
    sl.json = parsed.canvas;
    if (parsed.tableData !== undefined) sl.tableData = parsed.tableData;
    _restoreCurrentSlideCanvas(parsed.canvas);
  }

  /* Restore the canvas from a JSON snapshot (stays on current slide) */
  function _restoreCurrentSlideCanvas(json) {
    _isRestoring = true;
    _canvas.loadFromJSON(json, function() {
      var sl = _slides[_currentSlide];
      if (sl && sl.bgColor != null) _canvas.backgroundColor = sl.bgColor;
      _canvas.discardActiveObject();
      _canvas.requestRenderAll();
      _isRestoring = false;
      _syncUndoRedoBtns();
      syncRibbonToSelection();
      syncPropsPanel();
      _rebuildThumbPanel();
      _syncLayoutSelector();
      _startBlobAnimation();
      _renderTableLayer(_currentSlide);   /* restore HTML table overlay (tableData already set by undo/redo) */
    });
  }

  function _syncUndoRedoBtns() {
    var sl     = _slideHist();
    var hist   = sl ? sl.history   : [];
    var idx    = sl ? sl.historyIdx : -1;
    var canUndo = idx > 0;
    var canRedo = idx < hist.length - 1;
    var u = document.getElementById('btn-undo');
    var r = document.getElementById('btn-redo');
    /* Toggle the `off` class (CSS: .ed-btn.off{pointer-events:none}) — the markup ships
       these buttons with `off`, so if we only toggled `.disabled` they'd stay click-dead
       forever (only Ctrl+Z worked). */
    if (u) { u.disabled = !canUndo; u.classList.toggle('off', !canUndo); }
    if (r) { r.disabled = !canRedo; r.classList.toggle('off', !canRedo); }
  }

  function initHistory() {
    /* Hook canvas events that represent structural changes */
    _canvas.on('object:added',        saveState);
    _canvas.on('object:removed',      saveState);
    _canvas.on('object:modified',     saveState);
    _canvas.on('text:editing:exited', saveState);
    /* Save the initial blank-slate snapshot for slide 0 */
    saveState();
  }

  /* ── Export / Import JSON (for Python backend integration) ── */

  function exportJSON() {
    return _canvas.toJSON(_TOJSON_KEYS);
  }

  function loadJSON(data) {
    const obj = (typeof data === 'string') ? JSON.parse(data) : data;
    _isRestoring = true;
    _canvas.loadFromJSON(obj, function () {
      _canvas.discardActiveObject();
      _canvas.requestRenderAll();
      _isRestoring = false;
      _history      = [JSON.stringify(obj)];
      _historyIndex = 0;
      _syncUndoRedoBtns();
    });
  }

  /* ── Slide Spec Loader — converts Python-generated JSON → canvas ── */
  /* Visual design mirrors the CSS HTML themes exactly:
     - All slides: dark gradient background (same as CSS .slide.cover/.slide.body)
     - Chrome: floating mono text at top/bottom edges (no bar)
     - Display fonts: per-theme, weight 400 (elegant like CSS)
     - Keypoints-style content rows with accent left-border
  */

  var _SPEC_THEMES = {
    frankfurt: {
      coverGrad: [{offset:0,color:'#0f172a'},{offset:0.46,color:'#172554'},{offset:1,color:'#1d4ed8'}],
      endGrad:   [{offset:0,color:'#1e3a8a'},{offset:0.72,color:'#0f172a'}],
      accent:'#e2b96f', text:'#f8fafc', dim:'rgba(248,250,252,0.55)',
      panelText:'#0b0d12',
      cards:['#5b9bd5','#e07b6a','#7b68c8','#f0a050'],
      fontDisplay:"'Montserrat', system-ui, sans-serif",
      fontBody:"'Open Sans', system-ui, sans-serif",
      fontMono:"'IBM Plex Mono', monospace",
    },
    umn: {
      coverGrad: [{offset:0,color:'#7a0019'},{offset:0.55,color:'#500014'},{offset:1,color:'#330009'}],
      endGrad:   [{offset:0,color:'#7a0019'},{offset:0.72,color:'#330009'}],
      accent:'#ffcc33', text:'#ffffff', dim:'rgba(255,255,255,0.55)',
      panelText:'#1a0008',
      cards:['#900021','#2c6fad','#2a7a6f','#b8860b'],
      fontDisplay:"'Playfair Display', Georgia, serif",
      fontBody:"'Source Sans 3', system-ui, sans-serif",
      fontMono:"'IBM Plex Mono', monospace",
    },
    seriph: {
      coverGrad: [{offset:0,color:'#1a1a2e'},{offset:0.5,color:'#16213e'},{offset:1,color:'#0f3460'}],
      endGrad:   [{offset:0,color:'#16213e'},{offset:0.72,color:'#0a0a1a'}],
      accent:'#e94560', text:'#f8fafc', dim:'rgba(248,250,252,0.55)',
      panelText:'#0a0a0a',
      cards:['#c0415e','#4a6fa5','#7a5c8a','#5a8a5a'],
      fontDisplay:"'Cormorant Garamond', Georgia, serif",
      fontBody:"'Work Sans', system-ui, sans-serif",
      fontMono:"'JetBrains Mono', monospace",
    },
    scholarly: {
      coverGrad: [{offset:0,color:'#1e2a3a'},{offset:0.5,color:'#2d3e50'},{offset:1,color:'#3a5068'}],
      endGrad:   [{offset:0,color:'#2d3e50'},{offset:0.72,color:'#0f1a25'}],
      accent:'#f0a500', text:'#f0f0f0', dim:'rgba(240,240,240,0.55)',
      panelText:'#0f1a25',
      cards:['#3a7abf','#bf5a3a','#5a7a3a','#7a5abf'],
      fontDisplay:"'Lora', Georgia, serif",
      fontBody:"'IBM Plex Sans', system-ui, sans-serif",
      fontMono:"'IBM Plex Mono', monospace",
    },
    'improving-25': {
      coverGrad: [{offset:0,color:'#0f0c29'},{offset:0.5,color:'#302b63'},{offset:1,color:'#24243e'}],
      endGrad:   [{offset:0,color:'#302b63'},{offset:0.72,color:'#0f0c29'}],
      accent:'#a855f7', text:'#f8fafc', dim:'rgba(248,250,252,0.55)',
      panelText:'#0f0c29',
      cards:['#9333ea','#06b6d4','#f59e0b','#10b981'],
      fontDisplay:"'Crimson Pro', Georgia, serif",
      fontBody:"'Space Grotesk', system-ui, sans-serif",
      fontMono:"'Space Mono', monospace",
    },
    meetup: {
      coverGrad: [{offset:0,color:'#1a5276'},{offset:0.5,color:'#1b4f72'},{offset:1,color:'#1a252f'}],
      endGrad:   [{offset:0,color:'#1a5276'},{offset:0.72,color:'#1a252f'}],
      accent:'#5dade2', text:'#f8fafc', dim:'rgba(248,250,252,0.55)',
      panelText:'#1a252f',
      cards:['#2980b9','#e57373','#5a9a5a','#f0a050'],
      fontDisplay:"'Newsreader', Georgia, serif",
      fontBody:"'DM Sans', system-ui, sans-serif",
      fontMono:"'JetBrains Mono', monospace",
    },
    bricks: {
      coverGrad: [{offset:0,color:'#c0392b'},{offset:0.5,color:'#a93226'},{offset:1,color:'#922b21'}],
      endGrad:   [{offset:0,color:'#c0392b'},{offset:0.72,color:'#6e2016'}],
      accent:'#f1c40f', text:'#ffffff', dim:'rgba(255,255,255,0.6)',
      panelText:'#1a0a00',
      cards:['#c0392b','#d4891a','#8a4a2f','#6a7a8a'],
      fontDisplay:"'Spectral', Georgia, serif",
      fontBody:"'Archivo', system-ui, sans-serif",
      fontMono:"'IBM Plex Mono', monospace",
    },
    ivory: {
      coverGrad: [{offset:0,color:'#2c2c3e'},{offset:1,color:'#1a1a2e'}],
      endGrad:   [{offset:0,color:'#2c2c3e'},{offset:0.72,color:'#0f0f1e'}],
      accent:'#c9a96e', text:'#f8fafc', dim:'rgba(248,250,252,0.55)',
      contentBg:'#fffef5', contentText:'#1a1a2e', contentDim:'rgba(26,26,46,0.72)', contentAccent:'#b8913a',
      panelText:'#1a1a2e',
      cards:['#7a4f2e','#4a6fa5','#5a8a5a','#7a5c8a'],
      fontDisplay:"'Cormorant Garamond', Georgia, serif",
      fontBody:"'Work Sans', system-ui, sans-serif",
      fontMono:"'IBM Plex Mono', monospace",
    },
    sage: {
      coverGrad: [{offset:0,color:'#1b3a2d'},{offset:0.5,color:'#0f2318'},{offset:1,color:'#071a0f'}],
      endGrad:   [{offset:0,color:'#1b3a2d'},{offset:0.72,color:'#071a0f'}],
      accent:'#7ecba1', text:'#f0faf3', dim:'rgba(240,250,243,0.55)',
      contentBg:'#f4fcf7', contentText:'#071a0f', contentDim:'rgba(7,26,15,0.72)', contentAccent:'#1a6e40',
      panelText:'#071a0f',
      cards:['#1d6b45','#2e6da4','#7a5c8a','#8b6914'],
      fontDisplay:"'Lora', Georgia, serif",
      fontBody:"'Source Sans 3', system-ui, sans-serif",
      fontMono:"'JetBrains Mono', monospace",
    },
  };

  /* ── Spec Builder Helpers ── */

  /* Textbox with auto-wrap for spec slides */
  function _sTB(str, opts) {
    var tb = new fabric.Textbox(String(str || ''), Object.assign({
      editorType: 'text', lockRotation: false, splitByGrapheme: false,
    }, opts));
    /* Force word-wrap recalculation — Fabric.js v5 may not wrap correctly until after font loads */
    if (opts && opts.width) tb.initDimensions();
    return tb;
  }

  /* Non-selectable static element (chrome/deco) */
  function _sStatic(obj) {
    obj.set({ selectable: false, evented: false, hoverCursor: 'default' });
    return obj;
  }

  /* Get current slide palette from _currentSpec theme */
  function _getPal() {
    var theme = (_currentSpec && _currentSpec.meta && _currentSpec.meta.theme) || 'frankfurt';
    return _SPEC_THEMES[theme] || _SPEC_THEMES.frankfurt;
  }

  /* Split "Heading: body text" or "Heading — body text" into {title, body}.
     Falls back to full text as title when no separator is found. */
  function _splitHeadBody(text) {
    var s = String(text || '').trim();
    for (var _si = 0; _si < s.length; _si++) {
      if (s[_si] === ':' && _si > 0 && _si < 60) {
        return { title: s.slice(0, _si).trim(), body: s.slice(_si + 1).trim() };
      }
      if (s.slice(_si, _si + 3) === ' — ' && _si > 0) {
        return { title: s.slice(0, _si).trim(), body: s.slice(_si + 3).trim() };
      }
      if (s.slice(_si, _si + 3) === ' – ' && _si > 0) {
        return { title: s.slice(0, _si).trim(), body: s.slice(_si + 3).trim() };
      }
    }
    return { title: s, body: '' };
  }

  /* True when a spec has no real user content at all (blank new slide). */
  function _isSpecBlank(spec) {
    if (!spec) return true;
    if (spec.title && spec.title.trim()) return false;
    var dataFields = ['content','points','cols','cells','steps','conclusions','items','stats','cards'];
    for (var _di = 0; _di < dataFields.length; _di++) {
      var _df = spec[dataFields[_di]];
      if (Array.isArray(_df) && _df.length > 0) return false;
      if (typeof _df === 'string' && _df.trim()) return false;
    }
    if (spec.quote && spec.quote.trim()) return false;
    if (spec.sub_content_1 && spec.sub_content_1.length > 0) return false;
    if (spec.sub_content_2 && spec.sub_content_2.length > 0) return false;
    if (spec.latex_formula_block && spec.latex_formula_block.trim()) return false;
    if (spec.table_markdown && spec.table_markdown.trim()) return false;
    return true;
  }

  /* Returns skeleton placeholder data for a given layout, used when creating
     a blank slide — gives the user visible placeholder structure to fill in. */
  function _makePlaceholderSpec(lay) {
    function _pt(n) { var ic=['📌','🔑','💡','📋']; return { icon:ic[n%4], title:'Key point '+(n+1), body:'Add a brief description here.' }; }
    function _ps(n) { return { title:'Step '+(n+1), body:'Describe what happens in this step.' }; }
    function _pc(n,ic) { return { icon:ic, title:'Column '+(n+1), body:'Add your content here.', bullets:[] }; }
    function _pg(n,ic) { return { icon:ic, title:'Item '+(n+1), body:'Add a short description.' }; }
    function _pcc(n) { return { heading:'Conclusion '+(n+1), body:'Supporting detail here.' }; }
    function _pi(n) { return { title:'Topic '+(n+1), body:'Brief description.', duration:'' }; }
    switch (lay) {
      case 'only_content':
      case 'two_cols_content_layout':
        return { content: ['First bullet point here.', 'Second bullet point here.', 'Third bullet point here.'] };
      case 'two_contents_in_a_slide_layout':
        return { sub_title_1:'Left Column', sub_content_1:['First point.','Second point.'], sub_title_2:'Right Column', sub_content_2:['First point.','Second point.'] };
      case 'key_points_layout':
        return { points: [_pt(0),_pt(1),_pt(2)] };
      case 'steps_horizontal_layout':
        return { steps: [_ps(0),_ps(1),_ps(2),_ps(3)] };
      case 'three_cols_content_layout':
        return { cols: [_pc(0,'📌'),_pc(1,'🔑'),_pc(2,'💡')] };
      case 'grid_2x2_layout':
        return { cells: [_pg(0,'🔷'),_pg(1,'🔶'),_pg(2,'🔵'),_pg(3,'🟡')] };
      case 'conclusion_cards_layout':
        return { conclusions: [_pcc(0),_pcc(1),_pcc(2)] };
      case 'numbered_conclusions_layout':
        return { conclusions: [_pcc(0),_pcc(1),_pcc(2),_pcc(3)] };
      case 'agenda_layout':
        return { items: [_pi(0),_pi(1),_pi(2)] };
      case 'stats_cards_layout':
        return { stats: [{value:'100%',label:'Metric 1',body:''},{value:'50+',label:'Metric 2',body:''},{value:'3×',label:'Metric 3',body:''}] };
      case 'pricing_cards_layout':
        return { cards: [{name:'Basic',price:'Free',features:['Feature 1','Feature 2'],highlighted:false},{name:'Pro',price:'$9/mo',features:['Feature 1','Feature 2','Feature 3'],highlighted:true},{name:'Enterprise',price:'Contact us',features:['All Pro features'],highlighted:false}] };
      case 'quote_layout':
        return { quote:'Add your quote here...', attribution:'— Author Name' };
      case 'comparison_layout':
        return { table_markdown:'| Column A | Column B |\n|---|---|\n| Item 1 | Item 1 |\n| Item 2 | Item 2 |' };
      case 'table_above_layout':
        return { table_markdown:'| Header 1 | Header 2 |\n|---|---|\n| Data | Data |', content:['Supporting bullet point.'] };
      case 'image_left_layout': case 'image_right_layout':
      case 'image_above_layout': case 'image_below_layout':
        return { content:['First bullet point.','Second bullet point.'] };
      case 'two_image_left_layout': case 'two_image_right_layout':
      case 'two_image_above_layout': case 'two_image_below_layout':
        return { content:['First bullet point.','Second bullet point.'], img1_path:'', img2_path:'', caption1:'', caption2:'' };
      case 'image_fullscreen_overlay_layout':
        return { body:'Add a short caption or description over the image.', img_path:'' };
      case 'data_table_layout':
        return { headers:['Column A','Column B'], rows:[['Item 1','Value 1'],['Item 2','Value 2']] };
      case 'nested_bullets_layout':
        return { items:[{text:'Main point one', sub:['Sub-point A','Sub-point B']},{text:'Main point two', sub:['Sub-point C']}] };
      case 'research_question_layout':
        return { main_question:'What is the central research question?', sub_questions:['Sub-question 1?','Sub-question 2?','Sub-question 3?'] };
      case 'editorial_layout':
        return { eyebrow:'Feature', lede:'Add your lede paragraph here.', pull_quote:'A memorable pull quote.', pull_attribution:'Author' };
      case 'split_contrast_layout':
        return { left_title:'Before', left_items:['Old point 1','Old point 2'], right_title:'After', right_items:['New point 1','New point 2'] };
      case 'formula_top_layout': case 'formula_below_layout':
        return { latex_formula_block:'E = mc^2', content:['Explanation of the formula.'] };
      case 'config_and_greeting_slide': case 'cover_split_layout':
        return { short_title:'Presentation Title', title:'Presentation Title', author:'Presented by' };
      case 'section_divider_layout':
        return { title:'Section Title', section_number:'01' };
      case 'end_layout': case 'end_with_image_layout': case 'end_image_hero_layout':
        return { end_text:'Thank you' };
      case 'toc_layout': case 'toc_vertical_layout': case 'toc_described_layout': case 'toc_cards_layout':
        return { items:['First topic','Second topic','Third topic'] };
      default:
        return { content:['First bullet point.','Second bullet point.'] };
    }
  }

  /* Populate layout-specific data fields on a slide spec from spec.content.
     Called when the user changes layout via the dropdown, so the new layout
     has the structured data it needs (points, steps, cols, etc.). */
  function _synthesiseLayoutData(spec, lay) {
    var raw = spec.content || [];
    var items = Array.isArray(raw) ? raw : (typeof raw === 'string' ? raw.split('\n').filter(Boolean) : []);
    var _icons = ['📌', '🔑', '💡', '📋', '🎯', '⚡', '🔍', '📊'];
    if (lay === 'key_points_layout') {
      if (!spec.points || spec.points.length === 0) {
        spec.points = items.map(function(b, i) {
          var p = _splitHeadBody(b);
          var t = p.title, bd = p.body;
          /* Plain sentence (no separator found): auto-split first 4 words as title */
          if (!bd && t) {
            var words = t.split(/\s+/);
            if (words.length > 5) { t = words.slice(0, 4).join(' '); bd = words.slice(4).join(' '); }
          }
          return { icon: _icons[i % _icons.length], title: t, body: bd };
        });
      }
    } else if (lay === 'steps_horizontal_layout') {
      if (!spec.steps || spec.steps.length === 0) {
        spec.steps = items.slice(0, 5).map(function(b) {
          var p = _splitHeadBody(b);
          return { title: p.title, body: p.body };
        });
      }
    } else if (lay === 'conclusion_cards_layout' || lay === 'numbered_conclusions_layout') {
      if (!spec.conclusions || spec.conclusions.length === 0) {
        spec.conclusions = items.map(function(b) {
          var p = _splitHeadBody(b);
          return { heading: p.title, body: p.body || b };
        });
      }
    } else if (lay === 'three_cols_content_layout') {
      if (!spec.cols || spec.cols.length === 0) {
        var _cIcons = ['📌', '🔑', '💡'];
        spec.cols = items.slice(0, 3).map(function(b, i) {
          var p = _splitHeadBody(b);
          return { icon: _cIcons[i], title: p.title, body: p.body || b, bullets: [] };
        });
      }
    } else if (lay === 'grid_2x2_layout') {
      if (!spec.cells || spec.cells.length === 0) {
        var _gIcons = ['🔷', '🔶', '🔵', '🟡'];
        spec.cells = items.slice(0, 4).map(function(b, i) {
          var p = _splitHeadBody(b);
          return { icon: _gIcons[i], title: p.title, body: p.body || b };
        });
      }
    } else if (lay === 'agenda_layout') {
      if (!spec.items || spec.items.length === 0) {
        spec.items = items.map(function(b) {
          var p = _splitHeadBody(b);
          return { title: p.title, body: p.body || b, duration: '' };
        });
      }
    } else if (lay === 'stats_cards_layout') {
      if (!spec.stats || spec.stats.length === 0) {
        spec.stats = items.slice(0, 4).map(function(b) {
          var p = _splitHeadBody(b);
          return { value: '', label: p.title, body: p.body };
        });
      }
    } else if (lay === 'pricing_cards_layout') {
      if (!spec.cards || spec.cards.length === 0) {
        spec.cards = items.slice(0, 4).map(function(b, i) {
          var p = _splitHeadBody(b);
          return { name: p.title, price: '', features: p.body ? [p.body] : [b], highlighted: i === 0 };
        });
      }
    } else if (lay === 'split_contrast_layout') {
      if (!spec.left_items && !spec.right_items) {
        var _mid = Math.ceil(items.length / 2);
        spec.left_title  = spec.left_title  || 'Before';
        spec.right_title = spec.right_title || 'After';
        spec.left_items  = items.slice(0, _mid);
        spec.right_items = items.slice(_mid);
      }
    } else if (lay === 'research_question_layout') {
      if (!spec.main_question) {
        spec.main_question = items[0] || spec.title || '';
        spec.sub_questions = spec.sub_questions || items.slice(1, 4);
      }
    } else if (lay === 'editorial_layout') {
      if (!spec.lede) spec.lede = items.join(' ');
    } else if (lay === 'nested_bullets_layout') {
      if (!spec.items || spec.items.length === 0) {
        spec.items = items.map(function(b) { return { text: b, sub: [] }; });
      }
    }
    /* two_image_* / image_fullscreen keep spec.content (bullets); images default to
       empty placeholders. data_table / formula fall back to _makePlaceholderSpec when blank. */
  }

  /* Decorative blobs for content slides — replicates CSS .blobs::before/::after */
  function _sBlobs(pal) {
    _canvas.add(_sStatic(new fabric.Ellipse({
      left: -150, top: -130, rx: 250, ry: 240,
      fill: 'transparent', stroke: pal.accent, strokeWidth: 2,
      opacity: 0.14, editorType: 'deco',
    })));
    _canvas.add(_sStatic(new fabric.Ellipse({
      left: 1630, top: 780, rx: 210, ry: 200,
      fill: 'transparent', stroke: pal.accent, strokeWidth: 2,
      opacity: 0.10, editorType: 'deco',
    })));
  }

  /* Animate deco ellipse blobs: opacity pulse + scale breathing + slow float drift */
  function _startBlobAnimation() {
    var ver = ++_blobAnimVer;
    var t0  = null;
    var blobs = _canvas.getObjects().filter(function(o) {
      return o.editorType === 'deco' && o.type === 'ellipse';
    });
    if (blobs.length === 0) return;
    /* Snapshot base values so each run starts from the same origin */
    var bdata = blobs.map(function(o, idx) {
      return {
        baseOpacity: (o.opacity != null) ? o.opacity : 0.14,
        baseLeft:    o.left || 0,
        baseTop:     o.top  || 0,
        phase:       idx * 1.7,   /* stagger so blobs move out-of-sync */
      };
    });
    function step(ts) {
      if (_blobAnimVer !== ver) return;   /* cancelled by slide change */
      if (!t0) t0 = ts;
      var t   = (ts - t0) / 1000;
      var cur = _canvas.getObjects();
      var dirty = false;
      blobs.forEach(function(obj, idx) {
        if (cur.indexOf(obj) < 0) return;  /* blob no longer on canvas */
        var d  = bdata[idx];
        var ph = d.phase;
        /* 1. Opacity pulse: slow sine, range [0.3x, 1.8x] base */
        var opFactor = 1 + 0.75 * (0.5 + 0.5 * Math.sin(t * 0.55 + ph));
        obj.opacity = Math.min(0.55, d.baseOpacity * opFactor);
        /* 2. Scale breathing: ±6% on a slightly different frequency */
        var sc = 1 + 0.06 * Math.sin(t * 0.45 + ph + 1.3);
        obj.scaleX = sc;
        obj.scaleY = sc;
        /* 3. Slow float drift: ±12px horizontal, ±8px vertical */
        obj.left = d.baseLeft + 12 * Math.sin(t * 0.22 + ph);
        obj.top  = d.baseTop  +  8 * Math.sin(t * 0.28 + ph + 0.8);
        dirty = true;
      });
      if (dirty) _canvas.requestRenderAll();
      requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  /* Convert raw LaTeX source to readable Unicode pseudo-math for canvas display */
  function _cleanLatex(str) {
    if (!str) return '';
    var s = str;
    s = s.replace(/\\begin\{[^}]*\}/g, '').replace(/\\end\{[^}]*\}/g, '');
    s = s.replace(/\\\[/g, '').replace(/\\\]/g, '');
    s = s.replace(/\\\(/g, '').replace(/\\\)/g, '');  /* strip inline math delimiters \(...\) */
    s = s.replace(/\\\\/g, '\n');
    s = s.replace(/\\le\b|\\leq\b|&le\b|\\&le\b/g, '≤');
    s = s.replace(/\\ge\b|\\geq\b|&ge\b|\\&ge\b/g, '≥');
    s = s.replace(/\\ne\b|\\neq\b/g, '≠');
    s = s.replace(/\\dots\b|\\cdots\b|\\ldots\b/g, '…');
    s = s.replace(/\\vdots\b/g, '⋮');
    s = s.replace(/\\cdot\b/g, '·').replace(/\\times\b/g, '×').replace(/\\div\b/g, '÷').replace(/\\pm\b/g, '±');
    s = s.replace(/\\infty\b/g, '∞').replace(/\\sum\b/g, 'Σ').replace(/\\prod\b/g, 'Π').replace(/\\int\b/g, '∫');
    s = s.replace(/\\sqrt\{([^}]*)\}/g, '√($1)');
    s = s.replace(/\\frac\{([^}]*)\}\{([^}]*)\}/g, '($1)/($2)');
    s = s.replace(/\\alpha\b/g,'α').replace(/\\beta\b/g,'β').replace(/\\gamma\b/g,'γ').replace(/\\delta\b/g,'δ');
    s = s.replace(/\\epsilon\b/g,'ε').replace(/\\theta\b/g,'θ').replace(/\\lambda\b/g,'λ').replace(/\\mu\b/g,'μ');
    s = s.replace(/\\pi\b/g,'π').replace(/\\sigma\b/g,'σ').replace(/\\tau\b/g,'τ').replace(/\\phi\b/g,'φ');
    s = s.replace(/\\omega\b/g,'ω').replace(/\\Sigma\b/g,'Σ').replace(/\\Delta\b/g,'Δ').replace(/\\Omega\b/g,'Ω');
    s = s.replace(/\\text\{([^}]*)\}/g, '$1');
    s = s.replace(/\\quad\b/g, '  ').replace(/\\qquad\b/g, '   ');
    s = s.replace(/&/g, '');
    var SUB = {'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉',
               'a':'ₐ','e':'ₑ','i':'ᵢ','j':'ⱼ','k':'ₖ','m':'ₘ','n':'ₙ','p':'ₚ','r':'ᵣ','s':'ₛ','t':'ₜ'};
    var SUP = {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹','n':'ⁿ','i':'ⁱ'};
    s = s.replace(/_\{([^}]{2,})\}/g, function(_, m) { return m.split('').map(function(c){ return SUB[c]||c; }).join(''); });
    s = s.replace(/\^\{([^}]{2,})\}/g, function(_, m) { return m.split('').map(function(c){ return SUP[c]||c; }).join(''); });
    s = s.replace(/_([a-zA-Z0-9])/g, function(_, c){ return SUB[c] || ('_' + c); });
    s = s.replace(/\^([a-zA-Z0-9])/g, function(_, c){ return SUP[c] || ('^' + c); });
    s = s.replace(/\\[a-zA-Z]+/g, '');
    s = s.replace(/\{|\}/g, '');
    s = s.replace(/[ \t]{2,}/g, ' ');
    s = s.replace(/\n{3,}/g, '\n\n');
    return s.trim();
  }

  /* Clean inline LaTeX markers in bullet text, leave surrounding text intact.
     Handles both $...$ (dollar) and \(...\) (backslash-paren) delimiters. */
  function _stripInlineMath(str) {
    if (!str) return str;
    /* $...$ — inner must not contain $ or newline to avoid swallowing $$...$$ blocks */
    str = str.replace(/\$([^$\n]+?)\$/g, function(_, inner) { return _cleanLatex(inner); });
    /* \(...\) */
    str = str.replace(/\\\((.+?)\\\)/g, function(_, inner) { return _cleanLatex(inner); });
    return str;
  }

  /* Apply diagonal linear gradient as canvas background — pixel coords match 1920×1080 */
  function _sGradBg(stops) {
    _canvas.backgroundColor = new fabric.Gradient({
      type: 'linear',
      gradientUnits: 'pixels',
      coords: { x1: 0, y1: 0, x2: SLIDE_W, y2: SLIDE_H },
      colorStops: stops,
    });
  }

  /* Layouts that always use the dark gradient (cover + end + section dividers) */
  var _COVER_LAYOUTS = [
    'config_and_greeting_slide', 'cover_split_layout', 'section_divider_layout',
    'end_layout', 'end_with_image_layout', 'end_image_hero_layout',
  ];

  /* Return effective palette — light themes override text/accent/dim for content slides */
  function _effectivePal(lay, basePal) {
    if (!basePal.contentBg || _COVER_LAYOUTS.indexOf(lay) >= 0) return basePal;
    return Object.assign({}, basePal, {
      text:   basePal.contentText   || basePal.text,
      dim:    basePal.contentDim    || basePal.dim,
      accent: basePal.contentAccent || basePal.accent,
      _isLight: true,
    });
  }

  /* Set slide background: solid color for light themes, gradient for dark themes.
     spec.bgGrad overrides the theme gradient when set by the user. */
  function _setBg(pal, spec) {
    if (spec && spec.bgGrad) {
      _sGradBg(spec.bgGrad);
    } else if (pal._isLight && pal.contentBg) {
      _canvas.backgroundColor = pal.contentBg;
    } else {
      _sGradBg(pal.coverGrad);
    }
  }

  /* Adaptive font size: shrink if text would wrap beyond maxLines at maxFz */
  function _adaptFontSize(text, maxWidth, maxLines, maxFz) {
    maxFz   = maxFz   || 72;
    maxLines = maxLines || 2;
    var charsPerLine = Math.max(1, Math.floor(maxWidth / (maxFz * 0.52)));
    var estLines     = Math.ceil((text || '').length / charsPerLine);
    if (estLines <= maxLines) return maxFz;
    var scaled = Math.floor(maxFz * maxLines / estLines);
    return Math.max(32, scaled);
  }

  /* Standard slide title: top-anchored, measured, height-capped (~2 lines) + accent
     line. Consistent across content layouts so titles align and leave room for content.
     Returns the Y where content should start. opts: {x,w,top,maxFz,maxH,gap,accentW} */
  function _sTitleBlock(title, pal, opts) {
    opts = opts || {};
    var x = opts.x != null ? opts.x : 120;
    var w = opts.w || 1680;
    var top = opts.top != null ? opts.top : 150;
    var maxFz = opts.maxFz || 88;
    var maxH = opts.maxH || 200;
    var gap = opts.gap != null ? opts.gap : 34;
    var mk = function (fz) {
      return _sTB(title || '', { left: x, top: top, width: w, fontFamily: pal.fontDisplay,
        fontSize: fz, fontWeight: '400', fill: pal.text, charSpacing: -20, lineHeight: 1.05 });
    };
    var fz = Math.max(28, _adaptFontSize(title || '', w, 2, maxFz));
    var obj = mk(fz);
    if ((obj.height || 0) > maxH) {                 /* shrink to fit height cap */
      fz = Math.max(28, Math.floor(fz * maxH / obj.height));
      obj = mk(fz);
      while (fz > 28 && (obj.height || 0) > maxH) { fz -= 2; obj = mk(fz); }
    }
    _canvas.add(obj);
    var accentY = top + (obj.height || fz * 1.2) + 10;
    _sAccentLine(x, accentY, opts.accentW || 60, pal);
    return accentY + gap;
  }

  /* Chrome: mono-font text at top-left (dot + title) and bottom (author + page) */
  function _sChrome(meta, pageNum, pal, slideLayout) {
    var W = 1920, H = 1080;
    meta = meta || {};

    /* Skip all chrome on title/cover slides if hideOnTitle is set */
    var _COVER = ['config_and_greeting_slide','cover_split_layout'];
    var _END   = ['end_layout','end_with_image_layout','end_image_hero_layout'];
    if (meta.hideOnTitle && slideLayout && (_COVER.indexOf(slideLayout) !== -1 || _END.indexOf(slideLayout) !== -1)) return;

    var titleStr = meta.title ? String(meta.title).toUpperCase().slice(0, 72) : '';

    /* Top dot + title */
    _canvas.add(_sStatic(new fabric.Circle({ left: 80, top: 34, radius: 5, fill: pal.accent, editorType: 'chrome' })));
    if (titleStr) {
      _canvas.add(_sStatic(_sTB(titleStr, {
        left: 102, top: 28, width: 1600,
        fontFamily: pal.fontMono, fontSize: 20, charSpacing: 120,
        fill: pal.text, opacity: 0.72, editorType: 'chrome',
      })));
    }

    /* Bottom area — three zones:
       Left (x=80–650):  author or footer text
       Center (x=680–1240): date (if enabled)
       Right (x=1740–1840): page number */
    var bottomY = H - 50;

    /* Author / footer left */
    var showAuthor = meta.showAuthor === true;
    var authorStr = (meta.author && showAuthor) ? 'Presented by ' + meta.author : '';
    var showFooter = meta.showFooter;
    var footerStr  = showFooter ? (meta.footerText || '') : '';
    var leftStr = footerStr || authorStr;
    if (leftStr) {
      _canvas.add(_sStatic(_sTB(leftStr, {
        left: 80, top: bottomY, width: 580,
        fontFamily: pal.fontMono, fontSize: 20, fill: pal.text, opacity: 0.72, editorType: 'chrome',
      })));
    }

    /* Date center */
    var showDate = meta.showDate;
    if (showDate) {
      var dateStr = '';
      if (meta.dateAuto !== false) {
        var now = new Date();
        var fmt = meta.dateFormat || 'short';
        if      (fmt === 'long')   dateStr = now.toLocaleDateString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric'});
        else if (fmt === 'medium') dateStr = now.toLocaleDateString('en-US',{year:'numeric',month:'long',day:'numeric'});
        else if (fmt === 'month')  dateStr = now.toLocaleDateString('en-US',{year:'numeric',month:'long'});
        else                       dateStr = now.toLocaleDateString('en-US');
      } else {
        dateStr = meta.dateFixed || '';
      }
      if (dateStr) {
        _canvas.add(_sStatic(_sTB(dateStr, {
          left: 680, top: bottomY, width: 560,
          fontFamily: pal.fontMono, fontSize: 20, fill: pal.text, opacity: 0.72,
          textAlign: 'center', editorType: 'chrome',
        })));
      }
    }

    /* Page number right */
    var showPageNum = meta.showSlideNum !== false && meta.showPageNum !== false;
    if (pageNum > 0 && showPageNum) {
      _canvas.add(_sStatic(_sTB(String(pageNum).padStart(2, '0'), {
        left: W - 180, top: bottomY, width: 100,
        fontFamily: pal.fontMono, fontSize: 20, fill: pal.accent,
        textAlign: 'right', editorType: 'chrome',
      })));
    }
  }

  /* ─── HTML Table Overlay helpers ──────────────────────────────── */

  var _editingTableSlide     = -1;
  var _selectedHtmlTableSlide = -1;  // index of HTML table currently "selected" in layer
  /* Cell selection: ri=-1 means header row */
  var _tblSel = { si: -1, cells: [], anchor: null };
  /* Click-and-drag range selection (mousedown on a cell, drag across others, mouseup to finish) —
     mirrors the shift-click range logic in _tblSelectCell so both selection methods work. */
  var _tblDragging  = false;
  var _tblDragMoved = false;
  var _pendingTableData  = null;  // set by _specBuildSlide table layouts; consumed into _slides[i].tableData after each build

  function _isLightColor(hex) {
    var c = parseInt((hex || '#000').replace('#', ''), 16);
    var r = (c >> 16) & 255, g = (c >> 8) & 255, b = c & 255;
    return (0.299 * r + 0.587 * g + 0.114 * b) > 128;
  }

  function _parseMarkdownTable(md) {
    var lines = (md || '').trim().split('\n').filter(function(l) { return l.trim(); });
    if (lines.length < 2) return { headers: [], rows: [] };
    var parse = function(line) {
      return line.replace(/^\||\|$/g, '').split('|').map(function(c) { return c.trim(); });
    };
    var isSep = function(cells) {
      return cells.length > 0 && cells.every(function(c) { return /^:?-+:?$/.test(c || '-'); });
    };
    var headers = parse(lines[0]);
    var rows = lines.slice(2).map(parse).filter(function(r) { return !isSep(r); });
    return { headers: headers, rows: rows };
  }

  function _tableToMarkdown(headers, rows) {
    var sep = headers.map(function() { return '---'; });
    var toRow = function(cells) { return '| ' + cells.join(' | ') + ' |'; };
    return [toRow(headers), toRow(sep)].concat(rows.map(toRow)).join('\n');
  }

  /* Derive the .slide-table class list + inline CSS vars from styleOpts.
     SINGLE source of truth shared by the edit overlay (_renderTableLayer) AND
     Present mode (_renderPresTable) — previously present read different keys
     (bordered/banded/shaded/shading) and silently dropped all table styling. */
  function _tblClassesAndVars(so, pal) {
    so  = so  || {};
    pal = pal || _getPal();
    var headerTextCol = pal.accent || '#4f8ef7';
    var cellText      = pal.text   || '#ffffff';
    /* Clearly visible on every theme (old 0.1 alpha was near-invisible). */
    var borderColDef  = pal._isLight ? 'rgba(0,0,0,0.32)' : 'rgba(255,255,255,0.42)';
    var fontMono      = pal.fontMono || "'IBM Plex Mono',monospace";

    var cls = 'slide-table';
    if (so.bandedRows) cls += ' banded-rows';
    if (so.bandedCols) cls += ' banded-cols';
    if (so.firstCol)   cls += ' first-col';
    if (so.lastCol)    cls += ' last-col';
    if (so.totalRow)   cls += ' total-row';
    if (so.borderWidth > 0) cls += ' bordered';
    if (so.shadingColor)    cls += ' shaded';

    var styleVars = '--ed-table-accent:' + headerTextCol
      + ';--ed-table-cell-text:' + cellText
      + ';--ed-table-border:' + (so.borderColor || borderColDef)
      + ';--ed-table-border-w:' + (so.borderWidth ? so.borderWidth + 'px' : '0px')
      + ';--ed-table-font-mono:' + fontMono
      + (so.shadingColor ? ';--ed-table-shading:' + so.shadingColor : '')
      + ';';
    return { cls: cls, styleVars: styleVars };
  }

  function _renderTableLayer(slideIdx) {
    var layer = document.getElementById('ed-table-layer');
    if (!layer) return;
    layer.innerHTML = '';
    if (slideIdx < 0 || slideIdx >= _slides.length) return;
    var sl = _slides[slideIdx];
    if (!sl || !sl.tableData) return;

    var td = sl.tableData;
    /* Ensure styleOpts exists */
    if (!td.styleOpts) td.styleOpts = { headerRow: true, totalRow: false, bandedRows: true, firstCol: false, lastCol: false, bandedCols: false };
    var so = td.styleOpts;

    var parsed = _parseMarkdownTable(td.markdown);
    if (!parsed.headers.length) return;

    var slideSpec  = _currentSpec && _currentSpec.slides && _currentSpec.slides[slideIdx];
    var _tLay      = slideSpec ? (slideSpec.layout || '') : '';
    var basePal    = _currentSpec ? (_SPEC_THEMES[(_currentSpec.meta || {}).theme] || _SPEC_THEMES.frankfurt) : _SPEC_THEMES.frankfurt;
    var pal        = _effectivePal(_tLay, basePal);
    var headerTextCol = pal.accent  || '#4f8ef7';
    var cellText      = pal.text    || '#ffffff';
    var borderCol     = pal._isLight ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)';
    var fontMono      = pal.fontMono || "'IBM Plex Mono',monospace";

    /* ── Outer container — explicit height = td.h, drag handle is absolute overlay ── */
    var wrap = document.createElement('div');
    wrap.className = 'slide-table-wrap';
    wrap.style.cssText = 'left:' + td.x + 'px;top:' + td.y + 'px;width:' + td.w + 'px;height:' + td.h + 'px;';
    wrap.dataset.slideIdx = slideIdx;

    /* ── Scroll container fills entire wrap ── */
    var scrollArea = document.createElement('div');
    scrollArea.className = 'slide-table-scroll-area';
    /* height: 100% comes from CSS; drag handle floats above via position:absolute */

    /* ── Drag handle — absolute overlay at top of wrap, doesn't add height ── */
    var dragHandle = document.createElement('div');
    dragHandle.className = 'slide-table-drag-handle';
    dragHandle.innerHTML = '&#9776; Table &nbsp;<span style="opacity:.5;font-size:10px">drag to move · click a cell to type</span>';

    /* ── The table ── */
    var tbl = document.createElement('table');
    var _cav = _tblClassesAndVars(so, pal);
    tbl.style.cssText = _cav.styleVars;
    tbl.className = _cav.cls;

    /* Ensure extra tableData fields exist */
    if (!td.rowHeights)  td.rowHeights  = {};
    if (!td.colWidths)   td.colWidths   = {};
    if (!td.cellStyles)  td.cellStyles  = {};
    if (!td.merges)      td.merges      = [];

    /* Build a set of cells covered by a merge (not the origin) */
    var _mergedCells = {};  /* key "ri,ci" → true  (ri=-1 for header) */
    td.merges.forEach(function(m) {
      for (var dr = 0; dr < (m.rowspan || 1); dr++) {
        for (var dc = 0; dc < (m.colspan || 1); dc++) {
          if (dr === 0 && dc === 0) continue;
          _mergedCells[(m.r + dr) + ',' + (m.c + dc)] = true;
        }
      }
    });
    function _getMerge(ri, ci) {
      return td.merges.find(function(m) { return m.r === ri && m.c === ci; }) || null;
    }

    /* <colgroup> for column widths */
    var cg = document.createElement('colgroup');
    var nCols = parsed.headers.length;
    for (var _ci = 0; _ci < nCols; _ci++) {
      var colEl = document.createElement('col');
      if (td.colWidths[_ci]) colEl.style.width = td.colWidths[_ci] + 'px';
      cg.appendChild(colEl);
    }
    tbl.appendChild(cg);

    /* Helper: apply selection click + dblclick edit on a cell element */
    function _makeInlineEditable(el, cellRi, cellCi, saveCallback) {
      el.dataset.ri = cellRi;
      el.dataset.ci = cellCi;

      el.addEventListener('mousedown', function(e) {
        if (el.contentEditable === 'true') return;
        if (e.button !== 0) return;
        _tblDragging  = true;
        _tblDragMoved = false;
        if (!e.shiftKey) _tblSelectCell(slideIdx, cellRi, cellCi, e);
      });
      el.addEventListener('mouseenter', function() {
        if (!_tblDragging || _tblSel.si !== slideIdx) return;
        _tblDragMoved = true;
        _tblSelectCell(slideIdx, cellRi, cellCi, { shiftKey: true });
      });
      el.addEventListener('click', function(e) {
        e.stopPropagation();
        if (el.contentEditable === 'true') return;
        /* A completed drag already set the range selection via mousedown/mouseenter above —
           don't let the trailing click collapse it back down to a single cell. */
        if (_tblDragMoved) { _tblDragMoved = false; return; }
        _tblSelectCell(slideIdx, cellRi, cellCi, e);
      });
      el.addEventListener('dblclick', function(e) {
        e.stopPropagation();
        if (el.contentEditable === 'true') return;
        el.contentEditable = 'true';
        el.classList.add('cell-editing');
        el.focus();
        var range = document.createRange();
        range.selectNodeContents(el);
        var wsel = window.getSelection();
        wsel.removeAllRanges();
        wsel.addRange(range);
      });
      el.addEventListener('keydown', function(e) {
        if (el.contentEditable !== 'true') return;
        e.stopPropagation();
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); el.blur(); }
        if (e.key === 'Escape') {
          el.contentEditable = 'false';
          el.classList.remove('cell-editing');
          el.textContent = el.dataset.origText || '';
        }
        if (e.key === 'Tab') {
          e.preventDefault();
          el.blur();
          var allCells = Array.from(tbl.querySelectorAll('th,td'));
          var idx = allCells.indexOf(el);
          var next = allCells[e.shiftKey ? idx - 1 : idx + 1];
          if (next) next.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
        }
      });
      el.addEventListener('blur', function() {
        if (el.contentEditable !== 'true') return;
        el.contentEditable = 'false';
        el.classList.remove('cell-editing');
        var newText = el.textContent.trim();
        if (newText === el.dataset.origText) return;
        saveCallback(newText);
      });
    }

    function _applyCellStyle(el, ri, ci) {
      var cs = td.cellStyles[ri + ',' + ci];
      if (!cs) return;
      if (cs.align)  el.style.textAlign    = cs.align;
      if (cs.valign) el.style.verticalAlign = cs.valign === 'middle' ? 'middle' : cs.valign;
    }

    var thead = tbl.createTHead();
    if (so.headerRow !== false) {
      var hrow = thead.insertRow();
      if (td.rowHeights[-1]) hrow.style.height = td.rowHeights[-1] + 'px';   /* header height (parity with present) */
      parsed.headers.forEach(function(h, ci) {
        if (_mergedCells['-1,' + ci]) return;
        var th = document.createElement('th');
        var mg = _getMerge(-1, ci);
        if (mg) { if (mg.rowspan > 1) th.rowSpan = mg.rowspan; if (mg.colspan > 1) th.colSpan = mg.colspan; }
        th.textContent = _stripInlineMath(h);
        th.dataset.origText = _stripInlineMath(h);
        _applyCellStyle(th, -1, ci);
        _makeInlineEditable(th, -1, ci, function(newText) {
          var slNow = _slides[slideIdx];
          if (!slNow || !slNow.tableData) return;
          var p = _parseMarkdownTable(slNow.tableData.markdown);
          if (p.headers[ci] === newText) return;
          p.headers[ci] = newText;
          slNow.tableData.markdown = _tableToMarkdown(p.headers, p.rows);
          slNow.hasUserContent = true;
          if (slideIdx === _currentSlide) saveState();
          th.dataset.origText = newText;
        });
        hrow.appendChild(th);
      });
    }

    var tbody = tbl.createTBody();
    parsed.rows.forEach(function(r, ri) {
      var row = tbody.insertRow();
      if (td.rowHeights[ri]) row.style.height = td.rowHeights[ri] + 'px';
      r.forEach(function(c, ci) {
        if (_mergedCells[ri + ',' + ci]) return;
        var td2 = row.insertCell();
        var mg = _getMerge(ri, ci);
        if (mg) { if (mg.rowspan > 1) td2.rowSpan = mg.rowspan; if (mg.colspan > 1) td2.colSpan = mg.colspan; }
        td2.textContent = _stripInlineMath(c);
        td2.dataset.origText = _stripInlineMath(c);
        _applyCellStyle(td2, ri, ci);
        _makeInlineEditable(td2, ri, ci, function(newText) {
          var slNow = _slides[slideIdx];
          if (!slNow || !slNow.tableData) return;
          var p = _parseMarkdownTable(slNow.tableData.markdown);
          if (!p.rows[ri] || p.rows[ri][ci] === newText) return;
          p.rows[ri][ci] = newText;
          slNow.tableData.markdown = _tableToMarkdown(p.headers, p.rows);
          slNow.hasUserContent = true;
          if (slideIdx === _currentSlide) saveState();
          td2.dataset.origText = newText;
        });
      });
    });

    /* ── Column / row border drag-resize (PowerPoint/Canva-style: grab the border
       between two cells). Skipped when the table has merged cells, since a merged
       cell's DOM index no longer lines up 1:1 with logical column/row index. */
    if (!td.merges.length) {
      var _zoomNow = function() { return parseFloat(document.documentElement.style.getPropertyValue('--ed-zoom')) || 1; };

      var _refRow = (so.headerRow !== false) ? hrow : (tbody.rows[0] || null);
      if (_refRow) {
        var _refCells = Array.from(_refRow.cells);
        for (var _rc = 0; _rc < _refCells.length - 1; _rc++) {
          (function(ci, cellL, cellR) {
            cellL.style.position = 'relative';
            var cr = document.createElement('div');
            cr.className = 'slide-table-col-resizer';
            cr.addEventListener('mousedown', function(e) {
              e.preventDefault(); e.stopPropagation();
              var z = _zoomNow();
              var startX  = e.clientX;
              var startLW = cellL.getBoundingClientRect().width / z;
              var startRW = cellR.getBoundingClientRect().width / z;
              function onMM(ev) {
                var dx = (ev.clientX - startX) / z;
                var newL = Math.max(40, Math.round(startLW + dx));
                var newR = Math.max(40, Math.round(startRW - (newL - startLW)));
                td.colWidths[ci]     = newL;
                td.colWidths[ci + 1] = newR;
                var colEls = tbl.querySelectorAll('col');
                if (colEls[ci])     colEls[ci].style.width     = newL + 'px';
                if (colEls[ci + 1]) colEls[ci + 1].style.width = newR + 'px';
              }
              function onMU() {
                document.removeEventListener('mousemove', onMM);
                document.removeEventListener('mouseup', onMU);
                _slides[slideIdx].hasUserContent = true;
                if (slideIdx === _currentSlide) saveState();
              }
              document.addEventListener('mousemove', onMM);
              document.addEventListener('mouseup', onMU);
            });
            cellL.appendChild(cr);
          })(_rc, _refCells[_rc], _refCells[_rc + 1]);
        }
      }

      var _rowDescs = [];
      if (so.headerRow !== false && hrow) _rowDescs.push({ key: -1, el: hrow });
      Array.from(tbody.rows).forEach(function(r, ri) { _rowDescs.push({ key: ri, el: r }); });
      for (var _rr = 0; _rr < _rowDescs.length - 1; _rr++) {
        (function(rowKey, rowEl) {
          var firstCell = rowEl.cells[0];
          if (!firstCell) return;
          firstCell.style.position = 'relative';
          var rowRz = document.createElement('div');
          rowRz.className = 'slide-table-row-resizer';
          rowRz.addEventListener('mousedown', function(e) {
            e.preventDefault(); e.stopPropagation();
            var z = _zoomNow();
            var startY = e.clientY;
            var startH = td.rowHeights[rowKey] || (rowEl.getBoundingClientRect().height / z);
            function onMM(ev) {
              var dy = (ev.clientY - startY) / z;
              var newH = Math.max(30, Math.round(startH + dy));
              td.rowHeights[rowKey] = newH;
              rowEl.style.height = newH + 'px';
            }
            function onMU() {
              document.removeEventListener('mousemove', onMM);
              document.removeEventListener('mouseup', onMU);
              _slides[slideIdx].hasUserContent = true;
              if (slideIdx === _currentSlide) saveState();
            }
            document.addEventListener('mousemove', onMM);
            document.addEventListener('mouseup', onMU);
          });
          firstCell.appendChild(rowRz);
        })(_rowDescs[_rr].key, _rowDescs[_rr].el);
      }
    }

    scrollArea.appendChild(tbl);
    /* Append scroll area first, then drag handle on top (z-index overlay) */
    wrap.appendChild(scrollArea);
    wrap.appendChild(dragHandle);

    /* ── Resize handles (E, S, SE edges) ── */
    ['e', 's', 'se'].forEach(function(dir) {
      var rh = document.createElement('div');
      rh.className = 'slide-table-resize-handle rh-' + dir;
      rh.addEventListener('mousedown', function(e) {
        e.preventDefault();
        e.stopPropagation();
        var startX = e.clientX, startY = e.clientY;
        var startW = td.w, startH = td.h;
        var _rDragging = true;
        function onRMM(ev) {
          if (!_rDragging) return;
          var z = parseFloat(document.documentElement.style.getPropertyValue('--ed-zoom')) || 1;
          if (dir === 'e' || dir === 'se') {
            td.w = Math.max(200, Math.round(startW + (ev.clientX - startX) / z));
            wrap.style.width = td.w + 'px';
          }
          if (dir === 's' || dir === 'se') {
            td.h = Math.max(40, Math.round(startH + (ev.clientY - startY) / z));
            wrap.style.height = td.h + 'px';  /* wrap has explicit height, scroll area is 100% */
          }
        }
        function onRMU() {
          _rDragging = false;
          document.removeEventListener('mousemove', onRMM);
          document.removeEventListener('mouseup', onRMU);
          if (_slides[slideIdx]) {
            _slides[slideIdx].tableData.w = td.w;
            _slides[slideIdx].tableData.h = td.h;
            _slides[slideIdx].hasUserContent = true;
            if (slideIdx === _currentSlide) saveState();
          }
          var sh = document.getElementById('fmt-tbl-size-h');
          var sw = document.getElementById('fmt-tbl-size-w');
          if (sh) sh.value = td.h;
          if (sw) sw.value = td.w;
        }
        document.addEventListener('mousemove', onRMM);
        document.addEventListener('mouseup', onRMU);
      });
      wrap.appendChild(rh);
    });

    /* ── Delete button ── */
    var delBtn = document.createElement('button');
    delBtn.className = 'slide-table-del-btn';
    delBtn.title = 'Delete table';
    delBtn.innerHTML = '&#x2715;';
    delBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      _slides[slideIdx].tableData = null;
      _slides[slideIdx].hasUserContent = true;
      _deselectHtmlTable();
      _renderTableLayer(slideIdx);
      if (slideIdx === _currentSlide) saveState();
    });
    wrap.appendChild(delBtn);

    /* ── Drag to reposition (drag handle only) ── */
    var _tdrag = false, _tdX, _tdY, _tdOrigX, _tdOrigY;
    dragHandle.addEventListener('mousedown', function(e) {
      _tdrag = true;
      _tdX = e.clientX; _tdY = e.clientY;
      _tdOrigX = td.x; _tdOrigY = td.y;
      e.preventDefault();
    });
    var _onMM = function(e) {
      if (!_tdrag) return;
      var z = parseFloat(document.documentElement.style.getPropertyValue('--ed-zoom')) || 1;
      td.x = Math.round(_tdOrigX + (e.clientX - _tdX) / z);
      td.y = Math.round(_tdOrigY + (e.clientY - _tdY) / z);
      wrap.style.left = td.x + 'px';
      wrap.style.top  = td.y + 'px';
    };
    var _onMU = function() {
      if (!_tdrag) return;
      _tdrag = false;
      if (_slides[slideIdx]) {
        _slides[slideIdx].tableData.x = td.x;
        _slides[slideIdx].tableData.y = td.y;
        _slides[slideIdx].hasUserContent = true;
        if (slideIdx === _currentSlide) saveState();
      }
      document.removeEventListener('mousemove', _onMM);
      document.removeEventListener('mouseup', _onMU);
    };
    dragHandle.addEventListener('mousedown', function() {
      document.addEventListener('mousemove', _onMM);
      document.addEventListener('mouseup', _onMU);
    });

    /* ── Click to select (show contextual ribbon tabs) ── */
    wrap.addEventListener('click', function(e) {
      e.stopPropagation();
      _selectHtmlTable(slideIdx);
    });

    /* Modal editor removed — tables are edited directly on-canvas (click cell to type,
       Tab/arrows to move, ribbon Table Layout for rows/cols). */

    layer.appendChild(wrap);
    /* Restore cell selection highlight after re-render */
    if (_tblSel.si === slideIdx && _tblSel.cells.length) {
      _tblHighlightSelection(slideIdx);
    }
  }

  /* ── Cell selection helpers ── */
  function _tblSelectCell(si, ri, ci, e) {
    _selectHtmlTable(si);
    var newCell = { ri: ri, ci: ci };
    if (e && e.shiftKey && _tblSel.anchor && _tblSel.si === si) {
      /* Range selection from anchor to this cell */
      var ar = _tblSel.anchor.ri, ac = _tblSel.anchor.ci;
      var r0 = Math.min(ar, ri), r1 = Math.max(ar, ri);
      var c0 = Math.min(ac, ci), c1 = Math.max(ac, ci);
      _tblSel.cells = [];
      for (var rr = r0; rr <= r1; rr++) {
        for (var cc = c0; cc <= c1; cc++) {
          _tblSel.cells.push({ ri: rr, ci: cc });
        }
      }
    } else {
      _tblSel = { si: si, cells: [newCell], anchor: newCell };
    }
    _tblHighlightSelection(si);
    _tblSyncCellRibbon(si);
  }

  function _tblHighlightSelection(si) {
    var wrap = document.querySelector('.slide-table-wrap[data-slide-idx="' + si + '"]');
    if (!wrap) return;
    wrap.querySelectorAll('th,td').forEach(function(el) { el.classList.remove('cell-selected', 'cell-anchor'); });
    if (_tblSel.si !== si) return;
    _tblSel.cells.forEach(function(c) {
      var el = wrap.querySelector((c.ri === -1 ? 'thead th' : 'tbody tr:nth-child(' + (c.ri + 1) + ') td') + ':nth-child(' + (c.ci + 1) + ')');
      if (el) el.classList.add('cell-selected');
    });
    if (_tblSel.anchor) {
      var a = _tblSel.anchor;
      var aEl = wrap.querySelector((a.ri === -1 ? 'thead th' : 'tbody tr:nth-child(' + (a.ri + 1) + ') td') + ':nth-child(' + (a.ci + 1) + ')');
      if (aEl) aEl.classList.add('cell-anchor');
    }
  }

  function _tblSyncCellRibbon(si) {
    var sl = _slides[si];
    if (!sl || !sl.tableData || !_tblSel.cells.length) return;
    var td = sl.tableData;
    var fc = _tblSel.cells[0];
    var hEl = document.getElementById('fmt-tbl-cell-h');
    var wEl = document.getElementById('fmt-tbl-cell-w');
    if (hEl) hEl.value = td.rowHeights[fc.ri] || '';
    if (wEl) wEl.value = td.colWidths[fc.ci]  || '';
  }

  /* Insert a fresh cols×rows table (PowerPoint grid picker) on the current slide,
     then select + edit the first header cell so the user can type immediately. */
  function _insertTableGrid(cols, rows) {
    if (_currentSlide < 0 || !_slides[_currentSlide]) return;
    cols = Math.max(1, cols | 0);
    rows = Math.max(1, rows | 0);
    var sl = _slides[_currentSlide];
    if (sl.tableData && sl.tableData.markdown) {
      var p0 = _parseMarkdownTable(sl.tableData.markdown);
      var hasContent = p0.headers.some(function (h) { return h && h.trim(); }) ||
        p0.rows.some(function (r) { return r.some(function (c) { return c && c.trim(); }); });
      if (hasContent && !confirm('Replace the existing table on this slide?')) return;
    }
    var headers = [];
    for (var c = 0; c < cols; c++) headers.push('Column ' + (c + 1));
    var bodyRows = [];
    for (var r = 0; r < Math.max(0, rows - 1); r++) {
      var row = [];
      for (var c2 = 0; c2 < cols; c2++) row.push('');
      bodyRows.push(row);
    }
    var _tW = 1600;
    var _colW = {};                                   /* equal widths → columns fit the box */
    for (var _ci = 0; _ci < cols; _ci++) _colW[_ci] = Math.floor(_tW / cols);
    sl.tableData = {
      markdown: _tableToMarkdown(headers, bodyRows),
      x: 160, y: 280, w: _tW, h: Math.max(140, Math.min(820, rows * 72)),
      styleOpts: { headerRow: true, totalRow: false, bandedRows: true, firstCol: false, lastCol: false, bandedCols: false },
      colWidths: _colW, rowHeights: {}, cellStyles: {}, merges: [],
    };
    sl.hasUserContent = true;
    _renderTableLayer(_currentSlide);
    saveState();
    _switchTab('table-design');
    _tblSelectCell(_currentSlide, -1, 0, null);
    setTimeout(function () { _tblBeginEditAnchor(); }, 0);
  }

  /* Find the overlay <th>/<td> element for the current anchor cell. */
  function _tblAnchorEl() {
    if (_tblSel.si < 0 || !_tblSel.anchor) return null;
    var wrap = document.querySelector('.slide-table-wrap[data-slide-idx="' + _tblSel.si + '"]');
    if (!wrap) return null;
    var a = _tblSel.anchor;
    var sel = (a.ri === -1 ? 'thead th' : 'tbody tr:nth-child(' + (a.ri + 1) + ') td') +
      ':nth-child(' + (a.ci + 1) + ')';
    return wrap.querySelector(sel);
  }

  /* Enter inline edit on the anchor cell. initialChar != null → type-over (replace). */
  function _tblBeginEditAnchor(initialChar) {
    var el = _tblAnchorEl();
    if (!el || el.isContentEditable) return;
    el.contentEditable = 'true';
    el.classList.add('cell-editing');
    el.focus();
    var range = document.createRange();
    if (initialChar != null) {
      el.textContent = initialChar;
      range.selectNodeContents(el);
      range.collapse(false);   /* caret at end */
    } else {
      range.selectNodeContents(el);   /* select all for overwrite-on-type */
    }
    var wsel = window.getSelection();
    wsel.removeAllRanges();
    wsel.addRange(range);
  }

  /* Clear text of all currently-selected cells (Delete/Backspace when not editing). */
  function _tblClearSelectedCells() {
    var i = _selectedHtmlTableSlide;
    var sl = _slides[i];
    if (!sl || !sl.tableData || !_tblSel.cells.length) return;
    var p = _parseMarkdownTable(sl.tableData.markdown);
    var changed = false;
    _tblSel.cells.forEach(function (c) {
      if (c.ri === -1) { if (p.headers[c.ci]) { p.headers[c.ci] = ''; changed = true; } }
      else if (p.rows[c.ri] && p.rows[c.ri][c.ci]) { p.rows[c.ri][c.ci] = ''; changed = true; }
    });
    if (!changed) return;
    sl.tableData.markdown = _tableToMarkdown(p.headers, p.rows);
    sl.hasUserContent = true;
    _renderTableLayer(i);
    if (i === _currentSlide) saveState();
    _tblHighlightSelection(i);
  }

  /* Move the single-cell selection with arrow keys (Excel-like). ri=-1 is the header. */
  function _tblMoveSelection(key) {
    var i = _selectedHtmlTableSlide;
    var sl = _slides[i];
    if (!sl || !sl.tableData || !_tblSel.anchor) return;
    var p = _parseMarkdownTable(sl.tableData.markdown);
    var nCols = p.headers.length, nRows = p.rows.length;
    var ri = _tblSel.anchor.ri, ci = _tblSel.anchor.ci;
    if (key === 'ArrowLeft')  ci = Math.max(0, ci - 1);
    else if (key === 'ArrowRight') ci = Math.min(nCols - 1, ci + 1);
    else if (key === 'ArrowUp')    ri = (ri <= 0) ? -1 : ri - 1;
    else if (key === 'ArrowDown')  ri = (ri === -1) ? (nRows ? 0 : -1) : Math.min(nRows - 1, ri + 1);
    _tblSelectCell(i, ri, ci, null);
  }

  /* Type-to-edit: when a cell is selected but NOT being edited, printable keys begin
     an overwrite edit; Enter/F2 edit; Delete clears; arrows move — like PPT/Canva/Excel. */
  function _tblTypeToEditKey(e) {
    if (_selectedHtmlTableSlide < 0 || !_tblSel.anchor || !_tblSel.cells.length) return;
    var ae = document.activeElement;
    if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'SELECT' || ae.tagName === 'TEXTAREA')) return;
    if (ae && ae.isContentEditable) return;   /* already editing a cell */
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'Enter' || e.key === 'F2') { e.preventDefault(); e.stopPropagation(); _tblBeginEditAnchor(); return; }
    if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); e.stopPropagation(); _tblClearSelectedCells(); return; }
    if (e.key.indexOf('Arrow') === 0) { e.preventDefault(); e.stopPropagation(); _tblMoveSelection(e.key); return; }
    if (e.key.length === 1) { e.preventDefault(); e.stopPropagation(); _tblBeginEditAnchor(e.key); return; }
  }

  function _openTableEditor(slideIdx) {
    _editingTableSlide = slideIdx;
    var sl = _slides[slideIdx];
    if (!sl) return;
    var parsed = _parseMarkdownTable(sl.tableData ? sl.tableData.markdown : '');

    var tbl = document.getElementById('ed-table-edit');
    if (!tbl) return;
    tbl.innerHTML = '';

    var thead = tbl.createTHead();
    var hrow  = thead.insertRow();
    var initHeaders = parsed.headers.length ? parsed.headers : ['Column 1', 'Column 2'];
    initHeaders.forEach(function(h) {
      var th = document.createElement('th');
      th.contentEditable = 'true'; th.textContent = h;
      hrow.appendChild(th);
    });

    var tbody = tbl.createTBody();
    var initRows = parsed.rows.length ? parsed.rows : [['', '']];
    initRows.forEach(function(r) {
      var row = tbody.insertRow();
      r.forEach(function(c) {
        var td2 = row.insertCell();
        td2.contentEditable = 'true'; td2.textContent = c;
      });
    });

    var modal = document.getElementById('ed-table-modal');
    if (modal) modal.style.display = 'block';

    tbl.addEventListener('keydown', function _tabNav(e) {
      if (e.key !== 'Tab') return;
      e.preventDefault();
      var cells = Array.from(tbl.querySelectorAll('th[contenteditable],td[contenteditable]'));
      var idx = cells.indexOf(document.activeElement);
      var next = cells[e.shiftKey ? idx - 1 : idx + 1];
      if (next) next.focus();
    });
  }

  function _closeTableEditor(apply) {
    if (apply && _editingTableSlide >= 0) {
      var tbl = document.getElementById('ed-table-edit');
      if (tbl) {
        var headers = Array.from(tbl.querySelectorAll('thead th')).map(function(th) { return (th.innerText || '').trim(); });
        var rows    = Array.from(tbl.querySelectorAll('tbody tr')).map(function(tr) {
          return Array.from(tr.querySelectorAll('td')).map(function(td2) { return (td2.innerText || '').trim(); });
        });
        var md = _tableToMarkdown(headers, rows);
        var sl = _slides[_editingTableSlide];
        if (sl) {
          if (!sl.tableData) {
            sl.tableData = { markdown: md, x: 120, y: 200, w: 1680, h: 680 };
          } else {
            sl.tableData.markdown = md;
          }
          /* Modal add/del row & col always act at the table END, so shifting is
             never needed — but deletions can leave style keys / merges pointing
             past the new bounds. Prune them to the new dimensions. */
          _tblPruneIndices(sl.tableData, rows.length, headers.length);
          sl.hasUserContent = true;
          _renderTableLayer(_editingTableSlide);
          if (_editingTableSlide === _currentSlide) saveState();
        }
      }
    }
    var modal = document.getElementById('ed-table-modal');
    if (modal) modal.style.display = 'none';
    _editingTableSlide = -1;
  }

  function _selectHtmlTable(slideIdx) {
    _selectedHtmlTableSlide = slideIdx;
    _showEl('ed-tab-table-design', true);
    _showEl('ed-tab-table-layout', true);
    /* Only switch to table-design on fresh selection, not from within modify helpers */
    var _activePanel = document.querySelector('.tab-panel.active');
    var _activeOnTable = _activePanel && (_activePanel.dataset.panel === 'table-design' || _activePanel.dataset.panel === 'table-layout');
    if (!_activeOnTable) _switchTab('table-design');

    var td = _slides[slideIdx] && _slides[slideIdx].tableData;
    if (td) {
      var sh = document.getElementById('fmt-tbl-size-h');
      var sw = document.getElementById('fmt-tbl-size-w');
      if (sh) sh.value = td.h;
      if (sw) sw.value = td.w;
      /* Sync style checkboxes */
      var so = td.styleOpts || {};
      function _setCB(id, val) { var el = document.getElementById(id); if (el) el.checked = !!val; }
      _setCB('fmt-tbl-header-row',   so.headerRow  !== false);
      _setCB('fmt-tbl-total-row',    !!so.totalRow);
      _setCB('fmt-tbl-banded-rows',  so.bandedRows !== false);
      _setCB('fmt-tbl-first-col',    !!so.firstCol);
      _setCB('fmt-tbl-last-col',     !!so.lastCol);
      _setCB('fmt-tbl-banded-cols',  !!so.bandedCols);
      /* Sync border/shading controls */
      var _bc = document.getElementById('fmt-tbl-border-color');
      var _bb = document.getElementById('fmt-tbl-border-bar');
      var _bw = document.getElementById('fmt-tbl-border-width');
      var _sh = document.getElementById('fmt-tbl-shading');
      if (_bc && so.borderColor) { _bc.value = so.borderColor; if (_bb) _bb.style.background = so.borderColor; }
      if (_bw && so.borderWidth) _bw.value = so.borderWidth;
      if (_sh && so.shadingColor) _sh.value = so.shadingColor;
    }
    document.querySelectorAll('.slide-table-wrap').forEach(function(el) { el.classList.remove('selected'); });
    var wrap = document.querySelector('.slide-table-wrap[data-slide-idx="' + slideIdx + '"]');
    if (wrap) wrap.classList.add('selected');
  }

  function _deselectHtmlTable() {
    if (_selectedHtmlTableSlide < 0) return;
    _selectedHtmlTableSlide = -1;
    _tblSel = { si: -1, cells: [], anchor: null };
    document.querySelectorAll('.slide-table-wrap').forEach(function(el) { el.classList.remove('selected'); });
    _showEl('ed-tab-table-design', false);
    _showEl('ed-tab-table-layout', false);
    var activeCtx = document.querySelector('.ed-tab.ctx-tab.active');
    if (activeCtx) _switchTab('home');
  }

  function _tableAddRow() {
    var tbl = document.getElementById('ed-table-edit');
    if (!tbl) return;
    var tbody = tbl.tBodies[0];
    if (!tbody) return;
    var nCols = tbl.rows[0] ? tbl.rows[0].cells.length : 2;
    var row = tbody.insertRow();
    for (var c = 0; c < nCols; c++) {
      var td2 = row.insertCell();
      td2.contentEditable = 'true';
    }
  }

  function _tableDelRow() {
    var tbl = document.getElementById('ed-table-edit');
    if (!tbl) return;
    var tbody = tbl.tBodies[0];
    if (tbody && tbody.rows.length > 1) tbody.deleteRow(tbody.rows.length - 1);
  }

  function _tableAddCol() {
    var tbl = document.getElementById('ed-table-edit');
    if (!tbl || !tbl.tHead || !tbl.tBodies[0]) return;
    Array.from(tbl.tHead.rows).forEach(function(r) {
      var th = document.createElement('th');
      th.contentEditable = 'true'; r.appendChild(th);
    });
    Array.from(tbl.tBodies[0].rows).forEach(function(r) {
      var td2 = r.insertCell(); td2.contentEditable = 'true';
    });
  }

  function _tableDelCol() {
    var tbl = document.getElementById('ed-table-edit');
    if (!tbl) return;
    var nCols = tbl.rows[0] ? tbl.rows[0].cells.length : 0;
    if (nCols <= 1) return;
    Array.from(tbl.rows).forEach(function(r) { if (r.cells.length > 1) r.deleteCell(r.cells.length - 1); });
  }

  /* ── HTML table layer helpers (for _selectedHtmlTableSlide) ── */

  /* Remap the index-keyed style dictionaries after a structural change so
     rowHeights/colWidths/cellStyles/merges keep pointing at the same logical
     cells. change = {type:'addRow'|'delRow'|'addCol'|'delCol', at:index}.
     Body rows are 0-based; header row is -1 and is never shifted. */
  function _tblRemapIndices(td, change) {
    if (!td || !change) return;
    var isRow = change.type === 'addRow' || change.type === 'delRow';
    var isAdd = change.type === 'addRow' || change.type === 'addCol';
    var at    = change.at;

    function shiftDict(dict) {
      if (!dict) return dict;
      var out = {};
      Object.keys(dict).forEach(function(k) {
        var idx = parseInt(k, 10);
        if (isNaN(idx)) return;
        if (idx === -1) { out[-1] = dict[k]; return; }   /* header key untouched */
        if (isAdd)      { out[idx >= at ? idx + 1 : idx] = dict[k]; }
        else if (idx !== at) { out[idx > at ? idx - 1 : idx] = dict[k]; }
      });
      return out;
    }
    if (isRow) td.rowHeights = shiftDict(td.rowHeights);
    else       td.colWidths  = shiftDict(td.colWidths);

    if (td.cellStyles) {
      var cs = {};
      Object.keys(td.cellStyles).forEach(function(k) {
        var parts = k.split(',');
        var ri = parseInt(parts[0], 10), ci = parseInt(parts[1], 10);
        if (isNaN(ri) || isNaN(ci)) return;
        if (isRow && ri !== -1) {
          if (isAdd) { if (ri >= at) ri += 1; }
          else { if (ri === at) return; if (ri > at) ri -= 1; }
        } else if (!isRow) {
          if (isAdd) { if (ci >= at) ci += 1; }
          else { if (ci === at) return; if (ci > at) ci -= 1; }
        }
        cs[ri + ',' + ci] = td.cellStyles[k];
      });
      td.cellStyles = cs;
    }

    if (Array.isArray(td.merges)) {
      var out2 = [];
      td.merges.forEach(function(m) {
        var r = m.r, c = m.c, rs = m.rowspan || 1, cn = m.colspan || 1;
        if (isRow) {
          if (r === -1) {
            /* header merge spanning into body rows 0..rs-2 */
            var lastBody = rs - 2;
            if (rs > 1 && at <= lastBody) rs += (isAdd ? 1 : -1);
          } else if (isAdd) {
            if (r >= at) r += 1;
            else if (at <= r + rs - 1) rs += 1;      /* inserted inside span */
          } else {
            if (at < r) r -= 1;
            else if (at <= r + rs - 1) rs -= 1;      /* deleted inside span */
          }
        } else if (isAdd) {
          if (c >= at) c += 1;
          else if (at <= c + cn - 1) cn += 1;
        } else {
          if (at < c) c -= 1;
          else if (at <= c + cn - 1) cn -= 1;
        }
        if (rs <= 1 && cn <= 1) return;               /* merge dissolved */
        out2.push({ r: r, c: c, rowspan: rs, colspan: cn });
      });
      td.merges = out2;
    }
  }

  /* Drop style keys / merges that point outside the table's new dimensions
     (used after the bulk modal, whose add/del ops always act at the END). */
  function _tblPruneIndices(td, nRows, nCols) {
    if (!td) return;
    function pruneDict(dict, max) {
      if (!dict) return dict;
      var out = {};
      Object.keys(dict).forEach(function(k) {
        var idx = parseInt(k, 10);
        if (isNaN(idx)) return;
        if (idx === -1 || idx < max) out[idx] = dict[k];
      });
      return out;
    }
    td.rowHeights = pruneDict(td.rowHeights, nRows);
    td.colWidths  = pruneDict(td.colWidths,  nCols);
    if (td.cellStyles) {
      var cs = {};
      Object.keys(td.cellStyles).forEach(function(k) {
        var p = k.split(',');
        var ri = parseInt(p[0], 10), ci = parseInt(p[1], 10);
        if (isNaN(ri) || isNaN(ci)) return;
        if ((ri === -1 || ri < nRows) && ci < nCols) cs[ri + ',' + ci] = td.cellStyles[k];
      });
      td.cellStyles = cs;
    }
    if (Array.isArray(td.merges)) {
      td.merges = td.merges.filter(function(m) {
        return (m.r === -1 || m.r < nRows) && m.c < nCols;
      }).map(function(m) {
        /* clamp spans to the new bounds */
        var rs = m.rowspan || 1, cn = m.colspan || 1;
        if (m.r >= 0) rs = Math.min(rs, nRows - m.r);
        else rs = Math.min(rs, nRows + 1);           /* header + body rows */
        cn = Math.min(cn, nCols - m.c);
        return { r: m.r, c: m.c, rowspan: rs, colspan: cn };
      }).filter(function(m) { return (m.rowspan || 1) > 1 || (m.colspan || 1) > 1; });
    }
  }

  /* Keep the cell selection pointing at the same logical cells after a change */
  function _tblRemapSelAfter(change) {
    if (_tblSel.si < 0 || !_tblSel.anchor) return;
    function sh(cell) {
      if (!cell) return null;
      var ri = cell.ri, ci = cell.ci;
      if (change.type === 'addRow')      { if (ri >= change.at) ri += 1; }
      else if (change.type === 'delRow') { if (ri === change.at) return null; if (ri > change.at) ri -= 1; }
      else if (change.type === 'addCol') { if (ci >= change.at) ci += 1; }
      else if (change.type === 'delCol') { if (ci === change.at) return null; if (ci > change.at) ci -= 1; }
      return { ri: ri, ci: ci };
    }
    var na = sh(_tblSel.anchor);
    if (!na) { _tblSel = { si: -1, cells: [], anchor: null }; return; }
    _tblSel.anchor = na;
    _tblSel.cells  = _tblSel.cells.map(sh).filter(Boolean);
  }

  /* fn(p) mutates the parsed table and may RETURN a change descriptor
     ({type, at}) to trigger index remapping of styles/merges/selection. */
  function _tblHtmlModify(fn) {
    var i = _selectedHtmlTableSlide;
    if (i < 0) return;
    var sl = _slides[i];
    if (!sl || !sl.tableData) return;
    var p = _parseMarkdownTable(sl.tableData.markdown);
    var change = fn(p) || null;
    sl.tableData.markdown = _tableToMarkdown(p.headers, p.rows);
    if (change) {
      _tblRemapIndices(sl.tableData, change);
      _tblRemapSelAfter(change);
    }
    sl.hasUserContent = true;
    _renderTableLayer(i);
    if (i === _currentSlide) saveState();
    /* Restore .selected class on new wrap without re-triggering tab switch */
    document.querySelectorAll('.slide-table-wrap').forEach(function(el) { el.classList.remove('selected'); });
    var _newWrap = document.querySelector('.slide-table-wrap[data-slide-idx="' + i + '"]');
    if (_newWrap) _newWrap.classList.add('selected');
  }

  /* Anchor cell of the current selection on the selected table, or null */
  function _tblAnchorCell() {
    if (_tblSel.si < 0 || _tblSel.si !== _selectedHtmlTableSlide || !_tblSel.anchor) return null;
    return _tblSel.anchor;
  }

  function _tblHtmlAddRow(above) {
    var a = _tblAnchorCell();
    _tblHtmlModify(function(p) {
      var empty = new Array(Math.max(p.headers.length, 1)).fill('');
      var at;
      if (a) at = (a.ri === -1) ? 0 : (above ? a.ri : a.ri + 1);   /* header → insert as first body row */
      else   at = above ? 0 : p.rows.length;                       /* no selection → table extremes */
      at = Math.max(0, Math.min(at, p.rows.length));
      p.rows.splice(at, 0, empty);
      return { type: 'addRow', at: at };
    });
  }

  function _tblHtmlDelRow() {
    var a = _tblAnchorCell();
    if (a && a.ri === -1) { _showToast('Header row cannot be deleted'); return; }
    _tblHtmlModify(function(p) {
      if (p.rows.length <= 1) return;
      var at = a ? Math.min(a.ri, p.rows.length - 1) : p.rows.length - 1;
      p.rows.splice(at, 1);
      return { type: 'delRow', at: at };
    });
  }

  function _tblHtmlAddCol(left) {
    var a = _tblAnchorCell();
    _tblHtmlModify(function(p) {
      var at = a ? (left ? a.ci : a.ci + 1) : (left ? 0 : p.headers.length);
      at = Math.max(0, Math.min(at, p.headers.length));
      p.headers.splice(at, 0, 'New');
      p.rows.forEach(function(r) { r.splice(at, 0, ''); });
      return { type: 'addCol', at: at };
    });
  }

  function _tblHtmlDelCol() {
    var a = _tblAnchorCell();
    _tblHtmlModify(function(p) {
      if (p.headers.length <= 1) return;
      var at = a ? Math.min(a.ci, p.headers.length - 1) : p.headers.length - 1;
      p.headers.splice(at, 1);
      p.rows.forEach(function(r) { if (r.length > at) r.splice(at, 1); });
      return { type: 'delCol', at: at };
    });
  }

  /* NOTE: legacy _sDrawTable/_sDrawTableFromMarkdown (Fabric-drawn tables) were
     removed — tables are an HTML overlay now; thumbnails/PNG use
     _buildTempTableFabricObjects. Legacy files are converted by the load-time
     migration in loadFromSlideSpec. */

  /* ── Table editing helpers ───────────────────────────────────── */

  /* When any 1 table cell is selected, auto-expand to ALL cells of that table */
  function _autoSelectTable(e) {
    if (!e.selected || e.selected.length !== 1) return;
    var obj = e.selected[0];
    if (!obj.tableId) return;
    var all = _canvas.getObjects().filter(function(o) {
      return o.tableId === obj.tableId;
    });
    if (all.length <= 1) return;
    var sel = new fabric.ActiveSelection(all, { canvas: _canvas });
    _canvas.setActiveObject(sel);
    _canvas.requestRenderAll();
  }

  function _getActiveTableId() {
    var obj = _canvas.getActiveObject();
    if (!obj) return null;
    if (obj.type === 'activeSelection' && obj._objects && obj._objects[0]) {
      return obj._objects[0].tableId || null;
    }
    return obj.tableId || null;
  }

  function tableAddRow() {
    var tid = _getActiveTableId();
    if (!tid) return;
    var cells   = _canvas.getObjects().filter(function(o) { return o.tableId === tid; });
    var maxRow  = cells.reduce(function(m, o) { return Math.max(m, o.tableRow || 0); }, 0);
    var nCols   = cells.reduce(function(m, o) { return Math.max(m, (o.tableCol || 0) + 1); }, 0);
    var refBg   = cells.find(function(o) { return o.isTableBg && o.tableRow === maxRow && o.tableCol === 0; });
    if (!refBg) return;
    var newY    = refBg.top + refBg.height;
    var cellW   = refBg.width;
    var cellH   = refBg.height;
    var x0      = refBg.left;
    var pal     = _getPal();
    var newBgFill = (maxRow % 2 === 0) ? 'rgba(255,255,255,0.02)' : 'rgba(255,255,255,0.05)';
    var refTxt0 = cells.find(function(o) { return !o.isTableBg && o.tableRow === maxRow && o.tableCol === 0; });

    for (var ci = 0; ci < nCols; ci++) {
      var cx = x0 + ci * cellW;
      var rect = new fabric.Rect({
        left: cx, top: newY, width: cellW, height: cellH,
        fill: newBgFill, stroke: 'rgba(255,255,255,0.08)', strokeWidth: 1,
        editorType: 'deco', hasControls: false, hasBorders: false,
      });
      rect.tableId = tid; rect.tableRow = maxRow + 1; rect.tableCol = ci; rect.isTableBg = true;
      _canvas.add(rect);
      var tb = _sTB('', {
        left: cx + 8, top: newY + 4, width: cellW - 16,
        fontFamily: pal.fontBody,
        fontSize: refTxt0 ? refTxt0.fontSize : 20,
        fill: pal.text, editorType: 'table', splitByGrapheme: false,
        hasControls: false, hasBorders: false,
      });
      tb.tableId = tid; tb.tableRow = maxRow + 1; tb.tableCol = ci;
      _canvas.add(tb);
    }
    _canvas.discardActiveObject();
    _canvas.requestRenderAll();
    saveState();
  }

  function tableDelRow() {
    var tid = _getActiveTableId();
    if (!tid) return;
    var cells   = _canvas.getObjects().filter(function(o) { return o.tableId === tid; });
    var maxRow  = cells.reduce(function(m, o) { return Math.max(m, o.tableRow || 0); }, 0);
    if (maxRow === 0) return; // don't delete header
    cells.filter(function(o) { return o.tableRow === maxRow; })
         .forEach(function(o) { _canvas.remove(o); });
    _canvas.discardActiveObject();
    _canvas.requestRenderAll();
    saveState();
  }

  /* ── Formula popup editor ───────────────────────────────────── */

  function _openFormulaModal(obj) {
    var modal   = document.getElementById('formula-modal');
    var input   = document.getElementById('formula-modal-input');
    var preview = document.getElementById('formula-modal-preview');
    if (!modal || !input || !preview) return;
    input.value = obj._latexSource || obj.text || '';
    preview.textContent = _cleanLatex(input.value);
    modal.style.display = 'flex';
    modal._targetObj = obj;
    modal.dataset.mode = 'edit';

    input.oninput = function() {
      preview.textContent = _cleanLatex(input.value);
    };
    document.getElementById('formula-modal-cancel').onclick = function() {
      modal.style.display = 'none';
      modal.dataset.mode = '';
    };
  }

  /* ── End formula popup editor ────────────────────────────────── */

  /* ── End table editing helpers ───────────────────────────────── */

  /* Render a bullet list as a SINGLE Textbox — Fabric.js measures actual height,
   * no char-width estimation needed. Font size shrinks until the textbox fits.
   * items   — array of strings or {title,text,body} objects
   * x, y    — top-left of the textbox
   * w       — column width
   * availH  — max vertical space; fz decrements until tb.height ≤ availH
   * fzMax   — maximum font size
   * pal     — palette
   * opts    — { prefix: '•  '|'→  ', fill, fzMin, lineH }
   * returns — y + actual textbox height */
  function _sBulletList(items, x, y, w, availH, fzMax, pal, opts) {
    if (!items || !items.length) return y;
    opts   = opts || {};
    var fzMin  = opts.fzMin  || 18;
    var lineHR = opts.lineH  || 1.35;
    var fill   = opts.fill   || pal.text;
    var prefix = (opts.prefix !== undefined) ? opts.prefix : '•  ';

    var txts = items.map(function(item) {
      var s = (typeof item === 'string') ? item
            : (item.title || item.text || item.body || String(item));
      return _stripInlineMath(s);
    });

    var combined = txts.map(function(t) { return prefix + t; }).join('\n');

    /* Shrink fz until Fabric.js-measured tb.height fits in availH */
    var fz = fzMax;
    var tb;
    while (fz >= fzMin) {
      tb = _sTB(combined, {
        left: x, top: y, width: w,
        fontFamily: pal.fontBody, fontSize: fz, fill: fill, lineHeight: lineHR,
      });
      if ((tb.height || 0) <= availH) break;
      fz -= 2;
    }
    if (tb) _canvas.add(tb);
    return y + (tb ? (tb.height || 0) : 0);
  }

  /* Accent horizontal line */
  function _sAccentLine(x, y, w, pal) {
    _canvas.add(_sStatic(new fabric.Rect({
      left: x, top: y, width: w, height: 3, fill: pal.accent, editorType: 'deco',
    })));
  }

  /* Eyebrow: accent line + mono uppercase label (used in cover) */
  function _sEyebrow(label, x, y, pal) {
    _canvas.add(_sStatic(new fabric.Rect({
      left: x, top: y + 10, width: 88, height: 2, fill: pal.accent, editorType: 'deco',
    })));
    _canvas.add(_sStatic(_sTB(String(label || '').toUpperCase(), {
      left: x + 100, top: y, width: 1200,
      fontFamily: pal.fontMono, fontSize: 22, charSpacing: 200,
      fill: pal.dim, editorType: 'deco',
    })));
  }

  /* Draw an image scaled-to-fit inside a slot (or a dashed placeholder), + optional caption.
     Mirrors the single-image branch; reused by two_image_* and fullscreen layouts. */
  function _sImageInSlot(imgPath, imgCache, x, y, w, h, pal, caption) {
    var capX = x, capW = w, capBottom = y + h;   /* default = slot; overridden by real image */
    if (imgPath && imgCache && imgCache[imgPath]) {
      var el   = imgCache[imgPath].getElement();
      var fImg = new fabric.Image(el, { editorType: 'image', crossOrigin: 'anonymous' });
      var sc   = Math.min(w / fImg.width, h / fImg.height);
      fImg.scale(sc);
      var fitW = fImg.getScaledWidth(), fitH = fImg.getScaledHeight();
      var iLeft = x + (w - fitW) / 2, iTop = y + (h - fitH) / 2;
      fImg.set({ left: iLeft, top: iTop, editorType: 'image' });
      _canvas.add(fImg);
      capX = iLeft; capW = fitW; capBottom = iTop + fitH;
    } else {
      var plFill   = pal._isLight ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.03)';
      var plStroke = pal._isLight ? 'rgba(0,0,0,0.18)' : 'rgba(255,255,255,0.18)';
      _canvas.add(_sStatic(new fabric.Rect({
        left: x, top: y, width: w, height: h,
        fill: plFill, stroke: plStroke, strokeWidth: 1, strokeDashArray: [12, 8],
        rx: 8, ry: 8, editorType: 'image',
      })));
      _canvas.add(_sStatic(_sTB('Click to add image', {
        left: x, top: y + h / 2 - 18, width: w,
        fontFamily: pal.fontMono, fontSize: 24, charSpacing: 120,
        fill: pal.dim, textAlign: 'center', editorType: 'deco',
      })));
    }
    if (caption) {
      _canvas.add(_sStatic(_sTB(_stripInlineMath(caption), {
        left: capX, top: capBottom + 8, width: capW,
        fontFamily: pal.fontBody, fontSize: 20, fontStyle: 'italic',
        fill: pal.text, opacity: 0.85,
        textAlign: 'center', editorType: 'deco',
      })));
    }
  }

  function _specBuildSlide(s, pal, meta, pageNum, imgCache) {
    var W = 1920, H = 1080;
    var lay = s.layout || '';
    meta    = meta    || {};
    pageNum = pageNum || 0;
    /* For light themes (ivory, sage), content slides get overridden text/accent/dim */
    pal = _effectivePal(lay, pal);

    /* ── Cover / greeting ── */
    if (lay === 'config_and_greeting_slide' || lay === 'cover_split_layout') {
      _setBg(pal, s);
      /* Eyebrow line — accent bar + date */
      var eyebrow = s.date || meta.date || '';
      if (eyebrow) _sEyebrow(eyebrow, 120, 180, pal);
      /* Main title h1 — vertically CENTERED in the left region and auto-shrunk by
         MEASURED height so a long (4-line) title never collides with the meta line.
         Region = below the eyebrow → 36px above the meta divider (at H-220). */
      var _cvTitleTxt = s.short_title || s.title || meta.title || 'Untitled';
      var _cvRegionTop = eyebrow ? 250 : 200;
      var _cvRegionBot = (H - 220) - 36;
      var _cvAvailH    = _cvRegionBot - _cvRegionTop;
      var _cvMeasure = function (fz) {
        return _sTB(_cvTitleTxt, {
          left: 0, top: 0, width: 1520,
          fontFamily: pal.fontDisplay, fontSize: fz, fontWeight: '400',
          fill: pal.text, lineHeight: 0.96, charSpacing: -20,
        });
      };
      var _cvTitleFz = Math.min(140, _adaptFontSize(_cvTitleTxt, 1520, 3, 140));
      var _cvMsr = _cvMeasure(_cvTitleFz);
      if ((_cvMsr.height || 0) > _cvAvailH && _cvMsr.height) {
        _cvTitleFz = Math.max(40, Math.floor(_cvTitleFz * _cvAvailH / _cvMsr.height));
        _cvMsr = _cvMeasure(_cvTitleFz);
        while (_cvTitleFz > 40 && (_cvMsr.height || 0) > _cvAvailH) {
          _cvTitleFz -= 2; _cvMsr = _cvMeasure(_cvTitleFz);
        }
      }
      var _cvH   = _cvMsr.height || (_cvTitleFz * 1.2);
      var _cvTop = Math.round(_cvRegionTop + Math.max(0, (_cvAvailH - _cvH) / 2));
      _canvas.add(_sTB(_cvTitleTxt, {
        left: 120, top: _cvTop, width: 1520,
        fontFamily: pal.fontDisplay, fontSize: _cvTitleFz, fontWeight: '400',
        fill: pal.text, lineHeight: 0.96, charSpacing: -20,
      }));
      /* Meta-row at bottom — always shown (matches HTML .cover .meta-row) */
      var byLine = s.author || meta.author || '';
      var inst   = s.institution || meta.institution || '';
      _canvas.add(_sStatic(new fabric.Rect({
        left: 120, top: H - 220, width: 1680, height: 1,
        fill: pal.text, opacity: 0.45, editorType: 'deco',
      })));
      var byTxt = byLine ? 'Presented by  ' + byLine : '';
      if (byTxt) {
        _canvas.add(_sTB(byTxt, {
          left: 120, top: H - 195, width: 820,
          fontFamily: pal.fontBody, fontSize: 26, fill: pal.dim,
        }));
      }
      if (inst) {
        _canvas.add(_sStatic(_sTB(inst, {
          left: W - 740, top: H - 195, width: 620,
          fontFamily: pal.fontMono, fontSize: 22, fill: pal.dim,
          textAlign: 'right', editorType: 'deco',
        })));
      }
      _sChrome(meta, 0, pal, lay);
      /* Decorative blobs — replicates CSS .cover::before / ::after (accent oval outlines at bottom-right) */
      _canvas.add(_sStatic(new fabric.Ellipse({
        left: 1300, top: 460, rx: 430, ry: 410,
        fill: 'transparent', stroke: pal.accent, strokeWidth: 3,
        opacity: 0.22, editorType: 'deco',
      })));
      _canvas.add(_sStatic(new fabric.Ellipse({
        left: 1460, top: 680, rx: 260, ry: 250,
        fill: 'transparent', stroke: pal.accent, strokeWidth: 2,
        opacity: 0.13, editorType: 'deco',
      })));

    /* ── Section divider ── */
    } else if (lay === 'section_divider_layout') {
      _sGradBg(pal.endGrad);
      /* Accent stripe on left edge */
      _canvas.add(_sStatic(new fabric.Rect({
        left: 0, top: 0, width: 12, height: H,
        fill: pal.accent, editorType: 'deco',
      })));
      /* Section number: mono uppercase */
      if (s.section_number) {
        _canvas.add(_sStatic(_sTB(('SECTION · ' + s.section_number).toUpperCase(), {
          left: 120, top: 180,
          fontFamily: pal.fontMono, fontSize: 28, charSpacing: 220,
          fill: pal.accent, opacity: 0.85, editorType: 'deco',
        })));
      }
      /* Large title — autoscale from 200px down for long titles */
      var _sdTitleFz = _adaptFontSize(s.title || '', 1600, 2, 200);
      _sdTitleFz = Math.max(64, Math.min(200, _sdTitleFz));
      var _sdTitleTop = Math.round(H / 2 - _sdTitleFz * 0.75);
      _canvas.add(_sTB(s.title || '', {
        left: 120, top: _sdTitleTop, width: 1680,
        fontFamily: pal.fontDisplay, fontSize: _sdTitleFz, fontWeight: '400',
        fill: pal.text, charSpacing: -30, lineHeight: 1,
      }));
      /* Footer */
      if (s.part_label) {
        _canvas.add(_sStatic(new fabric.Rect({
          left: 120, top: H - 180, width: 1680, height: 2,
          fill: pal.text, opacity: 0.3, editorType: 'deco',
        })));
        _canvas.add(_sTB(s.part_label, {
          left: 120, top: H - 160,
          fontFamily: pal.fontBody, fontSize: 28, fill: pal.text, opacity: 0.75,
        }));
      }
      _sChrome(meta, pageNum, { dim: pal.text + 'aa', accent: pal.accent, fontMono: pal.fontMono, title: meta.title }, lay);

    /* ── End / Thank you ── */
    } else if (lay === 'end_layout' || lay === 'end_with_image_layout' || lay === 'end_image_hero_layout') {
      _sGradBg(pal.endGrad);
      /* Giant "Thank you" — adaptive: short text fills more vertical space */
      var endTxt = s.end_text || 'Thank you';
      var endFz  = _adaptFontSize(endTxt, 1680, 1, 320);
      endFz = Math.max(180, endFz);
      _canvas.add(_sTB(endTxt, {
        left: 120, top: 180, width: 1680,
        fontFamily: pal.fontDisplay, fontSize: endFz, fontWeight: '400',
        fill: pal.text, charSpacing: -30, lineHeight: 0.92,
      }));
      /* Acknowledgment / ack blurb (optional) */
      var ackTxt = s.acknowledgment || s.ack || '';
      if (ackTxt) {
        _canvas.add(_sTB(ackTxt, {
          left: 120, top: 180 + endFz * 1.05, width: 1200,
          fontFamily: pal.fontBody, fontSize: 28, fill: pal.dim,
          fontStyle: 'italic', opacity: 0.8,
        }));
      }
      /* Footer — 3 columns: PRESENTED BY / INSTITUTION (opt) / Q&A SESSION */
      var endAuthor = s.author || meta.author || '';
      var endInst   = s.institution || meta.institution || '';
      var footY = H - 220;
      /* Horizontal rule */
      _canvas.add(_sStatic(new fabric.Rect({
        left: 120, top: footY, width: 1680, height: 1,
        fill: pal.text, opacity: 0.55, editorType: 'deco',
      })));
      footY += 32;
      /* Column widths: if institution → 3 cols; else 2 cols */
      var _eCols = endInst
        ? [{ x: 120, w: 500, k: 'Presented by', v: endAuthor },
           { x: 690,  w: 500, k: 'Institution',  v: endInst },
           { x: 1260, w: 460, k: 'Q&A Session',  v: 'Open discussion', vAccent: true }]
        : [{ x: 120,  w: 720, k: 'Presented by', v: endAuthor },
           { x: 1000, w: 720, k: 'Q&A Session',  v: 'Open discussion', vAccent: true }];
      _eCols.forEach(function(col) {
        /* Label (uppercase mono, dim) */
        _canvas.add(_sStatic(_sTB(col.k.toUpperCase(), {
          left: col.x, top: footY, width: col.w,
          fontFamily: pal.fontMono, fontSize: 22, charSpacing: 160,
          fill: pal.dim, editorType: 'deco',
        })));
        /* Value */
        if (col.v) {
          _canvas.add(_sTB(col.v, {
            left: col.x, top: footY + 34, width: col.w,
            fontFamily: pal.fontDisplay, fontSize: 38,
            fill: col.vAccent ? pal.accent : pal.text,
            fontStyle: col.vAccent ? 'italic' : 'normal',
            lineHeight: 1.1,
          }));
        }
      });
      _sChrome(meta, 0, pal, lay);

    /* ── TOC layouts ── */
    } else if (lay === 'toc_layout' || lay === 'toc_vertical_layout' || lay === 'toc_described_layout' || lay === 'toc_cards_layout') {
      _setBg(pal, s);
      /* Heading via shared title block (consistent) — grid sits just below it (not sparse). */
      var _tocCy   = _sTitleBlock(s.heading || s.title || 'Table of Contents', pal, { w: 1680, maxFz: 88, accentW: 80 });
      var tocItems = s.toc_content || s.items || [];
      var listTop  = _tocCy + 16;
      var availH   = H - listTop - 90;
      /* Two-column grid: left col x=120, right col x=960, each col width=780 */
      var colXs    = [120, 960];
      var colW     = 780;
      var colTextW = colW - 130;   /* width after number badge — extra margin for font variation */
      var nRows    = Math.ceil(tocItems.length / 2);

      var tocTxts = tocItems.map(function(item) {
        var t = (typeof item === 'string') ? item : (item.title || item.t || String(item));
        return t.replace(/^\d+[\.\)]\s*/, '');
      });

      /* Step 1: pick font size so the longest item fits in ≤2 lines */
      var tocFzInit = 34;
      function _tocEstFz(txt, w, maxL, fz) {
        var cpl = Math.max(1, Math.floor(w / (fz * 0.62)));
        var lines = Math.ceil(Math.max(1, txt.length) / cpl);
        if (lines <= maxL) return fz;
        return Math.max(18, Math.floor(fz * maxL / lines));
      }
      var tocFz = tocTxts.reduce(function(fz, txt) {
        return Math.min(fz, _tocEstFz(txt, colTextW, 2, fz));
      }, tocFzInit);

      /* Step 2: estimate line count for each item at tocFz */
      function _tocLines(txt, w, fz) {
        var cpl = Math.max(1, Math.floor(w / (fz * 0.62)));
        return Math.ceil(Math.max(1, txt.length) / cpl);
      }
      var lineH = tocFz * 1.30;    /* px per line */
      var PAD   = 40;              /* vertical padding above/below text within a row */

      /* Step 3: per-row heights = max(left item, right item) */
      var rowHeights = [];
      for (var ri = 0; ri < nRows; ri++) {
        var ltxt  = tocTxts[ri * 2]     || '';
        var rtxt  = tocTxts[ri * 2 + 1] || '';
        var lL    = _tocLines(ltxt, colTextW, tocFz);
        var rL    = rtxt ? _tocLines(rtxt, colTextW, tocFz) : 0;
        rowHeights.push(Math.max(56, Math.max(lL, rL) * lineH + PAD));
      }

      /* Step 4: scale down if total exceeds available height */
      var totalH = rowHeights.reduce(function(a, b) { return a + b; }, 0);
      if (totalH > availH) {
        var scale = availH / totalH;
        rowHeights = rowHeights.map(function(h) { return Math.round(h * scale); });
        tocFz = Math.max(16, Math.floor(tocFz * scale));
        lineH = tocFz * 1.30;
      }

      /* Step 4b: scale UP if content fits with room to spare.
         Must recalculate rowHeights at the candidate font size — larger font
         means fewer chars-per-line, so items may need more lines than before. */
      var _totalHUsed = rowHeights.reduce(function(a, b) { return a + b; }, 0);
      if (_totalHUsed > 0 && _totalHUsed < availH) {
        var _tryFz   = Math.min(48, Math.floor(tocFz * Math.min(availH / _totalHUsed, 1.6)));
        var _tryLH   = _tryFz * 1.30;
        /* Recompute row heights at candidate font size */
        var _tryRowH = [];
        for (var _ri2 = 0; _ri2 < nRows; _ri2++) {
          var _lt  = tocTxts[_ri2 * 2]     || '';
          var _rt  = tocTxts[_ri2 * 2 + 1] || '';
          var _lL2 = _tocLines(_lt, colTextW, _tryFz);
          var _rL2 = _rt ? _tocLines(_rt, colTextW, _tryFz) : 0;
          _tryRowH.push(Math.max(64, Math.max(_lL2, _rL2) * _tryLH + PAD));
        }
        var _tryTotal = _tryRowH.reduce(function(a, b) { return a + b; }, 0);
        if (_tryTotal <= availH) {
          /* Candidate fits — apply larger font and recalculated rows */
          rowHeights = _tryRowH;
          tocFz = _tryFz;
          lineH  = _tryLH;
        } else {
          /* Candidate overflows — keep current font, just spread rows to fill height */
          var _fillScale = availH / _totalHUsed;
          rowHeights = rowHeights.map(function(h) { return Math.round(h * _fillScale); });
        }
      }

      /* Step 4c: nudge down a little if there's slack (capped so it stays near the title). */
      var _finalUsed = rowHeights.reduce(function(a, b) { return a + b; }, 0);
      if (_finalUsed < availH) {
        listTop += Math.min(60, Math.round((availH - _finalUsed) / 2));
      }

      /* Step 5: compute cumulative Y positions */
      var rowY = [];
      var curY = listTop;
      for (var ri = 0; ri < nRows; ri++) {
        rowY.push(curY);
        curY += rowHeights[ri];
      }

      /* vertical center separator */
      _canvas.add(_sStatic(new fabric.Rect({
        left: 940, top: listTop, width: 1, height: curY - listTop,
        fill: pal.accent, opacity: 0.4, editorType: 'deco',
      })));

      /* Draw items — number badge scaled up (was tiny), bold accent. */
      var numFz = Math.max(34, Math.round(tocFz * 1.05));
      tocTxts.forEach(function(txt, idx) {
        var col  = idx % 2;
        var row  = Math.floor(idx / 2);
        var cx   = colXs[col];
        var yy   = rowY[row];
        var rH   = rowHeights[row];
        var num  = String(idx + 1).padStart(2, '0');
        /* separator line above each row (left col only) */
        if (col === 0) {
          _canvas.add(_sStatic(new fabric.Rect({
            left: 120, top: yy, width: 1680, height: 1,
            fill: pal.text, opacity: 0.4, editorType: 'deco',
          })));
        }
        /* number — bigger, bold accent, vertically centered within row */
        _canvas.add(_sStatic(_sTB(num, {
          left: cx, top: yy + (rH - numFz * 1.2) / 2, width: 90,
          fontFamily: pal.fontMono, fontSize: numFz, fontWeight: '700', charSpacing: 40,
          fill: pal.accent, editorType: 'deco',
        })));
        /* title text — starts with small top padding */
        _canvas.add(_sTB(txt, {
          left: cx + 100, top: yy + PAD / 2, width: colTextW,
          fontFamily: pal.fontDisplay, fontSize: tocFz,
          fontWeight: '400', fill: pal.text, lineHeight: 1.30,
        }));
      });
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── Two-column content ── */
    } else if (lay === 'two_contents_in_a_slide_layout') {
      _setBg(pal, s);
      var _2cBase = Math.max(300, _sTitleBlock(s.title, pal, { w: 1680, maxFz: 84, maxH: 150 }));
      var colW   = 720;
      var col2X  = 120 + colW + 120;
      /* Column separator */
      _canvas.add(_sStatic(new fabric.Rect({
        left: col2X - 40, top: _2cBase, width: 1, height: H - _2cBase - 50,
        fill: pal.accent, opacity: 0.4, editorType: 'deco',
      })));
      var blocks = [
        { t: s.sub_title_1 || '', items: s.sub_content_1 || [], x: 120 },
        { t: s.sub_title_2 || '', items: s.sub_content_2 || [], x: col2X },
      ];
      blocks.forEach(function(bl) {
        /* Column top border */
        _canvas.add(_sStatic(new fabric.Rect({
          left: bl.x, top: _2cBase, width: colW, height: 3,
          fill: pal.accent, editorType: 'deco',
        })));
        if (bl.t) {
          /* Mono tag label */
          _canvas.add(_sStatic(_sTB(String(bl.t).toUpperCase(), {
            left: bl.x, top: _2cBase + 16, width: colW,
            fontFamily: pal.fontMono, fontSize: 22, charSpacing: 180,
            fill: pal.accent, editorType: 'deco',
          })));
        }
        var items  = Array.isArray(bl.items) ? bl.items : [String(bl.items)];
        var yOff   = _2cBase + (bl.t ? 90 : 30);
        var avail2c = H - yOff - 80;
        _sBulletList(items, bl.x, yOff, colW, avail2c, 28, pal, { prefix: '→  ' });
      });
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── Quote ── */
    } else if (lay === 'quote_layout') {
      _setBg(pal, s);
      /* Large decorative quotation mark */
      _canvas.add(_sStatic(_sTB('"', {
        left: 160, top: 140,
        fontFamily: pal.fontDisplay, fontSize: 220, fontStyle: 'italic',
        fill: pal.accent, opacity: 0.25, lineHeight: 0.7, editorType: 'deco',
      })));
      /* Quote text */
      var _quoteText = s.quote || 'Add your quote here...';
      var _quoteOpacity = s.quote ? 1.0 : 0.35;
      var _quoteFz = _adaptFontSize(_quoteText, 1520, 4, 80);
      _canvas.add(_sTB(_quoteText, {
        left: 200, top: 320, width: 1520,
        fontFamily: pal.fontDisplay, fontSize: _quoteFz, fontWeight: '400',
        fontStyle: 'italic', fill: pal.text, lineHeight: 1.1, charSpacing: -15,
        opacity: _quoteOpacity,
      }));
      /* Attribution */
      var _attrText = s.attribution || (s.quote ? '' : '— Author Name');
      if (_attrText) {
        _sAccentLine(200, H - 230, 80, pal);
        _canvas.add(_sTB('—  ' + _attrText, {
          left: 200, top: H - 200, width: 1000,
          fontFamily: pal.fontMono, fontSize: 24, charSpacing: 180,
          fill: pal.dim,
        }));
      }
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── Image layouts ── */
    } else if (lay === 'image_left_layout' || lay === 'image_right_layout' ||
               lay === 'image_above_layout' || lay === 'image_below_layout') {
      _setBg(pal, s);
      _sBlobs(pal);   /* draw blobs first so they sit behind image and text */
      var isLeft  = (lay === 'image_left_layout');
      var isRight = (lay === 'image_right_layout');
      var isAbove = (lay === 'image_above_layout');
      var isBelow = (lay === 'image_below_layout');

      /* Slot dimensions */
      var imgX  = isLeft ? 120 : isRight ? 980 : 240;
      var imgW  = (isLeft || isRight) ? 820 : 1440;
      var imgH  = (isLeft || isRight) ? 760 : 360;  /* reduced from 420 for above/below */
      /* Reserve space for caption below image (above chrome area starting at H-50) */
      var capH  = (s.caption && (isAbove || isBelow)) ? 44 : 0;
      /* For image_above, imgY2 is computed dynamically after title is drawn */
      /* For image_below: keep image+caption above chrome; chrome reserve = 50px */
      var imgY2 = isAbove ? -1 : (isBelow ? (H - 50 - capH - imgH - 8) : 180);

      /* Text column */
      var textX = isLeft ? 980 : 120;
      var textW = isRight ? 840 : (isLeft ? 840 : 1680);

      /* Title — standardized top-anchored block (was vertically centered → looked "low"). */
      var bullets = s.content || [];
      if (typeof bullets === 'string') bullets = [bullets];
      var cy = _sTitleBlock(s.title, pal, {
        x: textX, w: textW, top: 140, maxFz: (isLeft || isRight) ? 76 : 96, maxH: 180,
      });

      if (isAbove) { imgY2 = cy; }   /* image sits between title and bullets */

      var bStart  = isAbove ? (imgY2 + imgH + capH + 24) : cy;
      var bAvailH = isBelow ? Math.max(1, imgY2 - bStart - 20) : (H - bStart - 80);
      _sBulletList(bullets, textX, bStart, textW, bAvailH, 32, pal,
                   { prefix: '•  ', lineH: (isLeft || isRight) ? 1.5 : 1.38 });

      /* Image — scaleToFit within slot, centered. Track the ACTUAL placed image box
         so the caption sits right under the image (not the letterboxed slot bottom). */
      var imgPath = s.img_path || '';
      var _capX = imgX, _capW = imgW, _capBottom = imgY2 + imgH;
      if (imgPath) {
        var _cachedImg = imgCache && imgCache[imgPath];
        if (_cachedImg) {
          var _imgEl  = _cachedImg.getElement();
          var _fImg   = new fabric.Image(_imgEl, { editorType: 'image', crossOrigin: 'anonymous' });
          var _scaleX = imgW / _fImg.width;
          var _scaleY = imgH / _fImg.height;
          var _scale  = Math.min(_scaleX, _scaleY);
          _fImg.scale(_scale);
          var _fitW   = _fImg.getScaledWidth();
          var _fitH   = _fImg.getScaledHeight();
          var _imgLeft = imgX + (imgW - _fitW) / 2;
          var _imgTop  = imgY2 + ((isLeft || isRight) ? (imgH - _fitH) / 2 : 0);
          _fImg.set({ left: _imgLeft, top: _imgTop, editorType: 'image' });
          _canvas.add(_fImg);
          _capX = _imgLeft; _capW = _fitW; _capBottom = _imgTop + _fitH;
        }
      } else {
        /* Placeholder */
        var _plFill   = pal._isLight ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.03)';
        var _plStroke = pal._isLight ? 'rgba(0,0,0,0.18)' : 'rgba(255,255,255,0.18)';
        _canvas.add(_sStatic(new fabric.Rect({
          left: imgX, top: imgY2, width: imgW, height: imgH,
          fill: _plFill, stroke: _plStroke, strokeWidth: 1, strokeDashArray: [12, 8],
          rx: 8, ry: 8, editorType: 'image',
        })));
        _canvas.add(_sStatic(_sTB('Click to add image', {
          left: imgX, top: imgY2 + imgH / 2 - 20, width: imgW,
          fontFamily: pal.fontMono, fontSize: 28, charSpacing: 120,
          fill: pal.dim, textAlign: 'center', editorType: 'deco',
        })));
      }
      /* Caption right below the ACTUAL image — readable (body font, larger, italic). */
      var _imgCap = _stripInlineMath(s.caption || '');
      if (_imgCap) {
        _canvas.add(_sStatic(_sTB(_imgCap, {
          left: _capX, top: _capBottom + 12, width: _capW,
          fontFamily: pal.fontBody, fontSize: 24, fontStyle: 'italic',
          fill: pal.text, opacity: 0.85,
          textAlign: 'center', editorType: 'deco',
        })));
      }
      _sChrome(meta, pageNum, pal, lay);

    /* ── key_points_layout: points = [{title, body}] ── */
    } else if (lay === 'key_points_layout') {
      _setBg(pal, s);

      var _kpCy   = _sTitleBlock(s.title, pal, { w: 1300, maxFz: 80, maxH: 150 });
      var kpts    = s.points || [];
      var kn      = kpts.length || 1;
      var kyBase  = Math.max(kn <= 3 ? 300 : 270, _kpCy);
      var kAvail = H - kyBase - 100;
      var kRowH  = Math.floor(kAvail / kn);
      kpts.forEach(function(pt, i) {
        var ptTitle = _stripInlineMath((typeof pt === 'string') ? pt : (pt.title || String(pt)));
        var ptBody  = _stripInlineMath((typeof pt === 'object') ? (pt.body || '') : '');
        var yy = kyBase + i * kRowH;
        /* Row background */
        _canvas.add(_sStatic(new fabric.Rect({
          left: 120, top: yy, width: 1680, height: kRowH - 10,
          fill: pal._isLight ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.04)', rx: 4, ry: 4, editorType: 'deco',
        })));
        /* Left accent border */
        _canvas.add(_sStatic(new fabric.Rect({
          left: 120, top: yy, width: 4, height: kRowH - 10,
          fill: pal.accent, editorType: 'deco',
        })));
        /* Index P·01 */
        _canvas.add(_sStatic(_sTB('P\xb7' + String(i + 1).padStart(2, '0'), {
          left: 136, top: yy + 12, width: 80,
          fontFamily: pal.fontMono, fontSize: 18, charSpacing: 80,
          fill: pal.accent, editorType: 'deco',
        })));
        /* Point title */
        var ptFz = Math.max(22, Math.min(38, Math.floor(kRowH * 0.4)));
        _canvas.add(_sTB(ptTitle, {
          left: 232, top: yy + 10, width: 1548,
          fontFamily: pal.fontDisplay, fontSize: ptFz, fontWeight: '400', fill: pal.text,
        }));
        /* Point body */
        if (ptBody) {
          _canvas.add(_sTB(ptBody, {
            left: 232, top: yy + 14 + ptFz * 1.2, width: 1548,
            fontFamily: pal.fontBody, fontSize: Math.max(18, ptFz - 10), fill: pal.dim,
          }));
        }
      });
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── steps_horizontal_layout: steps = [{title, body}] ── */
    } else if (lay === 'steps_horizontal_layout') {
      _setBg(pal, s);

      /* ── Title: proportional auto-scale, MAX 160px ≈ 2 lines ── */
      var MAX_ST_H   = 160;
      var _stTitleFz = _adaptFontSize(s.title || '', 1680, 2, 80);
      _stTitleFz = Math.max(28, _stTitleFz);
      var _stMeasure = _sTB(s.title || '', {
        left: 0, top: 0, width: 1680,
        fontFamily: pal.fontDisplay, fontSize: _stTitleFz, fontWeight: '400',
        fill: pal.text, charSpacing: -20, lineHeight: 0.95,
      });
      if ((_stMeasure.height || 0) > MAX_ST_H) {
        _stTitleFz = Math.max(28, Math.floor(_stTitleFz * MAX_ST_H / _stMeasure.height));
        _stMeasure = _sTB(s.title || '', {
          left: 0, top: 0, width: 1680,
          fontFamily: pal.fontDisplay, fontSize: _stTitleFz, fontWeight: '400',
          fill: pal.text, charSpacing: -20, lineHeight: 0.95,
        });
        while (_stTitleFz > 28 && (_stMeasure.height || 0) > MAX_ST_H) {
          _stTitleFz -= 2;
          _stMeasure = _sTB(s.title || '', {
            left: 0, top: 0, width: 1680,
            fontFamily: pal.fontDisplay, fontSize: _stTitleFz, fontWeight: '400',
            fill: pal.text, charSpacing: -20, lineHeight: 0.95,
          });
        }
      }
      var _stActualH = _stMeasure.height || (_stTitleFz * 1.2);

      _canvas.add(_sTB(s.title || '', {
        left: 120, top: 200, width: 1680,
        fontFamily: pal.fontDisplay, fontSize: _stTitleFz, fontWeight: '400',
        fill: pal.text, charSpacing: -20, lineHeight: 0.95,
      }));

      var steps = s.steps || [];
      var sn = Math.min(steps.length, 5) || 1;
      var stepW = Math.floor((W - 120 - 80) / sn) - 20;
      /* Steps Y start derived from ACTUAL title height + generous gap */
      var _stepsY = Math.max(440, Math.round(200 + _stActualH + 60));
      /* Connector line */
      _canvas.add(_sStatic(new fabric.Rect({
        left: 120, top: _stepsY - 2, width: W - 200, height: 2,
        fill: pal.accent, opacity: 0.3, editorType: 'deco',
      })));
      steps.slice(0, 5).forEach(function(step, i) {
        var st = _stripInlineMath((typeof step === 'string') ? step : (step.title || ''));
        var bd = _stripInlineMath((typeof step === 'object') ? (step.body || '') : '');
        var sx = 120 + i * (stepW + 20);
        /* Step number circle — centered on connector line */
        _canvas.add(_sStatic(new fabric.Circle({
          left: sx + stepW / 2 - 38, top: _stepsY - 42, radius: 38,
          fill: pal.accent, editorType: 'deco',
        })));
        _canvas.add(_sStatic(_sTB(String(i + 1), {
          left: sx + stepW / 2 - 38, top: _stepsY - 26, width: 76,
          fontFamily: pal.fontDisplay, fontSize: 36, fill: pal.panelText,
          textAlign: 'center', editorType: 'deco',
        })));
        /* Step title — bold, pushed down from circle */
        _canvas.add(_sTB(st, {
          left: sx, top: _stepsY + 56, width: stepW,
          fontFamily: pal.fontDisplay, fontSize: 34, fontWeight: '700',
          fill: pal.text, textAlign: 'center',
        }));
        /* Step body — larger, theme text color */
        if (bd) {
          _canvas.add(_sTB(bd, {
            left: sx, top: _stepsY + 148, width: stepW,
            fontFamily: pal.fontBody, fontSize: 26, fill: pal.text, opacity: 0.72,
            textAlign: 'center',
          }));
        }
      });
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── three_cols_content_layout: cols = [{icon, title, body, bullets}] ── */
    } else if (lay === 'three_cols_content_layout') {
      _setBg(pal, s);

      /* ── Title: proportional auto-scale, MAX 150px ≈ 2 lines ── */
      var MAX_3C_H   = 150;
      var _3cTitleFz = _adaptFontSize(s.title || '', 1680, 2, 80);
      _3cTitleFz = Math.max(28, _3cTitleFz);
      var _3cMeasure = _sTB(s.title || '', {
        left: 0, top: 0, width: 1680,
        fontFamily: pal.fontDisplay, fontSize: _3cTitleFz, fontWeight: '400',
        fill: pal.text, charSpacing: -20, lineHeight: 0.95,
      });
      if ((_3cMeasure.height || 0) > MAX_3C_H) {
        _3cTitleFz = Math.max(28, Math.floor(_3cTitleFz * MAX_3C_H / _3cMeasure.height));
        _3cMeasure = _sTB(s.title || '', {
          left: 0, top: 0, width: 1680,
          fontFamily: pal.fontDisplay, fontSize: _3cTitleFz, fontWeight: '400',
          fill: pal.text, charSpacing: -20, lineHeight: 0.95,
        });
        while (_3cTitleFz > 28 && (_3cMeasure.height || 0) > MAX_3C_H) {
          _3cTitleFz -= 2;
          _3cMeasure = _sTB(s.title || '', {
            left: 0, top: 0, width: 1680,
            fontFamily: pal.fontDisplay, fontSize: _3cTitleFz, fontWeight: '400',
            fill: pal.text, charSpacing: -20, lineHeight: 0.95,
          });
        }
      }
      var _3cActualH = _3cMeasure.height || (_3cTitleFz * 1.2);
      var _3cColY    = Math.max(340, Math.round(160 + _3cActualH + 30));

      _canvas.add(_sTB(s.title || '', {
        left: 120, top: 160, width: 1680,
        fontFamily: pal.fontDisplay, fontSize: _3cTitleFz, fontWeight: '400',
        fill: pal.text, charSpacing: -20, lineHeight: 0.95,
      }));
      var cols3 = s.cols || [];
      var col3W = Math.floor((W - 240 - 2 * 40) / 3);
      cols3.slice(0, 3).forEach(function(col, i) {
        var cx = 120 + i * (col3W + 40);
        var tag  = (typeof col === 'object') ? (col.icon || col.tag || ('#0' + (i+1))) : String(col);
        var ct   = (typeof col === 'object') ? (col.title || '') : '';
        var cb   = (typeof col === 'object') ? (col.body || '') : '';
        var cbul = (typeof col === 'object') ? (col.bullets || []) : [];
        /* Top accent bar — anchored to _3cColY */
        _canvas.add(_sStatic(new fabric.Rect({
          left: cx, top: _3cColY, width: col3W, height: 4, fill: pal.accent, editorType: 'deco',
        })));
        /* Tag / icon label */
        _canvas.add(_sStatic(_sTB(String(tag).toUpperCase(), {
          left: cx, top: _3cColY + 16, width: col3W,
          fontFamily: pal.fontMono, fontSize: 20, charSpacing: 180, fill: pal.accent, editorType: 'deco',
        })));
        /* Column title */
        _canvas.add(_sTB(ct, {
          left: cx, top: _3cColY + 74, width: col3W,
          fontFamily: pal.fontDisplay, fontSize: 44, fontWeight: '400', fill: pal.text,
        }));
        /* Body */
        _canvas.add(_sTB(cb, {
          left: cx, top: _3cColY + 164, width: col3W,
          fontFamily: pal.fontBody, fontSize: 24, fill: pal.dim,
        }));
        /* Bullets */
        _sBulletList(cbul.slice(0, 5), cx, _3cColY + 234, col3W, H - (_3cColY + 234) - 80, 22, pal, { prefix: '→  ' });
      });
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── formula layouts: show LaTeX as styled mono box ── */
    } else if (lay === 'formula_top_layout' || lay === 'formula_below_layout') {
      _setBg(pal, s);
      var fmlTitle = s.title || '';
      var fmlTex   = _cleanLatex(s.latex_formula_block || '');
      var fmlItems = s.content || [];
      if (typeof fmlItems === 'string') fmlItems = [fmlItems];
      /* Both formula_top and formula_below match HTML: title → formula → bullets */

      /* Title — adaptive size */
      var _fmlTitleFz = _adaptFontSize(fmlTitle, 1680, 2, 96);
      var _fmlTitleObj = _sTB(fmlTitle, {
        left: 120, top: 130, width: 1680,
        fontFamily: pal.fontDisplay, fontSize: _fmlTitleFz, fontWeight: '400',
        fill: pal.text, charSpacing: -20, lineHeight: 1.1,
      });
      _canvas.add(_fmlTitleObj);
      var _fmlTitleH = _fmlTitleObj.height || 80;
      _sAccentLine(120, 130 + _fmlTitleH + 10, 60, pal);

      /* Formula: large, auto-fit to width, in a box that HUGS the content and is centred. */
      var _fmlBoxFill = pal._isLight ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.06)';
      var _fmlPad = 40;                 /* inner padding */
      var _fmlMaxBoxW = 1680;
      var _fmlMaxTextW = _fmlMaxBoxW - _fmlPad * 2;
      var _fmlFz = 48;                  /* start big, shrink to fit width */
      var fmlTb = _sTB(fmlTex, {
        left: 0, top: 0, width: _fmlMaxTextW,
        fontFamily: pal.fontMono, fontSize: _fmlFz, fill: pal.text,
        textAlign: 'center', editorType: 'formula',
      });
      while (_fmlFz > 26 && (fmlTb.width || 0) > _fmlMaxTextW) {
        _fmlFz -= 2;
        fmlTb = _sTB(fmlTex, { left: 0, top: 0, width: _fmlMaxTextW,
          fontFamily: pal.fontMono, fontSize: _fmlFz, fill: pal.text,
          textAlign: 'center', editorType: 'formula' });
      }
      fmlTb._latexSource = s.formula || s.equation || '';
      var fmlTextH = fmlTb.height || 80;
      /* Actual content width (single-line formulas → hug; multi-line → measured width). */
      var _fmlContentW = Math.min(_fmlMaxTextW, Math.ceil(fmlTb.width || _fmlMaxTextW));
      var fmlBoxW = Math.min(_fmlMaxBoxW, Math.max(520, _fmlContentW + _fmlPad * 2));
      var fmlBoxX = Math.round((W - fmlBoxW) / 2);
      var fmlBoxH = Math.ceil(fmlTextH) + _fmlPad * 2;

      /* Formula just below title (matches HTML _formula_slide). */
      var fmlY = 130 + _fmlTitleH + 28;

      /* Box (hugs content, centred, 2px accent border) — this is the slide's core
         content (not chrome), so unlike other decorative card backgrounds it stays
         selectable/movable/resizable, and refits when the formula text changes. */
      var fmlBoxObj = new fabric.Rect({
        left: fmlBoxX, top: fmlY, width: fmlBoxW, height: fmlBoxH,
        fill: _fmlBoxFill, rx: 12, ry: 12,
        stroke: pal.accent, strokeWidth: 2, editorType: 'formula-box',
      });
      _canvas.add(fmlBoxObj);
      /* Formula text centred inside box */
      fmlTb.set({ left: fmlBoxX + _fmlPad, top: fmlY + _fmlPad, width: fmlBoxW - _fmlPad * 2 });
      fmlTb._formulaBox = fmlBoxObj;
      fmlTb._formulaPad = _fmlPad;
      _canvas.add(fmlTb);

      /* Bullets — always below formula box */
      var fmlBoxBottom   = fmlY + fmlBoxH + 24;
      var fmlBulletY     = fmlBoxBottom;
      var fmlBulletAvail = H - fmlBulletY - 80;
      _sBulletList(fmlItems, 140, fmlBulletY, 1640, fmlBulletAvail, 26, pal, { prefix: '•  ' });
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── two_cols_content_layout: split content list into 2 columns ── */
    } else if (lay === 'two_cols_content_layout') {
      _setBg(pal, s);
      var _tc2Base = Math.max(300, _sTitleBlock(s.title, pal, { w: 1680, maxFz: 80, maxH: 150 }));
      var tc2Items = s.content || [];
      if (typeof tc2Items === 'string') tc2Items = [tc2Items];
      var tc2Mid  = Math.ceil(tc2Items.length / 2);
      var tc2Left = tc2Items.slice(0, tc2Mid);
      var tc2Right = tc2Items.slice(tc2Mid);
      var tc2ColW = 740;
      /* Divider */
      _canvas.add(_sStatic(new fabric.Rect({
        left: 120 + tc2ColW + 20, top: _tc2Base, width: 1, height: H - _tc2Base - 50,
        fill: pal.accent, opacity: 0.4, editorType: 'deco',
      })));
      [tc2Left, tc2Right].forEach(function(col, ci) {
        var cx2 = 120 + ci * (tc2ColW + 60);
        _canvas.add(_sStatic(new fabric.Rect({
          left: cx2, top: _tc2Base, width: tc2ColW, height: 3, fill: pal.accent, editorType: 'deco',
        })));
        _sBulletList(col, cx2, _tc2Base + 26, tc2ColW, H - (_tc2Base + 26) - 80, 28, pal, { prefix: '•  ' });
      });
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── table_above_layout: title + markdown table + optional bullets ── */
    } else if (lay === 'table_above_layout') {
      _setBg(pal, s);
      var _taTitleFz = Math.max(28, _adaptFontSize(s.title || '', 1680, 2, 72));
      var _taMsr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_taTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20 });
      if ((_taMsr.height || 0) > 130) { _taTitleFz = Math.max(28, Math.floor(_taTitleFz * 130 / _taMsr.height)); _taMsr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_taTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20 }); }
      _canvas.add(_sTB(s.title || '', {
        left: 120, top: 160, width: 1680,
        fontFamily: pal.fontDisplay, fontSize: _taTitleFz, fontWeight: '400',
        fill: pal.text, charSpacing: -20,
      }));
      var _taBase = Math.max(240, Math.round(160 + (_taMsr.height || _taTitleFz * 1.2) + 14));
      _sAccentLine(120, _taBase, 80, pal);
      var tblMd = s.table_markdown || '';
      var _taTableY = _taBase + 22;
      var _taBullY  = _taTableY + 340 + 40;
      _pendingTableData = { markdown: tblMd, x: 120, y: _taTableY, w: 1680, h: 340, styleOpts: { headerRow: true, totalRow: false, bandedRows: true, firstCol: false, lastCol: false, bandedCols: false } };
      var tblBullets = s.content || [];
      if (typeof tblBullets === 'string') tblBullets = [tblBullets];
      _sBulletList(tblBullets, 120, _taBullY, 1680, H - _taBullY - 80, 26, pal, { prefix: '•  ' });
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── data_table_layout: title + headers/rows table ── */
    } else if (lay === 'data_table_layout') {
      _setBg(pal, s);
      var _dtTitleFz = Math.max(28, _adaptFontSize(s.title || '', 1680, 2, 72));
      var _dtMsr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_dtTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20 });
      if ((_dtMsr.height || 0) > 130) { _dtTitleFz = Math.max(28, Math.floor(_dtTitleFz * 130 / _dtMsr.height)); _dtMsr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_dtTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20 }); }
      _canvas.add(_sTB(s.title || '', {
        left: 120, top: 160, width: 1680,
        fontFamily: pal.fontDisplay, fontSize: _dtTitleFz, fontWeight: '400',
        fill: pal.text, charSpacing: -20,
      }));
      var _dtBase = Math.max(240, Math.round(160 + (_dtMsr.height || _dtTitleFz * 1.2) + 14));
      _sAccentLine(120, _dtBase, 80, pal);
      var _dtTableY = _dtBase + 22;
      var dtHeaders = s.headers || [];
      var dtRows    = s.rows || [];
      var _dtTableH = s.caption ? H - _dtTableY - 110 : H - _dtTableY - 80;
      var _dtMd = _tableToMarkdown(dtHeaders, dtRows);
      _pendingTableData = { markdown: _dtMd, x: 120, y: _dtTableY, w: 1680, h: _dtTableH, styleOpts: { headerRow: true, totalRow: false, bandedRows: true, firstCol: false, lastCol: false, bandedCols: false } };
      if (s.caption) {
        _canvas.add(_sTB(s.caption, {
          left: 120, top: H - 90, width: 1680,
          fontFamily: pal.fontMono, fontSize: 20, fill: pal.dim, textAlign: 'center',
        }));
      }
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── comparison_layout: full-slide table ── */
    } else if (lay === 'comparison_layout') {
      _setBg(pal, s);
      var _cmpTitle = s.title || s.heading || '';
      var _cmpTop = 160;
      if (_cmpTitle) {
        var _cmpTitleFz = Math.max(28, _adaptFontSize(_cmpTitle, 1680, 2, 84));
        var _cmpMsr = _sTB(_cmpTitle, { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_cmpTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20 });
        if ((_cmpMsr.height || 0) > 150) { _cmpTitleFz = Math.max(28, Math.floor(_cmpTitleFz * 150 / _cmpMsr.height)); _cmpMsr = _sTB(_cmpTitle, { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_cmpTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20 }); }
        _canvas.add(_sTB(_cmpTitle, {
          left: 120, top: 130, width: 1680,
          fontFamily: pal.fontDisplay, fontSize: _cmpTitleFz, fontWeight: '400',
          fill: pal.text, charSpacing: -20,
        }));
        var _cmpAccentY = Math.max(240, Math.round(130 + (_cmpMsr.height || _cmpTitleFz * 1.2) + 14));
        _sAccentLine(120, _cmpAccentY, 80, pal);
        _cmpTop = _cmpAccentY + 30;
      }
      _pendingTableData = { markdown: s.table_markdown || '', x: 120, y: _cmpTop, w: 1680, h: H - _cmpTop - 80, styleOpts: { headerRow: true, totalRow: false, bandedRows: true, firstCol: false, lastCol: false, bandedCols: false } };
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);

    /* ── grid_2x2_layout: cells = [{icon, title, body}] ── */
    } else if (lay === 'grid_2x2_layout') {
      _setBg(pal, s);
      var _g2TitleFz = _adaptFontSize(s.title || '', 1680, 2, 80);
      _g2TitleFz = Math.max(28, _g2TitleFz);
      var _g2Msr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_g2TitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 });
      while (_g2TitleFz > 28 && (_g2Msr.height || 0) > 150) { _g2TitleFz -= 2; _g2Msr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_g2TitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 }); }
      _canvas.add(_sTB(s.title || '', { left:120, top:160, width:1680, fontFamily:pal.fontDisplay, fontSize:_g2TitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 }));
      var _g2GridY = Math.max(320, Math.round(160 + (_g2Msr.height || _g2TitleFz * 1.2) + 30));
      var _g2Cells = s.cells || [];
      var _g2CellW = Math.floor((W - 240 - 40) / 2);
      var _g2CellH = Math.floor((H - _g2GridY - 60) / 2);
      _g2Cells.slice(0, 4).forEach(function(cell, i) {
        var col = i % 2, row = Math.floor(i / 2);
        var cx = 120 + col * (_g2CellW + 40);
        var cy = _g2GridY + row * (_g2CellH + 20);
        var icon  = typeof cell === 'object' ? (cell.icon || '') : '';
        var ct    = typeof cell === 'object' ? (cell.title || '') : String(cell);
        var cb    = typeof cell === 'object' ? (cell.body || '') : '';
        _canvas.add(_sStatic(new fabric.Rect({ left:cx, top:cy, width:_g2CellW, height:_g2CellH - 10, fill: pal._isLight ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.05)', rx:6, ry:6, editorType:'deco' })));
        _canvas.add(_sStatic(new fabric.Rect({ left:cx, top:cy, width:_g2CellW, height:4, fill:pal.accent, editorType:'deco' })));
        var _g2TFz = Math.max(22, Math.min(40, Math.floor(_g2CellH * 0.18)));
        var iconOff = icon ? 60 : 0;
        if (icon) _canvas.add(_sStatic(_sTB(icon, { left:cx+16, top:cy+14, width:50, fontFamily:pal.fontBody, fontSize:34, editorType:'deco' })));
        _canvas.add(_sTB(ct, { left:cx+16+iconOff, top:cy+14, width:_g2CellW-32-iconOff, fontFamily:pal.fontDisplay, fontSize:_g2TFz, fontWeight:'400', fill:pal.text }));
        if (cb) _canvas.add(_sTB(cb, { left:cx+16, top:cy+_g2TFz*1.5+22, width:_g2CellW-32, fontFamily:pal.fontBody, fontSize:Math.max(18,_g2TFz-12), fill:pal.dim }));
      });
      _sBlobs(pal); _sChrome(meta, pageNum, pal, lay);

    /* ── conclusion_cards_layout / numbered_conclusions_layout: conclusions = [{heading, body}] ── */
    } else if (lay === 'conclusion_cards_layout' || lay === 'numbered_conclusions_layout') {
      _setBg(pal, s);
      var _ccTitleFz = _adaptFontSize(s.title || '', 1300, 2, 80);
      _ccTitleFz = Math.max(28, _ccTitleFz);
      var _ccMsr = _sTB(s.title || '', { left:0, top:0, width:1300, fontFamily:pal.fontDisplay, fontSize:_ccTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 });
      while (_ccTitleFz > 28 && (_ccMsr.height || 0) > 150) { _ccTitleFz -= 2; _ccMsr = _sTB(s.title || '', { left:0, top:0, width:1300, fontFamily:pal.fontDisplay, fontSize:_ccTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 }); }
      _canvas.add(_sTB(s.title || '', { left:120, top:160, width:1300, fontFamily:pal.fontDisplay, fontSize:_ccTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 }));
      var _ccBase = Math.max(300, Math.round(160 + (_ccMsr.height || _ccTitleFz * 1.2) + 24));
      var _ccConcs = s.conclusions || s.points || s.items || [];
      var _ccN = _ccConcs.length || 1;
      var _ccRowH = Math.floor((H - _ccBase - 80) / _ccN);
      var _isNum = lay === 'numbered_conclusions_layout';
      _ccConcs.forEach(function(c, i) {
        var ch = typeof c === 'object' ? (c.heading || c.title || '') : String(c);
        var cb = typeof c === 'object' ? (c.body || '') : '';
        var yy = _ccBase + i * _ccRowH;
        _canvas.add(_sStatic(new fabric.Rect({ left:120, top:yy, width:1680, height:_ccRowH-10, fill: pal._isLight ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.04)', rx:4, ry:4, editorType:'deco' })));
        _canvas.add(_sStatic(new fabric.Rect({ left:120, top:yy, width:4, height:_ccRowH-10, fill:pal.accent, editorType:'deco' })));
        var badge = _isNum ? String(i+1) : '◆';
        _canvas.add(_sStatic(_sTB(badge, { left:136, top:yy+10, width:60, fontFamily:pal.fontMono, fontSize:20, charSpacing:_isNum?0:60, fill:pal.accent, editorType:'deco' })));
        var _ccHFz = Math.max(22, Math.min(40, Math.floor(_ccRowH * 0.38)));
        _canvas.add(_sTB(ch, { left:210, top:yy+10, width:1548, fontFamily:pal.fontDisplay, fontSize:_ccHFz, fontWeight:'400', fill:pal.text }));
        if (cb) _canvas.add(_sTB(cb, { left:210, top:yy+14+_ccHFz*1.2, width:1548, fontFamily:pal.fontBody, fontSize:Math.max(18,_ccHFz-12), fill:pal.dim }));
      });
      _sBlobs(pal); _sChrome(meta, pageNum, pal, lay);

    /* ── agenda_layout: items = [{title, body, duration}] ── */
    } else if (lay === 'agenda_layout') {
      _setBg(pal, s);
      var _agTitleFz = _adaptFontSize(s.title || '', 1680, 2, 80);
      _agTitleFz = Math.max(28, _agTitleFz);
      var _agMsr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_agTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 });
      while (_agTitleFz > 28 && (_agMsr.height || 0) > 150) { _agTitleFz -= 2; _agMsr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_agTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 }); }
      _canvas.add(_sTB(s.title || '', { left:120, top:160, width:1680, fontFamily:pal.fontDisplay, fontSize:_agTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 }));
      var _agBase = Math.max(310, Math.round(160 + (_agMsr.height || _agTitleFz * 1.2) + 30));
      var _agItems = s.items || s.points || [];
      var _agN = _agItems.length || 1;
      var _agRowH = Math.floor((H - _agBase - 80) / _agN);
      _agItems.forEach(function(item, i) {
        var it = typeof item === 'object' ? (item.title || '') : String(item);
        var ib = typeof item === 'object' ? (item.body || '') : '';
        var id = typeof item === 'object' ? (item.duration || '') : '';
        var yy = _agBase + i * _agRowH;
        _canvas.add(_sStatic(new fabric.Rect({ left:120, top:yy, width:1680, height:_agRowH-8, fill: pal._isLight ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.04)', rx:4, ry:4, editorType:'deco' })));
        _canvas.add(_sStatic(new fabric.Rect({ left:120, top:yy, width:4, height:_agRowH-8, fill:pal.accent, editorType:'deco' })));
        _canvas.add(_sStatic(_sTB(String(i+1).padStart(2,'0'), { left:136, top:yy+10, width:60, fontFamily:pal.fontMono, fontSize:22, charSpacing:60, fill:pal.accent, editorType:'deco' })));
        var _agTFz = Math.max(22, Math.min(42, Math.floor(_agRowH * 0.4)));
        _canvas.add(_sTB(it, { left:216, top:yy+10, width:id ? 1380 : 1452, fontFamily:pal.fontDisplay, fontSize:_agTFz, fontWeight:'400', fill:pal.text }));
        if (id) _canvas.add(_sStatic(_sTB(id, { left:1560, top:yy+10, width:200, fontFamily:pal.fontMono, fontSize:Math.max(16,_agTFz-14), fill:pal.accent, textAlign:'right', editorType:'deco' })));
        if (ib) _canvas.add(_sTB(ib, { left:216, top:yy+14+_agTFz*1.2, width:1452, fontFamily:pal.fontBody, fontSize:Math.max(18,_agTFz-14), fill:pal.dim }));
      });
      _sBlobs(pal); _sChrome(meta, pageNum, pal, lay);

    /* ── stats_cards_layout: stats = [{value, label, body}] ── */
    } else if (lay === 'stats_cards_layout') {
      _setBg(pal, s);
      var _stcTitleFz = _adaptFontSize(s.title || '', 1680, 2, 80);
      _stcTitleFz = Math.max(28, _stcTitleFz);
      var _stcMsr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_stcTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 });
      while (_stcTitleFz > 28 && (_stcMsr.height || 0) > 150) { _stcTitleFz -= 2; _stcMsr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_stcTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 }); }
      _canvas.add(_sTB(s.title || '', { left:120, top:160, width:1680, fontFamily:pal.fontDisplay, fontSize:_stcTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 }));
      var _stcY = Math.max(340, Math.round(160 + (_stcMsr.height || _stcTitleFz * 1.2) + 40));
      var _stcStats = s.stats || s.points || [];
      var _stcN = Math.min(_stcStats.length, 4) || 1;
      var _stcCW = Math.floor((W - 240 - (_stcN-1)*40) / _stcN);
      var _stcCH = H - _stcY - 80;
      _stcStats.slice(0, 4).forEach(function(st, i) {
        var sv = typeof st === 'object' ? (st.value || '') : '';
        var sl = typeof st === 'object' ? (st.label || String(st)) : String(st);
        var sb = typeof st === 'object' ? (st.body || '') : '';
        var cx = 120 + i * (_stcCW + 40);
        _canvas.add(_sStatic(new fabric.Rect({ left:cx, top:_stcY, width:_stcCW, height:_stcCH, fill: pal._isLight ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.05)', rx:8, ry:8, editorType:'deco' })));
        _canvas.add(_sStatic(new fabric.Rect({ left:cx, top:_stcY, width:_stcCW, height:5, fill:pal.accent, editorType:'deco' })));
        if (sv) _canvas.add(_sTB(sv, { left:cx+20, top:_stcY+30, width:_stcCW-40, fontFamily:pal.fontDisplay, fontSize:90, fontWeight:'400', fill:pal.accent, textAlign:'center' }));
        _canvas.add(_sTB(sl, { left:cx+20, top:_stcY+(sv?150:50), width:_stcCW-40, fontFamily:pal.fontDisplay, fontSize:34, fontWeight:'400', fill:pal.text, textAlign:'center' }));
        if (sb) _canvas.add(_sTB(sb, { left:cx+20, top:_stcY+(sv?210:100), width:_stcCW-40, fontFamily:pal.fontBody, fontSize:22, fill:pal.dim, textAlign:'center' }));
      });
      _sBlobs(pal); _sChrome(meta, pageNum, pal, lay);

    /* ── pricing_cards_layout: cards = [{name, price, features, highlighted}] ── */
    } else if (lay === 'pricing_cards_layout') {
      _setBg(pal, s);
      var _prcTitleFz = _adaptFontSize(s.title || '', 1680, 2, 80);
      _prcTitleFz = Math.max(28, _prcTitleFz);
      var _prcMsr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_prcTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 });
      while (_prcTitleFz > 28 && (_prcMsr.height || 0) > 150) { _prcTitleFz -= 2; _prcMsr = _sTB(s.title || '', { left:0, top:0, width:1680, fontFamily:pal.fontDisplay, fontSize:_prcTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 }); }
      _canvas.add(_sTB(s.title || '', { left:120, top:160, width:1680, fontFamily:pal.fontDisplay, fontSize:_prcTitleFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:0.95 }));
      var _prcY = Math.max(330, Math.round(160 + (_prcMsr.height || _prcTitleFz * 1.2) + 30));
      var _prcCards = s.cards || s.points || [];
      var _prcN = Math.min(_prcCards.length, 4) || 1;
      var _prcCW = Math.floor((W - 240 - (_prcN-1)*40) / _prcN);
      var _prcCH = H - _prcY - 80;
      _prcCards.slice(0, 4).forEach(function(card, i) {
        var cn = typeof card === 'object' ? (card.name || card.title || '') : String(card);
        var cp = typeof card === 'object' ? (card.price || '') : '';
        var cf = typeof card === 'object' ? (card.features || []) : [];
        var hi = typeof card === 'object' ? !!card.highlighted : false;
        var cx = 120 + i * (_prcCW + 40);
        _canvas.add(_sStatic(new fabric.Rect({ left:cx, top:_prcY, width:_prcCW, height:_prcCH, fill: hi ? pal.accent+'22' : (pal._isLight ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.05)'), rx:8, ry:8, editorType:'deco' })));
        if (hi) _canvas.add(_sStatic(new fabric.Rect({ left:cx, top:_prcY, width:_prcCW, height:6, fill:pal.accent, editorType:'deco' })));
        _canvas.add(_sTB(cn, { left:cx+20, top:_prcY+24, width:_prcCW-40, fontFamily:pal.fontDisplay, fontSize:36, fontWeight:'400', fill: hi ? pal.accent : pal.text, textAlign:'center' }));
        if (cp) _canvas.add(_sTB(cp, { left:cx+20, top:_prcY+80, width:_prcCW-40, fontFamily:pal.fontDisplay, fontSize:56, fontWeight:'400', fill:pal.accent, textAlign:'center' }));
        var fyStart = _prcY + (cp ? 160 : 90);
        cf.slice(0, 5).forEach(function(feat, fi) { _canvas.add(_sTB('✓  ' + feat, { left:cx+20, top:fyStart+fi*44, width:_prcCW-40, fontFamily:pal.fontBody, fontSize:22, fill:pal.dim })); });
      });
      _sBlobs(pal); _sChrome(meta, pageNum, pal, lay);

    /* ── Two images + bullets (left/right/above/below) ── */
    } else if (lay === 'two_image_left_layout'  || lay === 'two_image_right_layout' ||
               lay === 'two_image_above_layout' || lay === 'two_image_below_layout') {
      _setBg(pal, s); _sBlobs(pal);
      var _tiLeft  = (lay === 'two_image_left_layout');
      var _tiRight = (lay === 'two_image_right_layout');
      var _tiAbove = (lay === 'two_image_above_layout');
      var _tiImg1  = s.img1_path || (Array.isArray(s.images) ? s.images[0] : '') || '';
      var _tiImg2  = s.img2_path || (Array.isArray(s.images) ? s.images[1] : '') || '';
      var _tiBul   = s.content || s.items || s.bullets || [];
      if (typeof _tiBul === 'string') _tiBul = [_tiBul];
      var _tiTitle = s.title || '';

      if (_tiLeft || _tiRight) {
        var _imgColX = _tiLeft ? 120 : 1020;
        var _txtColX = _tiLeft ? 1020 : 120;
        var _txtW    = 780, _imW = 780, _imH = 370, _imGap = 44, _imY0 = 190;
        _sImageInSlot(_tiImg1, imgCache, _imgColX, _imY0, _imW, _imH, pal, s.caption1);
        _sImageInSlot(_tiImg2, imgCache, _imgColX, _imY0 + _imH + _imGap, _imW, _imH, pal, s.caption2);
        var _tiFz  = Math.max(34, _adaptFontSize(_tiTitle, _txtW, 3, 72));
        var _tiT   = _sTB(_tiTitle, { left:_txtColX, top:200, width:_txtW, fontFamily:pal.fontDisplay, fontSize:_tiFz, fontWeight:'400', fill:pal.text, charSpacing:-15, lineHeight:1.05 });
        _canvas.add(_tiT);
        var _tiAcc = 200 + (_tiT.height || _tiFz * 1.2) + 10;
        _sAccentLine(_txtColX, _tiAcc, 60, pal);
        _sBulletList(_tiBul, _txtColX, _tiAcc + 28, _txtW, H - (_tiAcc + 28) - 80, 30, pal, { prefix:'•  ', lineH:1.45 });
      } else {
        var _tiFz2 = Math.max(34, _adaptFontSize(_tiTitle, 1680, 2, 96));
        var _tiT2  = _sTB(_tiTitle, { left:120, top:130, width:1680, fontFamily:pal.fontDisplay, fontSize:_tiFz2, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:1.05 });
        _canvas.add(_tiT2);
        var _tiTB  = 130 + (_tiT2.height || _tiFz2 * 1.2) + 10;
        _sAccentLine(120, _tiTB, 80, pal);
        var _rowW = 820, _rowH = 360, _rowGap = 40, _row1X = 120, _row2X = 120 + 820 + 40;
        var _capReserve = (s.caption1 || s.caption2) ? 36 : 0;
        if (_tiAbove) {
          var _imgRowY = _tiTB + 24;
          _sImageInSlot(_tiImg1, imgCache, _row1X, _imgRowY, _rowW, _rowH, pal, s.caption1);
          _sImageInSlot(_tiImg2, imgCache, _row2X, _imgRowY, _rowW, _rowH, pal, s.caption2);
          var _bY = _imgRowY + _rowH + _capReserve + 24;
          _sBulletList(_tiBul, 120, _bY, 1680, H - _bY - 80, 30, pal, { prefix:'•  ' });
        } else {
          var _imgRowY2 = H - 50 - _rowH - _capReserve - 8;
          var _bY2      = _tiTB + 28;
          _sBulletList(_tiBul, 120, _bY2, 1680, Math.max(1, _imgRowY2 - _bY2 - 20), 30, pal, { prefix:'•  ' });
          _sImageInSlot(_tiImg1, imgCache, _row1X, _imgRowY2, _rowW, _rowH, pal, s.caption1);
          _sImageInSlot(_tiImg2, imgCache, _row2X, _imgRowY2, _rowW, _rowH, pal, s.caption2);
        }
      }
      _sChrome(meta, pageNum, pal, lay);

    /* ── Split contrast (Before / After) ── */
    } else if (lay === 'split_contrast_layout') {
      _setBg(pal, s);
      var _scHalf = W / 2;
      _canvas.add(_sStatic(new fabric.Rect({ left:_scHalf - 1, top:120, width:2, height:H - 260, fill:pal.accent, opacity:0.3, editorType:'deco' })));
      [{ tag:'Before', t:s.left_title,  items:s.left_items,  x:120 },
       { tag:'After',  t:s.right_title, items:s.right_items, x:_scHalf + 80 }].forEach(function(side) {
        var _sw = _scHalf - 200;
        _canvas.add(_sStatic(_sTB(side.tag.toUpperCase(), { left:side.x, top:150, width:_sw, fontFamily:pal.fontMono, fontSize:22, charSpacing:180, fill:pal.accent, editorType:'deco' })));
        var _scFz = Math.max(30, _adaptFontSize(side.t || '', _sw, 2, 56));
        var _scT  = _sTB(side.t || '', { left:side.x, top:200, width:_sw, fontFamily:pal.fontDisplay, fontSize:_scFz, fontWeight:'400', fill:pal.text, charSpacing:-10, lineHeight:1.05 });
        _canvas.add(_scT);
        var _scY = 200 + (_scT.height || _scFz * 1.2) + 24;
        var _scItems = Array.isArray(side.items) ? side.items : (side.items ? [side.items] : []);
        _sBulletList(_scItems, side.x, _scY, _sw, H - _scY - 100, 28, pal, { prefix:'•  ' });
      });
      _sBlobs(pal); _sChrome(meta, pageNum, pal, lay);

    /* ── Research question ── */
    } else if (lay === 'research_question_layout') {
      _setBg(pal, s);
      var _rqFz = Math.max(30, _adaptFontSize(s.title || '', 1680, 2, 84));
      var _rqT  = _sTB(s.title || '', { left:120, top:130, width:1680, fontFamily:pal.fontDisplay, fontSize:_rqFz, fontWeight:'400', fill:pal.text, charSpacing:-20 });
      _canvas.add(_rqT);
      var _rqY = 130 + (_rqT.height || _rqFz * 1.2) + 30;
      _canvas.add(_sStatic(new fabric.Rect({ left:120, top:_rqY, width:1680, height:230, fill: pal._isLight ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.05)', stroke:pal.accent, strokeWidth:2, rx:10, ry:10, editorType:'deco' })));
      _canvas.add(_sStatic(_sTB('MAIN RQ', { left:150, top:_rqY + 20, width:400, fontFamily:pal.fontMono, fontSize:20, charSpacing:160, fill:pal.accent, editorType:'deco' })));
      _canvas.add(_sTB(s.main_question || '', { left:150, top:_rqY + 56, width:1620, fontFamily:pal.fontDisplay, fontSize:40, fontWeight:'400', fill:pal.text, lineHeight:1.15 }));
      var _sqs = s.sub_questions || [];
      var _sqY = _rqY + 270, _sqW = Math.floor((1680 - 2 * 40) / 3);
      _sqs.slice(0, 3).forEach(function(sq, i) {
        var _sx = 120 + i * (_sqW + 40);
        _sAccentLine(_sx, _sqY, 40, pal);
        _canvas.add(_sStatic(_sTB('SUB-Q ' + ('0' + (i + 1)).slice(-2), { left:_sx, top:_sqY + 12, width:_sqW, fontFamily:pal.fontMono, fontSize:18, charSpacing:120, fill:pal.accent, editorType:'deco' })));
        _canvas.add(_sTB(String(sq || ''), { left:_sx, top:_sqY + 48, width:_sqW, fontFamily:pal.fontBody, fontSize:26, fill:pal.text, lineHeight:1.3 }));
      });
      _sBlobs(pal); _sChrome(meta, pageNum, pal, lay);

    /* ── Fullscreen image with text overlay ── */
    } else if (lay === 'image_fullscreen_overlay_layout') {
      _setBg(pal, s);
      if (s.img_path && imgCache && imgCache[s.img_path]) {
        var _ifEl  = imgCache[s.img_path].getElement();
        var _ifImg = new fabric.Image(_ifEl, { editorType:'image', crossOrigin:'anonymous' });
        var _ifSc  = Math.max(W / _ifImg.width, H / _ifImg.height);
        _ifImg.scale(_ifSc);
        _ifImg.set({ left:(W - _ifImg.getScaledWidth()) / 2, top:(H - _ifImg.getScaledHeight()) / 2, editorType:'image' });
        _canvas.add(_ifImg);
      } else {
        _canvas.add(_sStatic(new fabric.Rect({ left:0, top:0, width:W, height:H, fill: pal._isLight ? '#dddddd' : '#222222', editorType:'image' })));
        _canvas.add(_sStatic(_sTB('Click to add image', { left:0, top:H / 2 - 20, width:W, fontFamily:pal.fontMono, fontSize:32, charSpacing:120, fill:'rgba(255,255,255,0.5)', textAlign:'center', editorType:'deco' })));
      }
      _canvas.add(_sStatic(new fabric.Rect({ left:0, top:H - 560, width:W, height:560, fill:'#000000', opacity:0.55, editorType:'deco' })));
      _canvas.add(_sStatic(new fabric.Rect({ left:120, top:H - 380, width:80, height:6, fill:pal.accent, editorType:'deco' })));
      var _ifFz = Math.max(36, _adaptFontSize(s.title || '', 1500, 2, 96));
      _canvas.add(_sTB(s.title || '', { left:120, top:H - 350, width:1500, fontFamily:pal.fontDisplay, fontSize:_ifFz, fontWeight:'400', fill:'#ffffff', charSpacing:-15, lineHeight:1.05 }));
      if (s.body) _canvas.add(_sTB(String(s.body), { left:120, top:H - 200, width:1500, fontFamily:pal.fontBody, fontSize:30, fill:'rgba(255,255,255,0.85)', lineHeight:1.3 }));
      _sChrome(meta, pageNum, pal, lay);

    /* ── Editorial (eyebrow + big title + lede + pull-quote) ── */
    } else if (lay === 'editorial_layout') {
      _setBg(pal, s);
      if (s.eyebrow) _canvas.add(_sStatic(_sTB(String(s.eyebrow).toUpperCase(), { left:120, top:120, width:1680, fontFamily:pal.fontMono, fontSize:22, charSpacing:200, fill:pal.accent, editorType:'deco' })));
      var _edFz = Math.max(40, _adaptFontSize(s.title || '', 1680, 2, 110));
      var _edT  = _sTB(s.title || '', { left:120, top:170, width:1680, fontFamily:pal.fontDisplay, fontSize:_edFz, fontWeight:'400', fill:pal.text, charSpacing:-20, lineHeight:1.0 });
      _canvas.add(_edT);
      var _edY = 170 + (_edT.height || _edFz * 1.2) + 40;
      _sAccentLine(120, _edY - 20, 80, pal);
      var _ledeW = s.pull_quote ? 1000 : 1680;
      _canvas.add(_sTB(String(s.lede || ''), { left:120, top:_edY, width:_ledeW, fontFamily:pal.fontBody, fontSize:32, fill:pal.text, lineHeight:1.45 }));
      if (s.pull_quote) {
        var _pqX = 1180, _pqW = 620;
        _canvas.add(_sStatic(_sTB('“', { left:_pqX, top:_edY - 30, fontFamily:pal.fontDisplay, fontSize:120, fontStyle:'italic', fill:pal.accent, opacity:0.3, editorType:'deco' })));
        _canvas.add(_sTB(String(s.pull_quote), { left:_pqX, top:_edY + 90, width:_pqW, fontFamily:pal.fontDisplay, fontSize:38, fontStyle:'italic', fill:pal.text, lineHeight:1.2 }));
        if (s.pull_attribution) _canvas.add(_sStatic(_sTB('— ' + s.pull_attribution, { left:_pqX, top:_edY + 330, width:_pqW, fontFamily:pal.fontMono, fontSize:22, fill:pal.dim, editorType:'deco' })));
      }
      if (s.footline_left || s.footline_right) {
        _canvas.add(_sStatic(_sTB(String(s.footline_left || ''),  { left:120, top:H - 90, width:840, fontFamily:pal.fontMono, fontSize:20, fill:pal.dim, editorType:'deco' })));
        _canvas.add(_sStatic(_sTB(String(s.footline_right || ''), { left:960, top:H - 90, width:840, fontFamily:pal.fontMono, fontSize:20, fill:pal.dim, textAlign:'right', editorType:'deco' })));
      }
      _sChrome(meta, pageNum, pal, lay);

    /* ── Nested bullets (items with sub-bullets) ── */
    } else if (lay === 'nested_bullets_layout') {
      _setBg(pal, s);
      var _nbFz = Math.max(28, _adaptFontSize(s.title || '', 1680, 2, 96));
      var _nbT  = _sTB(s.title || '', { left:120, top:130, width:1680, fontFamily:pal.fontDisplay, fontSize:_nbFz, fontWeight:'400', fill:pal.text, charSpacing:-20 });
      _canvas.add(_nbT);
      var _nbY = 130 + (_nbT.height || _nbFz * 1.2) + 10;
      _sAccentLine(120, _nbY, 80, pal);
      var _nbItems = Array.isArray(s.items) ? s.items : (Array.isArray(s.content) ? s.content : []);
      var _nbLines = [];
      _nbItems.forEach(function(it) {
        if (it && typeof it === 'object') {
          _nbLines.push('•  ' + _stripInlineMath(it.text || ''));
          (it.sub || []).forEach(function(sb) { _nbLines.push('      ◦  ' + _stripInlineMath(String(sb))); });
        } else {
          _nbLines.push('•  ' + _stripInlineMath(String(it)));
        }
      });
      var _nbStart = _nbY + 28, _nbAvail = H - _nbStart - 80;
      var _nbCombined = _nbLines.join('\n');
      var _nbFz2 = 32, _nbTB;
      while (_nbFz2 >= 18) {
        _nbTB = _sTB(_nbCombined, { left:120, top:_nbStart, width:1680, fontFamily:pal.fontBody, fontSize:_nbFz2, fill:pal.text, lineHeight:1.4 });
        if ((_nbTB.height || 0) <= _nbAvail) break;
        _nbFz2 -= 2;
      }
      if (_nbTB) _canvas.add(_nbTB);
      _sBlobs(pal); _sChrome(meta, pageNum, pal, lay);

    /* ── Default: adaptive bullet list for all remaining layouts ── */
    } else {
      _setBg(pal, s);
      var titleTxt = s.title || s.heading || '';
      var yContent = titleTxt ? _sTitleBlock(titleTxt, pal, { w: 1680, accentW: 80 }) : 200;
      var contentItems = s.content || s.items || s.bullets || s.points || [];
      if (typeof contentItems === 'string') contentItems = [contentItems];
      var availH = H - yContent - 80;
      if (contentItems.length > 0) {
        _sBulletList(contentItems, 120, yContent, 1680, availH, 36, pal);
      } else {
        _canvas.add(_sTB('Click to add content…', {
          left: 120, top: yContent + 20, width: 1680,
          fontFamily: pal.fontBody, fontSize: 36,
          fill: pal.dim, fontStyle: 'italic',
          opacity: 0.45, editorType: 'placeholder',
        }));
      }
      _sBlobs(pal);
      _sChrome(meta, pageNum, pal, lay);
    }

    _canvas.requestRenderAll();
  }

  function loadFromSlideSpec(spec, opts) {
    if (!spec) return;
    opts = opts || {};
    var _reTheme = !!opts.reTheme;   /* theme change: rebuild fresh with new palette */
    var slides = spec.slides;
    if (!Array.isArray(slides) || slides.length === 0) return;

    /* Sort slides: covers first, end slides last, middle slides in between */
    var _COVER_LAY = ['config_and_greeting_slide', 'cover_split_layout'];
    var _END_LAY   = ['end_layout', 'end_with_image_layout', 'end_image_hero_layout'];
    var _sCovers = slides.filter(function(s) { return _COVER_LAY.indexOf(s.layout) !== -1; });
    var _sEnds   = slides.filter(function(s) { return _END_LAY.indexOf(s.layout) !== -1; });
    var _sMids   = slides.filter(function(s) {
      return _COVER_LAY.indexOf(s.layout) === -1 && _END_LAY.indexOf(s.layout) === -1;
    });
    slides = _sCovers.concat(_sMids).concat(_sEnds);
    spec.slides = slides;

    var pal = _SPEC_THEMES[(spec.meta && spec.meta.theme)] || _SPEC_THEMES.frankfurt;

    /* Capture current state BEFORE resetting (so theme changes preserve edits) */
    /* When restoring from a Save-as-HTML export (_canvasJsons present), always start at slide 0
       so the viewer doesn't inadvertently reopen on the last (end) slide the author was viewing */
    var _targetSlide = spec._canvasJsons ? 0 : Math.min(_currentSlide || 0, slides.length - 1);
    var _savedSlides = null;
    /* Only capture _savedSlides when there is actual user content in the current in-memory
       session (e.g. theme change). On a fresh page load the initial _slides stub has no
       hasUserContent, so _savedSlides stays null and the autosave early-return path works.
       NOTE: checking `hasUserContent` alone is NOT enough — initHistory() calls saveState()
       once at bootstrap to snapshot the still-blank canvas, which sets hasUserContent=true on
       that stub slide even though it has zero objects. Require actual objects too, otherwise
       every fresh load/Save-As reopen is misdetected as a theme-change and _canvasJsons/autosave
       restored JSON gets discarded and rebuilt from the original spec (all edits lost). */
    if ((_reTheme || _slides.some(function(s) {
      return s.hasUserContent && s.json && Array.isArray(s.json.objects) && s.json.objects.length > 0;
    })) && _slides && _slides.length > 0) {
      /* Flush live canvas edits into current slide's JSON */
      if (_slides[_currentSlide] && _canvas) {
        _slides[_currentSlide].json = _canvas.toJSON(_TOJSON_KEYS);
      }
      _savedSlides = _slides.map(function(sl) {
        return {
          json:       sl.json ? JSON.stringify(sl.json) : null,
          edited:     sl.hasUserContent === true,
          tableData:  sl.tableData || null,
          transition: sl.transition || null,
        };
      });
    }

    _currentSpec = spec;

    _slides = slides.map(function() {
      return { json: null, thumb: null, presImage: null };
    });
    /* Restore baked-in canvas JSONs from a Save-as-HTML export.
       Skipped on reTheme: we want a fresh spec-rebuild with the new palette; user
       edits are still preserved via the _savedSlides merge below. */
    if (!_reTheme && Array.isArray(spec._canvasJsons)) {
      spec._canvasJsons.forEach(function(j, i) {
        if (j && _slides[i]) { _slides[i].json = j; _slides[i].hasUserContent = true; }
      });
    }
    /* Restore baked-in table data from a Save-as-HTML export */
    if (Array.isArray(spec._slideTables)) {
      spec._slideTables.forEach(function(t, i) {
        if (t && _slides[i]) _slides[i].tableData = t;
      });
    }
    /* Restore baked-in per-slide transitions from a Save-as-HTML export */
    if (Array.isArray(spec._slideTransitions)) {
      spec._slideTransitions.forEach(function(tr, i) {
        if (tr && _slides[i]) _slides[i].transition = tr;
      });
    }

    /* ── Migrate legacy Fabric-drawn tables to HTML overlay ──────────────────
       Old exported files (before HTML overlay) stored the table as Fabric Rect +
       Textbox objects (editorType:'table' / isTableBg:true) in _canvasJsons.
       Strip those objects and reconstruct tableData so the HTML overlay shows
       instead.  Position is read from the first Fabric rect so it matches exactly. */
    slides.forEach(function(slideSpec, i) {
      var sl = _slides[i];
      if (!sl || !sl.hasUserContent || !sl.json) return;
      var json = sl.json;
      if (typeof json === 'string') { try { json = JSON.parse(json); } catch(e) { return; } }
      if (!json || !Array.isArray(json.objects)) return;

      /* NOTE: intentionally does NOT flag plain `rect`+editorType:'deco' objects (e.g. the
         accent-line under a table slide's title, added by _sAccentLine on every table_above/
         data_table/comparison slide) as a legacy table marker — that used to false-positive on
         ordinary theme decoration, stripping it and forcing a full slide rebuild on every load. */
      var hasFabricTable = json.objects.some(function(o) {
        return o.editorType === 'table' || o.isTableBg;
      });
      if (!hasFabricTable) return;

      /* Extract geometry from first header rect (row 0, col 0) */
      var bgObjs = json.objects.filter(function(o) { return o.isTableBg; });
      var firstBg = bgObjs.filter(function(o) { return o.tableRow === 0 && o.tableCol === 0; })[0];
      var maxRow = 0, maxCol = 0;
      bgObjs.forEach(function(o) {
        if ((o.tableRow || 0) > maxRow) maxRow = o.tableRow || 0;
        if ((o.tableCol || 0) > maxCol) maxCol = o.tableCol || 0;
      });
      var lastBg = bgObjs.filter(function(o) { return o.tableRow === maxRow && o.tableCol === 0; })[0];
      var rightBg = bgObjs.filter(function(o) { return o.tableRow === 0 && o.tableCol === maxCol; })[0];

      var tX = firstBg ? (firstBg.left || 120) : 120;
      var tY = firstBg ? (firstBg.top  || 200) : 200;
      var tW = (firstBg && rightBg) ? Math.round((rightBg.left || tX) + (rightBg.width || 100) - tX) : 1680;
      var tH = (firstBg && lastBg)  ? Math.round((lastBg.top  || tY) + (lastBg.height || 60) - tY) : 500;

      /* Strip old Fabric table objects (text cells, serialized bg rects) */
      json.objects = json.objects.filter(function(o) {
        if (o.editorType === 'table') return false;
        if (o.isTableBg) return false;
        return true;
      });
      sl.json = json;

      /* Reconstruct tableData from spec if not already set */
      if (!sl.tableData) {
        var lay = slideSpec.layout || '';
        var md  = '';
        if (lay === 'comparison_layout' || lay === 'table_above_layout') {
          md = slideSpec.table_markdown || '';
        } else if (lay === 'data_table_layout') {
          md = _tableToMarkdown(slideSpec.headers || [], slideSpec.rows || []);
        }
        if (md) sl.tableData = { markdown: md, x: tX, y: tY, w: tW, h: tH };
      }
    });
    /* ── End migration ───────────────────────────────────────────────────── */

    /* ── LocalStorage auto-restore: DISABLED ─────────────────────────────────
       Auto-restore was removed by design: it silently reloaded a previous
       browser session's edits (keyed only by deck title), so a freshly
       regenerated deck kept the OLD state instead of showing the new content.
       Edits now persist ONLY via "Save As HTML" (Ctrl+S → baked _canvasJsons),
       which is handled above and is untouched here. On a fresh generated file
       we proactively purge any stale autosave for this deck title so nothing
       lingers from older builds. */
    if (!_reTheme && !spec._canvasJsons) {
      try {
        var _staleTitle = ((spec.meta && spec.meta.title) || 'deck').replace(/[^a-z0-9]/gi, '_').substring(0, 40);
        localStorage.removeItem('FE_as_' + _staleTitle);
      } catch (e) { /* private mode / unavailable */ }
    }

    /* Save-As reopen (spec._canvasJsons) is restored VERBATIM — no forced rebuild/re-theme.
       A file the user saved IS a snapshot: reopening must show exactly what was saved
       (custom text colors, fonts, positions, styles, lock state). We intentionally do NOT
       capture _savedSlides here anymore; the old "re-apply spec.meta.theme on every reopen"
       behavior silently overwrote user-chosen text colors with the theme color. If the user
       wants to re-theme after reopening, _applyTheme (reTheme) handles it via the capture above. */

    _currentSlide = 0;

    var meta = spec.meta || {};

    /* ── Step 1: Collect every unique img_path so we can pre-load them all ── */
    var imgUrls = [];
    slides.forEach(function(s) {
      if (s.img_path)  imgUrls.push(s.img_path);
      if (s.img1_path) imgUrls.push(s.img1_path);
      if (s.img2_path) imgUrls.push(s.img2_path);
      if (Array.isArray(s.images)) s.images.forEach(function(u) { if (u) imgUrls.push(u); });
    });
    imgUrls = imgUrls.filter(function(v, i, a) { return a.indexOf(v) === i; });

    /* ── Step 2: Pre-load images in parallel, then build slides synchronously ── */
    var imgCache = {};

    function buildAllSlides() {
      _batchSave = true;
      slides.forEach(function(slideSpec, i) {
        var _isTableLayout = slideSpec.layout === 'comparison_layout' ||
                             slideSpec.layout === 'table_above_layout' ||
                             slideSpec.layout === 'data_table_layout';

        /* Determine whether a fresh spec-build is required.
           Force rebuild only when: no cached JSON, tableData missing on table slide,
           or JSON still contains old Fabric table objects (text cells or background rects).
           This lets clean JSONs (already migrated or freshly built) take the early-return
           path so user edits (title changes, moved objects, etc.) are preserved. */
        var _needsFresh = !_slides[i] || !_slides[i].json;
        if (!_needsFresh && _isTableLayout && !_slides[i].tableData) {
          _needsFresh = true;
        }
        if (!_needsFresh && _slides[i] && _slides[i].json) {
          var _jCheck = _slides[i].json;
          if (typeof _jCheck === 'string') { try { _jCheck = JSON.parse(_jCheck); } catch(e) { _needsFresh = true; } }
          if (!_needsFresh && _jCheck && Array.isArray(_jCheck.objects)) {
            /* Don't flag plain deco rects (e.g. accent line under a table slide's title) as
               legacy Fabric table markers — only real leftover table objects count. */
            _needsFresh = _jCheck.objects.some(function(o) {
              return o.editorType === 'table' || o.isTableBg;
            });
          }
        }

        /* Early return: JSON is already clean — preserve user edits. */
        if (!_savedSlides && !_needsFresh && _slides[i] && _slides[i].hasUserContent && _slides[i].json) {
          var _preJson = _slides[i].json;
          if (typeof _preJson === 'string') { try { _preJson = JSON.parse(_preJson); } catch(e) {} }
          _slides[i].json      = _preJson;
          _slides[i].history   = [JSON.stringify(_preJson)];
          _slides[i].historyIdx = 0;
          /* Refresh stale tableData.markdown when 0 data rows (avoids wiping user-edited table) */
          if (_isTableLayout && _slides[i].tableData &&
              _parseMarkdownTable(_slides[i].tableData.markdown).rows.length === 0) {
            var _freshMd = (slideSpec.layout === 'data_table_layout')
              ? _tableToMarkdown(slideSpec.headers || [], slideSpec.rows || [])
              : (slideSpec.table_markdown || '');
            if (_freshMd) {
              _slides[i].tableData = {
                markdown: _freshMd,
                x: _slides[i].tableData.x, y: _slides[i].tableData.y,
                w: _slides[i].tableData.w, h: _slides[i].tableData.h,
              };
            }
          }
          return;
        }

        /* Fresh build: first load or stale JSON with old Fabric table objects. */
        var _savedTblData = (_isTableLayout && _slides[i]) ? (_slides[i].tableData || null) : null;
        _pendingTableData = null;
        _canvas.clear();
        _specBuildSlide(slideSpec, pal, meta, i + 1, imgCache);
        _slides[i].tableData = _savedTblData || _pendingTableData;
        _canvas.renderAll();
        _renderThumb(i);
        _slides[i].bgColor = _canvas.backgroundColor;
        var snap    = _canvas.toJSON(_TOJSON_KEYS);
        _slides[i].json = snap;
        _slides[i].history   = [JSON.stringify(snap)];
        _slides[i].historyIdx = 0;
      });

      /* Restore user edits: keep new theme decos, restore user content (images, text positions) */
      if (_savedSlides) {
        _savedSlides.forEach(function(saved, i) {
          if (!saved.edited || !saved.json || i >= _slides.length) return;
          var oldData;
          try { oldData = JSON.parse(saved.json); } catch(e) { return; }
          if (!oldData || !oldData.objects) return;
          /* Non-deco objects = user content (text, images, user-added shapes) */
          var userContent = oldData.objects.filter(function(obj) {
            return obj.editorType !== 'deco' && obj.editorType !== 'chrome';
          });
          if (userContent.length === 0) return;
          /* Standard pipeline-generated text (editorType:'text', set by _sTB — titles, bullets,
             etc.) tracks the theme's text color across theme changes. User-added text via the
             "Add Text" tool has no editorType, so it's left untouched here — only the theme's
             own generated text re-colors, never a color the user explicitly chose.
             Use the LAYOUT-EFFECTIVE palette (contentText for light themes like sage/ivory),
             not the base pal.text — otherwise edited content slides get light text on a light
             background (invisible). Cover/section/end layouts keep base text via _effectivePal. */
          var effPal = _effectivePal((slides[i] && slides[i].layout) || '', pal);
          userContent.forEach(function(obj) {
            if (obj.editorType === 'text' && effPal && effPal.text) obj.fill = effPal.text;
            /* Formula layout's box+text are user-editable (not 'deco') so they're preserved
               above like any other content — but still need to pick up the new theme's colors. */
            if (obj.editorType === 'formula' && effPal && effPal.text) obj.fill = effPal.text;
            if (obj.editorType === 'formula-box' && effPal) {
              obj.fill = effPal._isLight ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.06)';
              if (effPal.accent) obj.stroke = effPal.accent;
            }
          });
          /* New theme's deco objects (accent lines, chrome, background decorations) */
          var themedDecos = (_slides[i].json.objects || []).filter(function(obj) {
            return obj.editorType === 'deco' || obj.editorType === 'chrome';
          });
          /* Merge: new-theme decos + user content with preserved positions */
          _slides[i].json = Object.assign({}, _slides[i].json, {
            objects: themedDecos.concat(userContent),
          });
          /* Reset history to merged state so Ctrl+Z starts from here */
          _slides[i].history       = [JSON.stringify(_slides[i].json)];
          _slides[i].historyIdx    = 0;
          _slides[i].hasUserContent = true;
        });
        /* Restore per-slide table data + transition preserved before theme change.
           For EDITED slides, saved.tableData must OVERRIDE the fresh pipeline table
           the rebuild produced — otherwise table edits are lost on a theme switch. */
        _savedSlides.forEach(function(saved, i) {
          if (saved.tableData && _slides[i] && (saved.edited || !_slides[i].tableData)) {
            _slides[i].tableData = saved.tableData;
          }
          if (saved.transition && _slides[i] && !_slides[i].transition) {
            _slides[i].transition = saved.transition;
          }
        });
      }
      _batchSave = false;

      /* Navigate to previously active slide, not always slide 0 */
      /* Always avoid restoring to an end slide (backend may put it at index 0) */
      var _END_LAYOUTS = ['end_layout', 'end_with_image_layout', 'end_image_hero_layout'];
      var _restoreIdx  = Math.min(_targetSlide, _slides.length - 1);
      if (_END_LAYOUTS.indexOf((slides[_restoreIdx] && slides[_restoreIdx].layout) || '') !== -1) {
        /* Target is an end slide — find the first non-end slide instead */
        for (var _ei = 0; _ei < slides.length; _ei++) {
          if (_END_LAYOUTS.indexOf(slides[_ei].layout || '') === -1) {
            _restoreIdx = _ei;
            break;
          }
        }
      }
      _isRestoring = true;
      _canvas.loadFromJSON(_slides[_restoreIdx].json, function() {
        if (_slides[_restoreIdx].bgColor != null) _canvas.backgroundColor = _slides[_restoreIdx].bgColor;
        _canvas.discardActiveObject();
        _canvas.requestRenderAll();
        _isRestoring = false;
        _currentSlide = _restoreIdx;
        /* Init per-slide history for every slide on fresh load */
        _slides.forEach(function(sl) {
          if (!sl.history || sl.history.length === 0) {
            sl.history   = sl.json ? [JSON.stringify(sl.json)] : [];
            sl.historyIdx = Math.max(0, sl.history.length - 1);
          }
        });
        _syncUndoRedoBtns();
        _rebuildThumbPanel();
        _renderTableLayer(_currentSlide);
        var activeTheme = (_currentSpec && _currentSpec.meta && _currentSpec.meta.theme) || 'frankfurt';
        _highlightThemeBtn(activeTheme);
        _startBlobAnimation();
        /* Deferred: generate thumbnails for slides pre-loaded from _canvasJsons (Save-as-HTML restore) */
        var _thumbQ = [];
        _slides.forEach(function(sl, qi) { if (sl.hasUserContent && sl.json && !sl.thumb && qi !== _currentSlide) _thumbQ.push(qi); });
        if (_thumbQ.length > 0) {
          /* Snapshot active slide's canvas state NOW (before any thumb-gen load overwrites it).
             This is the authoritative state to restore to when thumbnail generation finishes. */
          var _thumbRestoreJson  = _slides[_currentSlide].json;
          var _thumbRestoreBg    = _slides[_currentSlide].bgColor;
          function _processThumbQ() {
            if (_thumbQ.length === 0) {
              /* Restore the slide the user is actually on when thumbnails finish.
                 _currentSlide may have changed if user navigated during thumb gen.
                 _slides[_currentSlide].json has the correct content (saved by gotoSlide). */
              _isRestoring = true;
              var _finalSlide = _currentSlide;
              var _finalJson  = (_slides[_finalSlide] && _slides[_finalSlide].json) ? _slides[_finalSlide].json : _thumbRestoreJson;
              var _finalBg    = (_slides[_finalSlide] && _slides[_finalSlide].bgColor != null) ? _slides[_finalSlide].bgColor : _thumbRestoreBg;
              _canvas.loadFromJSON(_finalJson, function() {
                if (_finalBg != null) _canvas.backgroundColor = _finalBg;
                _canvas.discardActiveObject(); _canvas.requestRenderAll();
                _isRestoring = false;
                _rebuildThumbPanel();
                _renderTableLayer(_currentSlide);
              });
              return;
            }
            var qi = _thumbQ.shift();
            if (_slides[qi].thumb) { _processThumbQ(); return; } /* already generated (user navigated to it) */
            _isRestoring = true;  /* prevent saveState from overwriting _slides[_currentSlide].json during thumb render */
            _canvas.loadFromJSON(_slides[qi].json, function() {
              if (_slides[qi].bgColor == null) _slides[qi].bgColor = _canvas.backgroundColor;
              _canvas.renderAll();
              _isRestoring = false;
              _renderThumb(qi);
              _isRestoring = true;  /* re-lock before next iteration */
              _processThumbQ();
            });
          }
          setTimeout(_processThumbQ, 400);
        }
      });
    }

    var pending = imgUrls.length;
    if (pending === 0) {
      buildAllSlides();
    } else {
      imgUrls.forEach(function(url) {
        fabric.Image.fromURL(url, function(img) {
          if (img) imgCache[url] = img;
          pending--;
          if (pending === 0) buildAllSlides();
        }, { crossOrigin: 'anonymous' });
      });
    }
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 4b — Slide Management
   *
   *  Each slide stores its own JSON snapshot + thumbnail.
   *  Switching slides: save current → load next.
   *  Thumbnails are JPEG data-URLs rendered at 10% scale.
   * ════════════════════════════════════════════════════════ */

  /* Save canvas JSON + thumbnail into _slides[_currentSlide] */
  function _saveCurrentSlide() {
    if (_currentSlide < 0 || _currentSlide >= _slides.length) return;
    var s = _slides[_currentSlide];
    if (!s) return;
    s.json = _canvas.toJSON(_TOJSON_KEYS);
  }

  /* Temp Fabric objects mirroring the HTML-overlay table, so toDataURL()/thumbnails
     include it (mirrors _renderTableLayer). Caller must remove them after capture. */
  function _buildTempTableFabricObjects(index) {
    var sl = _slides[index];
    if (!sl || !sl.tableData || !sl.tableData.markdown) return [];
    var td = sl.tableData;
    var parsed = _parseMarkdownTable(td.markdown);
    if (!parsed.headers.length) return [];

    var so        = td.styleOpts || {};
    var hasHeader = so.headerRow !== false;

    /* Palette — mirror _renderTableLayer (effective palette for light themes). */
    var slideSpec = _currentSpec && _currentSpec.slides && _currentSpec.slides[index];
    var _tLay     = slideSpec ? (slideSpec.layout || '') : '';
    var basePal   = _currentSpec ? (_SPEC_THEMES[(_currentSpec.meta || {}).theme] || _SPEC_THEMES.frankfurt) : _SPEC_THEMES.frankfurt;
    var pal       = _effectivePal(_tLay, basePal);
    var accent    = pal.accent || '#4f8ef7';
    var cellText  = pal.text   || '#ffffff';
    var borderCol = so.borderColor || (pal._isLight ? 'rgba(0,0,0,0.12)' : 'rgba(255,255,255,0.12)');
    var borderW   = (so.borderWidth && so.borderWidth > 0) ? so.borderWidth : 1;
    var fontBody  = pal.fontBody || 'system-ui, sans-serif';
    var fontMono  = pal.fontMono || "'IBM Plex Mono', monospace";
    var bandBg    = pal._isLight ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.05)';

    var nCols = parsed.headers.length;
    var PADX = 18, PADY = 7, LH = 1.3;

    /* Per-column widths: honour explicit td.colWidths; distribute the rest evenly. */
    var cw = td.colWidths || {}, sumExp = 0, nExp = 0;
    for (var _wi = 0; _wi < nCols; _wi++) { if (cw[_wi]) { sumExp += cw[_wi]; nExp++; } }
    var defW = (nCols > nExp) ? Math.max(40, (td.w - sumExp) / (nCols - nExp)) : 0;
    var widths = [], xs = [td.x];
    for (var _wj = 0; _wj < nCols; _wj++) { widths.push(cw[_wj] || defW); xs.push(xs[_wj] + widths[_wj]); }

    /* Merges (basic: colspan widen origin + skip covered cells). */
    var merges = td.merges || [];
    var mergedCells = {};
    merges.forEach(function(m) {
      for (var dr = 0; dr < (m.rowspan || 1); dr++)
        for (var dc = 0; dc < (m.colspan || 1); dc++)
          if (dr || dc) mergedCells[(m.r + dr) + ',' + (m.c + dc)] = true;
    });
    function _getMerge(ri, ci) { return merges.filter(function(m) { return m.r === ri && m.c === ci; })[0] || null; }
    function _spanW(ci, cs) { var w = 0; for (var k = 0; k < cs && (ci + k) < nCols; k++) w += widths[ci + k] || 0; return w; }

    var cellStyles = td.cellStyles || {};
    var rowHeights = td.rowHeights || {};
    var objs = [];
    function _push(o) { o.set({ selectable: false, evented: false, excludeFromExport: true }); _canvas.add(o); objs.push(o); }

    var maxBottom = td.y + td.h;
    var y = td.y;

    /* Render one row of cells at top `y`. Returns row height, or -1 if it would
       overflow the table box (→ stop, mirroring the overlay's overflow clip). */
    function _renderRow(cells, ri, isHeader, explicitH) {
      var fz  = isHeader ? 18 : 20;
      var fam = isHeader ? fontMono : fontBody;
      /* First pass: build textboxes, measure natural wrapped height. */
      var built = [];
      var maxTextH = 0;
      cells.forEach(function(c, ci) {
        if (ci >= nCols || mergedCells[ri + ',' + ci]) return;
        var mg = _getMerge(ri, ci);
        var cs = mg ? (mg.colspan || 1) : 1;
        var w  = _spanW(ci, cs);
        var cst = cellStyles[ri + ',' + ci] || {};
        var raw = _stripInlineMath(c);
        var txt = isHeader ? String(raw).toUpperCase() : raw;
        var bold = isHeader ? false
          : (ci === 0 || (so.firstCol && ci === 0) || (so.lastCol && ci === nCols - 1));
        var tb = new fabric.Textbox(txt, {
          left: xs[ci] + PADX, top: 0, width: Math.max(10, w - PADX * 2),
          fontFamily: fam, fontSize: fz, fontWeight: bold ? '700' : (isHeader ? '400' : '400'),
          fill: isHeader ? accent : cellText, lineHeight: LH,
          textAlign: cst.align || 'left', charSpacing: isHeader ? 120 : 0,
          splitByGrapheme: false,
        });
        built.push({ tb: tb, ci: ci, w: w });
        if ((tb.height || 0) > maxTextH) maxTextH = tb.height || 0;
      });
      var rowH = Math.max(explicitH || 0, Math.ceil(maxTextH) + PADY * 2 + (isHeader ? 3 : 0));
      /* Clip: once at least one row/header is drawn, stop before a row that would
         overflow the table box (mirrors the overlay's overflow:hidden). */
      if (objs.length > 0 && y + rowH > maxBottom) { return -1; }
      /* Second pass: background + border rects, then the measured textboxes. */
      built.forEach(function(b) {
        var bg = null;
        if (isHeader && so.shadingColor) bg = so.shadingColor;
        else if (!isHeader && so.bandedRows !== false && (ri % 2 === 1)) bg = bandBg;
        _push(new fabric.Rect({
          left: xs[b.ci], top: y, width: b.w, height: rowH,
          fill: bg || 'transparent', stroke: borderCol, strokeWidth: borderW,
        }));
        b.tb.set('top', y + PADY);
        _push(b.tb);
      });
      /* Header underline (2px accent) — matches CSS thead th border-bottom. */
      if (isHeader) {
        _push(new fabric.Rect({ left: td.x, top: y + rowH - 2, width: td.w, height: 2, fill: accent }));
      }
      /* Total-row top border. */
      if (!isHeader && so.totalRow && ri === parsed.rows.length - 1) {
        _push(new fabric.Rect({ left: td.x, top: y, width: td.w, height: 2, fill: accent }));
      }
      return rowH;
    }

    if (hasHeader) {
      var hh = _renderRow(parsed.headers, -1, true, rowHeights[-1] || 0);
      if (hh > 0) y += hh;
    }
    for (var ri = 0; ri < parsed.rows.length; ri++) {
      var rh = _renderRow(parsed.rows[ri], ri, false, rowHeights[ri] || 0);
      if (rh < 0) break;   /* clipped — remaining rows don't fit */
      y += rh;
    }
    return objs;
  }

  /* Render thumbnail + full-res presImage for slide at index (table baked in — see above). */
  function _renderThumb(index, thumbOnly) {
    var tempObjs = [];
    /* Adding/removing objects fires object:added/object:removed, which would otherwise trigger
       saveState() and bake these temporary preview objects into the slide's real JSON. Suppress
       history/autosave for the duration of this synchronous add-capture-remove sequence. */
    var _wasRestoring = _isRestoring;
    _isRestoring = true;
    try {
      tempObjs = _buildTempTableFabricObjects(index);
      if (tempObjs.length) _canvas.renderAll();
      var zoom = _canvas.getZoom();
      /* Thumbnail (small) */
      _slides[index].thumb = _canvas.toDataURL({ multiplier: 0.20 / zoom, format: 'jpeg', quality: 0.92 });
      /* Full-res presImage — skipped on frequent live updates (thumbOnly) for speed;
         regenerated on saveState / navigation. */
      if (!thumbOnly) {
        var presMultiplier = zoom > 0 ? (1 / zoom) : 1;
        _slides[index].presImage = _canvas.toDataURL({ multiplier: presMultiplier, format: 'jpeg', quality: 0.90 });
      }
    } catch (e) { /* canvas may be empty on first render */ }
    tempObjs.forEach(function(o) { _canvas.remove(o); });
    _isRestoring = _wasRestoring;
    _canvas.renderAll();
  }

  /* Lightweight in-place update of ONE slide's thumbnail <img> — avoids the flicker
     of rebuilding the whole strip (_rebuildThumbPanel wipes innerHTML). */
  function _updateThumbImage(index) {
    var list = document.getElementById('ed-thumb-list');
    if (!list || !_slides[index]) return;
    var div = list.querySelector('.ed-thumb[data-index="' + index + '"]');
    if (!div) { _rebuildThumbPanel(); return; }
    var img = div.querySelector('img');
    if (!img) {
      img = document.createElement('img');
      img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;border-radius:3px;';
      div.insertBefore(img, div.firstChild);
    }
    if (_slides[index].thumb) img.src = _slides[index].thumb;
  }

  /* Re-draw the entire slide thumbnail strip */
  function _rebuildThumbPanel() {
    var list = document.getElementById('ed-thumb-list');
    if (!list) return;
    list.innerHTML = '';

    _slides.forEach(function (slide, i) {
      var div = document.createElement('div');
      div.className = 'ed-thumb' + (i === _currentSlide ? ' active' : '');
      div.dataset.index = i;
      div.title = 'Slide ' + (i + 1);

      if (slide.thumb) {
        var img = document.createElement('img');
        img.src = slide.thumb;
        img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;border-radius:3px;';
        div.appendChild(img);
      }

      var num = document.createElement('span');
      num.className = 'ed-thumb-num';
      num.textContent = i + 1;
      div.appendChild(num);

      /* Delete button — visible on hover */
      if (_slides.length > 1) {
        var del = document.createElement('button');
        del.className = 'ed-thumb-del';
        del.textContent = '×';
        del.title = 'Delete slide';
        del.addEventListener('click', function (e) {
          e.stopPropagation();
          deleteSlide(i);
        });
        div.appendChild(del);
      }

      div.addEventListener('click', function () { gotoSlide(i); });
      list.appendChild(div);
    });
  }

  /* Switch to a different slide */
  function _syncBgSwatch() {
    var bg  = _canvas.backgroundColor;
    var col = (typeof bg === 'string' && bg.startsWith('#')) ? bg : '#ffffff';
    var bar = document.getElementById('bar-bg-color');
    var inp = document.getElementById('inp-bg-color');
    if (bar) bar.style.background = col;
    if (inp) inp.value = col;
  }

  function gotoSlide(index) {
    if (index === _currentSlide) return;
    if (index < 0 || index >= _slides.length) return;

    /* Save & thumbnail current slide */
    _saveCurrentSlide();
    _renderThumb(_currentSlide);

    _currentSlide = index;
    var s = _slides[index];

    _isRestoring = true;
    if (s.json) {
      _canvas.loadFromJSON(s.json, function () {
        if (s.bgColor != null) _canvas.backgroundColor = s.bgColor;
        _canvas.discardActiveObject();
        _canvas.requestRenderAll();
        _isRestoring = false;
        _renderTableLayer(index);
        _syncBgSwatch();
        _syncUndoRedoBtns();
        syncRibbonToSelection();
        syncPropsPanel();
        _syncLayoutSelector();
        _rebuildThumbPanel();
        _startBlobAnimation();
      });
    } else {
      _canvas.clear();
      _canvas.backgroundColor = '#ffffff';
      _canvas.requestRenderAll();
      _isRestoring = false;
      _renderTableLayer(index);
      _syncBgSwatch();
      _syncUndoRedoBtns();
      _syncLayoutSelector();
      _rebuildThumbPanel();
    }
  }

  /* Add a blank slide after the current one */
  function addSlide() {
    _saveCurrentSlide();
    _renderThumb(_currentSlide);

    var newIndex = _currentSlide + 1;
    _slides.splice(newIndex, 0, { json: null, thumb: null, history: [], historyIdx: -1 });

    /* Build a THEMED default template (theme bg + blobs + placeholder title/bullets)
       instead of a blank white slide — mirrors the layout-picker handler. */
    var _newSpec = { layout: 'only_content', title: 'Slide title' };
    var _ph = _makePlaceholderSpec('only_content');
    Object.keys(_ph).forEach(function (k) { _newSpec[k] = _ph[k]; });
    if (_currentSpec && _currentSpec.slides) {
      _currentSpec.slides.splice(newIndex, 0, _newSpec);
    }

    _currentSlide = newIndex;
    var pal = _getPal();
    _beginBatch();
    _canvas.clear();
    _pendingTableData = null;
    _specBuildSlide(_newSpec, pal, (_currentSpec && _currentSpec.meta) || {}, _currentSlide + 1, {});
    _slides[_currentSlide].tableData = _pendingTableData;
    _slides[_currentSlide].bgColor   = _canvas.backgroundColor;
    _renderTableLayer(_currentSlide);
    _canvas.requestRenderAll();
    _endBatch(); // saveState() → snapshot the themed slide

    _renderThumb(_currentSlide);
    _rebuildThumbPanel();
  }

  /* Duplicate current slide and insert after it */
  function duplicateSlide() {
    _saveCurrentSlide();
    _renderThumb(_currentSlide);

    var src      = _slides[_currentSlide];
    var newIndex = _currentSlide + 1;
    var clone    = {
      json:      src.json ? JSON.parse(JSON.stringify(src.json)) : null,
      thumb:     src.thumb || null,
      presImage: src.presImage || null,
      bgColor:   src.bgColor || null,
      tableData: src.tableData ? JSON.parse(JSON.stringify(src.tableData)) : null,
      /* Start duplicate with a fresh single-entry history (the cloned state) */
      history:   src.json ? [JSON.stringify(src.json)] : [],
      historyIdx: src.json ? 0 : -1,
    };
    _slides.splice(newIndex, 0, clone);

    _currentSlide = newIndex;

    _isRestoring = true;
    if (clone.json) {
      _canvas.loadFromJSON(clone.json, function() {
        if (clone.bgColor != null) _canvas.backgroundColor = clone.bgColor;
        _canvas.discardActiveObject();
        _canvas.requestRenderAll();
        _isRestoring = false;
        _renderTableLayer(_currentSlide);
        _syncBgSwatch();
        _syncUndoRedoBtns();
        _rebuildThumbPanel();
      });
    } else {
      _canvas.clear();
      _canvas.backgroundColor = '#ffffff';
      _canvas.requestRenderAll();
      _isRestoring = false;
      _renderTableLayer(_currentSlide);
      _rebuildThumbPanel();
    }
  }

  /* ── Presentation Mode ──────────────────────────────────────────────────
   * Renders each slide on a dedicated live fabric.StaticCanvas (the main canvas
   * is never touched) so we can play BOTH slide transitions AND per-object
   * entrance animations (data props: anim / animOrder / animDur / animDelay). */
  var _presOverlay   = null;
  var _presIndex     = 0;
  var _presCanvas    = null;   // fabric.StaticCanvas
  var _presScale     = 1;
  var _presDir       = 1;      // navigation direction (for directional transitions)
  var _presAnimTimers = [];

  function enterPresentation() {
    _saveCurrentSlide();
    _renderThumb(_currentSlide);

    var overlay = document.createElement('div');
    overlay.id = 'ed-pres-overlay';
    overlay.style.cssText =
      'position:fixed;inset:0;z-index:99999;background:#000;' +
      'display:flex;align-items:center;justify-content:center;overflow:hidden;';

    var canvasEl = document.createElement('canvas');
    canvasEl.id = 'ed-pres-canvas';
    canvasEl.style.cssText = 'display:block;';
    overlay.appendChild(canvasEl);

    /* Slide counter (auto-hides with the cursor) */
    var counter = document.createElement('div');
    counter.id = 'ed-pres-counter';
    counter.style.cssText = 'position:fixed;right:22px;bottom:16px;z-index:100002;' +
      'font:13px/1 system-ui,sans-serif;color:rgba(255,255,255,.6);letter-spacing:.05em;' +
      'pointer-events:none;transition:opacity .3s;';
    overlay.appendChild(counter);
    overlay._counter = counter;

    /* Auto-hide cursor + counter after 2s idle (like pptx). */
    overlay._idleHide = function() {
      overlay.style.cursor = '';
      if (overlay._counter) overlay._counter.style.opacity = '1';
      clearTimeout(overlay._idleT);
      overlay._idleT = setTimeout(function() {
        overlay.style.cursor = 'none';
        if (overlay._counter) overlay._counter.style.opacity = '0';
      }, 2000);
    };
    overlay.addEventListener('mousemove', overlay._idleHide);

    document.body.appendChild(overlay);
    _presOverlay = overlay;
    _presIndex   = _currentSlide;
    overlay._idleHide();

    var sw = window.innerWidth, sh = window.innerHeight;
    _presScale = Math.min(sw / SLIDE_W, sh / SLIDE_H);
    _presCanvas = new fabric.StaticCanvas(canvasEl, {
      width:  Math.round(SLIDE_W * _presScale),
      height: Math.round(SLIDE_H * _presScale),
      renderOnAddRemove: false,
      enableRetinaScaling: true,
    });
    _presCanvas.setZoom(_presScale);

    _presDir = 1;
    _presShowSlide(_presIndex);

    if (overlay.requestFullscreen) overlay.requestFullscreen().catch(function(){});

    overlay._keyHandler = function(e) {
      if (e.key === 'Escape' || e.key === 'q') { exitPresentation(); return; }
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === ' ') {
        e.preventDefault();
        if (_presIndex < _slides.length - 1) { _presDir = 1; _presIndex++; _presShowSlide(_presIndex); }
      }
      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (_presIndex > 0) { _presDir = -1; _presIndex--; _presShowSlide(_presIndex); }
      }
    };
    document.addEventListener('keydown', overlay._keyHandler);
    overlay.addEventListener('click', function() {
      if (_presIndex < _slides.length - 1) { _presDir = 1; _presIndex++; _presShowSlide(_presIndex); }
      else exitPresentation();
    });
    overlay._resize = function() { _presResize(); };
    window.addEventListener('resize', overlay._resize);
  }

  function _presResize() {
    if (!_presCanvas || !_presOverlay) return;
    var sw = window.innerWidth, sh = window.innerHeight;
    _presScale = Math.min(sw / SLIDE_W, sh / SLIDE_H);
    _presCanvas.setDimensions({ width: Math.round(SLIDE_W * _presScale), height: Math.round(SLIDE_H * _presScale) });
    _presCanvas.setZoom(_presScale);
    _presCanvas.requestRenderAll();
    _presOverlay.querySelectorAll('.ed-pres-tbl').forEach(function(el) { el.remove(); });
    var s = _slides[_presIndex];
    if (s && s.tableData && s.tableData.markdown) _renderPresTable(_presOverlay, s.tableData);
  }

  function _presTransitionOf(index) {
    var s = _slides[index];
    if (s && s.transition) return s.transition;
    var meta = _currentSpec && _currentSpec.meta;
    return (meta && meta.transition) || 'fade';
  }

  function _presShowSlide(index) {
    if (!_presCanvas || !_presOverlay) return;
    var s = _slides[index];
    if (!s) return;

    if (_presOverlay._counter) _presOverlay._counter.textContent = (index + 1) + ' / ' + _slides.length;

    _presAnimTimers.forEach(function(t) { clearTimeout(t); });
    _presAnimTimers = [];

    var json = s.json;
    if (!json && index === _currentSlide) json = _canvas.toJSON(_TOJSON_KEYS);

    _presOverlay.querySelectorAll('.ed-pres-tbl').forEach(function(el) { el.remove(); });

    var afterLoad = function() {
      _presCanvas.getObjects().forEach(function(o) { o.selectable = false; o.evented = false; });
      _playPresSlideTransition(index);
      _animatePresObjects();
      _presCanvas.requestRenderAll();
      if (s.tableData && s.tableData.markdown) _renderPresTable(_presOverlay, s.tableData);
    };

    if (json) {
      _presCanvas.loadFromJSON(json, afterLoad);
    } else {
      _presCanvas.clear();
      _presCanvas.backgroundColor = '#ffffff';
      afterLoad();
    }
  }

  function _playPresSlideTransition(index) {
    var tr = _presTransitionOf(index);
    if (tr === 'none') return;
    var el = _presCanvas.lowerCanvasEl || document.getElementById('ed-pres-canvas');
    if (!el || !el.animate) return;
    var to = { opacity: 1, transform: 'none' };
    var from;
    switch (tr) {
      case 'slide-left':  from = { opacity: 0, transform: 'translateX(' + (_presDir >= 0 ? 8 : -8) + '%)' }; break;
      case 'slide-right': from = { opacity: 0, transform: 'translateX(' + (_presDir >= 0 ? -8 : 8) + '%)' }; break;
      case 'slide-up':    from = { opacity: 0, transform: 'translateY(8%)' }; break;
      case 'slide-down':  from = { opacity: 0, transform: 'translateY(-8%)' }; break;
      case 'zoom':        from = { opacity: 0, transform: 'scale(0.92)' }; break;
      case 'flip':        from = { opacity: 0, transform: 'perspective(1200px) rotateY(-30deg)' }; break;
      default:            from = { opacity: 0, transform: 'none' }; break; // fade
    }
    el.animate([from, to], { duration: 460, easing: 'cubic-bezier(.22,.61,.36,1)' });
  }

  function _easeOutCubic(p) { p -= 1; return p * p * p + 1; }

  function _presAnimStart(name, f) {
    name = String(name || '').replace('anim-', '');
    var s = { left: f.left, top: f.top, scaleX: f.scaleX, scaleY: f.scaleY, angle: f.angle, opacity: 0 };
    switch (name) {
      case 'fade-in':  break;
      case 'fly-left':  s.left = f.left - 400; break;
      case 'fly-right': s.left = f.left + 400; break;
      case 'fly-up':    s.top  = f.top  + 300; break;
      case 'fly-down':  s.top  = f.top  - 300; break;
      case 'zoom-in':   s.scaleX = f.scaleX * 0.3; s.scaleY = f.scaleY * 0.3; break;
      case 'zoom-out':  s.scaleX = f.scaleX * 1.7; s.scaleY = f.scaleY * 1.7; break;
      case 'rotate':    s.angle  = f.angle - 180; break;
      case 'bounce':    s.scaleX = f.scaleX * 0.4; s.scaleY = f.scaleY * 0.4; break;
      case 'flip-h':    s.scaleX = 0.001; break;
      case 'flip-v':    s.scaleY = 0.001; break;
      default:          break; // wipe / unknown → plain fade
    }
    return s;
  }

  /* One-shot preview of an object's entrance animation on the MAIN canvas.
   * Non-destructive: starts at the computed start-state and animates back to the
   * object's current (final) state. */
  function _previewObjAnim(o) {
    if (!o || !o.anim || o.anim === 'none') return;
    var finals = { left: o.left, top: o.top, scaleX: o.scaleX, scaleY: o.scaleY, angle: o.angle,
                   opacity: (o.opacity == null ? 1 : o.opacity) };
    var start = _presAnimStart(o.anim, finals);
    o.set(start);
    fabric.util.animate({
      startValue: 0, endValue: 1, duration: (parseFloat(o.animDur) || 0.5) * 1000,
      easing: function(t, b, c, d) { return _easeOutCubic(t / d); },
      onChange: function(p) {
        o.set({
          left:    start.left    + (finals.left    - start.left)    * p,
          top:     start.top     + (finals.top     - start.top)     * p,
          scaleX:  start.scaleX  + (finals.scaleX  - start.scaleX)  * p,
          scaleY:  start.scaleY  + (finals.scaleY  - start.scaleY)  * p,
          angle:   start.angle   + (finals.angle   - start.angle)   * p,
          opacity: start.opacity + (finals.opacity - start.opacity) * p,
        });
        o.setCoords();
        _canvas.requestRenderAll();
      },
      onComplete: function() { o.set(finals); o.setCoords(); _canvas.requestRenderAll(); },
    });
  }

  function _animatePresObjects() {
    if (!_presCanvas) return;
    var objs = _presCanvas.getObjects().filter(function(o) {
      return o.anim && o.anim !== 'none' && o.anim !== 'anim-none';
    });
    if (!objs.length) { _presCanvas.requestRenderAll(); return; }
    objs.sort(function(a, b) { return (a.animOrder || 0) - (b.animOrder || 0); });
    objs.forEach(function(o, i) {
      var finals = { left: o.left, top: o.top, scaleX: o.scaleX, scaleY: o.scaleY, angle: o.angle,
                     opacity: (o.opacity == null ? 1 : o.opacity) };
      var start  = _presAnimStart(o.anim, finals);
      o.set(start);           // move to start state, invisible until its turn
      var durMs   = (parseFloat(o.animDur)   || 0.5) * 1000;
      var delayMs = ((parseFloat(o.animDelay) || 0) + i * 0.18) * 1000;
      var timer = setTimeout(function() {
        fabric.util.animate({
          startValue: 0, endValue: 1, duration: durMs, easing: function(t, b, c, d) { return _easeOutCubic(t / d); },
          onChange: function(p) {
            o.set({
              left:    start.left    + (finals.left    - start.left)    * p,
              top:     start.top     + (finals.top     - start.top)     * p,
              scaleX:  start.scaleX  + (finals.scaleX  - start.scaleX)  * p,
              scaleY:  start.scaleY  + (finals.scaleY  - start.scaleY)  * p,
              angle:   start.angle   + (finals.angle   - start.angle)   * p,
              opacity: start.opacity + (finals.opacity - start.opacity) * p,
            });
            o.setCoords();
            _presCanvas.requestRenderAll();
          },
          onComplete: function() { o.set(finals); o.setCoords(); _presCanvas.requestRenderAll(); },
        });
      }, delayMs);
      _presAnimTimers.push(timer);
    });
    _presCanvas.requestRenderAll();
  }

  /* Render table as live HTML on top of the presentation overlay image */
  function _renderPresTable(overlay, td) {
    var parsed = _parseMarkdownTable(td.markdown);
    if (!parsed.headers.length) return;

    var SLIDE_W = 1920, SLIDE_H = 1080;
    var sw = overlay.clientWidth  || window.innerWidth;
    var sh = overlay.clientHeight || window.innerHeight;
    var scale = Math.min(sw / SLIDE_W, sh / SLIDE_H);
    var ox = (sw - SLIDE_W * scale) / 2;
    var oy = (sh - SLIDE_H * scale) / 2;

    /* Outer clip: occupies exactly the scaled table area on screen */
    var wrap = document.createElement('div');
    wrap.className = 'ed-pres-tbl';
    wrap.style.cssText = [
      'position:fixed;',
      'left:'   + Math.round(ox + td.x * scale) + 'px;',
      'top:'    + Math.round(oy + td.y * scale) + 'px;',
      'width:'  + Math.round(td.w * scale) + 'px;',
      'height:' + Math.round(td.h * scale) + 'px;',
      'overflow-y:auto;overflow-x:hidden;pointer-events:auto;z-index:100001;',
    ].join('');

    /* Inner: natural 1920-space width, natural (auto) height so long tables scroll. */
    var inner = document.createElement('div');
    inner.style.cssText = [
      'position:absolute;left:0;top:0;',
      'width:'  + td.w + 'px;',
      'transform:scale(' + scale + ');transform-origin:0 0;',
    ].join('');

    var styleOpts  = td.styleOpts  || {};
    /* colWidths/rowHeights/cellStyles/merges use the SAME shape & indexing convention
       the edit-mode table overlay (_renderTableLayer) writes: colWidths/rowHeights are
       dictionaries keyed by column/row index (row index -1 = header row), cellStyles is
       keyed by "ri,ci" (comma-separated, ri can be -1), merges use {r,c,rowspan,colspan}
       with the same ri/ci convention. Class list + CSS vars come from the shared
       _tblClassesAndVars helper so edit-overlay and present stay in lockstep. */
    var colWidths  = td.colWidths  || {};
    var rowHeights = td.rowHeights || {};
    var cellStyles = td.cellStyles || {};
    var merges     = td.merges     || [];

    var _presSpec = _currentSpec && _currentSpec.slides && _currentSpec.slides[_presIndex];
    var _presPal  = _effectivePal(_presSpec ? (_presSpec.layout || '') : '', _getPal());
    var _cavP     = _tblClassesAndVars(styleOpts, _presPal);

    var tbl = document.createElement('table');
    tbl.className = _cavP.cls;
    tbl.style.cssText = _cavP.styleVars
      + 'width:' + td.w + 'px;height:' + td.h + 'px;border-collapse:collapse;table-layout:fixed;';

    if (Object.keys(colWidths).length) {
      var cg = document.createElement('colgroup');
      parsed.headers.forEach(function(h, ci) {
        var col = document.createElement('col');
        if (colWidths[ci]) col.style.width = colWidths[ci] + 'px';
        cg.appendChild(col);
      });
      tbl.appendChild(cg);
    }

    /* Build covered-cell set for merge support (ri=-1 is the header row) */
    var covered = {};
    merges.forEach(function(m) {
      for (var r2 = m.r; r2 < m.r + m.rowspan; r2++) {
        for (var c2 = m.c; c2 < m.c + m.colspan; c2++) {
          if (r2 !== m.r || c2 !== m.c) covered[r2 + '_' + c2] = true;
        }
      }
    });

    function applyMerge(el, ri, ci) {
      var m = merges.filter(function(x) { return x.r === ri && x.c === ci; })[0];
      if (m) { el.rowSpan = m.rowspan; el.colSpan = m.colspan; }
    }
    function applyStyle(el, ri, ci) {
      var cs = cellStyles[ri + ',' + ci];
      if (!cs) return;
      if (cs.align)  el.style.textAlign     = cs.align;
      if (cs.valign) el.style.verticalAlign = cs.valign;
    }

    /* Header row — honour styleOpts.headerRow === false (same as the edit overlay) */
    if (styleOpts.headerRow !== false) {
      var thead = document.createElement('thead');
      var hrow  = document.createElement('tr');
      if (rowHeights[-1]) hrow.style.height = rowHeights[-1] + 'px';
      parsed.headers.forEach(function(h, ci) {
        if (covered['-1_' + ci]) return;
        var th = document.createElement('th');
        th.textContent = _stripInlineMath(h);
        applyMerge(th, -1, ci);
        applyStyle(th, -1, ci);
        hrow.appendChild(th);
      });
      thead.appendChild(hrow);
      tbl.appendChild(thead);
    }

    var tbody = document.createElement('tbody');
    parsed.rows.forEach(function(r, ri) {
      var tr = document.createElement('tr');
      if (rowHeights[ri]) tr.style.height = rowHeights[ri] + 'px';
      r.forEach(function(c, ci) {
        if (covered[ri + '_' + ci]) return;
        var td2 = document.createElement('td');
        td2.textContent = _stripInlineMath(c);
        applyMerge(td2, ri, ci);
        applyStyle(td2, ri, ci);
        tr.appendChild(td2);
      });
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);

    inner.appendChild(tbl);
    wrap.appendChild(inner);
    overlay.appendChild(wrap);
  }

  function exitPresentation() {
    if (!_presOverlay) return;
    document.removeEventListener('keydown', _presOverlay._keyHandler);
    if (_presOverlay._resize) window.removeEventListener('resize', _presOverlay._resize);
    _presAnimTimers.forEach(function(t) { clearTimeout(t); });
    _presAnimTimers = [];
    if (_presCanvas) { try { _presCanvas.dispose(); } catch (e) {} _presCanvas = null; }
    if (document.fullscreenElement) document.exitFullscreen().catch(function(){});
    _presOverlay.remove();
    _presOverlay = null;

    _clearGuides(); // clear any residual snap guide lines

    /* Restore the slide that was current when we entered */
    _isRestoring = true;
    var cur = _slides[_currentSlide];
    if (cur && cur.json) {
      _canvas.loadFromJSON(cur.json, function() {
        if (cur.bgColor != null) _canvas.backgroundColor = cur.bgColor;
        _canvas.discardActiveObject();
        _clearGuides();
        _canvas.requestRenderAll();
        _isRestoring = false;
        _renderTableLayer(_currentSlide);
        _syncBgSwatch();
        _syncUndoRedoBtns();
        syncRibbonToSelection();
        syncPropsPanel();
        _rebuildThumbPanel();
      });
    } else {
      _canvas.clear();
      _canvas.backgroundColor = '#ffffff';
      _clearGuides();
      _canvas.requestRenderAll();
      _isRestoring = false;
      _renderTableLayer(_currentSlide);
    }
  }

  /* Delete slide at index */
  function deleteSlide(index) {
    if (_slides.length <= 1) return;
    _slides.splice(index, 1);
    var newIndex = Math.min(index, _slides.length - 1);

    /* Skip saving — the deleted slide is gone; directly load newIndex */
    _currentSlide = newIndex;
    var s = _slides[newIndex];

    _isRestoring = true;
    if (s.json) {
      _canvas.loadFromJSON(s.json, function () {
        if (s.bgColor != null) _canvas.backgroundColor = s.bgColor;
        _canvas.discardActiveObject();
        _canvas.requestRenderAll();
        _isRestoring = false;
        _renderTableLayer(_currentSlide);
        _syncUndoRedoBtns();
        syncRibbonToSelection();
        syncPropsPanel();
        _rebuildThumbPanel();
      });
    } else {
      _canvas.clear();
      _canvas.backgroundColor = '#ffffff';
      _canvas.requestRenderAll();
      _isRestoring = false;
      _renderTableLayer(_currentSlide);
      _syncUndoRedoBtns();
      _rebuildThumbPanel();
    }
  }

  /* Thumbnail auto-refresh — near-instant, in-place (no full-strip rebuild / flicker).
     thumbOnly render skips the heavy presImage so it stays cheap on every keystroke. */
  function _scheduleThumbUpdate() {
    if (_isRestoring) return;   /* don't snapshot mid programmatic canvas swaps */
    clearTimeout(_scheduleThumbUpdate._t);
    _scheduleThumbUpdate._t = setTimeout(function () {
      if (_isRestoring) return;
      _renderThumb(_currentSlide, true);
      _updateThumbImage(_currentSlide);
    }, 180);
  }

  function initSlides() {
    /* Wire "Add Slide" button */
    var btn = document.getElementById('btn-add-slide');
    if (btn) btn.addEventListener('click', addSlide);

    /* Live thumbnail update on ANY change: add/remove/modify, live typing (text:changed),
       and during drag/resize/rotate (debounced) so the left preview tracks edits instantly. */
    _canvas.on('object:modified',     _scheduleThumbUpdate);
    _canvas.on('object:added',        _scheduleThumbUpdate);
    _canvas.on('object:removed',      _scheduleThumbUpdate);
    _canvas.on('text:editing:exited', _scheduleThumbUpdate);
    _canvas.on('text:changed',        _scheduleThumbUpdate);
    _canvas.on('object:moving',       _scheduleThumbUpdate);
    _canvas.on('object:scaling',      _scheduleThumbUpdate);
    _canvas.on('object:rotating',     _scheduleThumbUpdate);

    /* Type-to-edit on a selected table cell (capture phase → runs before bindKeys). */
    document.addEventListener('keydown', _tblTypeToEditKey, true);

    /* Initial render */
    _rebuildThumbPanel();
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 4b2 — Theme Panel & Effects Binding
   * ════════════════════════════════════════════════════════ */

  function initThemePanel() {
    /* ── Build theme grid ── */
    var grid = document.getElementById('theme-grid');
    if (grid) {
      grid.innerHTML = '';    /* clear first — prevents duplicates when innerHTML was captured by exportHTMLFile outerHTML */
      var themeNames = Object.keys(_SPEC_THEMES);
      themeNames.forEach(function(key) {
        var pal = _SPEC_THEMES[key];
        var stops = pal.coverGrad;
        /* Build a CSS gradient string to preview the theme */
        var gradCSS = 'linear-gradient(135deg, ' +
          stops.map(function(s) { return s.color + ' ' + Math.round(s.offset * 100) + '%'; }).join(', ') + ')';
        var btn = document.createElement('button');
        btn.className = 'ed-btn';
        btn.title = key;
        btn.style.cssText = [
          'padding:0;height:32px;border-radius:4px;overflow:hidden;',
          'background:' + gradCSS + ';',
          'border:2px solid transparent;',
          'position:relative;font-size:9px;color:rgba(255,255,255,.85);',
          'text-shadow:0 1px 2px rgba(0,0,0,.7);',
        ].join('');
        btn.textContent = key;
        btn.dataset.theme = key;
        btn.addEventListener('click', function() {
          _applyTheme(key);
        });
        grid.appendChild(btn);
      });
    }

    /* ── "↺ Theme Grad" re-apply gradient to current slide ── */
    var btnGrad = document.getElementById('btn-apply-theme-grad');
    if (btnGrad) {
      btnGrad.addEventListener('click', function() {
        if (!_currentSpec) return;
        var themeName = (_currentSpec.meta && _currentSpec.meta.theme) || 'frankfurt';
        var pal = _SPEC_THEMES[themeName] || _SPEC_THEMES.frankfurt;
        _sGradBg(pal.coverGrad);
        _canvas.requestRenderAll();
        _renderThumb(_currentSlide);
        saveState();
      });
    }

    /* ── Slide BG color picker (props panel) ── */
    var inpBgProps = document.getElementById('inp-slide-bg-props');
    if (inpBgProps) {
      inpBgProps.addEventListener('input', function() {
        setSlideBackground(inpBgProps.value);
        var bar = document.getElementById('bar-slide-bg-props');
        if (bar) bar.style.background = inpBgProps.value;
      });
    }

    /* ── Shadow checkbox ── */
    var chkShadow = document.getElementById('chk-shadow');
    if (chkShadow) {
      chkShadow.addEventListener('change', function() {
        var obj = _canvas.getActiveObject();
        if (!obj) return;
        var shadowCtrl = document.getElementById('shadow-controls');
        if (this.checked) {
          if (shadowCtrl) shadowCtrl.style.display = '';
          _applyShadow(obj);
        } else {
          if (shadowCtrl) shadowCtrl.style.display = 'none';
          obj.set('shadow', null);
          _canvas.requestRenderAll();
          saveState();
        }
      });
    }

    function _applyShadow(obj) {
      var x  = parseFloat(document.getElementById('prop-shadow-x')   && document.getElementById('prop-shadow-x').value)   || 5;
      var y  = parseFloat(document.getElementById('prop-shadow-y')   && document.getElementById('prop-shadow-y').value)   || 5;
      var bl = parseFloat(document.getElementById('prop-shadow-blur') && document.getElementById('prop-shadow-blur').value) || 10;
      var op = parseFloat(document.getElementById('prop-shadow-opacity') && document.getElementById('prop-shadow-opacity').value);
      if (isNaN(op)) op = 50;
      var col = document.getElementById('inp-shadow-color') && document.getElementById('inp-shadow-color').value || '#000000';
      var rgba = _hexToRgba(col, op / 100);
      obj.set('shadow', new fabric.Shadow({ color: rgba, offsetX: x, offsetY: y, blur: bl }));
      _canvas.requestRenderAll();
      saveState();
    }

    function _hexToRgba(hex, alpha) {
      var r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
      return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha.toFixed(2) + ')';
    }

    /* Shadow property inputs */
    ['prop-shadow-x','prop-shadow-y','prop-shadow-blur','prop-shadow-blur-num',
     'prop-shadow-opacity','prop-shadow-opacity-num','inp-shadow-color'].forEach(function(id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('input', function() {
        /* Sync slider <-> number pairs */
        if (id === 'prop-shadow-blur') {
          var nb = document.getElementById('prop-shadow-blur-num');
          if (nb && document.activeElement !== nb) nb.value = el.value;
        } else if (id === 'prop-shadow-blur-num') {
          var sl = document.getElementById('prop-shadow-blur');
          if (sl && document.activeElement !== sl) sl.value = el.value;
        } else if (id === 'prop-shadow-opacity') {
          var nb2 = document.getElementById('prop-shadow-opacity-num');
          if (nb2 && document.activeElement !== nb2) nb2.value = el.value;
        } else if (id === 'prop-shadow-opacity-num') {
          var sl2 = document.getElementById('prop-shadow-opacity');
          if (sl2 && document.activeElement !== sl2) sl2.value = el.value;
        } else if (id === 'inp-shadow-color') {
          var bar = document.getElementById('bar-shadow-color');
          if (bar) bar.style.background = el.value;
        }
        var obj = _canvas.getActiveObject();
        if (obj && document.getElementById('chk-shadow') && document.getElementById('chk-shadow').checked) {
          _applyShadow(obj);
        }
      });
    });
  }

  /* Highlight the active theme button in the theme grid */
  function _highlightThemeBtn(themeName) {
    var grid = document.getElementById('theme-grid');
    if (!grid) return;
    var pal = _SPEC_THEMES[themeName] || _SPEC_THEMES.frankfurt;
    grid.querySelectorAll('button[data-theme]').forEach(function(b) {
      b.style.borderColor = b.dataset.theme === themeName ? pal.accent : 'transparent';
    });
  }

  /* Apply a named theme — rebuilds ALL slides from stored spec */
  function _applyTheme(themeName) {
    var pal = _SPEC_THEMES[themeName];
    if (!pal) return;

    _highlightThemeBtn(themeName);

    if (_currentSpec) {
      if (!_currentSpec.meta) _currentSpec.meta = {};
      _currentSpec.meta.theme = themeName;
      /* DO NOT call saveState() here — it would mark the current slide as
         hasUserContent=true, causing the theme-merge to run for slides the
         user never edited, which preserves old theme colors on text objects.
         loadFromSlideSpec flushes the live canvas via _canvas.toJSON() itself.
         reTheme:true forces a fresh spec-rebuild with the new palette (ignoring
         baked _canvasJsons / autosave) while preserving user edits via merge. */
      loadFromSlideSpec(_currentSpec, { reTheme: true });
    } else {
      _sGradBg(pal.coverGrad);
      if (_slides[_currentSlide]) _slides[_currentSlide].bgColor = _canvas.backgroundColor;
      _canvas.requestRenderAll();
      _renderThumb(_currentSlide);
      saveState();
    }
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 4c — Object Builders
   * ════════════════════════════════════════════════════════ */

  /** Add a Textbox (word-wrapping, double-click to edit inline) */
  function addText(str, opts) {
    const obj = new fabric.Textbox(str || 'Click to edit', Object.assign({
      left:            SLIDE_W / 2 - 400,
      top:             SLIDE_H / 2 - 40,
      width:           800,
      fontSize:        60,
      fontFamily:      'Open Sans, sans-serif',
      fill:            '#1a1a2e',
      textAlign:       'left',
      editable:        true,
      splitByGrapheme: false,
    }, opts || {}));

    _canvas.add(obj);
    _canvas.setActiveObject(obj);
    _canvas.requestRenderAll();
    saveState();
    return obj;
  }

  /** Add a Rectangle */
  function addRect(opts) {
    const obj = new fabric.Rect(Object.assign({
      left:        SLIDE_W / 2 - 200,
      top:         SLIDE_H / 2 - 120,
      width:       400,
      height:      240,
      fill:        'rgba(79,142,247,0.15)',
      stroke:      '#4f8ef7',
      strokeWidth: 3,
      rx: 8, ry: 8,
    }, opts || {}));

    _canvas.add(obj);
    _canvas.setActiveObject(obj);
    _canvas.requestRenderAll();
    return obj;
  }

  function addCircle(opts) {
    const obj = new fabric.Ellipse(Object.assign({
      left:    SLIDE_W / 2 - 150,
      top:     SLIDE_H / 2 - 150,
      rx:      150,
      ry:      150,
      fill:    'rgba(79,142,247,0.15)',
      stroke:  '#4f8ef7',
      strokeWidth: 3,
    }, opts || {}));
    _canvas.add(obj);
    _canvas.setActiveObject(obj);
    _canvas.requestRenderAll();
    return obj;
  }

  function addLine(opts) {
    var x1 = SLIDE_W / 2 - 200, y1 = SLIDE_H / 2;
    var x2 = SLIDE_W / 2 + 200, y2 = SLIDE_H / 2;
    var obj = new fabric.Line([x1, y1, x2, y2], Object.assign({
      stroke: '#4f8ef7', strokeWidth: 4,
      fill: 'transparent', selectable: true,
    }, opts || {}));
    _canvas.add(obj);
    _canvas.setActiveObject(obj);
    _canvas.requestRenderAll();
    return obj;
  }

  function addArrow(opts) {
    /* Arrow built as a Path: shaft + arrowhead */
    var cx = SLIDE_W / 2, cy = SLIDE_H / 2;
    var len = 300, hw = 22, hl = 40; /* half-width, head-length */
    var path = [
      'M', cx - len / 2, cy,
      'L', cx + len / 2 - hl, cy,
      'L', cx + len / 2 - hl, cy - hw,
      'L', cx + len / 2, cy,
      'L', cx + len / 2 - hl, cy + hw,
      'L', cx + len / 2 - hl, cy,
    ].join(' ');
    var obj = new fabric.Path(path, Object.assign({
      fill: '#4f8ef7', stroke: 'transparent', strokeWidth: 0,
      editorType: 'arrow',
    }, opts || {}));
    _canvas.add(obj);
    _canvas.setActiveObject(obj);
    _canvas.requestRenderAll();
    return obj;
  }

  function addTriangle(opts) {
    var obj = new fabric.Triangle(Object.assign({
      left: SLIDE_W / 2 - 150, top: SLIDE_H / 2 - 130,
      width: 300, height: 260,
      fill: 'rgba(79,142,247,0.15)', stroke: '#4f8ef7', strokeWidth: 3,
    }, opts || {}));
    _canvas.add(obj);
    _canvas.setActiveObject(obj);
    _canvas.requestRenderAll();
    return obj;
  }

  function addStar(opts) {
    /* 5-point star via Path */
    var cx = SLIDE_W / 2, cy = SLIDE_H / 2;
    var R = 160, r = 64, n = 5;
    var pts = [];
    for (var i = 0; i < n * 2; i++) {
      var radius = (i % 2 === 0) ? R : r;
      var angle  = (Math.PI / n) * i - Math.PI / 2;
      pts.push((i === 0 ? 'M' : 'L') + ' ' + (cx + radius * Math.cos(angle)) + ' ' + (cy + radius * Math.sin(angle)));
    }
    pts.push('Z');
    var obj = new fabric.Path(pts.join(' '), Object.assign({
      fill: '#f0a500', stroke: '#c07800', strokeWidth: 2,
      editorType: 'star',
    }, opts || {}));
    _canvas.add(obj);
    _canvas.setActiveObject(obj);
    _canvas.requestRenderAll();
    return obj;
  }

  function addRoundedRect(opts) {
    var obj = new fabric.Rect(Object.assign({
      left: SLIDE_W / 2 - 200, top: SLIDE_H / 2 - 100,
      width: 400, height: 200,
      fill: 'rgba(79,142,247,0.15)', stroke: '#4f8ef7', strokeWidth: 3,
      rx: 40, ry: 40,
    }, opts || {}));
    _canvas.add(obj);
    _canvas.setActiveObject(obj);
    _canvas.requestRenderAll();
    return obj;
  }

  function addDiamond(opts) {
    var cx = SLIDE_W / 2, cy = SLIDE_H / 2;
    var w = 280, h = 220;
    var path = [
      'M', cx,       cy - h / 2,
      'L', cx + w / 2, cy,
      'L', cx,       cy + h / 2,
      'L', cx - w / 2, cy,
      'Z',
    ].join(' ');
    var obj = new fabric.Path(path, Object.assign({
      fill: 'rgba(79,142,247,0.15)', stroke: '#4f8ef7', strokeWidth: 3,
      editorType: 'diamond',
    }, opts || {}));
    _canvas.add(obj);
    _canvas.setActiveObject(obj);
    _canvas.requestRenderAll();
    return obj;
  }

  /* ── Lock / Unlock object ── */
  function lockObject(obj) {
    obj = obj || _canvas.getActiveObject();
    if (!obj) return;
    var isLocked = !!obj._edLocked;
    if (isLocked) {
      obj._edLocked = false;
      obj.set({ lockMovementX: false, lockMovementY: false,
                lockScalingX: false,  lockScalingY: false,
                lockRotation: false,  hasControls: true, hoverCursor: 'move' });
    } else {
      obj._edLocked = true;
      obj.set({ lockMovementX: true, lockMovementY: true,
                lockScalingX: true,  lockScalingY: true,
                lockRotation: true,  hasControls: false, hoverCursor: 'not-allowed' });
    }
    _canvas.requestRenderAll();
    saveState();
    _syncLockBtn(obj);
  }

  function _syncLockBtn(obj) {
    var btn = document.getElementById('btn-lock-obj');
    if (!btn) return;
    btn.textContent = (obj && obj._edLocked) ? '🔒 Unlock' : '🔓 Lock';
  }

  /* ── Group / Ungroup ── */
  function groupObjects() {
    var sel = _canvas.getActiveObject();
    if (!sel) return;
    if (sel.type === 'activeSelection') {
      var group = sel.toGroup();
      _canvas.setActiveObject(group);
      _canvas.requestRenderAll();
      saveState();
    }
  }

  function ungroupObjects() {
    var obj = _canvas.getActiveObject();
    if (!obj || obj.type !== 'group') return;
    var items = obj.toActiveSelection();
    _canvas.setActiveObject(items);
    _canvas.requestRenderAll();
    saveState();
  }

  /* ── Bullet / Numbered list quick-insert ── */
  var _BULLET_STYLES = {
    disc:    '• ',
    dash:    '– ',
    arrow:   '→ ',
    check:   '✓ ',
    diamond: '◆ ',
    circle:  '○ ',
    star:    '★ ',
    num:     null,   /* numbered: handled separately */
    alpha:   null,   /* alphabetic: a. b. c. */
    roman:   null,   /* roman: i. ii. iii. */
  };

  var _ROMAN = ['i','ii','iii','iv','v','vi','vii','viii','ix','x',
                'xi','xii','xiii','xiv','xv','xvi','xvii','xviii','xix','xx'];

  function addBulletList(items, style, opts) {
    style = style || 'disc';
    items = items || ['Item 1', 'Item 2', 'Item 3'];
    var lines = items.map(function(item, i) {
      var prefix = '';
      if (style === 'num')   prefix = (i + 1) + '. ';
      else if (style === 'alpha') prefix = String.fromCharCode(97 + i) + '. ';
      else if (style === 'roman') prefix = (_ROMAN[i] || (i + 1)) + '. ';
      else prefix = (_BULLET_STYLES[style] || '• ');
      return prefix + item;
    });
    var text = lines.join('\n');
    var obj  = new fabric.Textbox(text, Object.assign({
      left:            160,
      top:             SLIDE_H / 2 - 100,
      width:           1600,
      fontSize:        40,
      lineHeight:      1.5,
      fontFamily:      'Open Sans, sans-serif',
      fill:            '#1a1a2e',
      editorType:      'text',
      splitByGrapheme: false,
    }, opts || {}));
    _canvas.add(obj);
    _canvas.setActiveObject(obj);
    _canvas.requestRenderAll();
    saveState();
    return obj;
  }

  function _getBulletPrefix(type, idx) {
    var MAP = { disc:'• ', dash:'– ', arrow:'→ ', check:'✓ ', diamond:'◆ ', circle:'○ ', star:'★ ' };
    if (MAP[type]) return MAP[type];
    if (type === 'num')   return (idx + 1) + '. ';
    if (type === 'alpha') return String.fromCharCode(97 + idx) + '. ';
    if (type === 'roman') {
      var r = ['i','ii','iii','iv','v','vi','vii','viii','ix','x'];
      return (r[idx] || String(idx + 1)) + '. ';
    }
    return '• ';
  }
  function _stripBulletPrefix(line) {
    return line.replace(/^(•|–|→|✓|◆|○|★)\s|^(?:\d+|[a-z]|[ivxlcdm]+)\.\s/i, '');
  }
  function _toggleBullet(type) {
    var obj = _canvas.getActiveObject();
    var isTextObj = obj && (obj.type === 'textbox' || obj.type === 'i-text' || obj.type === 'text')
                        && obj.editorType !== 'deco' && obj.editorType !== 'chrome';
    if (isTextObj) {
      var lines    = (obj.text || '').split('\n');
      var stripped = lines.map(_stripBulletPrefix);
      var sameType = obj.bulletType === type;
      var newText  = sameType
        ? stripped.join('\n')
        : stripped.map(function(l, i) { return _getBulletPrefix(type, i) + l; }).join('\n');
      obj.set('bulletType', sameType ? null : type);
      obj.set('text', newText);
      _canvas.requestRenderAll(); saveState();
    } else {
      addBulletList(['First item', 'Second item', 'Third item'], type);
    }
  }
  function _changeLineSpacing(delta) {
    var obj = _canvas.getActiveObject();
    if (!obj || (obj.type !== 'textbox' && obj.type !== 'i-text')) return;
    var cur = typeof obj.lineHeight === 'number' ? obj.lineHeight : 1.2;
    obj.set('lineHeight', Math.max(0.8, Math.min(4.0, Math.round((cur + delta) * 100) / 100)));
    _canvas.requestRenderAll(); saveState();
  }
  function _setLineSpacing(val) {
    var obj = _canvas.getActiveObject();
    if (!obj || (obj.type !== 'textbox' && obj.type !== 'i-text')) return;
    obj.set('lineHeight', parseFloat(val));
    _canvas.requestRenderAll(); saveState();
  }

  /** Change the background color/CSS of the current slide */
  function setSlideBackground(color) {
    _canvas.backgroundColor = color;
    _canvas.requestRenderAll();
    /* update swatch */
    var bar = document.getElementById('bar-bg-color');
    var inp = document.getElementById('inp-bg-color');
    if (bar) bar.style.background = color;
    if (inp) inp.value = color;
    saveState();
  }

  /** Center selected object(s) on the slide — axis: 'h' | 'v' | 'both' */
  function alignOnSlide(axis) {
    var objs = _canvas.getActiveObjects();
    if (!objs.length) return;
    objs.forEach(function(obj) {
      var br = obj.getBoundingRect(true);
      if (axis === 'h' || axis === 'both') {
        obj.set('left', Math.round((SLIDE_W - br.width)  / 2 + (obj.left - br.left)));
      }
      if (axis === 'v' || axis === 'both') {
        obj.set('top',  Math.round((SLIDE_H - br.height) / 2 + (obj.top  - br.top)));
      }
      obj.setCoords();
    });
    _canvas.requestRenderAll();
    saveState();
  }

  /** Distribute 3+ selected objects evenly — axis: 'h' | 'v' */
  function distributeObjects(axis) {
    var objs = _canvas.getActiveObjects();
    if (objs.length < 3) return;

    if (axis === 'h') {
      objs.sort(function(a, b) { return a.getBoundingRect().left - b.getBoundingRect().left; });
      var first = objs[0].getBoundingRect().left;
      var last  = objs[objs.length - 1].getBoundingRect(true);
      last = last.left + last.width;
      var totalObjW = 0;
      objs.forEach(function(o) { totalObjW += o.getBoundingRect().width; });
      var gap = (last - first - totalObjW) / (objs.length - 1);
      var x = first;
      objs.forEach(function(o) {
        var br = o.getBoundingRect(true);
        o.set('left', Math.round(x + (o.left - br.left)));
        o.setCoords();
        x += br.width + gap;
      });
    } else {
      objs.sort(function(a, b) { return a.getBoundingRect().top - b.getBoundingRect().top; });
      var firstT = objs[0].getBoundingRect().top;
      var lastBR = objs[objs.length - 1].getBoundingRect(true);
      var lastT  = lastBR.top + lastBR.height;
      var totalObjH = 0;
      objs.forEach(function(o) { totalObjH += o.getBoundingRect().height; });
      var gapV = (lastT - firstT - totalObjH) / (objs.length - 1);
      var y = firstT;
      objs.forEach(function(o) {
        var br = o.getBoundingRect(true);
        o.set('top', Math.round(y + (o.top - br.top)));
        o.setCoords();
        y += br.height + gapV;
      });
    }
    _canvas.requestRenderAll();
    saveState();
  }

  /* ── Export functions ── */

  /* Snapshot the canvas to a PNG data-URL with the slide's HTML-overlay table
     baked in as temporary fabric objects (same pattern as _renderThumb).
     `mult` (optional): toDataURL multiplier. Pass 1/zoom for full 1920×1080. */
  function _canvasPNGWithTable(index, mult) {
    var tempObjs = [];
    var _wasRestoring = _isRestoring;
    _isRestoring = true;   /* suppress saveState from object:added/removed */
    var dataURL;
    try {
      tempObjs = _buildTempTableFabricObjects(index);
      if (tempObjs.length) _canvas.renderAll();
      dataURL = _canvas.toDataURL({ format: 'png', multiplier: mult || 1, quality: 1 });
    } finally {
      tempObjs.forEach(function(o) { _canvas.remove(o); });
      _isRestoring = _wasRestoring;
      _canvas.renderAll();
    }
    return dataURL;
  }

  /* Headless benchmark/eval capture: every slide → full-res PNG data-URL, static
     (no animations, blobs frozen, table baked), current slide restored. Promise<string[]>. */
  function captureAllPNG() {
    return new Promise(function(resolve) {
      _saveCurrentSlide();
      _blobAnimVer++;                       /* stop the decorative blob animation loop */
      var origIdx  = _currentSlide;
      var origJSON = _slides[origIdx] && _slides[origIdx].json ? JSON.stringify(_slides[origIdx].json) : null;
      var origBg   = _slides[origIdx] ? _slides[origIdx].bgColor : null;
      var mult     = 1 / (_canvas.getZoom() || 1);   /* → full 1920×1080 regardless of editor zoom */
      var out = [];
      var i = 0;
      function _finish() {
        if (origJSON) {
          _isRestoring = true;
          _canvas.loadFromJSON(JSON.parse(origJSON), function() {
            if (origBg != null) _canvas.backgroundColor = origBg;
            _canvas.requestRenderAll();
            _isRestoring = false;
            _currentSlide = origIdx;
            resolve(out);
          });
        } else { resolve(out); }
      }
      function _next() {
        if (i >= _slides.length) { _finish(); return; }
        var idx = i; i++;
        var slide = _slides[idx];
        if (!slide || !slide.json) { out.push(null); _next(); return; }
        _isRestoring = true;
        _canvas.loadFromJSON(slide.json, function() {
          if (slide.bgColor != null) _canvas.backgroundColor = slide.bgColor;
          _blobAnimVer++;                   /* ensure no stray blob RAF mutates this render */
          /* Re-wrap text with the (now-loaded) webfonts so soft line-breaks match the
             live canvas. Fabric wraps using whatever font is available at load time; if
             a webfont wasn't ready it would wrap with fallback metrics → extra lines →
             the captured PNG would differ from what the editor shows on screen. */
          _canvas.getObjects().forEach(function(o) {
            if ((o.type === 'textbox' || o.type === 'i-text' || o.type === 'text') &&
                typeof o.initDimensions === 'function') {
              o.initDimensions(); o.setCoords();
            }
          });
          _canvas.requestRenderAll();
          _isRestoring = false;
          _currentSlide = idx;              /* so _buildTempTableFabricObjects(idx) matches */
          /* Let layout/images settle before snapshotting the finished slide. */
          setTimeout(function() {
            out.push(_canvasPNGWithTable(idx, mult));
            _next();
          }, 120);
        });
      }
      /* Wait for webfonts before rendering any slide (correct text wrapping). */
      if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
        document.fonts.ready.then(_next, _next);
      } else {
        _next();
      }
    });
  }

  function exportCurrentPNG() {
    _saveCurrentSlide();
    var dataURL = _canvasPNGWithTable(_currentSlide);
    var a = document.createElement('a');
    a.href     = dataURL;
    a.download = 'slide-' + (_currentSlide + 1) + '.png';
    a.click();
  }

  function exportAllPNG() {
    /* Save current slide first */
    _saveCurrentSlide();
    _renderThumb(_currentSlide);

    var total    = _slides.length;
    var origIdx  = _currentSlide;
    var origJSON = _slides[origIdx] ? JSON.stringify(_slides[origIdx].json) : null;
    var i        = 0;

    function _next() {
      if (i >= total) {
        /* Restore original slide */
        if (origJSON) {
          _isRestoring = true;
          _canvas.loadFromJSON(JSON.parse(origJSON), function() {
            _canvas.requestRenderAll();
            _isRestoring = false;
          });
        }
        _currentSlide = origIdx;
        _rebuildThumbPanel();
        return;
      }
      var slide = _slides[i];
      var idx   = i;
      i++;
      if (!slide.json) { _next(); return; }
      _isRestoring = true;
      _canvas.loadFromJSON(slide.json, function() {
        _canvas.requestRenderAll();
        _isRestoring = false;
        /* Small delay so canvas finishes rendering */
        setTimeout(function() {
          var dataURL = _canvasPNGWithTable(idx);   /* bakes the slide's table overlay */
          var a = document.createElement('a');
          a.href     = dataURL;
          a.download = 'slide-' + (idx + 1) + '.png';
          a.click();
          setTimeout(_next, 200);
        }, 80);
      });
    }
    _next();
  }

  function exportJSONFile() {
    _saveCurrentSlide();
    var deck = {
      slides: _slides.map(function(s) { return s.json; }),
      tables: _slides.map(function(s) { return s.tableData || null; }),
      transitions: _slides.map(function(s) { return s.transition || null; }),
    };
    var blob = new Blob([JSON.stringify(deck, null, 2)], { type: 'application/json' });
    var a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = 'deck.json';
    a.click();
    setTimeout(function() { URL.revokeObjectURL(a.href); }, 2000);
  }

  /* Build a clean, self-contained, re-editable HTML string with all edits baked in. */
  function buildExportHTML() {
    _saveCurrentSlide();
    /* Augmented spec: original spec + per-slide canvas JSONs + table data baked in.
       On reopen, loadFromSlideSpec() detects _canvasJsons and restores edits verbatim. */
    var augSpec = {};
    if (_currentSpec) {
      augSpec.meta   = _currentSpec.meta;
      augSpec.slides = _currentSpec.slides;
    }
    augSpec._canvasJsons = _slides.map(function(s) { return s.json || null; });
    augSpec._slideTables = _slides.map(function(s) { return s.tableData || null; });
    augSpec._slideTransitions = _slides.map(function(s) { return s.transition || null; });
    var specJson = JSON.stringify(augSpec).replace(/<\/script>/gi, '<\\/script>');

    /* Use the pre-Fabric-init snapshot (clean DOM) captured in init(); the live DOM is
       mutated by Fabric.js (canvas wrappers) which breaks re-initialization. */
    var html = (typeof _RAW_PAGE_HTML === 'string' && _RAW_PAGE_HTML.length > 1000)
      ? _RAW_PAGE_HTML
      : document.documentElement.outerHTML;

    /* Splice the new spec in. Prefer explicit markers emitted by
       fabric_editor_builder.py; fall back to the legacy naive splice for old files. */
    var startM = '/*__DECK_SPEC_START__*/';
    var endM   = '/*__DECK_SPEC_END__*/';
    var si = html.indexOf(startM);
    var ei = html.indexOf(endM);
    if (si !== -1 && ei !== -1 && ei > si) {
      html = html.substring(0, si + startM.length) +
             '\nvar DECK_SPEC = ' + specJson + ';\n' +
             html.substring(ei);
    } else {
      var marker = 'var DECK_SPEC = ';
      var idx = html.indexOf(marker);
      if (idx !== -1) {
        var endScript = html.indexOf('<' + '/script>', idx);
        html = html.substring(0, idx) + marker + specJson + ';\n' + html.substring(endScript);
      }
    }
    return '<!doctype html>' + html.replace(/^<!doctype[^>]*/i, '');
  }

  function _exportTitleSlug() {
    return (_currentSpec && _currentSpec.meta && _currentSpec.meta.title)
      ? _currentSpec.meta.title.replace(/[^a-z0-9]+/gi, '_').substring(0, 50)
      : 'presentation';
  }

  /* Save = write in place (File System Access API) or download; via editor_save.js */
  function exportHTMLFile() {
    var html = buildExportHTML();
    var name = _exportTitleSlug() + '.html';
    if (window.EditorSave) {
      window.EditorSave.saveHtml(html, name);
    } else {
      var blob = new Blob([html], { type: 'text/html' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = _exportTitleSlug() + '_edited.html';
      a.click();
      setTimeout(function() { URL.revokeObjectURL(a.href); }, 2000);
    }
  }

  function importJSONFile() {
    var inp = document.getElementById('inp-json-file');
    if (!inp) return;
    inp.value = '';
    inp.onchange = function() {
      var file = inp.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function(e) {
        try {
          var deck = JSON.parse(e.target.result);
          var slides = deck.slides || (Array.isArray(deck) ? deck : [deck]);
          _slides = slides.map(function(j, i) {
            return {
              json: j, thumb: null,
              tableData:  (deck.tables      && deck.tables[i])      || null,
              transition: (deck.transitions && deck.transitions[i]) || null,
              history: j ? [JSON.stringify(j)] : [], historyIndex: j ? 0 : -1,
            };
          });
          _currentSlide = 0;
          _isRestoring = true;
          _canvas.loadFromJSON(_slides[0].json, function() {
            _canvas.discardActiveObject();
            _canvas.requestRenderAll();
            _isRestoring = false;
            _history      = _slides[0].history.slice();
            _historyIndex = _slides[0].historyIndex;
            _syncUndoRedoBtns();
            _rebuildThumbPanel();
            _renderTableLayer(0);
          });
        } catch(err) {
          console.error('[FabricEditor] JSON import failed:', err);
        }
      };
      reader.readAsText(file);
    };
    inp.click();
  }

  /** Add an Image from URL or data-URL */
  function addImage(src) {
    fabric.Image.fromURL(src, function (img) {
      if (!img || !img.width) {
        console.error('[FabricEditor] Image load failed or empty.');
        return;
      }
      const maxW = SLIDE_W * 0.5;
      const maxH = SLIDE_H * 0.5;
      const scale = Math.min(maxW / img.width, maxH / img.height, 1);
      img.set({
        left:    SLIDE_W / 2,
        top:     SLIDE_H / 2,
        originX: 'center',
        originY: 'center',
        scaleX:  scale,
        scaleY:  scale,
      });
      _canvas.add(img);
      _canvas.setActiveObject(img);
      _canvas.requestRenderAll();
    }, { crossOrigin: 'anonymous' });
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 5 — Text Style Helper
   *
   *  Applies a style property to the active object.
   *  When an IText is in editing mode, applies only to the
   *  selected character range; otherwise to the whole object.
   * ════════════════════════════════════════════════════════ */
  function _applyTextStyle(prop, value) {
    const obj = _canvas.getActiveObject();
    if (!obj) return;
    if (obj.isEditing) {
      const styles = {};
      styles[prop] = value;
      obj.setSelectionStyles(styles);
      obj.dirty = true;
    } else {
      obj.set(prop, value);
    }
    _canvas.requestRenderAll();
  }

  function _getTextStyle(prop) {
    const obj = _canvas.getActiveObject();
    if (!obj) return null;
    if (obj.isEditing) {
      const styles = obj.getSelectionStyles();
      return styles.length ? styles[0][prop] : obj[prop];
    }
    return obj[prop];
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 6 — editorCmd dispatcher
   *
   *  Single entry-point for all ribbon → canvas actions.
   *  External HTML buttons can call: window.editorCmd('bold')
   * ════════════════════════════════════════════════════════ */
  function editorCmd(cmd, value) {
    const obj = _canvas.getActiveObject();

    switch (cmd) {
      /* ── Text formatting ── */
      case 'bold':
        _applyTextStyle('fontWeight',
          _getTextStyle('fontWeight') === 'bold' ? 'normal' : 'bold');
        break;

      case 'italic':
        _applyTextStyle('fontStyle',
          _getTextStyle('fontStyle') === 'italic' ? 'normal' : 'italic');
        break;

      case 'underline':
        _applyTextStyle('underline', !_getTextStyle('underline'));
        break;

      case 'fontFamily':
        _applyTextStyle('fontFamily', value);
        break;

      case 'fontSize':
        _applyTextStyle('fontSize', parseInt(value, 10));
        break;

      case 'textColor':
        _applyTextStyle('fill', value);
        break;

      /* ── Text alignment ── */
      case 'align':
        if (obj) { obj.set('textAlign', value); _canvas.requestRenderAll(); }
        break;

      /* ── Shape styling ── */
      case 'fillColor':
        if (obj) { obj.set('fill', value); _canvas.requestRenderAll(); }
        break;

      case 'strokeColor':
        if (obj) { obj.set('stroke', value); _canvas.requestRenderAll(); }
        break;

      case 'strokeWidth':
        if (obj) { obj.set('strokeWidth', parseInt(value, 10)); _canvas.requestRenderAll(); }
        break;

      /* ── Object operations ── */
      case 'delete':
        if (obj) {
          const objs = _canvas.getActiveObjects();
          _canvas.discardActiveObject();
          objs.forEach(o => _canvas.remove(o));
          _canvas.requestRenderAll();
        }
        break;

      case 'bringForward':
        if (obj) { _canvas.bringForward(obj); _canvas.requestRenderAll(); }
        break;

      case 'sendBackward':
        if (obj) { _canvas.sendBackwards(obj); _canvas.requestRenderAll(); }
        break;

      case 'bringToFront':
        if (obj) { _canvas.bringToFront(obj); _canvas.requestRenderAll(); }
        break;

      case 'sendToBack':
        if (obj) { _canvas.sendToBack(obj); _canvas.requestRenderAll(); }
        break;

      case 'duplicate':
        if (obj) {
          obj.clone(function (clone) {
            clone.set({ left: obj.left + 20, top: obj.top + 20 });
            _canvas.add(clone);
            _canvas.setActiveObject(clone);
            _canvas.requestRenderAll();
          });
        }
        break;

      case 'group':   groupObjects();   break;
      case 'ungroup': ungroupObjects(); break;
      case 'lock':    lockObject();     break;

      default:
        console.warn('[FabricEditor] Unknown command:', cmd);
    }
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 7 — Ribbon Sync
   *
   *  When the user selects an object, update ribbon controls
   *  to reflect the object's current properties.
   * ════════════════════════════════════════════════════════ */
  var _SHAPE_TYPES_ALL = ['rect','circle','ellipse','polygon','path','triangle','line'];

  function syncRibbonToSelection() {
    const obj = _canvas.getActiveObject();
    const isText  = obj && (obj.type === 'i-text' || obj.type === 'textbox' || obj.type === 'text');
    const isShape = obj && _SHAPE_TYPES_ALL.indexOf(obj.type) !== -1;

    if (!obj) return;

    if (isText) {
      _setToggle('btn-bold',      obj.fontWeight === 'bold');
      _setToggle('btn-italic',    obj.fontStyle  === 'italic');
      _setToggle('btn-underline', !!obj.underline);

      const ff = document.getElementById('sel-font-family');
      if (ff) {
        var rawFont = obj.fontFamily || '';
        ff.value = rawFont;
        if (ff.value !== rawFont) {
          var first = rawFont.split(',')[0].trim().replace(/['"]/g, '').toLowerCase();
          for (var _oi = 0; _oi < ff.options.length; _oi++) {
            var opt = ff.options[_oi].value.split(',')[0].trim().replace(/['"]/g, '').toLowerCase();
            if (opt === first || opt.indexOf(first) === 0 || first.indexOf(opt) === 0) {
              ff.selectedIndex = _oi; break;
            }
          }
        }
      }

      const fs = document.getElementById('inp-font-size');
      if (fs) fs.value = Math.round(obj.fontSize || 60);

      if (typeof obj.fill === 'string' && obj.fill.startsWith('#')) {
        const tc = document.getElementById('inp-text-color');   if (tc) tc.value = obj.fill;
        const tb = document.getElementById('bar-text-color');   if (tb) tb.style.background = obj.fill;
      }

      _setToggle('btn-align-left',    obj.textAlign === 'left');
      _setToggle('btn-align-center',  obj.textAlign === 'center');
      _setToggle('btn-align-right',   obj.textAlign === 'right');
      _setToggle('btn-align-justify', obj.textAlign === 'justify');
    }

    if (isShape) {
      if (typeof obj.fill === 'string' && obj.fill.startsWith('#')) {
        const fc  = document.getElementById('inp-fill-color');   if (fc)  fc.value = obj.fill;
        const fcb = document.getElementById('bar-fill-color');   if (fcb) fcb.style.background = obj.fill;
      }
      if (typeof obj.stroke === 'string' && obj.stroke.startsWith('#')) {
        const sc  = document.getElementById('inp-stroke-color'); if (sc)  sc.value = obj.stroke;
        const scb = document.getElementById('bar-stroke-color'); if (scb) scb.style.background = obj.stroke;
      }
      const sw = document.getElementById('inp-stroke-width');
      if (sw) sw.value = obj.strokeWidth || 0;
    }
  }

  var _LAYOUT_LABELS_SYNC = {
    only_content: 'Bullets', two_contents_in_a_slide_layout: 'Two columns',
    two_cols_content_layout: 'Two cols bullets', image_left_layout: 'Image left',
    image_right_layout: 'Image right', image_above_layout: 'Image above',
    image_below_layout: 'Image below', comparison_layout: 'Table',
    table_above_layout: 'Table + bullets', key_points_layout: 'Key points',
    steps_horizontal_layout: 'Steps', three_cols_content_layout: 'Three columns',
    grid_2x2_layout: '2×2 Grid', conclusion_cards_layout: 'Conclusions',
    numbered_conclusions_layout: 'Numbered conclusions', agenda_layout: 'Agenda',
    stats_cards_layout: 'Stats cards', pricing_cards_layout: 'Pricing cards',
    image_fullscreen_overlay_layout: 'Image full-screen',
    two_image_left_layout: 'Two images left', two_image_right_layout: 'Two images right',
    two_image_above_layout: 'Two images above', two_image_below_layout: 'Two images below',
    data_table_layout: 'Data table', nested_bullets_layout: 'Nested bullets',
    research_question_layout: 'Research question', editorial_layout: 'Editorial',
    formula_top_layout: 'Formula (top)', formula_below_layout: 'Formula (below)',
    section_divider_layout: 'Section divider', quote_layout: 'Quote',
    config_and_greeting_slide: 'Cover', end_layout: 'End slide',
  };
  function _syncLayoutSelector() {
    if (!_currentSpec || !_currentSpec.slides) return;
    var spec = _currentSpec.slides[_currentSlide];
    var lay = (spec && spec.layout) || 'only_content';
    var lbl = document.getElementById('lbl-layout');
    if (lbl) lbl.textContent = _LAYOUT_LABELS_SYNC[lay] || lay;
    /* Also sync gradient swatches if spec has overrides */
    if (spec && spec.bgGrad) {
      var b1 = document.getElementById('bar-bg1'); var i1 = document.getElementById('inp-bg1');
      var b2 = document.getElementById('bar-bg2'); var i2 = document.getElementById('inp-bg2');
      if (spec.bgGrad[0]) { if (b1) b1.style.background = spec.bgGrad[0].color; if (i1) i1.value = spec.bgGrad[0].color; }
      if (spec.bgGrad[1]) { if (b2) b2.style.background = spec.bgGrad[1].color; if (i2) i2.value = spec.bgGrad[1].color; }
    }
  }

  function _toggleGroup(id, show) {
    const el = document.getElementById(id);
    if (el) el.style.display = show ? '' : 'none';
  }

  function _alignObj(dir) {
    var o = _canvas.getActiveObject();
    if (!o) return;
    var W = SLIDE_W, H = SLIDE_H;
    var ow = o.width  * (o.scaleX || 1);
    var oh = o.height * (o.scaleY || 1);
    if      (dir === 'left')    o.set('left', 0);
    else if (dir === 'centerH') o.set('left', (W - ow) / 2);
    else if (dir === 'right')   o.set('left', W - ow);
    else if (dir === 'top')     o.set('top', 0);
    else if (dir === 'middleV') o.set('top', (H - oh) / 2);
    else if (dir === 'bottom')  o.set('top', H - oh);
    o.setCoords();
    _canvas.requestRenderAll();
    saveState();
  }

  function _setToggle(id, active) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('on', !!active);
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 7b — Properties Panel Sync
   *  Updates right-side panel in real-time as objects move.
   * ════════════════════════════════════════════════════════ */
  function syncPropsPanel() {
    const obj    = _canvas.getActiveObject();
    const hasObj = !!obj && obj.type !== 'activeSelection';
    const isText = hasObj && (obj.type === 'i-text' || obj.type === 'textbox' || obj.type === 'text');
    const isImage = hasObj && (obj.type === 'image' || (obj.editorType === 'image' && obj.type === 'rect'));
    const isEquation = hasObj && isText && (obj.editorType === 'formula' || obj.editorType === 'equation');
    const isTable = hasObj && (obj.editorType === 'table');
    /* ActiveSelection containing table cells (whole table selected) */
    const isActiveTable = !hasObj && !!(obj && obj.type === 'activeSelection' &&
      obj._objects && obj._objects[0] && obj._objects[0].tableId);

    _show('props-empty',      !hasObj && !isActiveTable);
    _show('props-transform',   hasObj || isActiveTable);
    _show('props-typography',  isText && !isEquation);
    _show('props-image',       isImage);
    _show('props-equation',    isEquation);
    _show('props-table',       isTable || isActiveTable);
    _show('props-effects',     hasObj);
    _show('props-animation',   hasObj);
    _show('props-layer',       hasObj);

    /* Slide-level transition (independent of object selection) */
    var trSel = document.getElementById('prop-slide-transition');
    if (trSel) {
      var cur = _slides[_currentSlide];
      trSel.value = (cur && cur.transition) ? cur.transition
                  : ((_currentSpec && _currentSpec.meta && _currentSpec.meta.transition) || 'fade');
    }

    /* Image replace button visibility */
    var replaceBtn = document.getElementById('btn-replace-img');
    if (replaceBtn) replaceBtn.style.display = isImage ? 'inline-flex' : 'none';

    if (!hasObj) return;

    /* Entrance animation */
    _setProp('prop-anim',       obj.anim || 'none');
    _setProp('prop-anim-dur',   obj.animDur   != null ? obj.animDur   : 0.5);
    _setProp('prop-anim-delay', obj.animDelay != null ? obj.animDelay : 0);
    _setProp('prop-anim-order', obj.animOrder != null ? obj.animOrder : 0);

    /* Position */
    _setProp('prop-x', Math.round(obj.left));
    _setProp('prop-y', Math.round(obj.top));

    /* Size (account for scaling) */
    _setProp('prop-w', Math.round(obj.getScaledWidth  ? obj.getScaledWidth()  : obj.width  * (obj.scaleX || 1)));
    _setProp('prop-h', Math.round(obj.getScaledHeight ? obj.getScaledHeight() : obj.height * (obj.scaleY || 1)));

    /* Rotation */
    _setProp('prop-angle', Math.round(obj.angle || 0));

    /* Opacity */
    const opPct = Math.round((obj.opacity == null ? 1 : obj.opacity) * 100);
    _setProp('prop-opacity',     opPct);
    _setProp('prop-opacity-num', opPct);
    const slider = document.getElementById('prop-opacity');
    if (slider) slider.value = opPct;

    /* Typography */
    if (isText && !isEquation) {
      _setProp('prop-line-height',  (obj.lineHeight  != null ? obj.lineHeight  : 1.2).toFixed(2));
      _setProp('prop-char-spacing', Math.round(obj.charSpacing || 0));
    }

    /* Image: populate URL field */
    if (isImage) {
      var imgSrc = '';
      try { imgSrc = obj.getSrc ? obj.getSrc() : (obj._element ? obj._element.src : ''); } catch(e){}
      _setProp('prop-img-url', imgSrc || '');
    }

    /* Equation: populate textarea */
    if (isEquation) {
      var eqEl = document.getElementById('prop-equation-src');
      if (eqEl && document.activeElement !== eqEl) eqEl.value = obj.text || '';
    }

    /* Table: populate textarea */
    if (isTable) {
      var tblEl = document.getElementById('prop-table-src');
      if (tblEl && document.activeElement !== tblEl) tblEl.value = obj.text || '';
    }

    /* Shadow controls */
    if (hasObj) {
      var shadow = obj.shadow;
      var hasShadow = !!(shadow && (shadow.blur > 0 || shadow.offsetX || shadow.offsetY));
      var chkShadow = document.getElementById('chk-shadow');
      var shadowCtrl = document.getElementById('shadow-controls');
      if (chkShadow) chkShadow.checked = hasShadow;
      if (shadowCtrl) shadowCtrl.style.display = hasShadow ? '' : 'none';
      if (hasShadow && shadow) {
        _setProp('prop-shadow-x',   shadow.offsetX || 0);
        _setProp('prop-shadow-y',   shadow.offsetY || 0);
        _setProp('prop-shadow-blur', Math.round(shadow.blur || 0));
        _setProp('prop-shadow-blur-num', Math.round(shadow.blur || 0));
        var sBar = document.getElementById('bar-shadow-color');
        if (sBar) sBar.style.background = shadow.color || 'rgba(0,0,0,0.5)';
        /* Infer opacity from shadow color alpha */
        var shadowOpac = 50;
        try {
          var m = String(shadow.color || '').match(/rgba?\([^)]+,\s*([\d.]+)\)/);
          if (m) shadowOpac = Math.round(parseFloat(m[1]) * 100);
        } catch(e) {}
        _setProp('prop-shadow-opacity', shadowOpac);
        _setProp('prop-shadow-opacity-num', shadowOpac);
        var slOp = document.getElementById('prop-shadow-opacity');
        if (slOp) slOp.value = shadowOpac;
        var slBl = document.getElementById('prop-shadow-blur');
        if (slBl) slBl.value = Math.round(shadow.blur || 0);
      }
    }

    /* Lock button label */
    _syncLockBtn(obj);

    /* Group/Ungroup button label */
    var grpBtn = document.getElementById('btn-group-obj');
    if (grpBtn) {
      if (obj.type === 'group') grpBtn.textContent = '⊟ Ungroup';
      else if (obj.type === 'activeSelection') grpBtn.textContent = '⊞ Group';
      else grpBtn.textContent = '⊞ Group';
    }
  }

  function _show(id, visible) {
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? '' : 'none';
  }
  function _setProp(id, val) {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.value = val;
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 7c — Status Bar Sync
   * ════════════════════════════════════════════════════════ */
  function syncStatusBar() {
    if (!_canvas) return;
    _setText('st-zoom',    Math.round(_canvas.getZoom() * 100) + '%');
    _setText('st-objects', _canvas.getObjects().length);
  }

  function syncCursorPos(e) {
    const wrap = document.getElementById('st-pos-wrap');
    if (!wrap) return;
    if (!e) { wrap.style.display = 'none'; return; }
    const pt = _canvas.getPointer(e.e || e);
    wrap.style.display = '';
    _setText('st-x', Math.round(pt.x));
    _setText('st-y', Math.round(pt.y));
  }

  function _setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 7d — Context Menu
   * ════════════════════════════════════════════════════════ */
  function bindContextMenu() {
    const menu = document.getElementById('ed-context-menu');
    if (!menu) return;

    /* Open on right-click over canvas */
    _canvas.on('mouse:down', function (opt) {
      if (opt.e.button !== 2) { _closeCtxMenu(); return; }
      opt.e.preventDefault();
      if (!_canvas.getActiveObject()) return;
      menu.style.left = opt.e.clientX + 'px';
      menu.style.top  = opt.e.clientY + 'px';
      menu.classList.add('open');
    });

    /* Dispatch menu items */
    menu.querySelectorAll('.ed-ctx-item').forEach(function (item) {
      item.addEventListener('click', function () {
        editorCmd(item.dataset.cmd);
        _closeCtxMenu();
      });
    });

    /* Close on outside click */
    document.addEventListener('click', _closeCtxMenu);
    document.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  }

  function _closeCtxMenu() {
    const menu = document.getElementById('ed-context-menu');
    if (menu) menu.classList.remove('open');
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 8 — Keyboard Shortcuts
   * ════════════════════════════════════════════════════════ */
  function bindKeys() {
    document.addEventListener('keydown', function (e) {
      const tag = document.activeElement && document.activeElement.tagName;

      /* F5 — enter presentation mode (works everywhere) */
      if (e.key === 'F5') { e.preventDefault(); enterPresentation(); return; }

      /* Ctrl/Cmd+S — save in place (File System Access API) / download (works everywhere) */
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === 's' || e.key === 'S')) {
        e.preventDefault(); exportHTMLFile(); return;
      }

      /* Escape — close the bulk table-edit modal without applying, if it's open */
      if (e.key === 'Escape' && _editingTableSlide >= 0) {
        e.preventDefault();
        _closeTableEditor(false);
        return;
      }

      /* Don't intercept while typing in ribbon inputs */
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;

      const ctrl  = e.ctrlKey || e.metaKey;
      const shift = e.shiftKey;

      /* Undo / Redo */
      if (ctrl && !shift && e.key === 'z') { e.preventDefault(); undo(); return; }
      if (ctrl && (e.key === 'y' || (shift && e.key === 'z'))) { e.preventDefault(); redo(); return; }

      /* Text formatting */
      if (ctrl && e.key === 'b') { e.preventDefault(); editorCmd('bold'); return; }
      if (ctrl && e.key === 'i') { e.preventDefault(); editorCmd('italic'); return; }
      if (ctrl && e.key === 'u') { e.preventDefault(); editorCmd('underline'); return; }

      /* Delete / Backspace */
      if (e.key === 'Delete' || e.key === 'Backspace') {
        const active = _canvas.getActiveObject();
        if (active && !active.isEditing) { e.preventDefault(); editorCmd('delete'); return; }
      }

      /* Copy / Cut / Paste */
      if (ctrl && e.key === 'c') {
        var _co = _canvas.getActiveObject();
        if (_co && !_co.isEditing) {
          e.preventDefault();
          _co.clone(function(c) { _clipboard = c; });
        }
        return;
      }
      if (ctrl && e.key === 'x') {
        var _xo = _canvas.getActiveObject();
        if (_xo && !_xo.isEditing) {
          e.preventDefault();
          _xo.clone(function(c) { _clipboard = c; });
          _canvas.remove(_xo); _canvas.requestRenderAll(); saveState();
        }
        return;
      }
      if (ctrl && e.key === 'v') {
        var _vo = _canvas.getActiveObject();
        if (!_vo || !_vo.isEditing) {
          e.preventDefault();
          if (!_clipboard) return;
          _clipboard.clone(function(c) {
            c.set({ left: (c.left || 0) + 20, top: (c.top || 0) + 20 });
            _canvas.add(c);
            _canvas.setActiveObject(c);
            _canvas.requestRenderAll(); saveState();
          });
        }
        return;
      }

      /* Duplicate */
      if (ctrl && e.key === 'd') { e.preventDefault(); editorCmd('duplicate'); return; }
      if (ctrl && !shift && e.key === 'g') { e.preventDefault(); editorCmd('group');   return; }
      if (ctrl &&  shift && e.key === 'G') { e.preventDefault(); editorCmd('ungroup'); return; }
      if (ctrl && e.key === 'l') { e.preventDefault(); editorCmd('lock'); return; }

      /* Select All */
      if (ctrl && e.key === 'a') {
        e.preventDefault();
        var all = _canvas.getObjects().filter(function(o) { return o.selectable !== false; });
        if (all.length) _canvas.setActiveObject(
          all.length === 1 ? all[0] : new fabric.ActiveSelection(all, { canvas: _canvas })
        );
        _canvas.requestRenderAll();
        return;
      }

      /* Escape — deselect / exit text editing */
      if (e.key === 'Escape') {
        var obj = _canvas.getActiveObject();
        if (obj && obj.isEditing) { obj.exitEditing(); }
        else { _canvas.discardActiveObject(); _canvas.requestRenderAll(); }
        return;
      }

      /* Arrow nudge — move selected objects 1px (10px with Shift) */
      const ARROWS = { ArrowLeft:[-1,0], ArrowRight:[1,0], ArrowUp:[0,-1], ArrowDown:[0,1] };
      if (ARROWS[e.key]) {
        const sel = _canvas.getActiveObject();
        if (sel) {
          e.preventDefault();
          const step = shift ? 10 : 1;
          const [dx, dy] = ARROWS[e.key];
          sel.set({
            left: Math.round(sel.left + dx * step),
            top:  Math.round(sel.top  + dy * step),
          });
          sel.setCoords();
          _canvas.requestRenderAll();
          /* Save after nudge (debounced) */
          clearTimeout(_nudgeSaveTimer);
          _nudgeSaveTimer = setTimeout(saveState, 400);
        }
        return;
      }

      /* Slide navigation — PageUp / PageDown */
      if (e.key === 'PageDown') { e.preventDefault(); gotoSlide(_currentSlide + 1); return; }
      if (e.key === 'PageUp')   { e.preventDefault(); gotoSlide(_currentSlide - 1); return; }

      /* Quick-insert */
      if (e.key === 't' && !ctrl) { addText(); }
    });
  }

  var _nudgeSaveTimer = null;

  /* Replace current selected object (image or placeholder rect) with a new image from src */
  function _replaceWithImageSrc(src, opts) {
    var cur = _canvas.getActiveObject();
    if (!cur) return;
    var cLeft = cur.left, cTop = cur.top;
    var cW = cur.getScaledWidth ? cur.getScaledWidth() : cur.width * (cur.scaleX || 1);
    var cH = cur.getScaledHeight ? cur.getScaledHeight() : cur.height * (cur.scaleY || 1);
    fabric.Image.fromURL(src, function(img) {
      if (!img) return;
      img.set({
        left: cLeft, top: cTop,
        scaleX: cW / img.width, scaleY: cH / img.height,
        editorType: 'image',
      });
      _canvas.remove(cur);
      _canvas.add(img);
      _canvas.setActiveObject(img);
      _canvas.requestRenderAll();
      saveState();
      syncPropsPanel();
    }, opts || {});
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 9 — Toolbar Wire-up
   * ════════════════════════════════════════════════════════ */
  /* Helper: open a dropdown using fixed positioning so it escapes overflow:hidden ribbon */
  function _openDropdown(btnEl, menuEl) {
    var isOpen = menuEl.classList.contains('open');
    document.querySelectorAll('.ed-dropdown-menu.open').forEach(function(m) { m.classList.remove('open'); });
    if (!isOpen) {
      var r = btnEl.getBoundingClientRect();
      menuEl.style.top  = (r.bottom + 2) + 'px';
      menuEl.style.left = r.left + 'px';
      menuEl.classList.add('open');
    }
  }

  function _switchTab(name) {
    document.querySelectorAll('.ed-tab').forEach(function(b) { b.classList.remove('active'); });
    document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
    var btn   = document.querySelector('.ed-tab[data-tab="' + name + '"]');
    var panel = document.querySelector('[data-panel="' + name + '"]');
    if (btn)   btn.classList.add('active');
    if (panel) panel.classList.add('active');
  }

  function _showEl(id, visible) {
    var el = document.getElementById(id);
    if (el) el.style.display = visible ? '' : 'none';
  }

  function _syncContextualTabs() {
    var obj    = _canvas.getActiveObject();
    var noSel  = !obj || obj.type === 'activeSelection';
    var eType  = obj ? (obj.editorType || '') : '';
    var oType  = obj ? (obj.type || '') : '';
    var isTable   = eType === 'table' || eType === 'tableCell' || _selectedHtmlTableSlide >= 0;
    var isImage   = eType === 'image';
    var isFormula = eType === 'formula';
    var isShape   = !noSel && !isTable && !isImage && !isFormula &&
                    ['rect','circle','ellipse','polygon','path','triangle','line'].indexOf(oType) !== -1;
    var isText    = !noSel && !isTable && !isImage && !isFormula && !isShape &&
                    (oType === 'i-text' || oType === 'textbox' || oType === 'text');
    _showEl('ed-tab-shape',        isShape || isText);
    _showEl('ed-tab-picture',      isImage);
    _showEl('ed-tab-table-design', isTable);
    _showEl('ed-tab-table-layout', isTable);
    _showEl('ed-tab-equation',     isFormula);
    /* Toggle shape-specific vs text-specific groups in Shape Format tab */
    _showEl('fmt-grp-shape-style', isShape);
    _showEl('fmt-grp-text-style',  isText);
    _showEl('fmt-grp-wordart',     isText);
    /* If active ctx tab became hidden, revert to home */
    var activeCtx = document.querySelector('.ed-tab.ctx-tab.active');
    if (activeCtx && activeCtx.style.display === 'none') _switchTab('home');
    /* Auto-switch on selection — ONLY for object kinds whose controls live on a
       dedicated tab. For text/shape we KEEP the current tab (Home already has font,
       size, colour, align), matching PowerPoint — no jarring tab jump; the Shape
       Format tab is revealed above for the user to open manually if wanted. */
    if (isImage)   _switchTab('picture-format');
    if (isTable)   _switchTab('table-design');
    if (isFormula) _switchTab('equation-format');
    /* Populate format values */
    if (obj && !noSel) _syncFormatValues(obj);
  }

  function _syncFormatValues(obj) {
    if (!obj) return;
    var b = obj.getBoundingRect ? obj.getBoundingRect() : {};
    var h = Math.round(b.height || (obj.height || 0) * (obj.scaleY || 1));
    var w = Math.round(b.width  || (obj.width  || 0) * (obj.scaleX || 1));
    /* Shape */
    var fsi = document.getElementById('fmt-inp-shape-h'); if (fsi) fsi.value = h;
    var fwi = document.getElementById('fmt-inp-shape-w'); if (fwi) fwi.value = w;
    if (typeof obj.fill === 'string' && obj.fill.startsWith('#')) {
      var ff = document.getElementById('fmt-shape-fill'); if (ff) ff.value = obj.fill;
      var ffb = document.getElementById('bar-shape-fill'); if (ffb) ffb.style.background = obj.fill;
    }
    if (typeof obj.stroke === 'string' && obj.stroke.startsWith('#')) {
      var fo = document.getElementById('fmt-shape-outline'); if (fo) fo.value = obj.stroke;
      var fb = document.getElementById('fmt-shape-outline-bar'); if (fb) fb.style.background = obj.stroke;
    }
    var sw = document.getElementById('fmt-shape-stroke-w'); if (sw) sw.value = obj.strokeWidth || 0;
    var op = document.getElementById('fmt-shape-opacity');  if (op) op.value = Math.round((obj.opacity || 1) * 100);
    /* Angle */
    var ag = document.getElementById('fmt-inp-shape-angle'); if (ag) ag.value = Math.round(obj.angle || 0);
    /* Text-specific controls */
    var oType = obj.type || '';
    var isTxt = oType === 'textbox' || oType === 'i-text' || oType === 'text';
    if (isTxt) {
      var tc = document.getElementById('fmt-text-color');
      var tb = document.getElementById('bar-fmt-text-color');
      var fill = typeof obj.fill === 'string' ? obj.fill : '#000000';
      if (tc) tc.value = fill.length === 7 ? fill : '#000000';
      if (tb) tb.style.background = fill;
      var th = document.getElementById('fmt-text-highlight');
      var thb = document.getElementById('bar-fmt-text-highlight');
      var bg = typeof obj.backgroundColor === 'string' ? obj.backgroundColor : '#FFFF00';
      if (th) th.value = bg.length === 7 ? bg : '#FFFF00';
      if (thb) thb.style.background = bg || '#FFFF00';
      var ts = document.getElementById('fmt-text-size'); if (ts) ts.value = obj.fontSize || 60;
      var to = document.getElementById('fmt-text-opacity'); if (to) to.value = Math.round((obj.opacity || 1) * 100);
      /* Text outline */
      var toc = document.getElementById('fmt-text-outline');
      var tob = document.getElementById('bar-fmt-text-outline');
      var tosw = document.getElementById('fmt-text-stroke-w');
      var outlineColor = (typeof obj.stroke === 'string' && obj.stroke.startsWith('#')) ? obj.stroke : null;
      if (outlineColor) {
        if (toc) toc.value = outlineColor;
        if (tob) tob.style.background = outlineColor;
      }
      if (tosw) tosw.value = obj.strokeWidth || 0;
    }
    /* Picture */
    var ph = document.getElementById('fmt-inp-pic-h'); if (ph) ph.value = h;
    var pw = document.getElementById('fmt-inp-pic-w'); if (pw) pw.value = w;
    var po = document.getElementById('fmt-pic-opacity'); if (po) po.value = Math.round((obj.opacity || 1) * 100);
    /* Table size */
    var th = document.getElementById('fmt-tbl-size-h'); if (th) th.value = h;
    var tw = document.getElementById('fmt-tbl-size-w'); if (tw) tw.value = w;
    /* Equation */
    var eqs = document.getElementById('fmt-eq-size'); if (eqs) eqs.value = obj.fontSize || 60;
    if (typeof obj.fill === 'string' && obj.fill.startsWith('#')) {
      var eqc = document.getElementById('fmt-eq-color'); if (eqc) eqc.value = obj.fill;
      var eqb = document.getElementById('fmt-eq-color-bar'); if (eqb) eqb.style.background = obj.fill;
    }
  }

  /* ════════════════════════════════════════════════════
   *  RICH COLOR PICKER  (reusable, PowerPoint-style)
   * ════════════════════════════════════════════════════ */
  var _cpEl  = null;
  var _cpOpts = {};

  /* Office Default theme palette — 10 columns */
  var _CP_THEME = ['#FFFFFF','#000000','#E7E6E6','#44546A','#4472C4','#ED7D31','#A5A5A5','#FFC000','#5B9BD5','#70AD47'];
  /* 5 tint/shade rows per column (positive = mix with white, negative = mix with black) */
  var _CP_TINTS = [0.8, 0.6, 0.4, -0.25, -0.5];
  /* Standard fixed colours */
  var _CP_STD   = ['#C00000','#FF0000','#FFC000','#FFFF00','#92D050','#00B050','#00B0F0','#0070C0','#002060','#7030A0'];

  function _cpMix(hex, amt) {
    if (!hex || hex[0] !== '#' || hex.length < 7) return hex || '#000000';
    var r=parseInt(hex.slice(1,3),16), g=parseInt(hex.slice(3,5),16), b=parseInt(hex.slice(5,7),16);
    if (amt > 0) { r=Math.round(r+(255-r)*amt); g=Math.round(g+(255-g)*amt); b=Math.round(b+(255-b)*amt); }
    else          { r=Math.round(r*(1+amt));      g=Math.round(g*(1+amt));      b=Math.round(b*(1+amt)); }
    return '#'+[r,g,b].map(function(v){return Math.max(0,Math.min(255,v)).toString(16).padStart(2,'0');}).join('');
  }

  function _cpBuild() {
    if (_cpEl) return;
    var el = document.createElement('div');
    el.id = 'ed-color-picker'; el.className = 'ed-color-picker';

    /* Theme grid rows: base row + 5 tint/shade rows */
    var h = '<div class="ecp-label">Theme Colors</div><div class="ecp-grid">';
    _CP_THEME.forEach(function(c){ h+='<div class="ecp-sw" data-color="'+c+'" style="background:'+c+'" title="'+c+'"></div>'; });
    _CP_TINTS.forEach(function(t){
      _CP_THEME.forEach(function(c){ var mc=_cpMix(c,t); h+='<div class="ecp-sw" data-color="'+mc+'" style="background:'+mc+'" title="'+mc+'"></div>'; });
    });
    h += '</div>';

    /* Standard colours */
    h += '<div class="ecp-label">Standard Colors</div><div class="ecp-row">';
    _CP_STD.forEach(function(c){ h+='<div class="ecp-sw" data-color="'+c+'" style="background:'+c+'" title="'+c+'"></div>'; });
    h += '</div>';

    /* Actions */
    h += '<div class="ecp-sep"></div>'
       + '<div class="ecp-option" id="ecp-nofill" style="display:none"><span class="ecp-option-icon">⊘</span>No Fill</div>'
       + '<div class="ecp-option" id="ecp-more"><span class="ecp-option-icon">⊕</span>More Colors…</div>'
       + '<div class="ecp-option" id="ecp-grad-toggle" style="display:none"><span class="ecp-option-icon">⬜</span>Gradient ›</div>'
       + '<div class="ecp-grad-sub" id="ecp-grad-sub" style="display:none">'
       +   '<div class="ecp-option ecp-grad-opt" data-grad="lr">→ Linear (horizontal)</div>'
       +   '<div class="ecp-option ecp-grad-opt" data-grad="tb">↓ Linear (vertical)</div>'
       +   '<div class="ecp-option ecp-grad-opt" data-grad="diag">↘ Diagonal</div>'
       +   '<div class="ecp-option ecp-grad-opt" data-grad="radial">◉ Radial</div>'
       + '</div>'
       + '<input type="color" id="ecp-more-inp" style="display:none">';

    el.innerHTML = h;
    document.body.appendChild(el);
    _cpEl = el;

    /* Swatch clicks → solid color */
    el.querySelectorAll('.ecp-sw').forEach(function(sw) {
      sw.addEventListener('click', function(e) {
        e.stopPropagation();
        if (_cpOpts.onColor) _cpOpts.onColor(sw.dataset.color);
        _cpClose();
      });
    });
    /* No Fill */
    document.getElementById('ecp-nofill').addEventListener('click', function(e) {
      e.stopPropagation();
      if (_cpOpts.onNoFill) _cpOpts.onNoFill();
      _cpClose();
    });
    /* More Colors → native picker */
    document.getElementById('ecp-more').addEventListener('click', function(e) {
      e.stopPropagation();
      var inp = document.getElementById('ecp-more-inp');
      inp.value = _cpOpts.currentColor || '#ffffff';
      inp.onchange = function(){ if (_cpOpts.onColor) _cpOpts.onColor(inp.value); _cpClose(); };
      inp.click();
    });
    /* Gradient toggle */
    document.getElementById('ecp-grad-toggle').addEventListener('click', function(e) {
      e.stopPropagation();
      var sub = document.getElementById('ecp-grad-sub');
      sub.style.display = sub.style.display === 'none' ? 'block' : 'none';
    });
    /* Gradient presets */
    el.querySelectorAll('.ecp-grad-opt').forEach(function(opt) {
      opt.addEventListener('click', function(e) {
        e.stopPropagation();
        if (_cpOpts.onGradient) _cpOpts.onGradient(opt.dataset.grad);
        _cpClose();
      });
    });
    /* Close on outside mousedown */
    document.addEventListener('mousedown', function(e) {
      if (_cpEl && _cpEl.classList.contains('open') && !_cpEl.contains(e.target)) _cpClose();
    }, true);
  }

  function _cpShow(anchorEl, opts) {
    _cpBuild();
    _cpOpts = opts || {};
    /* Toggle visibility of optional rows */
    var nf = document.getElementById('ecp-nofill');
    var gt = document.getElementById('ecp-grad-toggle');
    var gs = document.getElementById('ecp-grad-sub');
    if (nf) nf.style.display  = _cpOpts.noFill    ? '' : 'none';
    if (gt) gt.style.display  = _cpOpts.gradient  ? '' : 'none';
    if (gs) gs.style.display  = 'none';
    /* Viewport-safe positioning */
    var r = anchorEl.getBoundingClientRect();
    var left = r.left, top = r.bottom + 2;
    if (left + 210 > window.innerWidth)  left = window.innerWidth  - 214;
    if (top  + 340 > window.innerHeight) top  = r.top - 340;
    _cpEl.style.left = left + 'px';
    _cpEl.style.top  = top  + 'px';
    /* Close other dropdowns */
    document.querySelectorAll('.ed-dropdown-menu.open').forEach(function(m){ m.classList.remove('open'); });
    _cpEl.classList.add('open');
  }
  function _cpClose() { if (_cpEl) _cpEl.classList.remove('open'); }

  /* Apply a gradient preset fill to the active object */
  function _applyGradFill(gradType) {
    var obj = _canvas.getActiveObject();
    if (!obj) return;
    var c1 = '#4472C4', c2 = '#1a1a2e';
    if (typeof obj.fill === 'string' && obj.fill.startsWith('#')) c1 = obj.fill;
    var ow = obj.width || 200, oh = obj.height || 200;
    var coords, type = 'linear';
    if      (gradType === 'lr')    { coords = { x1:0, y1:0,   x2:ow, y2:0  }; }
    else if (gradType === 'tb')    { coords = { x1:0, y1:0,   x2:0,  y2:oh }; }
    else if (gradType === 'diag')  { coords = { x1:0, y1:0,   x2:ow, y2:oh }; }
    else { type='radial'; coords = { r1:0, r2:Math.max(ow,oh)/2, x1:ow/2, y1:oh/2, x2:ow/2, y2:oh/2 }; }
    obj.set('fill', new fabric.Gradient({ type:type, gradientUnits:'pixels', coords:coords,
      colorStops:[{ offset:0, color:c1 },{ offset:1, color:c2 }] }));
    _canvas.requestRenderAll(); saveState();
  }

  /* Wire a hidden <input type="color"> inside its parent label to show the rich picker instead.
     opts: { barId, noFill, gradient, onColor, onNoFill, onGradient, getColor } */
  function _wireCP(inputId, opts) {
    var inp = document.getElementById(inputId);
    if (!inp) return;
    var anchor = inp.closest('label') || inp.closest('[data-cp-anchor]') || inp.parentElement;
    if (!anchor) return;
    anchor.addEventListener('click', function(e) {
      e.preventDefault();
      e.stopPropagation();
      var curColor = (opts.getColor ? opts.getColor() : null) || inp.value || '#ffffff';
      _cpShow(anchor, {
        currentColor: curColor,
        noFill:    !!opts.noFill,
        gradient:  !!opts.gradient,
        onColor: function(c) {
          inp.value = c;
          if (opts.barId) { var b=document.getElementById(opts.barId); if(b) b.style.background=c; }
          if (opts.onColor) opts.onColor(c);
        },
        onNoFill: opts.onNoFill || null,
        onGradient: opts.onGradient || null,
      });
    });
  }

  function bindToolbar() {
    /* Tab switching */
    document.querySelectorAll('.ed-tab').forEach(function(btn) {
      btn.addEventListener('click', function() { _switchTab(btn.dataset.tab); });
    });

    /* Close all dropdowns when clicking outside */
    document.addEventListener('click', function() {
      document.querySelectorAll('.ed-dropdown-menu.open').forEach(function(m) { m.classList.remove('open'); });
    });

    /* Layout custom dropdown */
    var _LAYOUT_LABELS = {
      only_content: 'Bullets', two_contents_in_a_slide_layout: 'Two columns',
      two_cols_content_layout: 'Two cols bullets', image_left_layout: 'Image left',
      image_right_layout: 'Image right', image_above_layout: 'Image above',
      image_below_layout: 'Image below', comparison_layout: 'Table',
      table_above_layout: 'Table + bullets', key_points_layout: 'Key points',
      steps_horizontal_layout: 'Steps', three_cols_content_layout: 'Three columns',
      grid_2x2_layout: '2×2 Grid', conclusion_cards_layout: 'Conclusions',
      numbered_conclusions_layout: 'Numbered conclusions', agenda_layout: 'Agenda',
      stats_cards_layout: 'Stats cards', pricing_cards_layout: 'Pricing cards',
      image_fullscreen_overlay_layout: 'Image full-screen',
      two_image_left_layout: 'Two images left', two_image_right_layout: 'Two images right',
      two_image_above_layout: 'Two images above', two_image_below_layout: 'Two images below',
      data_table_layout: 'Data table', nested_bullets_layout: 'Nested bullets',
      research_question_layout: 'Research question', editorial_layout: 'Editorial',
      formula_top_layout: 'Formula (top)', formula_below_layout: 'Formula (below)',
      section_divider_layout: 'Section divider', quote_layout: 'Quote',
      config_and_greeting_slide: 'Cover', end_layout: 'End slide',
    };
    var ddLayoutBtn  = document.getElementById('btn-layout-toggle');
    var ddLayoutMenu = document.getElementById('dd-layout-menu');
    if (ddLayoutBtn && ddLayoutMenu) {
      ddLayoutBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        _openDropdown(ddLayoutBtn, ddLayoutMenu);
      });
      ddLayoutMenu.querySelectorAll('[data-layout]').forEach(function(item) {
        item.addEventListener('click', function(e) {
          e.stopPropagation();
          ddLayoutMenu.classList.remove('open');
          if (_currentSlide < 0 || !_currentSpec || !_currentSpec.slides) return;
          var spec = _currentSpec.slides[_currentSlide];
          if (!spec) return;
          var newLay = item.dataset.layout;
          spec.layout = newLay;
          if (_isSpecBlank(spec)) {
            /* Blank slide: inject placeholder skeleton so the layout is visible */
            var _ph = _makePlaceholderSpec(newLay);
            Object.keys(_ph).forEach(function(k) { spec[k] = _ph[k]; });
            if (!spec.title) spec.title = 'Slide title';   /* visible heading on the template */
          } else {
            /* Existing content: synthesize layout-specific fields from spec.content,
               then fall back to placeholder text for any field the new layout still
               needs but didn't get (covers every layout, not just the synthesized ones) */
            _synthesiseLayoutData(spec, newLay);
            var _phFallback = _makePlaceholderSpec(newLay);
            Object.keys(_phFallback).forEach(function(k) {
              var cur = spec[k];
              var empty = !cur || (Array.isArray(cur) && cur.length === 0) || (typeof cur === 'string' && !cur.trim());
              if (empty) spec[k] = _phFallback[k];
            });
          }
          var lbl = document.getElementById('lbl-layout');
          if (lbl) lbl.textContent = _LAYOUT_LABELS[newLay] || newLay;
          var pal = _getPal();
          _beginBatch();
          _canvas.clear();
          _pendingTableData = null;
          _specBuildSlide(spec, pal, _currentSpec.meta, _currentSlide + 1, {});
          _slides[_currentSlide].tableData = _pendingTableData;
          _renderTableLayer(_currentSlide);
          _canvas.requestRenderAll();
          _endBatch();
        });
      });
    }

    /* Zoom */
    _btn('btn-zoom-in',  zoomIn);
    _btn('btn-zoom-out', zoomOut);
    _btn('btn-zoom-fit', () => autoScale(computeScale()));

    /* History */
    _btn('btn-undo', undo);
    _btn('btn-redo', redo);

    /* Clipboard: Cut / Copy / Paste */
    _btn('btn-cut', function() {
      var obj = _canvas.getActiveObject();
      if (!obj) return;
      _clipboard = obj.toObject ? obj.toObject() : null;
      _canvas.remove(obj);
      _canvas.discardActiveObject();
      _canvas.requestRenderAll();
      saveState();
    });
    _btn('btn-copy', function() {
      var obj = _canvas.getActiveObject();
      if (!obj) return;
      obj.clone(function(c) { _clipboard = c; });
    });
    _btn('btn-paste', function() {
      if (!_clipboard) return;
      _clipboard.clone(function(c) {
        c.set({ left: c.left + 30, top: c.top + 30, evented: true });
        if (c.type === 'activeSelection') {
          c.canvas = _canvas;
          c.forEachObject(function(o) { _canvas.add(o); });
          c.setCoords();
        } else {
          _canvas.add(c);
        }
        _canvas.setActiveObject(c);
        _canvas.requestRenderAll();
        saveState();
      });
    });

    /* Insert — text & image (Home + Insert tabs share same handlers) */
    _btn('btn-add-text',  () => addText());
    _btn('btn-ins-text',  () => addText());
    _btn('btn-add-img',   () => document.getElementById('inp-img-file').click());
    _btn('btn-ins-img',   () => document.getElementById('inp-img-file').click());

    /* Home tab quick-access shape buttons */
    _btn('btn-home-rect',     () => addRect());
    _btn('btn-home-circle',   () => addCircle());
    _btn('btn-home-triangle', () => addTriangle());
    _btn('btn-home-line',     () => addLine());
    _btn('btn-home-arrow',    () => addArrow());

    /* Shape dropdown (Home tab) */
    var ddShapesBtn  = document.getElementById('btn-shapes-toggle');
    var ddShapesMenu = document.getElementById('dd-shapes-menu');
    if (ddShapesBtn && ddShapesMenu) {
      ddShapesBtn.addEventListener('click', function(e) { e.stopPropagation(); _openDropdown(ddShapesBtn, ddShapesMenu); });
    }
    _btn('btn-add-rect',     () => { addRect();        ddShapesMenu && ddShapesMenu.classList.remove('open'); });
    _btn('btn-add-rounded',  () => { addRoundedRect(); ddShapesMenu && ddShapesMenu.classList.remove('open'); });
    _btn('btn-add-circle',   () => { addCircle();      ddShapesMenu && ddShapesMenu.classList.remove('open'); });
    _btn('btn-add-triangle', () => { addTriangle();    ddShapesMenu && ddShapesMenu.classList.remove('open'); });
    _btn('btn-add-diamond',  () => { addDiamond();     ddShapesMenu && ddShapesMenu.classList.remove('open'); });
    _btn('btn-add-star',     () => { addStar();        ddShapesMenu && ddShapesMenu.classList.remove('open'); });
    _btn('btn-add-line',     () => { addLine();        ddShapesMenu && ddShapesMenu.classList.remove('open'); });
    _btn('btn-add-arrow',    () => { addArrow();       ddShapesMenu && ddShapesMenu.classList.remove('open'); });

    /* Insert tab shapes dropdown */
    var ddInsShapesBtn  = document.getElementById('btn-ins-shapes-toggle');
    var ddInsShapesMenu = document.getElementById('dd-ins-shapes-menu');
    if (ddInsShapesBtn && ddInsShapesMenu) {
      ddInsShapesBtn.addEventListener('click', function(e) { e.stopPropagation(); _openDropdown(ddInsShapesBtn, ddInsShapesMenu); });
    }
    var _insShapeMap = { rect: addRect, rounded: addRoundedRect, circle: addCircle,
                         triangle: addTriangle, diamond: addDiamond, star: addStar,
                         line: addLine, arrow: addArrow };
    ddInsShapesMenu && ddInsShapesMenu.querySelectorAll('[data-ins]').forEach(function(item) {
      item.addEventListener('click', function(e) {
        e.stopPropagation();
        var fn = _insShapeMap[item.dataset.ins];
        if (fn) fn();
        ddInsShapesMenu.classList.remove('open');
      });
    });

    /* Bullet split-button (Home tab) */
    _btn('btn-bullet-main', function() { _toggleBullet('disc'); });
    _btn('btn-bullet-dd', function(e) {
      e.stopPropagation();
      _openDropdown(document.getElementById('btn-bullet-dd'), document.getElementById('dd-bullet-menu'));
    });
    var ddBulletMenu = document.getElementById('dd-bullet-menu');
    ddBulletMenu && ddBulletMenu.querySelectorAll('[data-bullet]').forEach(function(item) {
      item.addEventListener('click', function(e) {
        e.stopPropagation(); _toggleBullet(item.dataset.bullet); ddBulletMenu.classList.remove('open');
      });
    });
    /* Numbering split-button (Home tab) */
    _btn('btn-num-main', function() { _toggleBullet('num'); });
    _btn('btn-num-dd', function(e) {
      e.stopPropagation();
      _openDropdown(document.getElementById('btn-num-dd'), document.getElementById('dd-num-menu'));
    });
    var ddNumMenu = document.getElementById('dd-num-menu');
    ddNumMenu && ddNumMenu.querySelectorAll('[data-num]').forEach(function(item) {
      item.addEventListener('click', function(e) {
        e.stopPropagation(); _toggleBullet(item.dataset.num); ddNumMenu.classList.remove('open');
      });
    });
    /* Line spacing split-button (Home tab) */
    _btn('btn-ls-main', function(e) { e.stopPropagation(); _openDropdown(document.getElementById('btn-ls-main'), document.getElementById('dd-ls-menu')); });
    _btn('btn-ls-dd',   function(e) { e.stopPropagation(); _openDropdown(document.getElementById('btn-ls-dd'),   document.getElementById('dd-ls-menu')); });
    var ddLsMenu = document.getElementById('dd-ls-menu');
    ddLsMenu && ddLsMenu.querySelectorAll('[data-ls]').forEach(function(item) {
      item.addEventListener('click', function(e) {
        e.stopPropagation(); _setLineSpacing(item.dataset.ls); ddLsMenu.classList.remove('open');
      });
    });

    /* Insert tab lists dropdown */
    var ddInsListsBtn  = document.getElementById('btn-ins-lists-toggle');
    var ddInsListsMenu = document.getElementById('dd-ins-lists-menu');
    if (ddInsListsBtn && ddInsListsMenu) {
      ddInsListsBtn.addEventListener('click', function(e) { e.stopPropagation(); _openDropdown(ddInsListsBtn, ddInsListsMenu); });
    }
    ddInsListsMenu && ddInsListsMenu.querySelectorAll('[data-list]').forEach(function(item) {
      item.addEventListener('click', function(e) {
        e.stopPropagation();
        addBulletList(['Item 1', 'Item 2', 'Item 3'], item.dataset.list);
        ddInsListsMenu.classList.remove('open');
      });
    });

    /* File input → addImage (or replace placeholder if pending) */
    const fileInp = document.getElementById('inp-img-file');
    if (fileInp) {
      fileInp.addEventListener('change', function () {
        const file = fileInp.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = function (ev) {
          const ph = _pendingImagePlaceholder;
          _pendingImagePlaceholder = null;
          if (ph) {
            /* Replace placeholder rect + its "Click to add image" label */
            var phW = ph.width * (ph.scaleX || 1);
            var phH = ph.height * (ph.scaleY || 1);
            var toRemove = _canvas.getObjects().filter(function(o) {
              if (o === ph) return true;
              if (o.editorType === 'deco' && o.text === 'Click to add image') {
                return o.left >= ph.left - 2 && o.top >= ph.top - 2 &&
                       o.left <= ph.left + phW + 2 && o.top <= ph.top + phH + 2;
              }
              return false;
            });
            toRemove.forEach(function(o) { _canvas.remove(o); });
            fabric.Image.fromURL(ev.target.result, function(img) {
              var scale = Math.min(phW / img.width, phH / img.height);
              img.set({
                left: ph.left + (phW - img.width * scale) / 2,
                top:  ph.top  + (phH - img.height * scale) / 2,
                scaleX: scale, scaleY: scale, editorType: 'image',
              });
              _canvas.add(img);
              _canvas.setActiveObject(img);
              _canvas.requestRenderAll();
              saveState();
            });
          } else {
            addImage(ev.target.result);
          }
        };
        reader.readAsDataURL(file);
        fileInp.value = '';
      });
    }

    /* ── Insert tab extra buttons ── */
    _btn('btn-slide-new-ins', function() { addSlide(); });
    /* ── Insert Table: PowerPoint-style R×C grid picker ── */
    (function () {
      var GCOLS = 8, GROWS = 10;
      var ddBtn  = document.getElementById('btn-ins-table');
      var ddMenu = document.getElementById('dd-ins-table-menu');
      var grid   = document.getElementById('tbl-grid');
      var label  = document.getElementById('tbl-grid-label');
      if (!ddBtn || !ddMenu || !grid) return;
      var cells = [];
      function _hl(c, r) {   /* highlight top-left c×r block */
        cells.forEach(function (el) {
          var on = el._c <= c && el._r <= r;
          el.classList.toggle('on', on);
        });
        if (label) label.textContent = (c && r) ? (c + ' × ' + r + ' Table') : 'Insert table';
      }
      for (var r = 1; r <= GROWS; r++) {
        for (var c = 1; c <= GCOLS; c++) {
          var cell = document.createElement('div');
          cell.className = 'ed-tbl-grid-cell';
          cell._c = c; cell._r = r;
          cell.addEventListener('mouseenter', (function (cc, rr) { return function () { _hl(cc, rr); }; })(c, r));
          cell.addEventListener('click', (function (cc, rr) {
            return function (e) { e.stopPropagation(); ddMenu.classList.remove('open'); _insertTableGrid(cc, rr); };
          })(c, r));
          grid.appendChild(cell);
          cells.push(cell);
        }
      }
      grid.addEventListener('mouseleave', function () { _hl(0, 0); });
      ddBtn.addEventListener('click', function (e) { e.stopPropagation(); _hl(0, 0); _openDropdown(ddBtn, ddMenu); });
    })();
    _btn('btn-table-apply',   function() { _closeTableEditor(true); });
    _btn('btn-table-cancel',  function() { _closeTableEditor(false); });
    _btn('btn-table-add-row', _tableAddRow);
    _btn('btn-table-del-row', _tableDelRow);
    _btn('btn-table-add-col', _tableAddCol);
    _btn('btn-table-del-col', _tableDelCol);
    var _tblBackdrop = document.getElementById('ed-table-modal-backdrop');
    if (_tblBackdrop) _tblBackdrop.addEventListener('click', function() { _closeTableEditor(false); });

    /* ── Ribbon: Table Layout → rows / cols (selection-aware, see _tblAnchorCell) ── */
    _btn('fmt-tbl-row-above', function() { _tblHtmlAddRow(true);  });
    _btn('fmt-tbl-row-below', function() { _tblHtmlAddRow(false); });
    _btn('fmt-tbl-del',       function() { _tblHtmlDelRow();      });
    _btn('fmt-tbl-del-col',   function() { _tblHtmlDelCol();      });
    _btn('fmt-tbl-col-left',  function() { _tblHtmlAddCol(true);  });
    _btn('fmt-tbl-col-right', function() { _tblHtmlAddCol(false); });

    /* ── Ribbon: Table Layout → table size ── */
    function _tblResizeRestore(i) {
      document.querySelectorAll('.slide-table-wrap').forEach(function(el) { el.classList.remove('selected'); });
      var nw = document.querySelector('.slide-table-wrap[data-slide-idx="' + i + '"]');
      if (nw) nw.classList.add('selected');
    }
    _oncommit('fmt-tbl-size-h', function(v) {
      var i = _selectedHtmlTableSlide;
      if (i < 0 || !_slides[i] || !_slides[i].tableData) return;
      var n = parseInt(v, 10); if (!n || n < 20) return;
      _slides[i].tableData.h = n;
      _slides[i].hasUserContent = true;
      _renderTableLayer(i);
      if (i === _currentSlide) saveState();
      _tblResizeRestore(i);
    });
    _oncommit('fmt-tbl-size-w', function(v) {
      var i = _selectedHtmlTableSlide;
      if (i < 0 || !_slides[i] || !_slides[i].tableData) return;
      var n = parseInt(v, 10); if (!n || n < 20) return;
      _slides[i].tableData.w = n;
      _slides[i].hasUserContent = true;
      _renderTableLayer(i);
      if (i === _currentSlide) saveState();
      _tblResizeRestore(i);
    });

    /* ── Ribbon: Table Design → style checkboxes ── */
    function _tblHtmlStyleToggle(soKey, cbId) {
      var cb = document.getElementById(cbId);
      if (!cb) return;
      cb.addEventListener('change', function() {
        var i = _selectedHtmlTableSlide;
        if (i < 0 || !_slides[i] || !_slides[i].tableData) return;
        if (!_slides[i].tableData.styleOpts) _slides[i].tableData.styleOpts = {};
        _slides[i].tableData.styleOpts[soKey] = cb.checked;
        _slides[i].hasUserContent = true;
        _renderTableLayer(i);
        if (i === _currentSlide) saveState();
        _tblResizeRestore(i);
      });
    }
    _tblHtmlStyleToggle('headerRow',  'fmt-tbl-header-row');
    _tblHtmlStyleToggle('totalRow',   'fmt-tbl-total-row');
    _tblHtmlStyleToggle('bandedRows', 'fmt-tbl-banded-rows');
    _tblHtmlStyleToggle('firstCol',   'fmt-tbl-first-col');
    _tblHtmlStyleToggle('lastCol',    'fmt-tbl-last-col');
    _tblHtmlStyleToggle('bandedCols', 'fmt-tbl-banded-cols');

    /* ── Ribbon: Table Layout → Cell H / W ── */
    _oncommit('fmt-tbl-cell-h', function(v) {
      var i = _selectedHtmlTableSlide;
      if (i < 0 || !_slides[i] || !_slides[i].tableData) return;
      var n = parseInt(v, 10) || 0;
      _tblSel.cells.forEach(function(c) {
        if (n > 0) _slides[i].tableData.rowHeights[c.ri] = n;
        else delete _slides[i].tableData.rowHeights[c.ri];
      });
      _slides[i].hasUserContent = true;
      _renderTableLayer(i);
      if (i === _currentSlide) saveState();
      _tblResizeRestore(i);
    });
    _oncommit('fmt-tbl-cell-w', function(v) {
      var i = _selectedHtmlTableSlide;
      if (i < 0 || !_slides[i] || !_slides[i].tableData) return;
      var n = parseInt(v, 10) || 0;
      _tblSel.cells.forEach(function(c) {
        if (n > 0) _slides[i].tableData.colWidths[c.ci] = n;
        else delete _slides[i].tableData.colWidths[c.ci];
      });
      _slides[i].hasUserContent = true;
      _renderTableLayer(i);
      if (i === _currentSlide) saveState();
      _tblResizeRestore(i);
    });

    /* ── Ribbon: Table Layout → Alignment ── */
    function _tblAlignSelected(align, valign) {
      var i = _selectedHtmlTableSlide;
      if (i < 0 || !_slides[i] || !_slides[i].tableData) return;
      if (!_slides[i].tableData.cellStyles) _slides[i].tableData.cellStyles = {};
      _tblSel.cells.forEach(function(c) {
        var key = c.ri + ',' + c.ci;
        var ex = _slides[i].tableData.cellStyles[key] || {};
        _slides[i].tableData.cellStyles[key] = { align: align || ex.align, valign: valign || ex.valign };
      });
      _slides[i].hasUserContent = true;
      _renderTableLayer(i);
      if (i === _currentSlide) saveState();
      _tblResizeRestore(i);
    }
    _btn('fmt-tbl-align-tl', function() { _tblAlignSelected('left',   'top');    });
    _btn('fmt-tbl-align-tc', function() { _tblAlignSelected('center', 'top');    });
    _btn('fmt-tbl-align-tr', function() { _tblAlignSelected('right',  'top');    });
    _btn('fmt-tbl-align-ml', function() { _tblAlignSelected('left',   'middle'); });
    _btn('fmt-tbl-align-mc', function() { _tblAlignSelected('center', 'middle'); });
    _btn('fmt-tbl-align-mr', function() { _tblAlignSelected('right',  'middle'); });
    _btn('fmt-tbl-align-bl', function() { _tblAlignSelected('left',   'bottom'); });
    _btn('fmt-tbl-align-bc', function() { _tblAlignSelected('center', 'bottom'); });
    _btn('fmt-tbl-align-br', function() { _tblAlignSelected('right',  'bottom'); });

    /* ── Ribbon: Merge Cells ── */
    _btn('fmt-tbl-merge', function() {
      var i = _selectedHtmlTableSlide;
      if (i < 0 || !_slides[i] || !_slides[i].tableData) return;
      if (!_tblSel.cells || _tblSel.cells.length < 2) return;
      var minR = Infinity, maxR = -Infinity, minC = Infinity, maxC = -Infinity;
      _tblSel.cells.forEach(function(c) {
        if (c.ri < minR) minR = c.ri; if (c.ri > maxR) maxR = c.ri;
        if (c.ci < minC) minC = c.ci; if (c.ci > maxC) maxC = c.ci;
      });
      if (minR === Infinity) return;
      /* A merge spanning the header row AND body rows would need a rowSpan that
         crosses the <thead>/<tbody> boundary — invalid HTML that renders wrong.
         Block it with a hint instead. */
      if (minR === -1 && maxR >= 0) {
        _showToast('Cannot merge header cells with body cells');
        return;
      }
      /* Remove overlapping merges */
      var merges = (_slides[i].tableData.merges || []).filter(function(m) {
        var mr2 = m.r + (m.rowspan || 1) - 1, mc2 = m.c + (m.colspan || 1) - 1;
        return !(m.r <= maxR && mr2 >= minR && m.c <= maxC && mc2 >= minC);
      });
      /* Add new merge (only if spanning more than 1 cell) */
      if (maxR > minR || maxC > minC) {
        merges.push({ r: minR, c: minC, rowspan: maxR - minR + 1, colspan: maxC - minC + 1 });
      }
      _slides[i].tableData.merges = merges;
      _slides[i].hasUserContent = true;
      _renderTableLayer(i);
      if (i === _currentSlide) saveState();
      /* Select merged cell after render */
      _tblSel = { si: i, cells: [{ ri: minR, ci: minC }], anchor: { ri: minR, ci: minC } };
      _tblHighlightSelection(i);
      _tblSyncCellRibbon(i);
      _tblResizeRestore(i);
    });

    /* ── Ribbon: Unmerge Cells — splits any merge that overlaps the current selection ── */
    _btn('fmt-tbl-unmerge', function() {
      var i = _selectedHtmlTableSlide;
      if (i < 0 || !_slides[i] || !_slides[i].tableData) return;
      if (!_tblSel.cells || !_tblSel.cells.length) return;
      var existing = _slides[i].tableData.merges || [];
      if (!existing.length) return;
      var kept = [];
      var removedAny = false;
      existing.forEach(function(m) {
        var mr2 = m.r + (m.rowspan || 1) - 1, mc2 = m.c + (m.colspan || 1) - 1;
        var overlapsSelection = _tblSel.cells.some(function(c) {
          return c.ri >= m.r && c.ri <= mr2 && c.ci >= m.c && c.ci <= mc2;
        });
        if (overlapsSelection) { removedAny = true; } else { kept.push(m); }
      });
      if (!removedAny) return;
      _slides[i].tableData.merges = kept;
      _slides[i].hasUserContent = true;
      _renderTableLayer(i);
      if (i === _currentSlide) saveState();
      _tblHighlightSelection(i);
      _tblSyncCellRibbon(i);
      _tblResizeRestore(i);
    });

    /* ── Ribbon: Table Design → border color/width, shading ── */
    function _tblApplyStyleOpt(key, val) {
      var i = _selectedHtmlTableSlide;
      if (i < 0 || !_slides[i] || !_slides[i].tableData) return;
      if (!_slides[i].tableData.styleOpts) _slides[i].tableData.styleOpts = {};
      _slides[i].tableData.styleOpts[key] = val;
      _slides[i].hasUserContent = true;
      _renderTableLayer(i);
      if (i === _currentSlide) saveState();
      _tblResizeRestore(i);
    }
    var _elBorderColor = document.getElementById('fmt-tbl-border-color');
    var _elBorderBar   = document.getElementById('fmt-tbl-border-bar');
    var _elBorderWidth = document.getElementById('fmt-tbl-border-width');
    var _elShading     = document.getElementById('fmt-tbl-shading');
    if (_elBorderColor) {
      _elBorderColor.addEventListener('input', function() {
        if (_elBorderBar) _elBorderBar.style.background = _elBorderColor.value;
        _tblApplyStyleOpt('borderColor', _elBorderColor.value);
      });
    }
    /* NOTE: border-bar / shading-label click handlers that used to open the NATIVE
       color dialog were removed — they double-opened on top of the custom _wireCP
       picker. The _wireCP bindings below now write styleOpts directly. */
    if (_elBorderWidth) {
      _elBorderWidth.addEventListener('change', function() {
        _tblApplyStyleOpt('borderWidth', parseInt(_elBorderWidth.value, 10) || 0);
      });
    }
    if (_elShading) {
      _elShading.addEventListener('input', function() {
        _tblApplyStyleOpt('shadingColor', _elShading.value);
      });
    }

    /* Deselect HTML table when Fabric canvas gets a selection */
    _canvas.on('selection:created', function() { _deselectHtmlTable(); });
    _canvas.on('selection:updated', function() { _deselectHtmlTable(); });
    /* Deselect HTML table when clicking empty canvas area */
    _canvas.on('mouse:down', function(e) { if (!e.target) _deselectHtmlTable(); });

    _btn('btn-ins-link', function() {
      var t = new fabric.IText('Hyperlink text', {
        left: SLIDE_W / 2 - 200, top: SLIDE_H / 2,
        fontFamily: 'Inter, sans-serif', fontSize: 32,
        fill: '#4da3ff', underline: true,
        editorType: 'placeholder',
      });
      _canvas.add(t);
      _canvas.setActiveObject(t);
      _canvas.requestRenderAll();
      saveState();
    });
    _btn('btn-ins-wordart', function() {
      var t = new fabric.IText('WordArt', {
        left: SLIDE_W / 2 - 200, top: SLIDE_H / 2 - 60,
        fontFamily: 'Georgia, serif', fontSize: 72,
        fill: '#ffffff', fontWeight: 'bold', charSpacing: 80,
        stroke: '#4da3ff', strokeWidth: 1,
        editorType: 'placeholder',
      });
      _canvas.add(t);
      _canvas.setActiveObject(t);
      _canvas.requestRenderAll();
      saveState();
    });
    /* Date & Slide number are managed by the Header & Footer system (single source of
       truth, per-deck, idempotent) — open that modal instead of adding a loose duplicate
       text box on top of the chrome that's already there. */
    _btn('btn-ins-date',     function() { var b = document.getElementById('btn-ins-hf'); if (b) b.click(); });
    _btn('btn-ins-slidenum', function() { var b = document.getElementById('btn-ins-hf'); if (b) b.click(); });
    _btn('btn-ins-formula', function() {
      var modal   = document.getElementById('formula-modal');
      var inp     = document.getElementById('formula-modal-input');
      var preview = document.getElementById('formula-modal-preview');
      var cancel  = document.getElementById('formula-modal-cancel');
      if (!modal || !inp) return;
      inp.value = '';
      if (preview) preview.textContent = '';
      /* Live preview while typing */
      inp.oninput = function() { if (preview) preview.textContent = _cleanLatex(inp.value); };
      /* Cancel closes modal */
      if (cancel) cancel.onclick = function() { modal.style.display = 'none'; modal.dataset.mode = ''; };
      modal._targetObj = null;
      modal.style.display = 'flex';
      modal.dataset.mode = 'insert';
    });

    /* ── Insert Symbol modal ── */
    var _SYMBOLS = {
      greek:   'α β γ δ ε ζ η θ ι κ λ μ ν ξ π ρ σ τ υ φ χ ψ ω Α Β Γ Δ Ε Ζ Η Θ Ι Κ Λ Μ Ν Ξ Ο Π Ρ Σ Τ Υ Φ Χ Ψ Ω'.split(' '),
      math:    '± × ÷ ≠ ≈ ≡ ≤ ≥ ∞ √ ∑ ∏ ∫ ∂ ∇ ∈ ∉ ⊂ ⊃ ∪ ∩ ∀ ∃ ¬ ∧ ∨ ⊕ ⊗ ℝ ℤ ℕ ℚ ½ ⅓ ¼ ⅔ ¾ ‰'.split(' '),
      arrows:  '← → ↑ ↓ ↔ ↕ ⇐ ⇒ ⇑ ⇓ ⇔ ↗ ↘ ↙ ↖ ↺ ↻ ⟵ ⟶ ⟷ ⟹ ⟸ ↵ ⤴ ⤵ ⟼ ↦'.split(' '),
      special: '© ® ™ § ¶ † ‡ • · … ′ ″ ° ¦ € £ ¥ ¢ ₿ ₹ ¿ ¡ æ ø å ñ ü ö ä'.split(' ')
    };

    function _symShowCat(cat) {
      var grid = document.getElementById('sym-grid');
      if (!grid) return;
      var syms = _SYMBOLS[cat] || [];
      grid.innerHTML = syms.map(function(s) {
        return '<button class="sym-char-btn" title="' + s + '">' + s + '</button>';
      }).join('');
      var preview   = document.getElementById('sym-preview');
      var insertBtn = document.getElementById('btn-sym-insert');
      if (preview)   preview.textContent = '—';
      if (insertBtn) { insertBtn.disabled = true; insertBtn.dataset.char = ''; }
      grid.querySelectorAll('.sym-char-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
          grid.querySelectorAll('.sym-char-btn').forEach(function(b) { b.classList.remove('sym-selected'); });
          btn.classList.add('sym-selected');
          if (preview)   preview.textContent = btn.textContent;
          if (insertBtn) { insertBtn.disabled = false; insertBtn.dataset.char = btn.textContent; }
        });
      });
    }

    _btn('btn-ins-symbol', function() {
      var modal = document.getElementById('symbol-modal');
      if (!modal) return;
      document.querySelectorAll('.sym-tab').forEach(function(t) { t.classList.remove('active'); });
      var first = document.querySelector('.sym-tab[data-cat="greek"]');
      if (first) first.classList.add('active');
      _symShowCat('greek');
      modal.style.display = 'flex';
    });

    document.querySelectorAll('.sym-tab').forEach(function(tab) {
      tab.addEventListener('click', function() {
        document.querySelectorAll('.sym-tab').forEach(function(t) { t.classList.remove('active'); });
        tab.classList.add('active');
        _symShowCat(tab.dataset.cat);
      });
    });

    _btn('btn-sym-insert', function() {
      var insertBtn = document.getElementById('btn-sym-insert');
      var sym = insertBtn && insertBtn.dataset.char;
      if (!sym) return;
      var obj = _canvas.getActiveObject();
      if (obj && (obj.type === 'textbox' || obj.type === 'i-text') && obj.isEditing) {
        obj.insertChars(sym);
        _canvas.requestRenderAll();
      } else {
        var t = new fabric.Textbox(sym, {
          left: SLIDE_W / 2 - 100, top: SLIDE_H / 2 - 60,
          width: 200, fontSize: 80,
          fontFamily: 'Open Sans, sans-serif',
          fill: '#1a1a2e', textAlign: 'center',
        });
        _canvas.add(t); _canvas.setActiveObject(t);
        _canvas.requestRenderAll(); saveState();
      }
      document.getElementById('symbol-modal').style.display = 'none';
    });

    _btn('btn-sym-close', function() {
      var m = document.getElementById('symbol-modal');
      if (m) m.style.display = 'none';
    });

    /* ── Shape Format tab controls ── */
    _onchange('fmt-shape-fill', function(v) {
      var o = _canvas.getActiveObject();
      if (!o) return;
      o.set('fill', v); _canvas.requestRenderAll(); saveState();
    });
    _onchange('fmt-shape-outline', function(v) {
      var o = _canvas.getActiveObject();
      if (!o) return;
      o.set('stroke', v); _canvas.requestRenderAll(); saveState();
    });
    _onchange('fmt-shape-stroke-w', function(v) {
      var o = _canvas.getActiveObject();
      if (!o) return;
      o.set('strokeWidth', +v || 0); _canvas.requestRenderAll(); saveState();
    });
    _onchange('fmt-shape-opacity', function(v) {
      var o = _canvas.getActiveObject();
      if (!o) return;
      o.set('opacity', Math.max(0, Math.min(1, v === '' ? 1 : parseFloat(v) / 100))); _canvas.requestRenderAll(); saveState();
    });
    _btn('fmt-shape-bring-fwd',   function() { editorCmd('bringForward'); });
    _btn('fmt-shape-send-bk',     function() { editorCmd('sendBackward'); });
    _btn('fmt-shape-bring-front', function() { editorCmd('bringToFront'); });
    _btn('fmt-shape-send-back',   function() { editorCmd('sendToBack'); });

    /* Align buttons */
    _btn('fmt-align-left',    function() { _alignObj('left'); });
    _btn('fmt-align-centerH', function() { _alignObj('centerH'); });
    _btn('fmt-align-right',   function() { _alignObj('right'); });
    _btn('fmt-align-top',     function() { _alignObj('top'); });
    _btn('fmt-align-middleV', function() { _alignObj('middleV'); });
    _btn('fmt-align-bottom',  function() { _alignObj('bottom'); });

    /* Rotate + Flip */
    _btn('fmt-rotate-cw',  function() {
      var o = _canvas.getActiveObject(); if (!o) return;
      o.set('angle', ((o.angle || 0) + 90) % 360);
      _canvas.requestRenderAll(); saveState();
    });
    _btn('fmt-rotate-ccw', function() {
      var o = _canvas.getActiveObject(); if (!o) return;
      o.set('angle', ((o.angle || 0) - 90 + 360) % 360);
      _canvas.requestRenderAll(); saveState();
    });
    _btn('fmt-flip-h', function() {
      var o = _canvas.getActiveObject(); if (!o) return;
      o.set('flipX', !o.flipX);
      _canvas.requestRenderAll(); saveState();
    });
    _btn('fmt-flip-v', function() {
      var o = _canvas.getActiveObject(); if (!o) return;
      o.set('flipY', !o.flipY);
      _canvas.requestRenderAll(); saveState();
    });

    /* Rotation angle input */
    (function() {
      var angleEl = document.getElementById('fmt-inp-shape-angle');
      if (angleEl) angleEl.addEventListener('change', function() {
        var o = _canvas.getActiveObject(); if (!o) return;
        o.set('angle', +angleEl.value || 0);
        _canvas.requestRenderAll(); saveState();
      });
    })();

    /* Shape style presets */
    var _SHAPE_PRESETS = [
      { fill: '#4472C4', stroke: 'transparent', strokeWidth: 0, opacity: 1 },
      { fill: '#1a1a2e', stroke: 'transparent', strokeWidth: 0, opacity: 1 },
      { fill: 'transparent', stroke: '#4472C4', strokeWidth: 3, opacity: 1 },
      { fill: '#4472C420', stroke: '#4472C4', strokeWidth: 2, opacity: 1 },
      { fill: '#ED7D31', stroke: 'transparent', strokeWidth: 0, opacity: 1 },
      { fill: 'transparent', stroke: '#ED7D31', strokeWidth: 3, opacity: 1 },
    ];
    /* Init preset swatch colors */
    _SHAPE_PRESETS.forEach(function(p, i) {
      var el = document.getElementById('fmt-preset-' + i);
      if (el) el.style.background = p.fill === 'transparent' ? 'transparent' : p.fill;
    });
    document.querySelectorAll('.fmt-shape-preset').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var idx = parseInt(btn.dataset.preset || '0', 10);
        var p = _SHAPE_PRESETS[idx];
        if (!p) return;
        var o = _canvas.getActiveObject();
        if (!o) return;
        o.set({ fill: p.fill, stroke: p.stroke, strokeWidth: p.strokeWidth, opacity: p.opacity });
        _canvas.requestRenderAll(); saveState();
        /* Sync UI */
        var ff = document.getElementById('fmt-shape-fill'); if (ff && p.fill !== 'transparent') ff.value = p.fill;
        var fb = document.getElementById('bar-shape-fill'); if (fb) fb.style.background = p.fill !== 'transparent' ? p.fill : '#cccccc';
        var sw = document.getElementById('fmt-shape-stroke-w'); if (sw) sw.value = p.strokeWidth;
        var op = document.getElementById('fmt-shape-opacity'); if (op) op.value = Math.round(p.opacity * 100);
      });
    });

    /* Text-specific controls */
    _onchange('fmt-text-color', function(v) {
      var o = _canvas.getActiveObject(); if (!o) return;
      o.set('fill', v);
      var b = document.getElementById('bar-fmt-text-color'); if (b) b.style.background = v;
      _canvas.requestRenderAll(); saveState();
    });
    _onchange('fmt-text-highlight', function(v) {
      var o = _canvas.getActiveObject(); if (!o) return;
      o.set('backgroundColor', v);
      var b = document.getElementById('bar-fmt-text-highlight'); if (b) b.style.background = v;
      _canvas.requestRenderAll(); saveState();
    });
    _onchange('fmt-text-size', function(v) {
      var o = _canvas.getActiveObject(); if (!o) return;
      o.set('fontSize', Math.max(6, +v || 60));
      _canvas.requestRenderAll(); saveState();
    });
    _onchange('fmt-text-opacity', function(v) {
      var o = _canvas.getActiveObject(); if (!o) return;
      o.set('opacity', Math.max(0, Math.min(1, v === '' ? 1 : parseFloat(v) / 100)));
      _canvas.requestRenderAll(); saveState();
    });

    /* Text effects in Shape Format */
    _btn('fmt-txt-bold',      function() { editorCmd('bold'); });
    _btn('fmt-txt-italic',    function() { editorCmd('italic'); });
    _btn('fmt-txt-underline', function() { editorCmd('underline'); });
    _btn('fmt-txt-strikethrough', function() {
      var o = _canvas.getActiveObject(); if (!o) return;
      o.set('linethrough', !o.linethrough);
      _canvas.requestRenderAll(); saveState();
    });
    _btn('fmt-txt-shadow', function() {
      var o = _canvas.getActiveObject(); if (!o) return;
      o.set('shadow', o.shadow ? null : new fabric.Shadow({ color: 'rgba(0,0,0,0.5)', blur: 8, offsetX: 3, offsetY: 3 }));
      _canvas.requestRenderAll(); saveState();
    });
    (function() {
      function _applySize(wId, hId) {
        var o = _canvas.getActiveObject();
        if (!o) return;
        var wEl = document.getElementById(wId), hEl = document.getElementById(hId);
        if (wEl && wEl.value) { var w = +wEl.value; if (w > 0) o.scaleToWidth(w); }
        if (hEl && hEl.value) { var h = +hEl.value; if (h > 0) o.scaleToHeight(h); }
        _canvas.requestRenderAll(); saveState();
      }
      var shW = document.getElementById('fmt-inp-shape-w');
      var shH = document.getElementById('fmt-inp-shape-h');
      if (shW) shW.addEventListener('change', function() { _applySize('fmt-inp-shape-w', 'fmt-inp-shape-h'); });
      if (shH) shH.addEventListener('change', function() { _applySize('fmt-inp-shape-w', 'fmt-inp-shape-h'); });
    })();

    /* ── Picture Format tab controls ── */
    _btn('fmt-pic-replace', function() {
      var f = document.getElementById('fmt-pic-file');
      if (f) f.click();
    });
    (function() {
      var fmtPicFile = document.getElementById('fmt-pic-file');
      if (fmtPicFile) {
        fmtPicFile.addEventListener('change', function() {
          var file = fmtPicFile.files[0];
          if (!file) return;
          var cur = _canvas.getActiveObject();
          if (!cur || !(cur instanceof fabric.Image)) return;
          var cL = cur.left, cT = cur.top;
          var cW = cur.getScaledWidth(), cH = cur.getScaledHeight();
          var reader = new FileReader();
          reader.onload = function(ev) {
            fabric.Image.fromURL(ev.target.result, function(img) {
              if (!img) return;
              img.set({ left: cL, top: cT, editorType: 'image' });
              var scale = Math.min(cW / img.width, cH / img.height);
              img.scale(scale);
              _canvas.remove(cur);
              _canvas.add(img);
              _canvas.setActiveObject(img);
              _canvas.requestRenderAll();
              saveState();
            });
          };
          reader.readAsDataURL(file);
          fmtPicFile.value = '';
        });
      }
      document.querySelectorAll('[data-imgfit]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          var fit = btn.dataset.imgfit;
          var cur = _canvas.getActiveObject();
          if (!cur || !(cur instanceof fabric.Image)) return;
          var cw = cur.getScaledWidth(), ch = cur.getScaledHeight();
          var scale;
          if (fit === 'cover')   scale = Math.max(cw / cur.width, ch / cur.height);
          else if (fit === 'contain') scale = Math.min(cw / cur.width, ch / cur.height);
          else { cur.set({ scaleX: cw / cur.width, scaleY: ch / cur.height }); _canvas.requestRenderAll(); saveState(); return; }
          cur.scale(scale);
          _canvas.requestRenderAll();
          saveState();
        });
      });
    })();
    _onchange('fmt-pic-opacity', function(v) {
      var o = _canvas.getActiveObject();
      if (!o) return;
      o.set('opacity', Math.max(0, Math.min(1, v === '' ? 1 : parseFloat(v) / 100))); _canvas.requestRenderAll(); saveState();
    });
    (function() {
      function _applyPicSize() {
        var o = _canvas.getActiveObject();
        if (!o) return;
        var wEl = document.getElementById('fmt-inp-pic-w');
        var hEl = document.getElementById('fmt-inp-pic-h');
        if (wEl && wEl.value) { var w = +wEl.value; if (w > 0) o.scaleToWidth(w); }
        if (hEl && hEl.value) { var h = +hEl.value; if (h > 0) o.scaleToHeight(h); }
        _canvas.requestRenderAll(); saveState();
      }
      var picW = document.getElementById('fmt-inp-pic-w');
      var picH = document.getElementById('fmt-inp-pic-h');
      if (picW) picW.addEventListener('change', _applyPicSize);
      if (picH) picH.addEventListener('change', _applyPicSize);
    })();

    /* Legacy Fabric-drawn-table handlers (Design colors, row/col, cell size,
       align, overall size keyed by _getActiveTableId) were REMOVED — they
       double-bound the same ribbon ids as the HTML-overlay system above and
       only serviced pre-overlay files, which the load-time migration already
       converts. The HTML-overlay handlers are the single wiring now. */

    /* ── Equation Format tab controls ── */
    _btn('fmt-eq-edit', function() {
      var obj = _canvas.getActiveObject();
      if (!obj || obj.editorType !== 'formula') return;
      _openFormulaModal(obj);
    });
    _onchange('fmt-eq-size', function(v) {
      var o = _canvas.getActiveObject();
      if (!o || o.editorType !== 'formula') return;
      o.set('fontSize', +v || 28); _canvas.requestRenderAll(); saveState();
    });
    _onchange('fmt-eq-color', function(v) {
      var o = _canvas.getActiveObject();
      if (!o || o.editorType !== 'formula') return;
      o.set('fill', v); _canvas.requestRenderAll(); saveState();
    });

    /* ── Header & Footer modal ── */
    (function() {
      var _g = function(id) { return document.getElementById(id); };
      /* Update preview indicators */
      function _hfUpdatePreview() {
        var showDate   = _g('hf-show-date')     && _g('hf-show-date').checked;
        var showNum    = _g('hf-show-slidenum')  && _g('hf-show-slidenum').checked;
        var showFooter = _g('hf-show-footer')    && _g('hf-show-footer').checked;
        if (_g('hf-prev-date'))   _g('hf-prev-date').style.display   = showDate   ? '' : 'none';
        if (_g('hf-prev-num'))    _g('hf-prev-num').style.display    = showNum    ? '' : 'none';
        if (_g('hf-prev-footer')) _g('hf-prev-footer').style.display = showFooter ? '' : 'none';
      }
      /* Toggle date sub-options enabled state */
      function _hfDateToggle() {
        var on = _g('hf-show-date') && _g('hf-show-date').checked;
        ['hf-date-auto','hf-date-fixed','hf-date-format','hf-date-fixed-val'].forEach(function(id) {
          var el = _g(id); if (el) el.disabled = !on;
        });
        _hfUpdatePreview();
      }
      _btn('btn-ins-hf', function() {
        var meta = (_currentSpec && _currentSpec.meta) || {};
        if (_g('hf-inp-title'))      _g('hf-inp-title').value      = meta.title       || '';
        if (_g('hf-inp-author'))     _g('hf-inp-author').value     = meta.author      || '';
        if (_g('hf-inp-inst'))       _g('hf-inp-inst').value       = meta.institution || '';
        if (_g('hf-show-author'))    _g('hf-show-author').checked  = meta.showAuthor  !== false;
        if (_g('hf-show-date'))      _g('hf-show-date').checked    = !!meta.showDate;
        if (_g('hf-show-slidenum'))  _g('hf-show-slidenum').checked = meta.showSlideNum !== false && meta.showPageNum !== false;
        if (_g('hf-show-footer'))    _g('hf-show-footer').checked  = !!meta.showFooter;
        if (_g('hf-footer-text'))    _g('hf-footer-text').value    = meta.footerText  || '';
        if (_g('hf-date-fixed-val')) _g('hf-date-fixed-val').value = meta.dateFixed   || '';
        if (_g('hf-date-format'))    _g('hf-date-format').value    = meta.dateFormat  || 'short';
        var autoEl = _g('hf-date-auto'), fixEl = _g('hf-date-fixed');
        if (autoEl && fixEl) { if (meta.dateAuto === false) { fixEl.checked = true; } else { autoEl.checked = true; } }
        if (_g('hf-hide-title'))     _g('hf-hide-title').checked   = !!meta.hideOnTitle;
        _hfDateToggle();
        var m = _g('modal-hf'); if (m) m.style.display = 'flex';
      });
      /* Live preview updates */
      ['hf-show-date','hf-show-slidenum','hf-show-footer'].forEach(function(id) {
        var el = _g(id); if (el) el.addEventListener('change', _hfUpdatePreview);
      });
      if (_g('hf-show-date')) _g('hf-show-date').addEventListener('change', _hfDateToggle);
      _btn('btn-hf-close', function() { var m = _g('modal-hf'); if (m) m.style.display = 'none'; });
      _btn('btn-hf-apply', function() {
        if (!_currentSpec) return;
        var dateAuto = !(_g('hf-date-fixed') && _g('hf-date-fixed').checked);
        _currentSpec.meta = Object.assign({}, _currentSpec.meta, {
          title:        _g('hf-inp-title')     ? _g('hf-inp-title').value     : (_currentSpec.meta || {}).title,
          author:       _g('hf-inp-author')    ? _g('hf-inp-author').value    : (_currentSpec.meta || {}).author,
          institution:  _g('hf-inp-inst')      ? _g('hf-inp-inst').value      : (_currentSpec.meta || {}).institution,
          showAuthor:   _g('hf-show-author')   ? _g('hf-show-author').checked  : true,
          showDate:     _g('hf-show-date')     ? _g('hf-show-date').checked    : false,
          dateAuto:     dateAuto,
          dateFormat:   _g('hf-date-format')   ? _g('hf-date-format').value    : 'short',
          dateFixed:    _g('hf-date-fixed-val')? _g('hf-date-fixed-val').value : '',
          showSlideNum: _g('hf-show-slidenum') ? _g('hf-show-slidenum').checked : true,
          showPageNum:  _g('hf-show-slidenum') ? _g('hf-show-slidenum').checked : true,
          showFooter:   _g('hf-show-footer')   ? _g('hf-show-footer').checked  : false,
          footerText:   _g('hf-footer-text')   ? _g('hf-footer-text').value    : '',
          hideOnTitle:  _g('hf-hide-title')    ? _g('hf-hide-title').checked   : false,
        });
        var meta = _currentSpec.meta, pal = _getPal();
        _isRestoring = true; _batchSave = true;
        _slides.forEach(function(sl, i) {
          if (!_currentSpec.slides[i]) return;
          var lay = (_currentSpec.slides[i].layout) || 'only_content';
          var isEdited = sl.hasUserContent || (sl.history && sl.history.length > 1);

          if (isEdited && sl.json) {
            /* Smart chrome-only update: preserve all user content, only replace chrome elements */
            /* Step 1: Generate new chrome objects on a clean canvas */
            _canvas.clear();
            _sChrome(meta, i + 1, pal, lay);
            _canvas.renderAll();
            var newChromeObjs = _canvas.toJSON(_TOJSON_KEYS).objects || [];
            /* Step 2: Remove old chrome from slide JSON, prepend new chrome */
            var mergedJson = JSON.parse(JSON.stringify(sl.json));
            mergedJson.objects = (mergedJson.objects || []).filter(function(o) { return o.editorType !== 'chrome'; });
            mergedJson.objects = newChromeObjs.concat(mergedJson.objects);
            sl.json = mergedJson;
            /* Thumbnail will update when user navigates to that slide */
          } else {
            /* Unedited slide: full rebuild */
            _canvas.clear();
            _pendingTableData = null;
            _specBuildSlide(_currentSpec.slides[i], pal, meta, i + 1, {});
            sl.tableData = _pendingTableData;
            _canvas.renderAll();
            _renderThumb(i);
            sl.bgColor = _canvas.backgroundColor;
            sl.json    = _canvas.toJSON(_TOJSON_KEYS);
          }
        });
        _canvas.loadFromJSON(_slides[_currentSlide].json || {}, function() {
          if (_slides[_currentSlide].bgColor != null) _canvas.backgroundColor = _slides[_currentSlide].bgColor;
          _canvas.discardActiveObject(); _canvas.requestRenderAll();
          _isRestoring = false; _batchSave = false;
          _renderTableLayer(_currentSlide);
          saveState(); _rebuildThumbPanel(); _syncLayoutSelector();
        });
        var m = _g('modal-hf'); if (m) m.style.display = 'none';
      });
    })();

    /* ── formula-modal apply — single handler for both insert (new formula from the
       Insert tab) and edit (double-click an existing formula), dispatched by
       modal.dataset.mode so there's only one place fighting over .onclick ── */
    (function() {
      var fmlModal = document.getElementById('formula-modal');
      var fmlApply = document.getElementById('formula-modal-apply');
      if (!fmlModal || !fmlApply) return;
      fmlApply.onclick = function() {
        var cleaned = _cleanLatex(document.getElementById('formula-modal-input').value || '');
        if (fmlModal.dataset.mode === 'edit') {
          var obj = fmlModal._targetObj;
          if (obj) {
            obj.set('text', cleaned);
            obj._latexSource = document.getElementById('formula-modal-input').value;
            if (obj.editorType === 'formula' && obj._formulaBox) _refitFormulaBox(obj);
            _canvas.requestRenderAll();
            saveState();
          }
          fmlModal.style.display = 'none';
          fmlModal.dataset.mode = '';
          fmlModal._targetObj = null;
          return;
        }
        /* insert mode (default) */
        if (!cleaned) { fmlModal.style.display = 'none'; return; }
        var pal = _getPal();
        var fmlTb = _sTB(cleaned, {
          left: 160, top: SLIDE_H / 2 - 60, width: 1600,
          fontFamily: pal.fontMono, fontSize: 28, fill: pal.text,
          textAlign: 'center', editorType: 'formula',
        });
        fmlTb._latexSource = document.getElementById('formula-modal-input').value;
        _canvas.add(fmlTb);
        _canvas.setActiveObject(fmlTb);
        _canvas.requestRenderAll();
        saveState();
        fmlModal.style.display = 'none';
        fmlModal.dataset.mode = '';
      };
    })();

    /* Slide background color (solid, from Properties panel) */
    var bgInp = document.getElementById('inp-bg-color');
    if (bgInp) {
      bgInp.addEventListener('input', function() {
        setSlideBackground(bgInp.value);
      });
    }

    /* Gradient color pickers — BG1 / BG2 / Text / Accent (Design tab) */
    function _onGradChange() {
      if (_currentSlide < 0 || !_currentSpec || !_currentSpec.slides) return;
      var spec = _currentSpec.slides[_currentSlide];
      if (!spec) return;
      var c1 = (document.getElementById('inp-bg1') || {}).value || '#1a1a2e';
      var c2 = (document.getElementById('inp-bg2') || {}).value || '#0d3b6e';
      spec.bgGrad = [{ offset: 0, color: c1 }, { offset: 1, color: c2 }];
      _sGradBg(spec.bgGrad);
      _canvas.requestRenderAll();
      saveState();
    }
    ['inp-bg1','inp-bg2'].forEach(function(id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('input', function() {
        var bar = document.getElementById('bar-' + id.replace('inp-', ''));
        if (bar) bar.style.background = el.value;
        _onGradChange();
      });
    });

    /* Text / Accent color overrides */
    function _onThemeColorChange() {
      if (_currentSlide < 0 || !_currentSpec || !_currentSpec.slides) return;
      var spec = _currentSpec.slides[_currentSlide];
      if (!spec) return;
      var txtC = (document.getElementById('inp-theme-text')   || {}).value;
      var accC = (document.getElementById('inp-theme-accent') || {}).value;
      if (txtC) spec.overrideText   = txtC;
      if (accC) spec.overrideAccent = accC;
      var pal = _getPal();
      if (txtC) pal = Object.assign({}, pal, { text: txtC, dim: txtC + '99' });
      if (accC) pal = Object.assign({}, pal, { accent: accC });
      _beginBatch();
      _canvas.clear();
      _pendingTableData = null;
      _specBuildSlide(spec, pal, _currentSpec.meta, _currentSlide + 1, {});
      _slides[_currentSlide].tableData = _pendingTableData;
      _renderTableLayer(_currentSlide);
      _canvas.requestRenderAll();
      _endBatch();
    }
    ['inp-theme-text','inp-theme-accent'].forEach(function(id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('input', function() {
        var barId = id === 'inp-theme-text' ? 'bar-theme-text' : 'bar-theme-accent';
        var bar = document.getElementById(barId);
        if (bar) bar.style.background = el.value;
        _onThemeColorChange();
      });
    });

    /* Text format */
    _btn('btn-bold',      () => editorCmd('bold'));
    _btn('btn-italic',    () => editorCmd('italic'));
    _btn('btn-underline', () => editorCmd('underline'));

    _onchange('sel-font-family', v => editorCmd('fontFamily', v));
    _onchange('inp-font-size',   v => editorCmd('fontSize',   v));
    _onchange('inp-text-color',  v => editorCmd('textColor',  v));

    /* Alignment — sync ribbon after change so active state updates immediately */
    _btn('btn-align-left',    () => { editorCmd('align', 'left');    syncRibbonToSelection(); });
    _btn('btn-align-center',  () => { editorCmd('align', 'center');  syncRibbonToSelection(); });
    _btn('btn-align-right',   () => { editorCmd('align', 'right');   syncRibbonToSelection(); });
    _btn('btn-align-justify', () => { editorCmd('align', 'justify'); syncRibbonToSelection(); });

    /* Align on slide */
    _btn('btn-align-cx', () => alignOnSlide('h'));
    _btn('btn-align-cy', () => alignOnSlide('v'));
    _btn('btn-dist-h',   () => distributeObjects('h'));
    _btn('btn-dist-v',   () => distributeObjects('v'));

    /* Home tab — Arrange group */
    _btn('btn-home-group',    function() { editorCmd('group'); });
    _btn('btn-home-ungroup',  function() { editorCmd('ungroup'); });
    _btn('btn-home-to-front', function() { editorCmd('bringToFront'); });
    _btn('btn-home-to-back',  function() { editorCmd('sendToBack'); });
    _btn('btn-home-fwd',      function() { editorCmd('bringForward'); });
    _btn('btn-home-bk',       function() { editorCmd('sendBackward'); });

    /* Export dropdown toggle */
    var ddBtn  = document.getElementById('btn-export-toggle');
    var ddMenu = document.getElementById('dd-export-menu');
    if (ddBtn && ddMenu) {
      ddBtn.addEventListener('click', function(e) { e.stopPropagation(); _openDropdown(ddBtn, ddMenu); });
    }
    _btn('btn-export-png',  exportCurrentPNG);
    _btn('btn-export-all',  exportAllPNG);
    _btn('btn-save-html',   exportHTMLFile);
    _btn('btn-export-json', exportJSONFile);
    _btn('btn-import-json', importJSONFile);

    /* Shape format */
    _onchange('inp-fill-color',    v => editorCmd('fillColor',    v));
    _onchange('inp-stroke-color',  v => editorCmd('strokeColor',  v));
    _onchange('inp-stroke-width',  v => editorCmd('strokeWidth',  v));

    /* Table row controls (props panel) — prefer the HTML-overlay table; the
       legacy Fabric path only applies when a legacy table object is selected */
    _btn('btn-tbl-add-row', function() { if (_selectedHtmlTableSlide >= 0) _tblHtmlAddRow(false); else tableAddRow(); });
    _btn('btn-tbl-del-row', function() { if (_selectedHtmlTableSlide >= 0) _tblHtmlDelRow();      else tableDelRow(); });

    /* Image replace */
    _btn('btn-replace-img', function() {
      var f = document.getElementById('inp-replace-img-file');
      if (f) f.click();
    });
    var replaceFileInp = document.getElementById('inp-replace-img-file');
    if (replaceFileInp) {
      replaceFileInp.addEventListener('change', function(e) {
        var file = e.target.files[0];
        if (!file) return;
        var cur = _canvas.getActiveObject();
        if (!cur || !(cur instanceof fabric.Image)) return;
        var cLeft = cur.left, cTop = cur.top;
        var cW = cur.getScaledWidth(), cH = cur.getScaledHeight();
        var reader = new FileReader();
        reader.onload = function(ev) {
          fabric.Image.fromURL(ev.target.result, function(img) {
            if (!img) return;
            img.set({
              left: cLeft, top: cTop,
              scaleX: cW / img.width, scaleY: cH / img.height,
              editorType: 'image',
            });
            _canvas.remove(cur);
            _canvas.add(img);
            _canvas.setActiveObject(img);
            _canvas.requestRenderAll();
            saveState();
            syncPropsPanel();
          });
        };
        reader.readAsDataURL(file);
        e.target.value = '';
      });
    }

    /* Canvas selection events → sync ribbon + props + status + contextual tabs */
    function _onSelectionChange() {
      syncRibbonToSelection();
      syncPropsPanel();
      syncStatusBar();
      _syncContextualTabs();
    }
    /* Table: when 1 cell is selected, auto-expand to whole table */
    _canvas.on('selection:created', _autoSelectTable);
    _canvas.on('selection:updated', _autoSelectTable);
    _canvas.on('selection:created', _onSelectionChange);
    _canvas.on('selection:updated', _onSelectionChange);
    _canvas.on('selection:cleared', _onSelectionChange);
    /* Safety: re-sync on mouse-up when an object is active (covers re-clicking the
       already-selected object and the end of a drag — cases where selection:* may not fire). */
    _canvas.on('mouse:up', function() { if (_canvas.getActiveObject()) _onSelectionChange(); });

    /* Double-click: table cell → edit text; formula → popup editor */
    _canvas.on('mouse:dblclick', function(e) {
      var ptr = _canvas.getPointer(e.e);
      /* Search for a table Textbox under the cursor */
      var objs = _canvas.getObjects();
      for (var i = objs.length - 1; i >= 0; i--) {
        var o = objs[i];
        if (o instanceof fabric.Textbox && o.tableId) {
          var b = o.getBoundingRect(true);
          if (ptr.x >= b.left && ptr.x <= b.left + b.width &&
              ptr.y >= b.top  && ptr.y <= b.top  + b.height) {
            _canvas.discardActiveObject();
            _canvas.setActiveObject(o);
            o.enterEditing();
            _canvas.requestRenderAll();
            return;
          }
        }
      }
      /* Image placeholder rect → trigger file picker to replace it */
      for (var pi = objs.length - 1; pi >= 0; pi--) {
        var po = objs[pi];
        if (po instanceof fabric.Rect && po.editorType === 'image') {
          var phB = po.getBoundingRect(true);
          if (ptr.x >= phB.left && ptr.x <= phB.left + phB.width &&
              ptr.y >= phB.top  && ptr.y <= phB.top  + phB.height) {
            _pendingImagePlaceholder = po;
            document.getElementById('inp-img-file').click();
            return;
          }
        }
      }
      /* Formula textbox → open popup editor */
      var tgt = e.target;
      if (tgt instanceof fabric.Textbox && tgt.editorType === 'formula') {
        _openFormulaModal(tgt);
      }
    });
    _canvas.on('text:changed',      syncRibbonToSelection);
    _canvas.on('object:moving',     function() { syncPropsPanel(); syncStatusBar(); });
    _canvas.on('object:scaling',    function() { syncPropsPanel(); });
    _canvas.on('object:rotating',   function() { syncPropsPanel(); });
    _canvas.on('object:added',      syncStatusBar);
    _canvas.on('object:removed',    syncStatusBar);
    _canvas.on('mouse:move',        syncCursorPos);
    _canvas.on('mouse:out',         function() { syncCursorPos(null); });

    /* Color swatch bar updates (native picker fallback via _onchange) */
    _onchange('inp-text-color',   function(v) { _setSwatchBar('bar-text-color', v); });
    _onchange('inp-fill-color',   function(v) { _setSwatchBar('bar-fill-color', v); });
    _onchange('inp-stroke-color', function(v) { _setSwatchBar('bar-stroke-color', v); });

    /* ── Rich color picker wiring — all color buttons ── */
    /* Home: Text Color */
    _wireCP('inp-text-color', {
      barId: 'bar-text-color',
      getColor: function() { var o=_canvas.getActiveObject(); return o&&typeof o.fill==='string'?o.fill:null; },
      onColor: function(c) { _setSwatchBar('bar-text-color',c); editorCmd('textColor',c); },
    });
    /* Shape Format: Shape Fill */
    _wireCP('fmt-shape-fill', {
      barId: 'bar-shape-fill',
      noFill: true, gradient: true,
      getColor: function() { var o=_canvas.getActiveObject(); return o&&typeof o.fill==='string'?o.fill:null; },
      onColor: function(c) {
        _setSwatchBar('bar-shape-fill',c);
        var o=_canvas.getActiveObject(); if(!o) return;
        o.set('fill',c); _canvas.requestRenderAll(); saveState();
      },
      onNoFill: function() {
        var o=_canvas.getActiveObject(); if(!o) return;
        o.set('fill','transparent'); _canvas.requestRenderAll(); saveState();
      },
      onGradient: function(g) { _applyGradFill(g); },
    });
    /* Shape Format: Outline */
    _wireCP('fmt-shape-outline', {
      barId: 'fmt-shape-outline-bar',
      noFill: true,
      getColor: function() { var o=_canvas.getActiveObject(); return o&&typeof o.stroke==='string'?o.stroke:null; },
      onColor: function(c) {
        _setSwatchBar('fmt-shape-outline-bar',c);
        var o=_canvas.getActiveObject(); if(!o) return;
        o.set('stroke',c); if(!o.strokeWidth) o.set('strokeWidth',2);
        _canvas.requestRenderAll(); saveState();
      },
      onNoFill: function() {
        var o=_canvas.getActiveObject(); if(!o) return;
        o.set('stroke',null); _canvas.requestRenderAll(); saveState();
      },
    });
    /* Insert/Drawing tab: Fill Color */
    _wireCP('inp-fill-color', {
      barId: 'bar-fill-color',
      noFill: true, gradient: true,
      getColor: function() { var o=_canvas.getActiveObject(); return o&&typeof o.fill==='string'?o.fill:null; },
      onColor: function(c) { _setSwatchBar('bar-fill-color',c); editorCmd('fillColor',c); },
      onNoFill: function() { editorCmd('fillColor','transparent'); },
      onGradient: function(g) { _applyGradFill(g); },
    });
    /* Insert/Drawing tab: Stroke Color */
    _wireCP('inp-stroke-color', {
      barId: 'bar-stroke-color',
      noFill: true,
      getColor: function() { var o=_canvas.getActiveObject(); return o&&typeof o.stroke==='string'?o.stroke:null; },
      onColor: function(c) { _setSwatchBar('bar-stroke-color',c); editorCmd('strokeColor',c); },
      onNoFill: function() { editorCmd('strokeColor','transparent'); },
    });
    /* Design tab: BG gradient colors */
    _wireCP('inp-bg1', {
      barId: 'bar-bg1',
      onColor: function(c) { _setSwatchBar('bar-bg1',c); _onGradChange && _onGradChange(); },
    });
    _wireCP('inp-bg2', {
      barId: 'bar-bg2',
      onColor: function(c) { _setSwatchBar('bar-bg2',c); _onGradChange && _onGradChange(); },
    });
    /* Design tab: Theme text + accent */
    _wireCP('inp-theme-text',   { barId:'bar-theme-text',   onColor: function(c){ _setSwatchBar('bar-theme-text',c);   _onThemeColorChange && _onThemeColorChange(); } });
    _wireCP('inp-theme-accent', { barId:'bar-theme-accent', onColor: function(c){ _setSwatchBar('bar-theme-accent',c); _onThemeColorChange && _onThemeColorChange(); } });
    /* Table: shading + border color */
    /* Table shading / border color — write into the HTML-overlay table's styleOpts.
       (Previously these targeted _canvas.getActiveObject(), which is null when an
       HTML table is selected → the pickers silently did nothing.) */
    _wireCP('fmt-tbl-shading', { noFill:true,
      onColor: function(c){
        if (_selectedHtmlTableSlide >= 0) { _tblApplyStyleOpt('shadingColor', c); return; }
        var o=_canvas.getActiveObject(); if(o){o.set('fill',c);_canvas.requestRenderAll();saveState();}
      },
      onNoFill: function(){
        if (_selectedHtmlTableSlide >= 0) { _tblApplyStyleOpt('shadingColor', null); return; }
        var o=_canvas.getActiveObject(); if(o){o.set('fill','transparent');_canvas.requestRenderAll();saveState();}
      },
    });
    _wireCP('fmt-tbl-border-color', { barId:'fmt-tbl-border-bar',
      onColor: function(c){
        _setSwatchBar('fmt-tbl-border-bar', c);
        if (_selectedHtmlTableSlide >= 0) { _tblApplyStyleOpt('borderColor', c); return; }
        var o=_canvas.getActiveObject(); if(o){o.set('stroke',c);_canvas.requestRenderAll();saveState();}
      },
    });
    /* Shape Format: Text Color (for textbox objects) */
    _wireCP('fmt-text-color', {
      barId: 'bar-fmt-text-color',
      getColor: function() { var o=_canvas.getActiveObject(); return o&&typeof o.fill==='string'?o.fill:null; },
      onColor: function(c) {
        _setSwatchBar('bar-fmt-text-color',c);
        var o=_canvas.getActiveObject(); if(!o) return;
        o.set('fill',c); _canvas.requestRenderAll(); saveState();
      },
    });
    /* Shape Format: Text Highlight Color (backgroundColor) */
    _wireCP('fmt-text-highlight', {
      barId: 'bar-fmt-text-highlight',
      noFill: true,
      getColor: function() { var o=_canvas.getActiveObject(); return o&&typeof o.backgroundColor==='string'?o.backgroundColor:null; },
      onColor: function(c) {
        _setSwatchBar('bar-fmt-text-highlight',c);
        var o=_canvas.getActiveObject(); if(!o) return;
        o.set('backgroundColor',c); _canvas.requestRenderAll(); saveState();
      },
      onNoFill: function() {
        var o=_canvas.getActiveObject(); if(!o) return;
        o.set('backgroundColor',''); _canvas.requestRenderAll(); saveState();
      },
    });
    /* Shape Format: Text Outline Color */
    _wireCP('fmt-text-outline', {
      barId: 'bar-fmt-text-outline',
      noFill: true,
      getColor: function() { var o=_canvas.getActiveObject(); return o&&typeof o.stroke==='string'?o.stroke:null; },
      onColor: function(c) {
        _setSwatchBar('bar-fmt-text-outline',c);
        var o=_canvas.getActiveObject(); if(!o) return;
        o.set({ stroke: c, paintFirst: 'stroke' });
        if (!o.strokeWidth || o.strokeWidth === 0) o.set('strokeWidth', 1);
        _canvas.requestRenderAll(); saveState();
      },
      onNoFill: function() {
        var o=_canvas.getActiveObject(); if(!o) return;
        o.set({ stroke: null, strokeWidth: 0 }); _canvas.requestRenderAll(); saveState();
      },
    });
    _onchange('fmt-text-stroke-w', function(v) {
      var o=_canvas.getActiveObject(); if(!o) return;
      var w = parseFloat(v) || 0;
      o.set({ strokeWidth: w, paintFirst: 'stroke' });
      _canvas.requestRenderAll(); saveState();
    });
    /* Equation: text color */
    _wireCP('fmt-eq-color', {
      barId: 'fmt-eq-color-bar',
      onColor: function(c){ _setSwatchBar('fmt-eq-color-bar',c); var o=_canvas.getActiveObject(); if(o&&o.editorType==='formula'){o.set('fill',c);_canvas.requestRenderAll();saveState();} },
    });
    /* Properties panel: slide background */
    _wireCP('inp-slide-bg-props', {
      onColor: function(c) { if(typeof setSlideBackground==='function') setSlideBackground(c); },
    });
    /* Shadow color */
    _wireCP('inp-shadow-color', {
      onColor: function(c) {
        var el=document.getElementById('inp-shadow-color'); if(el) el.value=c;
        /* trigger any shadow apply logic that reads inp-shadow-color */
        var ev = new Event('input'); el && el.dispatchEvent(ev);
      },
    });

    /* Props panel inputs → apply to object */
    _onchange('prop-x',     function(v) { _applyProp('left',    parseFloat(v)); });
    _onchange('prop-y',     function(v) { _applyProp('top',     parseFloat(v)); });
    _onchange('prop-angle', function(v) { _applyProp('angle',   parseFloat(v)); });
    _onchange('prop-w',     function(v) { _applyScaledWidth(parseFloat(v)); });
    _onchange('prop-h',     function(v) { _applyScaledHeight(parseFloat(v)); });

    var opSlider = document.getElementById('prop-opacity');
    var opNum    = document.getElementById('prop-opacity-num');
    function _applyOpacity(v) {
      var obj = _canvas.getActiveObject();
      if (obj) { obj.set('opacity', Math.max(0, Math.min(1, v / 100))); _canvas.requestRenderAll(); }
      if (opSlider) opSlider.value = v;
      if (opNum)    opNum.value    = v;
    }
    if (opSlider) opSlider.addEventListener('input', function() { _applyOpacity(+opSlider.value); });
    if (opNum)    opNum.addEventListener('input',    function() { _applyOpacity(+opNum.value); });

    /* Typography props */
    _onchange('prop-line-height',  function(v) {
      var obj = _canvas.getActiveObject();
      if (obj && obj.set) { obj.set('lineHeight', parseFloat(v) || 1.2); _canvas.requestRenderAll(); saveState(); }
    });
    _onchange('prop-char-spacing', function(v) {
      var obj = _canvas.getActiveObject();
      if (obj && obj.set) { obj.set('charSpacing', parseFloat(v) || 0); _canvas.requestRenderAll(); saveState(); }
    });

    /* Entrance animation props (object-level) */
    _onchange('prop-anim', function(v) {
      var obj = _canvas.getActiveObject();
      if (!obj) return;
      if (v === 'none') { delete obj.anim; }
      else {
        obj.anim = v;
        if (obj.animDur   == null) obj.animDur   = parseFloat((document.getElementById('prop-anim-dur')   || {}).value) || 0.5;
        if (obj.animDelay == null) obj.animDelay = parseFloat((document.getElementById('prop-anim-delay') || {}).value) || 0;
        if (obj.animOrder == null) obj.animOrder = parseInt((document.getElementById('prop-anim-order')   || {}).value, 10) || 0;
      }
      saveState();
      if (v !== 'none') _previewObjAnim(obj);
    });
    _onchange('prop-anim-dur',   function(v) { var o = _canvas.getActiveObject(); if (o) { o.animDur   = parseFloat(v) || 0.5; saveState(); } });
    _onchange('prop-anim-delay', function(v) { var o = _canvas.getActiveObject(); if (o) { o.animDelay = parseFloat(v) || 0;   saveState(); } });
    _onchange('prop-anim-order', function(v) { var o = _canvas.getActiveObject(); if (o) { o.animOrder = parseInt(v, 10) || 0; saveState(); } });

    /* Slide-level transition */
    _onchange('prop-slide-transition', function(v) {
      if (_slides[_currentSlide]) _slides[_currentSlide].transition = (v === 'none' ? null : v);
      if (typeof _scheduleAutoSave === 'function') _scheduleAutoSave();
    });
    _btn('btn-transition-all', function() {
      var sel = document.getElementById('prop-slide-transition');
      var v = sel ? sel.value : 'fade';
      _slides.forEach(function(s) { s.transition = (v === 'none' ? null : v); });
      if (typeof _scheduleAutoSave === 'function') _scheduleAutoSave();
      if (window.EditorSave) window.EditorSave.toast('Transition applied to all slides');
    });

    /* Lock / Group in props panel */
    _btn('btn-lock-obj',  function() { lockObject(); });
    _btn('btn-group-obj', function() {
      var sel = _canvas.getActiveObject();
      if (!sel) return;
      if (sel.type === 'activeSelection') groupObjects();
      else if (sel.type === 'group') ungroupObjects();
    });

    /* Slide panel */
    _btn('btn-dup-slide',    duplicateSlide);
    _btn('btn-dup-slide-tb', duplicateSlide);
    _btn('btn-add-slide-tb', addSlide);
    _btn('btn-present',      enterPresentation);

    /* Layer buttons in props panel */
    _btn('btn-bring-front', function() { editorCmd('bringToFront'); });
    _btn('btn-send-back',   function() { editorCmd('sendToBack');   });
    _btn('btn-bring-fwd',   function() { editorCmd('bringForward'); });
    _btn('btn-send-bwd',    function() { editorCmd('sendBackward'); });
    _btn('btn-delete-obj',  function() { editorCmd('delete');       });

    /* ── Image editor ── */
    var replaceImgBtn  = document.getElementById('btn-replace-img');
    var replaceImgFile = document.getElementById('inp-replace-img-file');
    if (replaceImgBtn && replaceImgFile) {
      replaceImgBtn.addEventListener('click', function() { replaceImgFile.click(); });
      replaceImgFile.addEventListener('change', function() {
        var file = replaceImgFile.files[0];
        if (!file) return;
        var reader = new FileReader();
        reader.onload = function(ev) {
          _replaceWithImageSrc(ev.target.result);
        };
        reader.readAsDataURL(file);
        replaceImgFile.value = '';
      });
    }
    _btn('btn-load-img-url', function() {
      var urlEl = document.getElementById('prop-img-url');
      var url = urlEl ? urlEl.value.trim() : '';
      if (!url) return;
      _replaceWithImageSrc(url, { crossOrigin: 'anonymous' });
    });
    document.querySelectorAll('[data-imgfit]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var cur = _canvas.getActiveObject();
        if (!cur || cur.type !== 'image') return;
        var cw = cur.getScaledWidth(), ch = cur.getScaledHeight();
        var fit = btn.dataset.imgfit;
        var scale;
        if (fit === 'cover')   scale = Math.max(cw / cur.width, ch / cur.height);
        else if (fit === 'contain') scale = Math.min(cw / cur.width, ch / cur.height);
        else { cur.set({ scaleX: cw / cur.width, scaleY: ch / cur.height }); _canvas.requestRenderAll(); saveState(); return; }
        cur.set({ scaleX: scale, scaleY: scale });
        _canvas.requestRenderAll();
        saveState();
      });
    });

    /* ── Equation editor ── */
    _btn('btn-apply-equation', function() {
      var cur = _canvas.getActiveObject();
      if (!cur || (cur.type !== 'textbox' && cur.type !== 'i-text' && cur.type !== 'text')) return;
      var ta = document.getElementById('prop-equation-src');
      if (!ta) return;
      cur.set('text', ta.value);
      _canvas.requestRenderAll();
      saveState();
    });

    /* ── Table editor ── */
    _btn('btn-apply-table', function() {
      var cur = _canvas.getActiveObject();
      if (!cur) return;
      var ta = document.getElementById('prop-table-src');
      if (!ta) return;
      cur.set('text', ta.value);
      _canvas.requestRenderAll();
      saveState();
    });

    /* Initial state */
    syncRibbonToSelection();
    syncPropsPanel();
    syncStatusBar();
  }

  function _setSwatchBar(id, color) {
    var el = document.getElementById(id);
    if (el) el.style.background = color;
  }

  function _applyProp(prop, val) {
    var obj = _canvas.getActiveObject();
    if (!obj || isNaN(val)) return;
    obj.set(prop, val);
    obj.setCoords && obj.setCoords();
    _canvas.requestRenderAll();
  }

  function _applyScaledWidth(newW) {
    var obj = _canvas.getActiveObject();
    if (!obj || isNaN(newW) || newW <= 0) return;
    obj.set('scaleX', newW / obj.width);
    obj.setCoords && obj.setCoords();
    _canvas.requestRenderAll();
  }

  function _applyScaledHeight(newH) {
    var obj = _canvas.getActiveObject();
    if (!obj || isNaN(newH) || newH <= 0) return;
    obj.set('scaleY', newH / obj.height);
    obj.setCoords && obj.setCoords();
    _canvas.requestRenderAll();
  }

  function _btn(id, fn) {
    const el = document.getElementById(id);
    if (el) el.addEventListener('click', fn);
  }

  function _onchange(id, fn) {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', function () { fn(el.value); });
  }

  /* Like _onchange but fires on commit (Enter/blur) only — for inputs whose
     handler re-renders + pushes an undo entry (avoid one entry per keystroke). */
  function _oncommit(id, fn) {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', function () { fn(el.value); });
  }

  /* ════════════════════════════════════════════════════════
   *  SECTION 10 — Entry Point
   * ════════════════════════════════════════════════════════ */
  function init() {
    /* Capture clean DOM before Fabric.js wraps <canvas> in its own container divs.
       By the time init() fires (DOMContentLoaded / readyState check), ALL script
       elements are parsed and present in the DOM, so outerHTML is complete.
       exportHTMLFile() uses this snapshot instead of the Fabric-mutated live DOM. */
    if (!window._RAW_PAGE_HTML) {
      window._RAW_PAGE_HTML = document.documentElement.outerHTML;
    }
    if (typeof fabric === 'undefined') {
      console.error('[FabricEditor] Fabric.js not loaded. Add CDN <script> before this file.');
      /* Show on-screen error so it's visible without DevTools */
      const errEl = document.getElementById('ed-error');
      if (errEl) {
        errEl.style.display = 'flex';
        const detail = document.getElementById('ed-error-detail');
        if (detail) detail.textContent =
          'window.fabric không tồn tại.\n' +
          'Đảm bảo script CDN fabric.min.js được load TRƯỚC fabric_editor.js.';
      }
      return;
    }

    buildShell();
    initBoundingBox();   // must run before initCanvas so prototype defaults are set
    initCanvas();
    _applyRotateRenderer();
    initHistory();       // registers canvas events + saves initial snapshot
    initSnapGuides();    // overlay canvas for alignment lines
    initSlides();        // slide panel + thumbnail management
    autoScale();

    /* End a table cell click-and-drag range selection wherever the mouse is released —
       must be on document (not the cell itself) since the mouseup may land outside the table. */
    document.addEventListener('mouseup', function() { _tblDragging = false; });

    let _resizeTimer = null;
    window.addEventListener('resize', function () {
      clearTimeout(_resizeTimer);
      _resizeTimer = setTimeout(autoScale, 60);
    });
    document.addEventListener('fullscreenchange', function() {
      clearTimeout(_resizeTimer);
      _resizeTimer = setTimeout(autoScale, 100);
    });

    /* Save to localStorage immediately before the page unloads (covers Ctrl+R / tab close).
       This bypasses the 2-second debounce so no edits are lost on a quick refresh. */
    window.addEventListener('beforeunload', function() {
      if (_autoSaveTimer) { clearTimeout(_autoSaveTimer); _autoSaveTimer = null; }
      _autoSaveDo();
    });

    bindToolbar();
    bindKeys();
    bindContextMenu();
    initThemePanel();
    _syncUndoRedoBtns();

    console.info('[FabricEditor] Ready — canvas %dx%d zoom:%d%%',
      SLIDE_W, SLIDE_H, Math.round(_canvas.getZoom() * 100));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  /* ════════════════════════════════════════════════════════
   *  Public API
   * ════════════════════════════════════════════════════════ */
  window.FabricEditor = {
    /* Canvas access */
    getCanvas:          function () { return _canvas; },
    /* Viewport */
    autoScale:          autoScale,
    zoomIn:             zoomIn,
    zoomOut:            zoomOut,
    /* Object builders */
    addText:            addText,
    addRect:            addRect,
    addRoundedRect:     addRoundedRect,
    addCircle:          addCircle,
    addTriangle:        addTriangle,
    addDiamond:         addDiamond,
    addStar:            addStar,
    addLine:            addLine,
    addArrow:           addArrow,
    addImage:           addImage,
    addBulletList:      addBulletList,
    /* Command dispatcher */
    editorCmd:          editorCmd,
    /* Slide background */
    setSlideBackground: setSlideBackground,
    /* Alignment / distribution */
    alignOnSlide:       alignOnSlide,
    distributeObjects:  distributeObjects,
    /* Object operations */
    lockObject:         lockObject,
    groupObjects:       groupObjects,
    ungroupObjects:     ungroupObjects,
    /* History */
    undo:               undo,
    redo:               redo,
    saveState:          saveState,
    /* Slide management */
    addSlide:           addSlide,
    duplicateSlide:     duplicateSlide,
    deleteSlide:        deleteSlide,
    gotoSlide:          gotoSlide,
    getSlides:          function () { return _slides; },
    /* Presentation */
    enterPresentation:  enterPresentation,
    exitPresentation:   exitPresentation,
    /* Export / Import */
    exportCurrentPNG:   exportCurrentPNG,
    exportAllPNG:       exportAllPNG,
    captureAllPNG:      captureAllPNG,   /* headless benchmark/eval capture (static, full-res) */
    exportJSONFile:     exportJSONFile,
    importJSONFile:     importJSONFile,
    exportJSON:         exportJSON,
    loadJSON:           loadJSON,
    loadFromSlideSpec:  loadFromSlideSpec,
  };

  window.editorCmd = editorCmd;

}());
