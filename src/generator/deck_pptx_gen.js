'use strict';
const PptxGenJS = require('pptxgenjs');
const fs   = require('fs');

// cli
const argv = process.argv.slice(2);
const get  = f => { const i = argv.indexOf(f); return i >= 0 ? argv[i + 1] : null; };
const manifestPath = get('--manifest');
const outputPath   = get('--output');
if (!manifestPath || !outputPath) {
  console.error('Usage: node deck_pptx_gen.js --manifest <path> --output <path>');
  process.exit(1);
}
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

// theme
const T      = manifest.theme || {};
const BGDARK = T.bg_dark    || '500014';
const BGLIT  = T.bg_light   || 'F5F3EE';
const ACC    = T.accent     || 'FFCC33';
const FGDRK  = T.text_dark  || 'FFFFFF';
const FGLIT  = T.text_light || '0B0D12';
const DIMDRK = T.dim_dark   || 'AAAAAA';
const DIMLIT = T.dim_light  || '888888';

const FDISP = T.font_display || 'Playfair Display';
const FBODY = T.font_body    || 'Source Sans 3';
const FMONO = T.font_mono    || 'IBM Plex Mono';

const C1 = T.c1 || '5b9bd5';
const C2 = T.c2 || 'e07b6a';
const C3 = T.c3 || '7b68c8';
const C4 = T.c4 || 'f0a050';
const CARD_COLORS = [C1, C2, C3, C4];

const GRAD_STOPS = (manifest.theme && manifest.theme.grad_stops) || [];

function setBg(slide, isLight) {
  slide.background = { color: isLight ? BGLIT : BGDARK };
}

function buildGradBgXml(stops) {
  const gsLst = stops.map(([c, p]) =>
    `<a:gs pos="${Math.round(p * 100000)}"><a:srgbClr val="${c.toUpperCase()}"/></a:gs>`
  ).join('');
  return `<p:bg><p:bgPr><a:gradFill rotWithShape="1"><a:gsLst>${gsLst}</a:gsLst>` +
    `<a:lin ang="2700000" scaled="0"/></a:gradFill><a:effectLst/></p:bgPr></p:bg>`;
}

async function applyGradBg(buffer, stops, darkHex) {
  const JSZip   = require('jszip');
  const zip     = await JSZip.loadAsync(buffer);
  const gradXml = buildGradBgXml(stops);
  const darkVal = darkHex.toUpperCase();
  const slideRe = /^ppt\/slides\/slide\d+\.xml$/;
  await Promise.all(
    Object.keys(zip.files).filter(n => slideRe.test(n)).map(async name => {
      let xml = await zip.files[name].async('string');
      if (/<p:bg\b/.test(xml) && xml.includes(`val="${darkVal}"`)) {
        xml = xml.replace(/<p:bg\b[\s\S]*?<\/p:bg>/, gradXml);
      }
      zip.file(name, xml);
    })
  );
  return zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
}

function fgC(light)  { return light ? FGLIT  : FGDRK;  }
function dimC(light) { return light ? DIMLIT : DIMDRK; }

// geometry — LAYOUT_16x9: 10" × 5.625"
const W  = 10.00;
const H  = 5.625;
const MX = 0.50;              // horizontal margin
const BW = W - MX * 2;       // full body width = 9.0"

const CTOP_Y = 0.26, CTOP_H = 0.21;  // chrome-top band
const CBOT_Y = 5.18, CBOT_H = 0.21;  // chrome-bot band
const TTL_Y  = 0.58, TTL_H  = 0.80;  // title text box (bottom-aligned; 3 lines at 19pt fits)
const ULN_Y  = 1.41, ULN_H  = 0.04, ULN_W = 1.05;  // accent underline
const BODY_Y = 1.55;
const BODY_H = CBOT_Y - BODY_Y - 0.08;  // ≈ 3.55"

// Two-column split (55 / 45)
const LHS_W = BW * 0.55;
const RHS_X = MX + LHS_W + 0.15;
const RHS_W = BW - LHS_W - 0.15;

// latex-readable-unicode-text
const LMAP = {
  '\\leq':'≤','\\geq':'≥','\\neq':'≠','\\approx':'≈','\\equiv':'≡',
  '\\times':'×','\\cdot':'·','\\infty':'∞','\\pm':'±','\\mp':'∓',
  '\\alpha':'α','\\beta':'β','\\gamma':'γ','\\delta':'δ',
  '\\epsilon':'ε','\\varepsilon':'ε','\\zeta':'ζ','\\eta':'η','\\theta':'θ',
  '\\iota':'ι','\\kappa':'κ','\\lambda':'λ','\\mu':'μ',
  '\\nu':'ν','\\xi':'ξ','\\pi':'π','\\rho':'ρ',
  '\\sigma':'σ','\\tau':'τ','\\phi':'φ','\\varphi':'φ','\\chi':'χ',
  '\\psi':'ψ','\\omega':'ω',
  '\\Gamma':'Γ','\\Delta':'Δ','\\Theta':'Θ','\\Lambda':'Λ',
  '\\Xi':'Ξ','\\Pi':'Π','\\Sigma':'Σ','\\Phi':'Φ','\\Psi':'Ψ','\\Omega':'Ω',
  '\\rightarrow':'→','\\leftarrow':'←','\\Rightarrow':'⇒','\\Leftarrow':'⇐',
  '\\leftrightarrow':'↔','\\longrightarrow':'⟶','\\longleftarrow':'⟵',
  '\\sum':'∑','\\prod':'∏','\\int':'∫','\\oint':'∮',
  '\\sqrt':'√','\\nabla':'∇','\\partial':'∂',
  '\\forall':'∀','\\exists':'∃','\\in':'∈','\\notin':'∉',
  '\\subset':'⊂','\\supset':'⊃','\\subseteq':'⊆','\\supseteq':'⊇',
  '\\cup':'∪','\\cap':'∩','\\emptyset':'∅',
  '\\not':'¬','\\neg':'¬','\\wedge':'∧','\\vee':'∨',
  '\\langle':'⟨','\\rangle':'⟩','\\|':'‖',
  '\\cdots':'⋯','\\ldots':'…','\\vdots':'⋮','\\ddots':'⋱',
  '\\to':'→','\\gets':'←','\\mapsto':'↦',
  '\\le':'≤','\\ge':'≥','\\ne':'≠','\\ll':'≪','\\gg':'≫',
  '\\propto':'∝','\\sim':'∼','\\simeq':'≃','\\cong':'≅',
  '\\text':'',
};

// Unicode superscript / subscript maps
const SUP_MAP = {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵',
                 '6':'⁶','7':'⁷','8':'⁸','9':'⁹',
                 'n':'ⁿ','i':'ⁱ','+':'⁺','-':'⁻','=':'⁼','(':'⁽',')':'⁾'};
const SUB_MAP = {'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅',
                 '6':'₆','7':'₇','8':'₈','9':'₉',
                 'n':'ₙ','i':'ᵢ','j':'ⱼ','k':'ₖ','m':'ₘ','p':'ₚ',
                 'a':'ₐ','e':'ₑ','o':'ₒ','u':'ᵤ','x':'ₓ'};

function _mapStr(str, map) {
  return str.split('').map(c => map[c] || c).join('');
}

function toMathText(text) {
  // Full conversion of LaTeX to unicode math text (for formula boxes)
  if (!text) return '';
  text = String(text);
  // Strip display delimiters
  text = text.replace(/\$\$(.+?)\$\$/gs, '$1');
  text = text.replace(/\$(.+?)\$/gs,     '$1');
  text = text.replace(/\\\[(.+?)\\\]/gs, '$1');
  text = text.replace(/\\\((.+?)\\\)/gs, '$1');
  // Fractions: \frac{a}{b} → (a)/(b)
  text = text.replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, '($1)/($2)');
  // Superscripts: ^{abc} or ^x
  text = text.replace(/\^\{([^}]+)\}/g, (_, g) => _mapStr(g, SUP_MAP));
  text = text.replace(/\^([a-zA-Z0-9])/g, (_, c) => SUP_MAP[c] || `^${c}`);
  // Subscripts: _{abc} or _x
  text = text.replace(/\_\{([^}]+)\}/g, (_, g) => _mapStr(g, SUB_MAP));
  text = text.replace(/\_([a-zA-Z0-9])/g, (_, c) => SUB_MAP[c] || `_${c}`);
  // sqrt: \sqrt{x} → √x, \sqrt[n]{x} → ⁿ√x
  text = text.replace(/\\sqrt\[([^\]]+)\]\{([^{}]*)\}/g, (_, n, x) => `${_mapStr(n, SUP_MAP)}√(${x})`);
  text = text.replace(/\\sqrt\{([^{}]*)\}/g, (_, x) => `√(${x})`);
  // Apply symbol map
  for (const [k, v] of Object.entries(LMAP)) text = text.split(k).join(v);
  // Remove remaining LaTeX commands with braced args
  text = text.replace(/\\[a-zA-Z]+\{([^}]*)\}/g, '$1');
  text = text.replace(/\\[a-zA-Z]+/g, '');
  text = text.replace(/[{}]/g, '');
  // Alignment markers
  text = text.replace(/&/g, '  ').replace(/\\\\/g, '\n');
  return text.replace(/[ \t]+/g, ' ').trim();
}

function toPlain(text) {
  // Simplified version for bullets/titles (strips math, no unicode conversion)
  if (!text) return '';
  text = String(text);
  text = text.replace(/\$\$(.+?)\$\$/gs, '$1');
  text = text.replace(/\$(.+?)\$/gs,     '$1');
  text = text.replace(/\\\[(.+?)\\\]/gs, '$1');
  text = text.replace(/\\\((.+?)\\\)/gs, '$1');
  for (const [k, v] of Object.entries(LMAP)) text = text.split(k).join(v);
  text = text.replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, '($1)/($2)');
  text = text.replace(/\^\{([^}]+)\}/g, (_, g) => _mapStr(g, SUP_MAP));
  text = text.replace(/\^([a-zA-Z0-9])/g, (_, c) => SUP_MAP[c] || `^${c}`);
  text = text.replace(/\_\{([^}]+)\}/g, (_, g) => _mapStr(g, SUB_MAP));
  text = text.replace(/\_([a-zA-Z0-9])/g, (_, c) => SUB_MAP[c] || `_${c}`);
  text = text.replace(/\\[a-zA-Z]+\{([^}]*)\}/g, '$1');
  text = text.replace(/\\[a-zA-Z]+/g, '');
  text = text.replace(/[{}]/g, '');
  return text.replace(/\s+/g, ' ').trim();
}

// pptxgenjs-instance
const pptx    = new PptxGenJS();
pptx.layout   = 'LAYOUT_16x9';
pptx.title    = manifest.lecture_title || '';
pptx.author   = manifest.speaker       || '';
pptx.subject  = manifest.lecture_title || '';
pptx.company  = manifest.institution   || '';
pptx.revision = '1';

// shared-helpers
function addChrome(slide, isLight, pageNum) {
  const dim = dimC(isLight);
  const lt  = (manifest.lecture_title || '');
  const lbl = lt.length > 58 ? lt.slice(0, 58) + '…' : lt;

  // Accent dot
  slide.addShape(pptx.ShapeType.ellipse, {
    x: MX, y: CTOP_Y + 0.07, w: 0.08, h: 0.08,
    fill: { color: ACC }, line: { type: 'none' },
  });
  // Lecture title (truncated)
  if (lbl) {
    slide.addText(lbl, {
      x: MX + 0.14, y: CTOP_Y, w: BW - 0.14 - 1.13, h: CTOP_H,
      fontSize: 9, color: dim, fontFace: FMONO, valign: 'middle', charSpacing: 1.5,
    });
  }
  // Date
  if (manifest.date) {
    slide.addText(manifest.date, {
      x: MX, y: CBOT_Y, w: 2.25, h: CBOT_H,
      fontSize: 8, color: dim, fontFace: FMONO, valign: 'middle',
    });
  }
  // Page number
  if (pageNum) {
    slide.addText(String(pageNum), {
      x: W - MX - 0.68, y: CBOT_Y, w: 0.68, h: CBOT_H,
      fontSize: 8, color: ACC, fontFace: FMONO, valign: 'middle', align: 'right',
    });
  }
}

function titleFontSize(title) {
  const len = (title || '').length;
  if (len > 120) return 15;
  if (len > 90)  return 17;
  if (len > 70)  return 19;
  if (len > 50)  return 20;
  return 22;
}

function addTitle(slide, title, isLight, x, y, w, h, fontSize) {
  x  = (x  !== undefined) ? x  : MX;
  y  = (y  !== undefined) ? y  : TTL_Y;
  w  = (w  !== undefined) ? w  : BW;
  h  = (h  !== undefined) ? h  : TTL_H;
  fontSize = fontSize || titleFontSize(title);
  slide.addText(toPlain(title), {
    x, y, w, h, fontSize,
    fontFace: FDISP, color: fgC(isLight),
    valign: 'bottom', wrap: true, shrinkText: true,
  });
  // Accent underline bar below title
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: ULN_Y, w: ULN_W, h: ULN_H,
    fill: { color: ACC }, line: { type: 'none' },
  });
}

// Estimate font size so all bullets fit within w × h at render time
function bulletFontSize(bullets, w, h) {
  const effectiveW = Math.max(0.5, w - 0.22);
  for (const fs of [11, 10, 9]) {
    const charW   = fs * 0.011;  // conservative: accounts for word-boundary wrap waste
    const lineH   = fs * 1.25 / 72;
    const paraGap = 5 / 72;
    let totalH = 0.08;
    for (const b of bullets) {
      const charsPerLine = Math.max(1, Math.floor(effectiveW / charW));
      const lines = Math.max(1, Math.ceil((b || '').length / charsPerLine));
      totalH += lines * lineH + paraGap;
    }
    if (totalH <= h) return fs;
  }
  return 9;
}

// Minimum height for bullets at 10pt — conservative estimate for image layout positioning.
// Uses wider charW and larger line height to account for Vietnamese diacritics + word-wrap waste.
// Returns value × 1.3 safety factor so image is positioned far enough below bullet box.
function bulletsMinH(bullets, w) {
  const fs = 10;
  const ew = Math.max(0.5, w - 0.22);
  const cw = fs * 0.013, lh = fs * 1.35 / 72, pg = 6 / 72;
  let h = 0.18;
  for (const b of bullets) {
    const cpl = Math.max(1, Math.floor(ew / cw));
    h += Math.max(1, Math.ceil((b || '').length / cpl)) * lh + pg;
  }
  return h * 1.3;
}

// autoFit=true → fit:'resize' (spAutoFit — box grows to fit text); default → shrinkText (font shrinks)
function addBullets(slide, bullets, x, y, w, h, isLight, fontSize, autoFit) {
  if (!bullets || !bullets.length) return;
  const fs = fontSize || bulletFontSize(bullets, w, h);
  const items = bullets.map(b => ({
    text: toPlain(b),
    options: {
      bullet:        { code: '25B8', color: ACC },
      paraSpaceAfter: 5,
      color:         fgC(isLight),
      fontFace:      FBODY,
    },
  }));
  const fitOpt = autoFit ? { fit: 'resize' } : { shrinkText: true };
  slide.addText(items, { x, y, w, h, fontSize: fs, valign: 'top', margin: [0.05, 0.08, 0.05, 0], ...fitOpt });
}

function addImage(slide, imgPath, x, y, w, h) {
  if (!imgPath || !fs.existsSync(imgPath)) return;
  slide.addImage({
    path: imgPath, x, y, w, h,
    sizing: { type: 'contain', w, h },
  });
}

// cover
function buildCover(s) {
  const slide = pptx.addSlide();
  setBg(slide, false);

  const title   = toPlain(s.title || manifest.lecture_title || '');
  const speaker = s.speaker || manifest.speaker || '';
  const date    = manifest.date || '';

  // Left accent bar
  slide.addShape(pptx.ShapeType.rect, {
    x: MX - 0.21, y: 0.90, w: 0.05, h: H - 1.95,
    fill: { color: ACC }, line: { type: 'none' },
  });
  // Accent line above footer
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: H - 1.20, w: BW, h: 0.03,
    fill: { color: ACC }, line: { type: 'none' },
  });
  // Title
  slide.addText(title, {
    x: MX, y: 0.90, w: BW, h: H - 2.40,
    fontSize: 42, fontFace: FDISP,
    color: FGDRK, valign: 'middle', wrap: true, shrinkText: true,
    shadow: { type: 'outer', color: '000000', blur: 10, offset: 3, angle: 45, opacity: 0.40 },
  });
  // Footer
  const footer = [speaker, date].filter(Boolean).join('   ·   ');
  if (footer) {
    slide.addText(footer, {
      x: MX, y: H - 1.09, w: BW, h: 0.68,
      fontSize: 15, fontFace: FBODY, color: DIMDRK, valign: 'middle',
    });
  }
}

// toc
function buildToc(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);

  // Heading at top — mirrors HTML .toc h2 (full-width, top of body)
  slide.addText('Outline.', {
    x: MX, y: TTL_Y, w: BW, h: TTL_H,
    fontSize: 42, fontFace: FDISP, color: fgC(isLight), valign: 'bottom', shrinkText: true,
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: ULN_Y, w: ULN_W, h: ULN_H,
    fill: { color: ACC }, line: { type: 'none' },
  });

  const items = s.toc_items || [];
  const n = items.length;
  if (!n) return;

  // 2-column grid — mirrors HTML .toc-grid { grid-template-columns: 1fr 1fr }
  const COLS = 2;
  const colGap = 0.15;
  const colW = (BW - colGap) / COLS;
  const rows = Math.ceil(n / COLS);
  const rowH = Math.min(0.64, BODY_H / rows);
  // Scale title font with row count so all items render at the same size
  const titleFs = rows <= 2 ? 18 : rows <= 3 ? 16 : rows <= 4 ? 14 : 12;

  items.forEach((item, i) => {
    const col = i % COLS;
    const row = Math.floor(i / COLS);
    const x  = MX + col * (colW + colGap);
    const iy = BODY_Y + row * rowH;
    // Top divider line
    slide.addShape(pptx.ShapeType.rect, {
      x, y: iy, w: colW, h: 0.014,
      fill: { color: fgC(isLight), transparency: 75 }, line: { type: 'none' },
    });
    // Number
    slide.addText(item.n || String(i + 1).padStart(2, '0'), {
      x: x + 0.03, y: iy + 0.05, w: 0.60, h: rowH - 0.09,
      fontSize: 14, fontFace: FMONO, color: ACC, valign: 'middle',
    });
    // Title
    slide.addText(toPlain(item.t || ''), {
      x: x + 0.68, y: iy + 0.05, w: colW - 0.71, h: rowH - 0.09,
      fontSize: titleFs, fontFace: FDISP, color: fgC(isLight), valign: 'middle', wrap: true,
      shrinkText: true,
    });
  });
  // Bottom border for each column
  for (let col = 0; col < COLS; col++) {
    const colCount = items.filter((_, k) => k % COLS === col).length;
    if (colCount > 0) {
      const x  = MX + col * (colW + colGap);
      const iy = BODY_Y + colCount * rowH;
      slide.addShape(pptx.ShapeType.rect, {
        x, y: iy, w: colW, h: 0.014,
        fill: { color: fgC(isLight), transparency: 75 }, line: { type: 'none' },
      });
    }
  }
}

// bullets
function buildBullets(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const bs = s.bullets || [];
  addBullets(slide, bs, MX, BODY_Y, BW, BODY_H, isLight);
}

// formula
function _drawFormulaBox(slide, isLight, boxY, boxH) {
  const BAR_W  = 0.06;  // left accent bar width
  const boxBg  = isLight ? 'E8E4DC' : '1A0005';
  // Background panel (slight tint)
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: boxY, w: BW, h: boxH,
    fill: { color: boxBg, transparency: isLight ? 30 : 50 },
    line: { type: 'none' },
  });
  // Accent left bar — mirrors HTML border-left: 4px solid var(--accent)
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: boxY, w: BAR_W, h: boxH,
    fill: { color: ACC }, line: { type: 'none' },
  });
}

function buildFormula(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);

  const bullets  = s.bullets || [];
  const imgPath     = s.formula_image_path;
  const formulaText = s.formula_text;
  const hasBulls    = bullets.length > 0;

  if (imgPath && fs.existsSync(imgPath)) {
    const boxH = hasBulls ? BODY_H * 0.35 : BODY_H * 0.60;
    const boxY = hasBulls ? BODY_Y : BODY_Y + (BODY_H - boxH) / 2;
    _drawFormulaBox(slide, isLight, boxY, boxH);
    // Formula image (transparent bg) overlaid on top of the box
    addImage(slide, imgPath, MX + 0.08, boxY + 0.08, BW - 0.15, boxH - 0.15);
    if (hasBulls) {
      addBullets(slide, bullets, MX, BODY_Y + boxH + 0.11, BW, BODY_H - boxH - 0.15, isLight);
    }
  } else if (formulaText) {
    const boxH = hasBulls ? BODY_H * 0.35 : BODY_H * 0.60;
    const boxY = hasBulls ? BODY_Y : BODY_Y + (BODY_H - boxH) / 2;
    _drawFormulaBox(slide, isLight, boxY, boxH);
    const eqs = formulaText.split(/\n\s*\n/).map(e => toMathText(e.trim())).filter(Boolean);
    const mathFontSize = eqs.length > 3 ? 11 : eqs.length > 1 ? 12 : 15;
    slide.addText(eqs.join('\n\n'), {
      x: MX + 0.15, y: boxY + 0.11, w: BW - 0.23, h: boxH - 0.23,
      fontSize: mathFontSize, fontFace: 'Cambria Math',
      color: fgC(isLight), valign: 'middle', align: 'center',
    });
    if (hasBulls) {
      addBullets(slide, bullets, MX, boxY + boxH + 0.11, BW, BODY_H - boxH - 0.15, isLight);
    }
  }
}

// two-images-below
function buildTwoImgBelow(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);

  const bullets = s.bullets || [];
  const images  = s.images  || [];
  const nImgs   = Math.min(images.length, 2);
  const bullH   = nImgs > 0 ? BODY_H * 0.48 : BODY_H;
  const imgY    = BODY_Y + bullH + 0.10;
  const imgH    = BODY_H - bullH - 0.15;

  if (bullets.length) addBullets(slide, bullets, MX, BODY_Y, BW, bullH, isLight, 20);

  if (nImgs === 2) {
    const iW = (BW - 0.15) / 2;
    addImage(slide, images[0], MX,              imgY, iW, imgH);
    addImage(slide, images[1], MX + iW + 0.15,  imgY, iW, imgH);
  } else if (nImgs === 1) {
    addImage(slide, images[0], MX, imgY, BW, imgH);
  }
}

// two-images-right
function buildTwoImgRight(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);

  const bullets = s.bullets || [];
  const images  = s.images  || [];

  // Title in left column
  slide.addText(toPlain(s.title), {
    x: MX, y: TTL_Y, w: LHS_W, h: TTL_H,
    fontSize: titleFontSize(s.title), fontFace: FDISP, color: fgC(isLight), valign: 'bottom',
    wrap: true, shrinkText: true,
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: ULN_Y, w: ULN_W, h: ULN_H,
    fill: { color: ACC }, line: { type: 'none' },
  });

  addBullets(slide, bullets, MX, BODY_Y, LHS_W, BODY_H, isLight, 20);

  const nImgs = Math.min(images.length, 2);
  if (nImgs === 2) {
    const iH = (BODY_H - 0.11) / 2;
    addImage(slide, images[0], RHS_X, BODY_Y,             RHS_W, iH - 0.04);
    addImage(slide, images[1], RHS_X, BODY_Y + iH + 0.08, RHS_W, iH - 0.04);
  } else if (nImgs === 1) {
    addImage(slide, images[0], RHS_X, BODY_Y, RHS_W, BODY_H);
  }
}

// two-images-left — 2 stacked images on left, title + bullets on right
function buildTwoImgLeft(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);

  const bullets = s.bullets || [];
  const images  = s.images  || [];
  const nImgs   = Math.min(images.length, 2);

  if (nImgs === 2) {
    const iH = (BODY_H - 0.11) / 2;
    addImage(slide, images[0], MX, BODY_Y,             RHS_W, iH - 0.04);
    addImage(slide, images[1], MX, BODY_Y + iH + 0.08, RHS_W, iH - 0.04);
  } else if (nImgs === 1) {
    addImage(slide, images[0], MX, BODY_Y, RHS_W, BODY_H);
  }

  const rx = MX + RHS_W + 0.15;
  slide.addText(toPlain(s.title || ''), {
    x: rx, y: TTL_Y, w: LHS_W, h: TTL_H,
    fontSize: titleFontSize(s.title), fontFace: FDISP, color: fgC(isLight), valign: 'bottom',
    wrap: true, shrinkText: true,
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: rx, y: ULN_Y, w: ULN_W, h: ULN_H,
    fill: { color: ACC }, line: { type: 'none' },
  });
  addBullets(slide, bullets, rx, BODY_Y, LHS_W, BODY_H, isLight, 20);
}

// image-right
function buildImgRight(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);

  const bs  = s.bullets || [];
  const img = (s.images || [])[0];
  addBullets(slide, bs, MX, BODY_Y, LHS_W, BODY_H, isLight);
  addImage(slide, img, RHS_X, BODY_Y, RHS_W, BODY_H);
}

// image-left
function buildImgLeft(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);

  const bs  = s.bullets || [];
  const img = (s.images || [])[0];

  // Image on left
  addImage(slide, img, MX, BODY_Y - 0.38, RHS_W, BODY_H + 0.38);

  // Title and bullets on right
  const rx = MX + RHS_W + 0.15;
  slide.addText(toPlain(s.title), {
    x: rx, y: TTL_Y, w: LHS_W, h: TTL_H,
    fontSize: titleFontSize(s.title), fontFace: FDISP, color: fgC(isLight), valign: 'bottom',
    wrap: true, shrinkText: true,
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: rx, y: ULN_Y, w: ULN_W, h: ULN_H,
    fill: { color: ACC }, line: { type: 'none' },
  });
  addBullets(slide, bs, rx, BODY_Y, LHS_W, BODY_H, isLight);
}

// two-columns — HTML: no vertical divider, just gap between columns
function buildTwoCols(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);

  const cols = s.cols || [[], []];
  const GAP  = 0.38;
  const colW = (BW - GAP) / 2;

  addBullets(slide, cols[0] || [], MX,             BODY_Y, colW, BODY_H, isLight);
  addBullets(slide, cols[1] || [], MX + colW + GAP, BODY_Y, colW, BODY_H, isLight);
}

// table
function parseMdTable(md) {
  if (!md) return { headers: [], rows: [] };
  const lines  = md.split('\n').map(l => l.trim()).filter(Boolean);
  const data   = lines.filter(l => !/^\|?[-:| ]+\|?$/.test(l));
  const parse  = l => l.split(/(?<!\\)\|/).map(c => c.trim()).filter((_, _i, a) => a.length > 1);
  const rows   = data.map(parse).filter(r => r.length > 0);
  if (!rows.length) return { headers: [], rows: [] };
  const maxC   = Math.max(...rows.map(r => r.length));
  const normed = rows.map(r => [...r, ...Array(maxC - r.length).fill('')]);
  const nonE   = Array.from({length: maxC}, (_, i) => normed.some(r => (r[i] || '').trim()));
  const filt   = normed.map(r => r.filter((_, i) => nonE[i]));
  return { headers: filt[0] || [], rows: filt.slice(1) };
}

function buildTable(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);

  // Accept either parsed_table (from HTML) or table.table_markdown (JSON fallback)
  let headers = [], rows = [];
  if (s.parsed_table) {
    headers = s.parsed_table.headers || [];
    rows    = s.parsed_table.rows    || [];
  } else if (s.table) {
    const p = parseMdTable(s.table.table_markdown || '');
    headers = p.headers;
    rows    = p.rows;
  }
  if (!headers.length) {
    addBullets(slide, s.bullets || [], MX, BODY_Y, BW, BODY_H, isLight, 22);
    return;
  }

  const nCols  = headers.length;
  const nRows  = rows.length + 1;
  const rowH   = Math.min(0.52, (BODY_H - 0.50) / nRows);
  const tblH   = rowH * nRows;
  const tblTop = BODY_Y + Math.max(0, (BODY_H - tblH) / 2);

  const headBg   = BGDARK;
  const evenBg   = isLight ? 'E8E0D4' : '3D0010';
  const oddBg    = isLight ? BGLIT    : '2A0009';
  const borderC  = isLight ? 'CCBBAA' : '6A2A3A';

  const tblData = [
    headers.map(h => ({
      text: toPlain(h),
      options: {
        bold: true, color: 'FFFFFF',
        fill: { color: headBg },
        align: 'center', valign: 'middle',
        fontFace: FBODY, fontSize: 16,
      },
    })),
    ...rows.map((row, i) =>
      Array.from({length: nCols}, (_, j) => ({
        text: toPlain(row[j] || ''),
        options: {
          fill:  { color: i % 2 === 0 ? evenBg : oddBg },
          color: isLight ? FGLIT : FGDRK,
          align: 'center', valign: 'middle',
          fontFace: FBODY, fontSize: 14,
        },
      }))
    ),
  ];

  slide.addTable(tblData, {
    x: MX, y: tblTop, w: BW, rowH,
    colW: Array(nCols).fill(BW / nCols),
    border: { pt: 0.5, color: borderC },
  });

  const cap = (s.parsed_table && s.parsed_table.caption) || (s.table && s.table.table_caption);
  if (cap) {
    slide.addText(toPlain(cap), {
      x: MX, y: tblTop + tblH + 0.08, w: BW, h: 0.30,
      fontSize: 9, italic: true, color: dimC(isLight),
      fontFace: FBODY, align: 'center',
    });
  }
}

// table-above: table in top portion, bullets below
function buildTblAbove(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);

  let headers = [], rows = [];
  if (s.parsed_table) {
    headers = s.parsed_table.headers || [];
    rows    = s.parsed_table.rows    || [];
  } else if (s.table) {
    const p = parseMdTable(s.table.table_markdown || '');
    headers = p.headers;
    rows    = p.rows;
  }

  const bullets  = s.bullets || [];
  const hasBulls = bullets.length > 0;

  if (!headers.length) {
    addBullets(slide, bullets, MX, BODY_Y, BW, BODY_H, isLight);
    return;
  }

  const nCols   = headers.length;
  const nRows   = rows.length + 1;
  const maxTblH = hasBulls ? BODY_H * 0.55 : BODY_H * 0.92;
  const rowH    = Math.min(0.48, maxTblH / nRows);
  const tblH    = rowH * nRows;

  const headBg  = BGDARK;
  const evenBg  = isLight ? 'E8E0D4' : '3D0010';
  const oddBg   = isLight ? BGLIT    : '2A0009';
  const borderC = isLight ? 'CCBBAA' : '6A2A3A';

  const tblData = [
    headers.map(h => ({
      text: toPlain(h),
      options: { bold: true, color: 'FFFFFF', fill: { color: headBg }, align: 'center', valign: 'middle', fontFace: FBODY, fontSize: 14 },
    })),
    ...rows.map((row, i) =>
      Array.from({length: nCols}, (_, j) => ({
        text: toPlain(row[j] || ''),
        options: { fill: { color: i % 2 === 0 ? evenBg : oddBg }, color: isLight ? FGLIT : FGDRK, align: 'center', valign: 'middle', fontFace: FBODY, fontSize: 12 },
      }))
    ),
  ];

  slide.addTable(tblData, {
    x: MX, y: BODY_Y, w: BW, rowH,
    colW: Array(nCols).fill(BW / nCols),
    border: { pt: 0.5, color: borderC },
  });

  if (hasBulls) {
    const bullY = BODY_Y + tblH + 0.10;
    addBullets(slide, bullets, MX, bullY, BW, BODY_Y + BODY_H - bullY, isLight);
  }
}

// end
// end — HTML: large display title (320px), 3-col footer with border-top separator
function buildEnd(s) {
  const slide = pptx.addSlide();
  setBg(slide, false);
  addChrome(slide, false, s.page_num);
  // Large title
  slide.addText(toPlain(s.title || 'Thank You'), {
    x: MX, y: 0.75, w: BW, h: H - 2.40,
    fontSize: 60, fontFace: FDISP,
    color: FGDRK, align: 'left', valign: 'top', wrap: true,
  });
  // Footer separator line
  const footY = H - 1.22;
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: footY, w: BW, h: 0.014,
    fill: { color: FGDRK, transparency: 32 }, line: { type: 'none' },
  });
  // 3-column footer grid
  const colW = (BW - 0.30) / 3;
  const footItems = [
    { k: 'Presented by', v: manifest.speaker || '' },
    { k: 'Q&A Session', v: 'Open discussion' },
    { k: 'Thank you', v: '' },
  ];
  footItems.forEach((item, i) => {
    const x = MX + i * (colW + 0.15);
    slide.addText(item.k, {
      x, y: footY + 0.08, w: colW, h: 0.21,
      fontSize: 8, fontFace: FMONO, color: DIMDRK, charSpacing: 2,
    });
    if (item.v) {
      slide.addText(item.v, {
        x, y: footY + 0.30, w: colW, h: 0.34,
        fontSize: 12, fontFace: FDISP, color: FGDRK, wrap: true,
      });
    }
  });
}

// image-above
function buildImgAbove(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const img = (s.images || [])[0];
  const bullets = s.bullets || [];
  const hasImg = img && fs.existsSync(img);
  const hasBulls = bullets.length > 0;
  if (hasImg && hasBulls) {
    const minImgH = 0.55;
    const bullH = Math.min(BODY_H - minImgH - 0.11, Math.max(BODY_H * 0.32, bulletsMinH(bullets, BW)));
    const imgH  = BODY_H - bullH - 0.11;
    addImage(slide, img, MX, BODY_Y, BW, imgH);
    addBullets(slide, bullets, MX, BODY_Y + imgH + 0.08, BW, bullH, isLight, null, true);
  } else if (hasImg) {
    addImage(slide, img, MX, BODY_Y, BW, BODY_H);
  } else if (hasBulls) {
    addBullets(slide, bullets, MX, BODY_Y, BW, BODY_H, isLight);
  }
}

// image-below
function buildImgBelow(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const img = (s.images || [])[0];
  const bullets = s.bullets || [];
  const hasImg = img && fs.existsSync(img);
  const hasBulls = bullets.length > 0;
  if (hasImg && hasBulls) {
    const minImgH = 0.55;
    const bullH = Math.min(BODY_H - minImgH - 0.11, Math.max(BODY_H * 0.32, bulletsMinH(bullets, BW)));
    addBullets(slide, bullets, MX, BODY_Y, BW, bullH, isLight, null, true);
    addImage(slide, img, MX, BODY_Y + bullH + 0.08, BW, BODY_H - bullH - 0.11);
  } else if (hasImg) {
    addImage(slide, img, MX, BODY_Y, BW, BODY_H);
  } else if (hasBulls) {
    addBullets(slide, bullets, MX, BODY_Y, BW, BODY_H, isLight);
  }
}

// two-images-above
function buildTwoImgAbove(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const images = s.images || [];
  const bullets = s.bullets || [];
  const nImgs = Math.min(images.length, 2);
  const hasBulls = bullets.length > 0;
  const imgH = hasBulls ? BODY_H * 0.52 : BODY_H;
  if (nImgs === 2) {
    const iW = (BW - 0.15) / 2;
    addImage(slide, images[0], MX, BODY_Y, iW, imgH);
    addImage(slide, images[1], MX + iW + 0.15, BODY_Y, iW, imgH);
  } else if (nImgs === 1) {
    addImage(slide, images[0], MX, BODY_Y, BW, imgH);
  }
  if (hasBulls) {
    addBullets(slide, bullets, MX, BODY_Y + imgH + 0.08, BW, BODY_H - imgH - 0.11, isLight);
  }
}

// two-contents — HTML: subtitle has border-bottom:2px accent; no vertical divider
function buildTwoContents(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const blocks = s.blocks || [];
  const GAP  = 0.38;
  const colW = (BW - GAP) / 2;
  blocks.slice(0, 2).forEach((block, i) => {
    const x       = i === 0 ? MX : MX + colW + GAP;
    const subtitle = block.subtitle || '';
    const bullets  = block.bullets || [];
    if (subtitle) {
      slide.addText(toPlain(subtitle), {
        x, y: BODY_Y, w: colW, h: 0.39,
        fontSize: 21, fontFace: FDISP, color: fgC(isLight), bold: false, valign: 'bottom',
        shrinkText: true,
      });
      // Accent underline — mirrors border-bottom: 2px solid var(--accent)
      slide.addShape(pptx.ShapeType.rect, {
        x, y: BODY_Y + 0.33, w: colW, h: 0.017,
        fill: { color: ACC }, line: { type: 'none' },
      });
      addBullets(slide, bullets, x, BODY_Y + 0.41, colW, BODY_H - 0.45, isLight);
    } else {
      addBullets(slide, bullets, x, BODY_Y, colW, BODY_H, isLight);
    }
  });
}

// steps — HTML: .track::before = single horizontal line at circle center (y:38px), opacity:0.3
function buildSteps(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const steps = s.steps || [];
  const n = Math.min(steps.length, 5);
  if (!n) return;
  const stepW = BW / n;
  const stepY = BODY_Y + 0.11;
  const stepH = BODY_H - 0.15;
  const CIR_D = 0.40;
  // Single horizontal connecting line through circle centers (track::before)
  const lineY = stepY + CIR_D / 2 - 0.008;
  slide.addShape(pptx.ShapeType.rect, {
    x: MX + stepW / 2, y: lineY, w: BW - stepW, h: 0.017,
    fill: { color: ACC, transparency: 70 }, line: { type: 'none' },
  });
  steps.slice(0, n).forEach((step, i) => {
    const x   = MX + i * stepW;
    const cx  = x + stepW / 2 - CIR_D / 2;
    const isCurrent = !!step.current;
    // Circle — filled or outlined for "current" step
    slide.addShape(pptx.ShapeType.ellipse, {
      x: cx, y: stepY, w: CIR_D, h: CIR_D,
      fill: isCurrent ? { type: 'none' } : { color: ACC },
      line: isCurrent ? { color: ACC, pt: 2.0 } : { type: 'none' },
    });
    slide.addText(toPlain(step.num || String(i + 1)), {
      x: cx, y: stepY, w: CIR_D, h: CIR_D,
      fontSize: 14, fontFace: FDISP, color: isCurrent ? ACC : (isLight ? FGLIT : BGDARK),
      bold: true, align: 'center', valign: 'middle',
    });
    if (step.title) {
      slide.addText(toPlain(step.title), {
        x, y: stepY + CIR_D + 0.14, w: stepW - 0.08, h: 0.60,
        fontSize: 18, fontFace: FDISP, color: fgC(isLight),
        bold: false, align: 'left', wrap: true, valign: 'top', shrinkText: true,
      });
    }
    if (step.body) {
      slide.addText(toPlain(step.body), {
        x, y: stepY + CIR_D + 0.79, w: stepW - 0.08, h: stepH - CIR_D - 0.83,
        fontSize: 14, fontFace: FBODY, color: dimC(isLight),
        align: 'left', wrap: true, valign: 'top', shrinkText: true,
      });
    }
  });
}

// key-points — HTML: each .pt has border-left:4px accent + rgba bg + border-radius
function buildKeyPoints(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const points = s.points || [];
  const n = points.length;
  if (!n) return;
  const GAP  = 0.06;
  const rowH = (BODY_H - GAP * (n - 1)) / n;
  const titleFs = rowH >= 0.78 ? 16 : rowH >= 0.62 ? 14 : 12;
  const bodyFs  = rowH >= 0.78 ? 12 : rowH >= 0.62 ? 11 : 10;
  const titleH  = rowH * 0.48;
  const bodyY_off = rowH * 0.54;
  const bodyH   = rowH - bodyY_off - 0.04;
  points.forEach((pt, i) => {
    const y = BODY_Y + i * (rowH + GAP);
    // Semi-transparent background
    slide.addShape(pptx.ShapeType.rect, {
      x: MX, y, w: BW, h: rowH,
      fill: { color: isLight ? '000000' : 'FFFFFF', transparency: 96 },
      line: { type: 'none' },
    });
    // Left accent bar
    slide.addShape(pptx.ShapeType.rect, {
      x: MX, y, w: 0.04, h: rowH,
      fill: { color: ACC }, line: { type: 'none' },
    });
    // Index
    slide.addText(toPlain(pt.ix || `P·${String(i + 1).padStart(2, '0')}`), {
      x: MX + 0.11, y: y + 0.05, w: 0.54, h: rowH - 0.09,
      fontSize: titleFs - 2, fontFace: FMONO, color: ACC, valign: 'middle',
    });
    // Title
    slide.addText(toPlain(pt.title || ''), {
      x: MX + 0.68, y: y + 0.05, w: BW - 0.71, h: titleH,
      fontSize: titleFs, fontFace: FBODY, color: fgC(isLight), bold: true, valign: 'top',
      shrinkText: true,
    });
    // Body
    slide.addText(toPlain(pt.body || ''), {
      x: MX + 0.68, y: y + bodyY_off, w: BW - 0.71, h: bodyH,
      fontSize: bodyFs, fontFace: FBODY, color: dimC(isLight), valign: 'top',
      shrinkText: true,
    });
  });
}

// three-columns — HTML: each .col has border-top:4px accent + rgba bg + border-radius
function buildThreeCols(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const cols = s.cols || [];
  const n = Math.min(cols.length, 3);
  if (!n) return;
  const GAP  = 0.15;
  const colW = (BW - GAP * (n - 1)) / n;
  cols.slice(0, n).forEach((col, i) => {
    const x = MX + i * (colW + GAP);
    // Column background
    slide.addShape(pptx.ShapeType.rect, {
      x, y: BODY_Y, w: colW, h: BODY_H,
      fill: { color: isLight ? '000000' : 'FFFFFF', transparency: 96 },
      line: { type: 'none' },
    });
    // Top accent border
    slide.addShape(pptx.ShapeType.rect, {
      x, y: BODY_Y, w: colW, h: 0.04,
      fill: { color: ACC }, line: { type: 'none' },
    });
    let y = BODY_Y + 0.17;
    if (col.tag) {
      slide.addText(toPlain(col.tag), {
        x: x + 0.15, y, w: colW - 0.30, h: 0.30,
        fontSize: 14, fontFace: FMONO, color: ACC, valign: 'middle',
      });
      y += 0.34;
    }
    if (col.title) {
      slide.addText(toPlain(col.title), {
        x: x + 0.15, y, w: colW - 0.30, h: 0.60,
        fontSize: 27, fontFace: FBODY, color: fgC(isLight), bold: true, valign: 'top', wrap: true,
        shrinkText: true,
      });
      y += 0.64;
    }
    if (col.body) {
      slide.addText(toPlain(col.body), {
        x: x + 0.15, y, w: colW - 0.30, h: 0.90,
        fontSize: 14, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true,
        shrinkText: true,
      });
      y += 0.94;
    }
    if (col.bullets && col.bullets.length) {
      addBullets(slide, col.bullets, x + 0.15, y, colW - 0.30, BODY_Y + BODY_H - y - 0.08, isLight, 14);
    }
  });
}

// split-contrast
function buildSplitContrast(s) {
  const slide = pptx.addSlide();
  slide.background = { color: BGDARK };
  const sides = s.sides || [];
  const halfW = W / 2;
  sides.slice(0, 2).forEach((side, i) => {
    const x = i === 0 ? 0 : halfW;
    const isDark = i === 0;
    const bgColor = isDark ? BGDARK : 'F0EDE5';
    const fgColor = isDark ? FGDRK : FGLIT;
    slide.addShape(pptx.ShapeType.rect, {
      x, y: 0, w: halfW, h: H,
      fill: { color: bgColor }, line: { type: 'none' },
    });
    if (side.tag) {
      slide.addText(toPlain(side.tag), {
        x: x + 0.38, y: 0.75, w: halfW - 0.75, h: 0.30,
        fontSize: 9, fontFace: FMONO, color: ACC, valign: 'middle', charSpacing: 2,
      });
    }
    if (side.title) {
      slide.addText(toPlain(side.title), {
        x: x + 0.38, y: 1.16, w: halfW - 0.75, h: 0.60,
        fontSize: 17, fontFace: FDISP, color: fgColor, valign: 'top', wrap: true,
      });
    }
    if (side.bullets && side.bullets.length) {
      addBullets(slide, side.bullets, x + 0.38, 1.88, halfW - 0.75, H - 2.33, !isDark, 10);
    }
    if (isDark) {
      slide.addShape(pptx.ShapeType.rect, {
        x: halfW - 0.03, y: 0.38, w: 0.03, h: H - 0.75,
        fill: { color: ACC }, line: { type: 'none' },
      });
    }
  });
}

// conclusion-cards — HTML: horizontal timeline line + dot circles above 4 color cards
function buildConclCards(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const cards = s.cards || [];
  const n = Math.min(cards.length, 4);
  if (!n) return;
  const GAP    = 0.11;
  const cardW  = (BW - GAP * (n - 1)) / n;
  const DOT_D  = 0.29;
  const DOT_Y  = BODY_Y + 0.08;
  const CARD_Y = DOT_Y + DOT_D + 0.09;
  const cardH  = CBOT_Y - CARD_Y - 0.11;
  // Horizontal connecting line through dot centers
  const lineY = DOT_Y + DOT_D / 2 - 0.008;
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: lineY, w: BW, h: 0.017,
    fill: { color: isLight ? '000000' : 'FFFFFF', transparency: 80 }, line: { type: 'none' },
  });
  cards.slice(0, n).forEach((card, i) => {
    const x  = MX + i * (cardW + GAP);
    const cc = CARD_COLORS[i % CARD_COLORS.length];
    const cx = x + cardW / 2 - DOT_D / 2;
    // Timeline dot
    slide.addShape(pptx.ShapeType.ellipse, {
      x: cx, y: DOT_Y, w: DOT_D, h: DOT_D,
      fill: { color: 'FFFFFF' }, line: { color: cc, pt: 2 },
    });
    // Card background
    slide.addShape(pptx.ShapeType.rect, {
      x, y: CARD_Y, w: cardW, h: cardH,
      fill: { color: cc }, line: { type: 'none' },
    });
    // Number circle inside card
    const numD = 0.29;
    const numX = x + cardW / 2 - numD / 2;
    slide.addShape(pptx.ShapeType.ellipse, {
      x: numX, y: CARD_Y + 0.14, w: numD, h: numD,
      fill: { color: 'FFFFFF', transparency: 75 }, line: { type: 'none' },
    });
    slide.addText(toPlain(card.num || String(i + 1)), {
      x: numX, y: CARD_Y + 0.14, w: numD, h: numD,
      fontSize: 12, fontFace: FDISP, color: 'FFFFFF', bold: true, align: 'center', valign: 'middle',
    });
    if (card.heading) {
      slide.addText(toPlain(card.heading), {
        x: x + 0.09, y: CARD_Y + 0.48, w: cardW - 0.18, h: 0.68,
        fontSize: 23, fontFace: FBODY, color: 'FFFFFF', bold: true,
        align: 'center', valign: 'top', wrap: true, shrinkText: true,
      });
    }
    if (card.body) {
      slide.addText(toPlain(card.body), {
        x: x + 0.09, y: CARD_Y + 1.19, w: cardW - 0.18, h: cardH - 1.31,
        fontSize: 14, fontFace: FBODY, color: 'FFFFFF',
        align: 'center', valign: 'top', wrap: true, shrinkText: true,
      });
    }
  });
}

// numbered-conclusions — HTML: grid 120px|1fr, number 56px display font, heading 40px display
function buildNumConcl(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const rows = s.rows || [];
  const n = rows.length;
  if (!n) return;
  // Scale fonts with row count (mirrors CSS :has() auto-scale)
  let numFs = 30, hdgFs = 23, bFs = 14;
  if (n >= 7) { numFs = 21; hdgFs = 17; bFs = 11; }
  else if (n >= 6) { numFs = 24; hdgFs = 18; bFs = 12; }
  else if (n >= 5) { numFs = 27; hdgFs = 20; bFs = 13; }
  const NUM_W = 0.62;
  const GAP   = 0.17;
  const rowH  = Math.min(0.68, BODY_H / n);
  rows.forEach((row, i) => {
    const y = BODY_Y + i * rowH;
    // Top border
    slide.addShape(pptx.ShapeType.rect, {
      x: MX, y, w: BW, h: 0.018,
      fill: { color: fgC(isLight), transparency: 75 }, line: { type: 'none' },
    });
    // Large display number (accent)
    slide.addText(toPlain(row.n || String(i + 1).padStart(2, '0')), {
      x: MX, y: y + 0.05, w: NUM_W, h: rowH - 0.10,
      fontSize: numFs, fontFace: FDISP, color: ACC, valign: 'middle',
    });
    // Heading (display font)
    slide.addText(toPlain(row.heading || ''), {
      x: MX + NUM_W + GAP, y: y + 0.06, w: BW - NUM_W - GAP, h: rowH * 0.50,
      fontSize: hdgFs, fontFace: FDISP, color: fgC(isLight), valign: 'bottom', wrap: true,
      shrinkText: true,
    });
    // Body
    slide.addText(toPlain(row.body || ''), {
      x: MX + NUM_W + GAP, y: y + rowH * 0.52, w: BW - NUM_W - GAP, h: rowH * 0.44,
      fontSize: bFs, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true,
      shrinkText: true,
    });
  });
  // Bottom border after last row
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: BODY_Y + n * rowH, w: BW, h: 0.018,
    fill: { color: fgC(isLight), transparency: 75 }, line: { type: 'none' },
  });
}

// grid-2×2
function buildGrid2x2(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const cells = s.cells || [];
  const n = Math.min(cells.length, 4);
  if (!n) return;
  const cellW = (BW - 0.15) / 2;
  const cellH = (BODY_H - 0.11) / 2;
  const borderColor = isLight ? 'CCBBAA' : '555577';
  cells.slice(0, n).forEach((cell, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = MX + col * (cellW + 0.15);
    const y = BODY_Y + row * (cellH + 0.11);
    slide.addShape(pptx.ShapeType.rect, {
      x, y, w: cellW, h: cellH,
      fill: { color: isLight ? 'E8E4DC' : '1E0008', transparency: isLight ? 20 : 30 },
      line: { color: borderColor, pt: 0.5 },
    });
    slide.addShape(pptx.ShapeType.rect, {
      x: x + 0.11, y: y + 0.11, w: 0.19, h: 0.04,
      fill: { color: ACC }, line: { type: 'none' },
    });
    slide.addText(toPlain(cell.title || ''), {
      x: x + 0.11, y: y + 0.21, w: cellW - 0.23, h: 0.49,
      fontSize: 23, fontFace: FBODY, color: fgC(isLight), bold: true, valign: 'top', wrap: true,
      shrinkText: true,
    });
    slide.addText(toPlain(cell.body || ''), {
      x: x + 0.11, y: y + 0.74, w: cellW - 0.23, h: cellH - 0.83,
      fontSize: 14, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true,
      shrinkText: true,
    });
  });
}

// research-question — HTML: main-rq box (2px accent border, rgba bg), subs in 3-col grid
function buildResearchQuestion(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const mainQ = s.main_q || '';
  const subQs = s.sub_qs || [];
  const hasSubQs = subQs.length > 0;
  const mainBoxH = hasSubQs ? 1.16 : BODY_H;
  // Main RQ box — border:2px accent + semi-transparent bg
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: BODY_Y, w: BW, h: mainBoxH,
    fill: { color: isLight ? '000000' : 'FFFFFF', transparency: 96 },
    line: { color: ACC, pt: 1.5 },
  });
  slide.addText('Main RQ', {
    x: MX + 0.17, y: BODY_Y + 0.09, w: 0.90, h: 0.21,
    fontSize: 8, fontFace: FMONO, color: ACC, valign: 'middle', charSpacing: 2,
  });
  slide.addText(toPlain(mainQ), {
    x: MX + 0.17, y: BODY_Y + 0.33, w: BW - 0.33, h: mainBoxH - 0.44,
    fontSize: 21, fontFace: FDISP, color: fgC(isLight), valign: 'top', wrap: true, italic: true,
    shrinkText: true,
  });
  if (hasSubQs) {
    // Sub-questions in 3-column grid
    const subStartY = BODY_Y + mainBoxH + 0.14;
    const subH      = CBOT_Y - subStartY - 0.11;
    const subGAP    = 0.14;
    const subW      = (BW - subGAP * 2) / 3;
    subQs.slice(0, 3).forEach((sub, i) => {
      const x = MX + i * (subW + subGAP);
      slide.addShape(pptx.ShapeType.rect, {
        x, y: subStartY, w: subW, h: subH,
        fill: { color: isLight ? '000000' : 'FFFFFF', transparency: 96 }, line: { type: 'none' },
      });
      slide.addText(toPlain(sub.lbl || `Sub-Q ${String(i + 1).padStart(2, '0')}`), {
        x: x + 0.14, y: subStartY + 0.11, w: subW - 0.27, h: 0.20,
        fontSize: 11, fontFace: FMONO, color: ACC, valign: 'middle', charSpacing: 1.5,
      });
      slide.addText(toPlain(sub.q || ''), {
        x: x + 0.14, y: subStartY + 0.33, w: subW - 0.27, h: subH - 0.42,
        fontSize: 17, fontFace: FBODY, color: fgC(isLight), valign: 'top', wrap: true, shrinkText: true,
      });
    });
  }
}

// agenda
function buildAgenda(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  slide.addText(toPlain(s.title || ''), {
    x: MX, y: 0.83, w: 2.63, h: H - 1.80,
    fontSize: 27, fontFace: FDISP, color: fgC(isLight), valign: 'middle', italic: true,
  });
  const items = s.items || [];
  const listX = 3.38;
  const listW = W - listX - MX;
  const usable = H - 1.65;
  const rowH = Math.min(0.49, usable / Math.max(items.length, 1));
  const startY = 0.94;
  items.forEach((item, i) => {
    const iy = startY + i * rowH;
    slide.addShape(pptx.ShapeType.rect, {
      x: listX, y: iy, w: listW, h: 0.014,
      fill: { color: fgC(isLight), transparency: 75 }, line: { type: 'none' },
    });
    slide.addText(String(i + 1).padStart(2, '0'), {
      x: listX, y: iy + 0.05, w: 0.41, h: rowH - 0.06,
      fontSize: 14, fontFace: FMONO, color: ACC, valign: 'middle',
    });
    slide.addText(toPlain(item.title || ''), {
      x: listX + 0.45, y: iy + 0.05,
      w: item.duration ? listW - 1.05 : listW - 0.49,
      h: rowH - 0.06,
      fontSize: 17, fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true,
      shrinkText: true,
    });
    if (item.duration) {
      slide.addText(toPlain(item.duration), {
        x: W - MX - 0.60, y: iy + 0.05, w: 0.60, h: rowH - 0.06,
        fontSize: 12, fontFace: FMONO, color: dimC(isLight), valign: 'middle', align: 'right',
      });
    }
  });
  if (items.length > 0) {
    slide.addShape(pptx.ShapeType.rect, {
      x: listX, y: startY + items.length * rowH, w: listW, h: 0.014,
      fill: { color: fgC(isLight), transparency: 75 }, line: { type: 'none' },
    });
  }
}

// quote — HTML: blockquote 96px display font, .who::before = 80px accent line + mono dim text
function buildQuote(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  // Large opening mark
  slide.addText('”', {
    x: MX, y: 0.38, w: 0.90, h: 0.83,
    fontSize: 83, fontFace: FDISP, color: ACC, valign: 'top',
  });
  const quoteEndY = s.attribution ? CBOT_Y - 0.60 : CBOT_Y - 0.15;
  slide.addText(toPlain(s.quote || ''), {
    x: MX + 0.23, y: 0.90, w: BW - 0.23, h: quoteEndY - 0.94,
    fontSize: 27, fontFace: FDISP, color: fgC(isLight),
    valign: 'middle', wrap: true, italic: true,
  });
  if (s.attribution) {
    const attY = CBOT_Y - 0.51;
    // Accent line before attribution
    slide.addShape(pptx.ShapeType.rect, {
      x: MX + 0.23, y: attY + 0.11, w: 0.41, h: 0.017,
      fill: { color: ACC }, line: { type: 'none' },
    });
    slide.addText(toPlain(s.attribution), {
      x: MX + 0.75, y: attY, w: BW - 0.79, h: 0.34,
      fontSize: 9, fontFace: FMONO, color: dimC(isLight), valign: 'middle',
      charSpacing: 1.8,
    });
  }
}

// section-divider — HTML always uses accent-color background with dark panel text
function buildSectionDivider(s) {
  const slide = pptx.addSlide();
  slide.background = { color: ACC };
  addChrome(slide, true, s.page_num);  // chrome in "light" style (dark ink on accent bg)
  const fg  = FGLIT;   // panel-text = dark ink
  const dim = '666666';
  if (s.section_num) {
    slide.addText(toPlain(s.section_num), {
      x: MX, y: 0.83, w: BW, h: 0.29,
      fontSize: 11, fontFace: FMONO, color: fg, valign: 'middle', charSpacing: 3,
      transparency: 40,
    });
  }
  const titleLen = (s.title || '').length;
  const titleFs = titleLen > 60 ? 32 : titleLen > 40 ? 40 : titleLen > 25 ? 48 : 60;
  slide.addText(toPlain(s.title || ''), {
    x: MX, y: s.section_num ? 1.20 : 0.83, w: BW, h: H - 2.85,
    fontSize: titleFs, fontFace: FDISP, color: fg, valign: 'middle', wrap: true,
  });
  // Bottom separator line + lead text
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: H - 1.19, w: BW, h: 0.014,
    fill: { color: fg, transparency: 70 }, line: { type: 'none' },
  });
  if (s.lead) {
    slide.addText(toPlain(s.lead), {
      x: MX, y: H - 1.07, w: BW * 0.7, h: 0.38,
      fontSize: 10, fontFace: FBODY, color: dim, valign: 'middle',
    });
  }
}

// image-fullscreen-overlay
function buildImgFull(s) {
  const slide = pptx.addSlide();
  const img = (s.images || [])[0];
  if (img && fs.existsSync(img)) {
    slide.background = { path: img };
    slide.addShape(pptx.ShapeType.rect, {
      x: 0, y: 0, w: W, h: H,
      fill: { color: '000000', transparency: 45 }, line: { type: 'none' },
    });
  } else {
    slide.background = { color: BGDARK };
  }
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: 2.10, w: 0.23, h: 0.04,
    fill: { color: ACC }, line: { type: 'none' },
  });
  if (s.title) {
    slide.addText(toPlain(s.title), {
      x: MX, y: 2.25, w: BW, h: 0.90,
      fontSize: titleFontSize(s.title), fontFace: FDISP, color: FGDRK,
      valign: 'top', wrap: true,
      shadow: { type: 'outer', color: '000000', blur: 10, offset: 3, angle: 45, opacity: 0.60 },
    });
  }
  if (s.body_text) {
    slide.addText(toPlain(s.body_text), {
      x: MX, y: 3.26, w: BW, h: 1.13,
      fontSize: 12, fontFace: FBODY, color: 'DDDDDD', valign: 'top', wrap: true,
    });
  }
}

// stat-cards — HTML: .nums .n has border-top:2px accent, big display number, dim label below
function buildStat(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  if (s.title) {
    slide.addText(toPlain(s.title), {
      x: MX, y: 0.75, w: BW, h: 0.41,
      fontSize: 14, fontFace: FMONO, color: ACC, valign: 'middle', charSpacing: 1.5,
    });
  }
  const stats = s.stats || [];
  const n = Math.min(stats.length, 4);
  if (n) {
    const GAP   = 0.41;
    const cardW = (BW - GAP * (n - 1)) / n;
    const statY = 1.31;
    stats.slice(0, n).forEach((stat, i) => {
      const x = MX + i * (cardW + GAP);
      // Accent top border
      slide.addShape(pptx.ShapeType.rect, {
        x, y: statY, w: cardW, h: 0.017,
        fill: { color: ACC }, line: { type: 'none' },
      });
      // Large value
      slide.addText(toPlain(stat.value || ''), {
        x, y: statY + 0.08, w: cardW, h: 1.58,
        fontSize: 39, fontFace: FDISP, color: ACC, valign: 'top', align: 'left',
      });
      // Label (dim)
      slide.addText(toPlain(stat.label || ''), {
        x, y: statY + 1.69, w: cardW, h: 0.49,
        fontSize: 15, fontFace: FBODY, color: dimC(isLight), align: 'left', wrap: true,
        shrinkText: true,
      });
    });
  }
  if (s.note) {
    // Note with top border separator
    slide.addShape(pptx.ShapeType.rect, {
      x: MX, y: CBOT_Y - 0.47, w: BW * 0.55, h: 0.014,
      fill: { color: fgC(isLight), transparency: 85 }, line: { type: 'none' },
    });
    slide.addText(toPlain(s.note), {
      x: MX, y: CBOT_Y - 0.41, w: BW * 0.75, h: 0.32,
      fontSize: 9, fontFace: FMONO, color: dimC(isLight), italic: false,
    });
  }
}

// pricing-cards
function buildPricing(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const cards = s.cards || [];
  const n = Math.min(cards.length, 4);
  if (!n) return;
  const cardW = (BW - (n - 1) * 0.15) / n;
  cards.slice(0, n).forEach((card, i) => {
    const x = MX + i * (cardW + 0.15);
    const bgColor = isLight ? 'EDEAE2' : '2A0009';
    slide.addShape(pptx.ShapeType.rect, {
      x, y: BODY_Y, w: cardW, h: BODY_H,
      fill: { color: bgColor, transparency: 20 },
      line: { color: isLight ? 'CCBBAA' : ACC, pt: 0.5 },
    });
    slide.addText(toPlain(card.name || ''), {
      x: x + 0.11, y: BODY_Y + 0.15, w: cardW - 0.23, h: 0.38,
      fontSize: 18, fontFace: FBODY, color: fgC(isLight), bold: true, valign: 'middle', align: 'center',
      shrinkText: true,
    });
    slide.addText(toPlain(card.price || ''), {
      x: x + 0.11, y: BODY_Y + 0.56, w: cardW - 0.23, h: 0.49,
      fontSize: 24, fontFace: FDISP, color: ACC, valign: 'middle', align: 'center',
      shrinkText: true,
    });
    if (card.features && card.features.length) {
      addBullets(slide, card.features, x + 0.08, BODY_Y + 1.09, cardW - 0.15, BODY_H - 1.13, isLight, 14);
    }
  });
}

// toc-vertical — numbered vertical list: accent num | display title | dim page
function buildTocVertical(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  slide.addText(toPlain(s.heading || 'Contents'), {
    x: MX, y: TTL_Y, w: BW, h: 0.68,
    fontSize: 27, fontFace: FDISP, color: fgC(isLight), valign: 'bottom', shrinkText: true,
  });
  const items = s.toc_items || [];
  const n = items.length;
  if (!n) return;
  const NUM_W  = 0.60;
  const PAGE_W = 0.45;
  const GAP    = 0.15;
  const rowH   = Math.min(0.56, BODY_H / n);
  items.forEach((item, i) => {
    const y = BODY_Y + i * rowH;
    slide.addShape(pptx.ShapeType.rect, {
      x: MX, y, w: BW, h: 0.014,
      fill: { color: fgC(isLight), transparency: 75 }, line: { type: 'none' },
    });
    slide.addText(toPlain(item.n || String(i + 1).padStart(2, '0')), {
      x: MX, y: y + 0.05, w: NUM_W, h: rowH - 0.09,
      fontSize: 14, fontFace: FMONO, color: ACC, valign: 'middle', align: 'left',
    });
    slide.addText(toPlain(item.t || ''), {
      x: MX + NUM_W + GAP, y: y + 0.05, w: BW - NUM_W - GAP - (item.p ? PAGE_W + GAP : 0), h: rowH - 0.09,
      fontSize: 15, fontFace: FDISP, color: fgC(isLight), valign: 'middle', wrap: true, shrinkText: true,
    });
    if (item.p) {
      slide.addText(toPlain(item.p), {
        x: MX + BW - PAGE_W, y: y + 0.05, w: PAGE_W, h: rowH - 0.09,
        fontSize: 12, fontFace: FMONO, color: dimC(isLight), align: 'right', valign: 'middle',
      });
    }
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: BODY_Y + n * rowH, w: BW, h: 0.014,
    fill: { color: fgC(isLight), transparency: 75 }, line: { type: 'none' },
  });
}

// toc_cards – 4-card horizontal layout
function buildTocCards(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);

  // Heading
  slide.addText(toPlain(s.heading || 'Outline'), {
    x: MX, y: 0.75, w: BW, h: 0.64,
    fontSize: 42, fontFace: FDISP, color: fgC(isLight), bold: false,
  });

  const cards = s.cards || [];
  const n = Math.min(cards.length, 6);
  if (!n) return;

  const cardY  = 1.58;
  const gap    = 0.14;
  const COLS   = n <= 4 ? n : 3;
  const ROWS   = Math.ceil(n / COLS);
  const bodyH  = H - cardY - 0.64;
  const cardW  = (BW - gap * (COLS - 1)) / COLS;
  const cardH  = (bodyH - gap * (ROWS - 1)) / ROWS;

  cards.slice(0, n).forEach((card, i) => {
    const col = i % COLS;
    const row = Math.floor(i / COLS);
    const x   = MX + col * (cardW + gap);
    const y   = cardY + row * (cardH + gap);
    // Card background
    slide.addShape(pptx.ShapeType.rect, {
      x, y, w: cardW, h: cardH,
      fill: { color: isLight ? 'E8E4DC' : '1A0005', transparency: isLight ? 20 : 70 },
      line: { color: isLight ? 'CCCCCC' : 'FFFFFF', transparency: 85, pt: 0.5 },
    });
    // Number
    slide.addText(card.n || String(i + 1).padStart(2, '0'), {
      x: x + 0.17, y: y + 0.17, w: cardW - 0.33, h: 0.30,
      fontSize: 12, fontFace: FMONO, color: ACC, bold: false,
    });
    // Title (shrinks to fit)
    const titleH = card.desc ? Math.min(cardH * 0.45, 0.90) : cardH - 0.56;
    slide.addText(toPlain(card.title || ''), {
      x: x + 0.17, y: y + 0.51, w: cardW - 0.33, h: titleH,
      fontSize: 26, fontFace: FDISP, color: fgC(isLight),
      wrap: true, valign: 'top', shrinkText: true,
    });
    // Description — fills rest of card
    if (card.desc) {
      const descY     = y + 0.51 + titleH + 0.08;
      const descH     = y + cardH - descY - 0.11;
      const descColor = isLight ? '444444' : 'BBBBBB';
      slide.addText(toPlain(card.desc), {
        x: x + 0.17, y: descY, w: cardW - 0.33, h: descH,
        fontSize: 16, fontFace: FBODY, color: descColor,
        wrap: true, valign: 'top', shrinkText: true,
      });
    }
  });
}

// toc-described — numbered list with title + description + optional duration
function buildTocDescribed(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  slide.addText(toPlain(s.heading || "What we'll cover"), {
    x: MX, y: TTL_Y, w: BW, h: 0.68,
    fontSize: 27, fontFace: FDISP, color: fgC(isLight), valign: 'bottom', shrinkText: true,
  });
  const items = s.toc_items || [];
  const n = items.length;
  if (!n) return;
  const NUM_W = 0.60;
  const DUR_W = 0.53;
  const GAP   = 0.21;
  const rowH  = Math.min(0.68, BODY_H / n);
  items.forEach((item, i) => {
    const y = BODY_Y + i * rowH;
    slide.addShape(pptx.ShapeType.rect, {
      x: MX, y, w: BW, h: 0.014,
      fill: { color: fgC(isLight), transparency: 75 }, line: { type: 'none' },
    });
    slide.addText(toPlain(item.n || String(i + 1).padStart(2, '0')), {
      x: MX, y: y + 0.04, w: NUM_W, h: rowH - 0.08,
      fontSize: 14, fontFace: FMONO, color: ACC, valign: 'top', align: 'left',
    });
    const bodyW = BW - NUM_W - GAP - (item.dur ? DUR_W + GAP : 0);
    slide.addText(toPlain(item.t || ''), {
      x: MX + NUM_W + GAP, y: y + 0.04, w: bodyW, h: rowH * 0.52,
      fontSize: 15, fontFace: FDISP, color: fgC(isLight), valign: 'bottom', wrap: true, shrinkText: true,
    });
    if (item.d) {
      slide.addText(toPlain(item.d), {
        x: MX + NUM_W + GAP, y: y + rowH * 0.54, w: bodyW, h: rowH * 0.40,
        fontSize: 14, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true, shrinkText: true,
      });
    }
    if (item.dur) {
      slide.addText(toPlain(item.dur), {
        x: MX + BW - DUR_W, y: y + 0.04, w: DUR_W, h: 0.29,
        fontSize: 11, fontFace: FMONO, color: dimC(isLight), align: 'right', valign: 'top',
      });
    }
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: BODY_Y + n * rowH, w: BW, h: 0.014,
    fill: { color: fgC(isLight), transparency: 75 }, line: { type: 'none' },
  });
}

// cover-split — large title left, key-value meta rows right, vertical divider
function buildCoverSplit(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  const LHS_W = BW * 0.60;
  const RHS_X = MX + LHS_W + 0.17;
  const RHS_W = W - RHS_X - MX;
  // Vertical divider
  slide.addShape(pptx.ShapeType.rect, {
    x: MX + LHS_W + 0.06, y: 0.75, w: 0.014, h: H - 1.58,
    fill: { color: fgC(isLight), transparency: 85 }, line: { type: 'none' },
  });
  // Eyebrow with accent line
  if (s.eyebrow) {
    slide.addShape(pptx.ShapeType.rect, {
      x: MX, y: 0.84, w: 0.46, h: 0.017,
      fill: { color: ACC }, line: { type: 'none' },
    });
    slide.addText(toPlain(s.eyebrow), {
      x: MX + 0.54, y: 0.75, w: LHS_W - 0.62, h: 0.29,
      fontSize: 10, fontFace: FMONO, color: dimC(isLight), valign: 'middle', charSpacing: 2,
    });
  }
  // Large title
  const titleText = toPlain(manifest.lecture_title || s.title || '');
  const titleFs   = titleText.length > 60 ? 27 : titleText.length > 40 ? 33 : titleText.length > 25 ? 38 : 40;
  slide.addText(titleText, {
    x: MX, y: 1.24, w: LHS_W, h: H - 2.63,
    fontSize: titleFs, fontFace: FDISP, color: fgC(isLight), valign: 'middle', wrap: true,
  });
  // Right side: stacked key-value rows
  const rows  = s.meta_rows || [];
  const rowH  = Math.min(0.66, (H - 1.88) / Math.max(rows.length, 1));
  const rowsStartY = H - rows.length * rowH - 0.90;
  rows.forEach((row, i) => {
    const ry = rowsStartY + i * rowH;
    slide.addShape(pptx.ShapeType.rect, {
      x: RHS_X, y: ry, w: RHS_W, h: 0.014,
      fill: { color: fgC(isLight), transparency: 85 }, line: { type: 'none' },
    });
    slide.addText(toPlain(row.k || ''), {
      x: RHS_X, y: ry + 0.05, w: RHS_W, h: 0.20,
      fontSize: 8, fontFace: FMONO, color: dimC(isLight), valign: 'middle', charSpacing: 2,
    });
    slide.addText(toPlain(row.v || ''), {
      x: RHS_X, y: ry + 0.25, w: RHS_W, h: rowH - 0.29,
      fontSize: 14, fontFace: FDISP, color: fgC(isLight), valign: 'top', wrap: true,
    });
  });
  if (rows.length > 0) {
    slide.addShape(pptx.ShapeType.rect, {
      x: RHS_X, y: rowsStartY + rows.length * rowH, w: RHS_W, h: 0.014,
      fill: { color: fgC(isLight), transparency: 85 }, line: { type: 'none' },
    });
  }
}

// end-with-image — large title + footer on left, image on right
function buildEndWithImage(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  const LHS_W = (BW - 0.15) / 2;
  const RHS_X = MX + LHS_W + 0.15;
  const RHS_W = W - RHS_X - MX;
  // Large end title
  slide.addText(toPlain(s.title || 'Thank You'), {
    x: MX, y: 0.83, w: LHS_W, h: 2.89,
    fontSize: 45, fontFace: FDISP, color: fgC(isLight), valign: 'top', wrap: true,
  });
  // Footer separator
  const footY = CBOT_Y - 0.71;
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: footY, w: LHS_W, h: 0.014,
    fill: { color: fgC(isLight), transparency: 80 }, line: { type: 'none' },
  });
  const items = s.meta_items || [];
  items.slice(0, 3).forEach((item, i) => {
    const iy = footY + 0.08 + i * 0.21;
    slide.addText(toPlain(item.k || ''), {
      x: MX, y: iy, w: LHS_W, h: 0.14,
      fontSize: 7, fontFace: FMONO, color: dimC(isLight), charSpacing: 1.5,
    });
    if (item.v) {
      slide.addText(toPlain(item.v), {
        x: MX, y: iy + 0.14, w: LHS_W, h: 0.17,
        fontSize: 11, fontFace: FDISP, color: fgC(isLight),
      });
    }
  });
  // Image on right (edge to edge vertically)
  const img = (s.images || [])[0];
  if (img && fs.existsSync(img)) {
    addImage(slide, img, RHS_X, 0.26, RHS_W, H - 0.26 - 0.23);
  } else {
    slide.addShape(pptx.ShapeType.rect, {
      x: RHS_X, y: 0.26, w: RHS_W, h: H - 0.49,
      fill: { color: isLight ? '000000' : 'FFFFFF', transparency: 92 }, line: { type: 'none' },
    });
    slide.addText('Image', {
      x: RHS_X, y: H / 2 - 0.15, w: RHS_W, h: 0.30,
      fontSize: 9, fontFace: FMONO, color: dimC(isLight), align: 'center',
    });
  }
}

// end-image-hero — full-bleed image bg + dark gradient overlay + title at bottom
function buildEndImageHero(s) {
  const slide = pptx.addSlide();
  const img = (s.images || [])[0];
  if (img && fs.existsSync(img)) {
    slide.background = { path: img };
  } else {
    slide.background = { color: BGDARK };
  }
  // Bottom-heavy dark overlay
  slide.addShape(pptx.ShapeType.rect, {
    x: 0, y: H * 0.30, w: W, h: H * 0.70,
    fill: { color: '000000', transparency: 15 }, line: { type: 'none' },
  });
  addChrome(slide, false, s.page_num);
  // Accent bar
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: H - 1.99, w: 0.46, h: 0.017,
    fill: { color: ACC }, line: { type: 'none' },
  });
  // Large title
  const titleText = toPlain(s.title || 'Thank You');
  const titleFs   = titleText.length > 30 ? 45 : titleText.length > 20 ? 54 : 60;
  slide.addText(titleText, {
    x: MX, y: H - 1.88, w: BW, h: 1.20,
    fontSize: titleFs, fontFace: FDISP, color: 'FFFFFF', valign: 'top', wrap: true,
  });
  // Footer separator + items
  const footY = CBOT_Y - 0.08;
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: footY, w: BW, h: 0.014,
    fill: { color: 'FFFFFF', transparency: 80 }, line: { type: 'none' },
  });
  const items = s.meta_items || [];
  if (items.length) {
    const colW = (BW - 0.30 * (items.length - 1)) / items.length;
    items.slice(0, 3).forEach((item, i) => {
      const x = MX + i * (colW + 0.30);
      slide.addText(toPlain(item.k || ''), {
        x, y: footY + 0.05, w: colW, h: 0.17,
        fontSize: 7, fontFace: FMONO, color: 'FFFFFF', transparency: 50, charSpacing: 2,
      });
      if (item.v) {
        slide.addText(toPlain(item.v), {
          x, y: footY + 0.21, w: colW, h: 0.18,
          fontSize: 10, fontFace: FDISP, color: 'FFFFFF',
        });
      }
    });
  }
}

// editorial-light — light bg, large display title + lede left, sidebar (pull quote / meta) right
function buildEditorial(s) {
  const slide = pptx.addSlide();
  slide.background = { color: BGLIT };
  addChrome(slide, true, s.page_num);
  // Eyebrow: accent line + mono text
  if (s.eyebrow) {
    slide.addShape(pptx.ShapeType.rect, {
      x: MX, y: 0.84, w: 0.33, h: 0.017,
      fill: { color: ACC }, line: { type: 'none' },
    });
    slide.addText(toPlain(s.eyebrow), {
      x: MX + 0.41, y: 0.75, w: BW - 0.41, h: 0.29,
      fontSize: 10, fontFace: FMONO, color: DIMLIT, valign: 'middle', charSpacing: 2,
    });
  }
  const MAIN_W = BW * 0.56;
  const SIDE_X = MX + MAIN_W + 0.19;
  const SIDE_W = BW - MAIN_W - 0.19;
  // Large title
  const titleLen = (s.title || '').length;
  const titleFs  = titleLen > 60 ? 21 : titleLen > 40 ? 24 : titleLen > 25 ? 27 : 30;
  slide.addText(toPlain(s.title || ''), {
    x: MX, y: 1.16, w: MAIN_W, h: 2.10,
    fontSize: titleFs, fontFace: FDISP, color: FGLIT, valign: 'top', wrap: true,
  });
  // Lede text
  if (s.lede) {
    slide.addText(toPlain(s.lede), {
      x: MX, y: 3.41, w: MAIN_W, h: CBOT_Y - 3.53,
      fontSize: 15, fontFace: FBODY, color: FGLIT, transparency: 22, valign: 'top', wrap: true,
      shrinkText: true,
    });
  }
  // Sidebar: pull quote card (accent bg)
  if (s.pull_quote) {
    const pqH = s.pull_attribution ? 1.50 : 1.28;
    slide.addShape(pptx.ShapeType.rect, {
      x: SIDE_X, y: 1.16, w: SIDE_W, h: pqH,
      fill: { color: ACC }, line: { type: 'none' },
    });
    slide.addText('"', {
      x: SIDE_X + 0.14, y: 1.16, w: SIDE_W - 0.14, h: 0.45,
      fontSize: 36, fontFace: FDISP, color: FGLIT, transparency: 40, italic: true, valign: 'top',
    });
    slide.addText(toPlain(s.pull_quote), {
      x: SIDE_X + 0.14, y: 1.61, w: SIDE_W - 0.27, h: pqH - 0.75,
      fontSize: 13, fontFace: FDISP, color: FGLIT, valign: 'top', wrap: true,
    });
    if (s.pull_attribution) {
      slide.addText(toPlain(s.pull_attribution), {
        x: SIDE_X + 0.14, y: 1.16 + pqH - 0.27, w: SIDE_W - 0.27, h: 0.23,
        fontSize: 8, fontFace: FMONO, color: FGLIT, transparency: 30, charSpacing: 1.5,
      });
    }
  }
  // Sidebar: meta items
  if (s.meta && s.meta.length > 0) {
    const metaStartY = s.pull_quote ? 1.16 + (s.pull_attribution ? 1.50 : 1.28) + 0.19 : 1.16;
    s.meta.forEach((m, i) => {
      const my = metaStartY + i * 0.39;
      slide.addText(toPlain((m.key || '') + ' —'), {
        x: SIDE_X, y: my, w: SIDE_W, h: 0.17,
        fontSize: 7, fontFace: FMONO, color: DIMLIT, transparency: 40, charSpacing: 1,
      });
      slide.addText(toPlain(m.value || ''), {
        x: SIDE_X, y: my + 0.17, w: SIDE_W, h: 0.21,
        fontSize: 10, fontFace: FMONO, color: FGLIT,
      });
    });
  }
  // Footline
  if (s.footline_left || s.footline_right) {
    slide.addShape(pptx.ShapeType.rect, {
      x: MX, y: CBOT_Y - 0.45, w: BW, h: 0.014,
      fill: { color: FGLIT, transparency: 85 }, line: { type: 'none' },
    });
    slide.addText(toPlain(s.footline_left || ''), {
      x: MX, y: CBOT_Y - 0.39, w: BW / 2, h: 0.26,
      fontSize: 9, fontFace: FMONO, color: DIMLIT, valign: 'middle', charSpacing: 1,
    });
    if (s.footline_right) {
      slide.addText(toPlain(s.footline_right), {
        x: MX + BW / 2, y: CBOT_Y - 0.39, w: BW / 2, h: 0.26,
        fontSize: 9, fontFace: FMONO, color: DIMLIT, align: 'right', valign: 'middle', charSpacing: 1,
      });
    }
  }
}

// dispatch
for (const s of manifest.slides || []) {
  switch (s.type) {
    case 'cover':            buildCover(s);             break;
    case 'toc':              buildToc(s);               break;
    case 'toc_vertical':     buildTocVertical(s);       break;
    case 'toc_described':    buildTocDescribed(s);      break;
    case 'toc_cards':        buildTocCards(s);          break;
    case 'bullets':          buildBullets(s);           break;
    case 'formula':          buildFormula(s);           break;
    case 'twoimgbelow':      buildTwoImgBelow(s);       break;
    case 'twoimgright':      buildTwoImgRight(s);       break;
    case 'twoimgleft':       buildTwoImgLeft(s);        break;
    case 'imgright':         buildImgRight(s);          break;
    case 'imgleft':          buildImgLeft(s);           break;
    case 'imgabove':         buildImgAbove(s);          break;
    case 'imgbelow':         buildImgBelow(s);          break;
    case 'twoimgabove':      buildTwoImgAbove(s);       break;
    case 'twocols':          buildTwoCols(s);           break;
    case 'twocontents':      buildTwoContents(s);       break;
    case 'cmptable':         buildTable(s);             break;
    case 'tblabove':         buildTblAbove(s);          break;
    case 'stat':             buildStat(s);              break;
    case 'steps':            buildSteps(s);             break;
    case 'keypoints':        buildKeyPoints(s);         break;
    case 'threecol':         buildThreeCols(s);         break;
    case 'splitcontrast':    buildSplitContrast(s);     break;
    case 'conclcards':       buildConclCards(s);        break;
    case 'numconcl':         buildNumConcl(s);          break;
    case 'grid2x2':          buildGrid2x2(s);           break;
    case 'rquestion':        buildResearchQuestion(s);  break;
    case 'agenda':           buildAgenda(s);            break;
    case 'quote':            buildQuote(s);             break;
    case 'section-divider':  buildSectionDivider(s);   break;
    case 'imgfull':          buildImgFull(s);           break;
    case 'pricing':          buildPricing(s);           break;
    case 'cover_split':      buildCoverSplit(s);        break;
    case 'end_with_image':   buildEndWithImage(s);      break;
    case 'end_image_hero':   buildEndImageHero(s);      break;
    case 'editorial':        buildEditorial(s);         break;
    case 'end':              buildEnd(s);               break;
    default:                 buildBullets(s);           break;
  }
}

(async () => {
  try {
    const n = (manifest.slides || []).length;
    if (GRAD_STOPS.length >= 2) {
      const buf = await pptx.write({ outputType: 'nodebuffer' });
      const out  = await applyGradBg(buf, GRAD_STOPS, BGDARK);
      fs.writeFileSync(outputPath, out);
    } else {
      await pptx.writeFile({ fileName: outputPath });
    }
    console.log(`[pptx] ${n} slides → ${outputPath}`);
    process.exit(0);
  } catch (err) {
    console.error('[pptx] Error: ' + err.message);
    process.exit(1);
  }
})();
