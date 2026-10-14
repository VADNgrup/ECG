'use strict';
const PptxGenJS = require('pptxgenjs');
const fs   = require('fs');
const path = require('path');

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
const IS_LIGHT = (T.default_mode || 'dark') === 'light';
const FDISP = T.font_display || 'Georgia';
const FBODY = T.font_body    || 'Calibri';
const FMONO = T.font_mono    || 'Consolas';
const BODY_LINE = 1.24;

// ── text measurement with the fonts' real glyph widths (font_metrics.py) ─────────────────────────────────────────
// The deck can be switched to any embedded design, so a role (display, body, mono, math; `any` = display or body) is
// measured with, per character, the widest of its fonts in every design. PowerPoint draws a line LINE x the size and
// wraps greedily at spaces; with WRAP_SAFE of the width this never counts fewer lines than PowerPoint draws.
const FMET = JSON.parse(fs.readFileSync(path.join(__dirname, 'font_metrics.json'), 'utf8'));
const LINE = 1.2, WRAP_SAFE = 0.98;
// a flat list (addBullets): its bullet's hanging indent and its box insets [l, r, b, t] in points; the width its text
// wraps in is the box less both (mirrored by fit_model.BULLET_INSET)
const BULLET_IND_PT = 27, BULLET_MARGIN = [0.05, 0.08, 0.05, 0];
const BULLET_INSET = (BULLET_IND_PT + BULLET_MARGIN[0] + BULLET_MARGIN[1]) / 72;

function roleOf(face) {
  return face === FDISP ? 'display' : face === FMONO ? 'mono' : face === 'Cambria Math' ? 'math'
    : face === FBODY ? 'body' : 'any';
}

// Width of `s` in em.
function emW(s, role, bold) {
  const t = FMET[role || 'any'][bold ? 'bold' : 'regular'];
  let w = 0;
  for (const ch of String(s)) w += t[ch] !== undefined ? t[ch] : FMET.default;
  return w;
}

// Width of `s` in inches at fs pt.
function textW(s, fs, role, bold) { return emW(s, role, bold) * fs / 72; }

// Lines `text` takes in a text area w inches wide at fs pt ("\n" starts a paragraph); a word wider than the line breaks
// between characters, as PowerPoint does.
function wrapLines(text, w, fs, role, bold) {
  const avail = Math.max(0.05, w) * WRAP_SAFE * 72 / fs;
  let lines = 0;
  for (const para of String(text === undefined || text === null ? '' : text).split('\n')) {
    lines += 1;
    let cur = emW(para.match(/^\s*/)[0], role, bold);
    for (const m of para.matchAll(/(\S+)(\s*)/g)) {
      const ww = emW(m[1], role, bold);
      if (cur > 0 && cur + ww > avail) { lines++; cur = 0; }
      if (ww > avail) {
        for (const ch of m[1]) {
          const c = emW(ch, role, bold);
          if (cur > 0 && cur + c > avail) { lines++; cur = 0; }
          cur += c;
        }
      } else cur += ww;
      cur += emW(m[2], role, bold);
    }
  }
  return Math.max(1, lines);
}

// ── colours are theme roles ──────────────────────────────────────────────────
// Each colour a slide shows is registered as a role of the theme (a scheme colour, possibly lighter/darker), under a
// hex unique to that role; postProcess() turns every registered hex into its <a:schemeClr>, so applying another
// embedded design recolours the whole deck. Theme slots: dk1/lt1 = text/background of the deck's mode, dk2/lt2 = the
// other mode's text/background (inverse panels), accent1 = accent, accent2-5 = c1-c4, accent6 = dim text,
// hlink = ink on coloured fills. Literal colours (photo scrims, shadows) stay as they are.
const ROLE_XML = new Map();
const LITERAL = new Set(['FFFFFF', '000000', 'DDDDDD', 'FE00FE']);
function _nudge(h) {
  const b = parseInt(h.slice(4, 6), 16);
  return h.slice(0, 4) + (b >= 255 ? b - 1 : b + 1).toString(16).padStart(2, '0').toUpperCase();
}
function role(hex, slot, mods) {
  let h = String(hex || '888888').replace('#', '').toUpperCase();
  const xml = `<a:schemeClr val="${slot}">${mods || ''}`;
  for (let k = 0; k < 256; k++) {
    const have = ROLE_XML.get(h);
    if (have === xml || (have === undefined && !LITERAL.has(h))) break;
    h = _nudge(h);
  }
  ROLE_XML.set(h, xml);
  return h;
}
// HSL luminance modulation, as PowerPoint applies <a:lumMod>/<a:lumOff> (percent)
function lumHex(hex, mod, off) {
  const [r, g, b] = _hex2rgb(hex);
  const [hh, s, l] = _rgb2hsl(r, g, b);
  return _rgb2hex(..._hsl2rgb(hh, s, Math.max(0, Math.min(1, l * mod / 100 + (off || 0) / 100))));
}
function roleLum(baseHex, slot, mod, off) {
  const mods = `<a:lumMod val="${Math.round(mod * 1000)}"/>` + (off ? `<a:lumOff val="${Math.round(off * 1000)}"/>` : '');
  return role(lumHex(baseHex, mod, off), slot, mods);
}
// the ink that reads best on every coloured fill of a design: the card colours c1-c4, or (onAccent) the accent and
// the darker accent shade its gradients end in
function inkFor(theme, onAccent) {
  const fills = (onAccent ? [theme.accent, theme.accent && lumHex(theme.accent, 60)] : [theme.c1, theme.c2, theme.c3, theme.c4])
    .filter(c => c && String(c).length === 6);
  if (!fills.length) return 'FFFFFF';
  const worst = ink => Math.min(...fills.map(f => contrastRatio(ink, f)));
  return worst('111318') >= worst('FFFFFF') ? '111318' : 'FFFFFF';
}

const _T = {
  bgLight: T.bg_light || 'F5F3EE', bgDark: T.bg_dark || '500014', fgLight: T.text_light || '0B0D12',
  fgDark: T.text_dark || 'FFFFFF', dimLight: T.dim_light || '888888', dimDark: T.dim_dark || 'AAAAAA',
};
const BGLIT  = role(_T.bgLight, IS_LIGHT ? 'bg1' : 'bg2');
const BGDARK = role(_T.bgDark,  IS_LIGHT ? 'bg2' : 'bg1');
const FGLIT  = role(_T.fgLight, IS_LIGHT ? 'tx1' : 'tx2');
const FGDRK  = role(_T.fgDark,  IS_LIGHT ? 'tx2' : 'tx1');
const ACC    = role(T.accent || 'FFCC33', 'accent1');
const C1 = role(T.c1 || '5b9bd5', 'accent2');
const C2 = role(T.c2 || 'e07b6a', 'accent3');
const C3 = role(T.c3 || '7b68c8', 'accent4');
const C4 = role(T.c4 || 'f0a050', 'accent5');
const DIMLIT = IS_LIGHT ? role(_T.dimLight, 'accent6') : roleLum(_T.fgLight, 'tx2', 60, 40);
const DIMDRK = IS_LIGHT ? roleLum(_T.fgDark, 'tx2', 70) : role(_T.dimDark, 'accent6');
const INK    = role(inkFor(T), 'hlink');
const INK_ACC = role(inkFor(T, true), 'folHlink');
const CARD_COLORS = [C1, C2, C3, C4];
// the card colours lightened within their own role (HSL lightness 70-100% in any design): the page's dark ink reads on
// every one of them, which no single ink does on the full colours (they run dark to light)
const CARD_TINTS = [[T.c1 || '5b9bd5', 'accent2'], [T.c2 || 'e07b6a', 'accent3'], [T.c3 || '7b68c8', 'accent4'],
                    [T.c4 || 'f0a050', 'accent5']].map(([hex, slot]) => roleLum(hex, slot, 30, 70));
// a surface a little off the page background (boxes, table stripes, cards): darker on light pages, lighter on dark
function surfC(isLight, level) {
  level = level || 6;
  return isLight === IS_LIGHT
    ? (IS_LIGHT ? roleLum(BGLIT, 'bg1', 100 - level) : roleLum(BGDARK, 'bg1', 100 - 2 * level, 2 * level))
    : (IS_LIGHT ? roleLum(BGDARK, 'bg2', 100 - 2 * level, 2 * level) : roleLum(BGLIT, 'bg2', 100 - level));
}
// a box on the page: the page's text colour laid thinly over it, so it stays visible under a light or a dark design
function tintC(isLight, level) {
  const a = (level || 6) * 1.5, fg = isLight ? _T.fgLight : _T.fgDark, bg = isLight ? _T.bgLight : _T.bgDark;
  const [f, b] = [_hex2rgb(fg), _hex2rgb(bg)];
  const hex = _rgb2hex(...f.map((v, i) => Math.round(b[i] + (v - b[i]) * a / 100)));
  return role(hex, isLight === IS_LIGHT ? 'tx1' : 'tx2', `<a:alpha val="${Math.round(a * 1000)}"/>`);
}
// the accent a shade darker (gradients, tiers): pct = its luminance in percent of the accent's
function accShade(pct) { return pct >= 99.5 ? ACC : roleLum(ACC, 'accent1', pct); }

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

// The template's theme: colour scheme + font scheme of the palette, so Design > Colors/Fonts and newly typed text follow it.
function buildThemeXml(xml, theme) {
  theme = theme || T;
  const isLight = (theme.default_mode || 'dark') === 'light';
  const bgDark = theme.bg_dark || _T.bgDark, bgLight = theme.bg_light || _T.bgLight;
  const accent = theme.accent || T.accent || 'FFCC33';
  const fgDark = theme.text_dark || _T.fgDark, fgLight = theme.text_light || _T.fgLight;
  const dimDark = theme.dim_dark || _T.dimDark, dimLight = theme.dim_light || _T.dimLight;
  const c1 = theme.c1 || T.c1, c2 = theme.c2 || T.c2, c3 = theme.c3 || T.c3, c4 = theme.c4 || T.c4;
  const display = theme.font_display || FDISP, body = theme.font_body || FBODY;
  const clr = (n, v) => `<a:${n}><a:srgbClr val="${String(v).replace('#', '').toUpperCase()}"/></a:${n}>`;
  const name = 'LecSlide - ' + (theme.name || 'Custom');
  const scheme = `<a:clrScheme name="${name}">` + clr('dk1', isLight ? fgLight : fgDark) + clr('lt1', isLight ? bgLight : bgDark) +
    clr('dk2', isLight ? fgDark : fgLight) + clr('lt2', isLight ? bgDark : bgLight) + clr('accent1', accent) +
    clr('accent2', c1) + clr('accent3', c2) + clr('accent4', c3) + clr('accent5', c4) +
    clr('accent6', isLight ? dimLight : dimDark) + clr('hlink', inkFor({ c1, c2, c3, c4 })) + clr('folHlink', inkFor({ accent }, true)) + '</a:clrScheme>';
  const font = (tag, face) => `<a:${tag}><a:latin typeface="${face}"/><a:ea typeface=""/><a:cs typeface=""/></a:${tag}>`;
  const fonts = `<a:fontScheme name="${name}">` + font('majorFont', display) + font('minorFont', body) + '</a:fontScheme>';
  return xml.replace(/<a:clrScheme[\s\S]*?<\/a:clrScheme>/, scheme).replace(/<a:fontScheme[\s\S]*?<\/a:fontScheme>/, fonts)
    .replace(/(<a:theme\b[^>]*\bname=")[^"]*(")/, `$1${name}$2`)
    // PptxGenJS ships Office's family/variant GUID.  Reusing that GUID for
    // custom embedded designs makes PowerPoint treat unrelated themes as one
    // broken family, so custom themes intentionally have no external family.
    .replace(/<a:extLst>[\s\S]*?<thm15:themeFamily[\s\S]*?<\/a:extLst>/, '');
}

function escapeRe(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

// Change only palette-owned RGB/font literals into theme references.  Media,
// shadows and data-specific colours remain literal by design.
function themeAwareXml(xml) {
  xml = xml.replace(/<a:srgbClr val="([0-9A-Fa-f]{6})"\s*\/>/g, (m, hex) => {
    const x = ROLE_XML.get(hex.toUpperCase());
    return x ? x + '</a:schemeClr>' : m;
  });
  xml = xml.replace(/<a:srgbClr val="([0-9A-Fa-f]{6})"([^>/]*)>([\s\S]*?)<\/a:srgbClr>/g, (m, hex, _attrs, inner) => {
    const x = ROLE_XML.get(hex.toUpperCase());
    return x ? x + inner + '</a:schemeClr>' : m;
  });
  // Sentinel emitted by gradRect: one DrawingML gradient from the accent to its deeper shade (gradStops()).
  xml = xml.replace(/<a:solidFill><a:srgbClr val="FE00FE"\s*\/><\/a:solidFill>/gi,
    '<a:gradFill rotWithShape="1"><a:gsLst>' +
    '<a:gs pos="0"><a:schemeClr val="accent1"/></a:gs>' +
    '<a:gs pos="100000"><a:schemeClr val="accent1"><a:lumMod val="60000"/></a:schemeClr></a:gs>' +
    '</a:gsLst><a:lin ang="5400000" scaled="0"/></a:gradFill>');
  xml = xml.replace(new RegExp(`typeface="${escapeRe(FDISP)}"`, 'g'), 'typeface="+mj-lt"')
           .replace(new RegExp(`typeface="${escapeRe(FBODY)}"`, 'g'), 'typeface="+mn-lt"');
  return xml;
}

const EMU = 914400;
// A title bound to the layout placeholder gets the placeholder's box and size from pptxgenjs: put back the builder's.
const TITLE_BOXES = new Map();          // slide number -> [x, y, w, h, fontSize] of its bound title
function restoreTitle(xml, box) {
  if (!box) return xml;
  const [x, y, w, h, fs] = box;
  const e = v => Math.round(v * EMU);
  return xml.replace(/<p:sp>(?:(?!<\/p:sp>)[\s\S])*?<p:ph\b[^>]*?type="title"[\s\S]*?<\/p:sp>/, block => block
    .replace(/<a:xfrm>[\s\S]*?<\/a:xfrm>/, `<a:xfrm><a:off x="${e(x)}" y="${e(y)}"/><a:ext cx="${e(w)}" cy="${e(h)}"/></a:xfrm>`)
    .replace(/\bsz="\d+"/g, `sz="${Math.round(fs * 100)}"`)
    .replace(/<a:pPr\b(?![^>]*\balgn=)/g, '<a:pPr algn="l"'));
}

function removeUnusedSlidePlaceholders(xml) {
  // PptxGenJS materialises every layout placeholder as an empty shape on every
  // slide.  PowerPoint then shows those unused shapes as dashed boxes with
  // prompts ("Picture", "Explanation") in normal editing view.  Keep only
  // populated native placeholders; the layout parts still retain the complete
  // placeholder catalogue for Layout/Reset.
  return xml.replace(/<p:sp>[\s\S]*?<\/p:sp>/g, block => {
    if (!/<p:ph\b/.test(block)) return block;
    const text = [...block.matchAll(/<a:t>([\s\S]*?)<\/a:t>/g)].map(match => match[1]).join('').trim();
    return text ? block : '';
  });
}

function numberFrom(name, re) { const m = name.match(re); return m ? Number(m[1]) : 0; }

async function embedDesigns(zip, baseThemeXml) {
  const themes = Array.isArray(manifest.embedded_themes) && manifest.embedded_themes.length
    ? manifest.embedded_themes : [T];
  if (themes.length <= 1) return;
  const layoutNames = Object.keys(zip.files).filter(n => /^ppt\/slideLayouts\/slideLayout\d+\.xml$/.test(n))
    .sort((a, b) => numberFrom(a, /slideLayout(\d+)\.xml$/) - numberFrom(b, /slideLayout(\d+)\.xml$/));
  const baseLayouts = await Promise.all(layoutNames.map(async name => ({
    name, xml: await zip.files[name].async('string'),
    relsName: name.replace('ppt/slideLayouts/', 'ppt/slideLayouts/_rels/') + '.rels',
  })));
  for (const row of baseLayouts) row.rels = zip.files[row.relsName] ? await zip.files[row.relsName].async('string') : '';
  const baseMaster = await zip.files['ppt/slideMasters/slideMaster1.xml'].async('string');
  const baseMasterRels = await zip.files['ppt/slideMasters/_rels/slideMaster1.xml.rels'].async('string');
  let presentation = await zip.files['ppt/presentation.xml'].async('string');
  let presentationRels = await zip.files['ppt/_rels/presentation.xml.rels'].async('string');
  let contentTypes = await zip.files['[Content_Types].xml'].async('string');
  const relIds = [...presentationRels.matchAll(/Id="rId(\d+)"/g)].map(m => Number(m[1]));
  let nextRid = Math.max(0, ...relIds) + 1;
  const masterIds = [...presentation.matchAll(/<p:sldMasterId id="(\d+)"/g)].map(m => Number(m[1]));
  const baseMasterId = Math.max(2147483648, ...masterIds);
  const baseCount = baseLayouts.length;

  for (let themeIndex = 1; themeIndex < themes.length; themeIndex++) {
    const partNo = themeIndex + 1;
    const theme = themes[themeIndex];
    const firstLayout = themeIndex * baseCount + 1;
    const masterPath = `ppt/slideMasters/slideMaster${partNo}.xml`;
    const masterRelsPath = `ppt/slideMasters/_rels/slideMaster${partNo}.xml.rels`;
    const themePath = `ppt/theme/theme${partNo}.xml`;
    let masterRels = baseMasterRels.replace(/\.\.\/slideLayouts\/slideLayout(\d+)\.xml/g,
      (_m, n) => `../slideLayouts/slideLayout${firstLayout + Number(n) - 1}.xml`)
      .replace('../theme/theme1.xml', `../theme/theme${partNo}.xml`);
    // Master ids and layout ids share one global id space.  Each design owns
    // one master id followed by its layout ids (matching files saved by
    // PowerPoint itself), so reserve baseCount+1 ids per design.
    const idOffset = themeIndex * (baseCount + 1);
    const masterXml = baseMaster
      .replace('<p:sldMaster ', '<p:sldMaster preserve="1" ')
      .replace(/<p:cSld(?:\s+name="[^"]*")?/, `<p:cSld name="LecSlide - ${theme.name}"`)
      .replace(/(<p:sldLayoutId id=")(\d+)(")/g,
        (_m, lead, id, tail) => lead + String(Number(id) + idOffset) + tail);
    zip.file(masterPath, masterXml);
    zip.file(masterRelsPath, masterRels);
    zip.file(themePath, buildThemeXml(baseThemeXml, theme));
    for (let j = 0; j < baseLayouts.length; j++) {
      const newNo = firstLayout + j;
      const row = baseLayouts[j];
      zip.file(`ppt/slideLayouts/slideLayout${newNo}.xml`, row.xml);
      zip.file(`ppt/slideLayouts/_rels/slideLayout${newNo}.xml.rels`,
        row.rels.replace('../slideMasters/slideMaster1.xml', `../slideMasters/slideMaster${partNo}.xml`));
    }
    const rid = `rId${nextRid++}`;
    presentation = presentation.replace('</p:sldMasterIdLst>',
      `<p:sldMasterId id="${baseMasterId + idOffset}" r:id="${rid}"/></p:sldMasterIdLst>`);
    presentationRels = presentationRels.replace('</Relationships>',
      `<Relationship Id="${rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster${partNo}.xml"/></Relationships>`);
  }
  zip.file('ppt/presentation.xml', presentation);
  zip.file('ppt/_rels/presentation.xml.rels', presentationRels);
  // PptxGenJS writes speculative slide-master overrides (one per slide), even
  // though only slideMaster1.xml exists.  Once cloned masters are added those
  // stale entries become duplicate PartName overrides, which PowerPoint treats
  // as a corrupt package.  Rebuild the three design-related override groups
  // from the parts that actually exist in the zip.
  contentTypes = contentTypes.replace(
    /<Override\s+PartName="\/ppt\/(?:slideMasters\/slideMaster\d+|slideLayouts\/slideLayout\d+|theme\/theme\d+)\.xml"\s+ContentType="[^"]+"\s*\/>/g,
    '');
  const designParts = Object.keys(zip.files).filter(name =>
    /^ppt\/(?:slideMasters\/slideMaster\d+|slideLayouts\/slideLayout\d+|theme\/theme\d+)\.xml$/.test(name));
  const overrideFor = name => {
    const contentType = name.startsWith('ppt/slideMasters/')
      ? 'application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml'
      : name.startsWith('ppt/slideLayouts/')
        ? 'application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml'
        : 'application/vnd.openxmlformats-officedocument.theme+xml';
    return `<Override PartName="/${name}" ContentType="${contentType}"/>`;
  };
  contentTypes = contentTypes.replace('</Types>', designParts.map(overrideFor).join('') + '</Types>');
  zip.file('[Content_Types].xml', contentTypes);
}

async function postProcess(buffer) {
  const JSZip = require('jszip');
  const zip = await JSZip.loadAsync(buffer);
  const baseThemeXml = await zip.files['ppt/theme/theme1.xml'].async('string');
  zip.file('ppt/theme/theme1.xml', buildThemeXml(baseThemeXml, T));
  const deckXml = /^(ppt\/(?:slides|slideLayouts|slideMasters|charts)\/[^/]*\.xml)$/;
  await Promise.all(Object.keys(zip.files).filter(n => deckXml.test(n)).map(async name => {
    let xml = themeAwareXml(await zip.files[name].async('string'));
    const slideNo = name.match(/^ppt\/slides\/slide(\d+)\.xml$/);
    if (slideNo) xml = removeUnusedSlidePlaceholders(restoreTitle(xml, TITLE_BOXES.get(Number(slideNo[1]))));
    zip.file(name, xml);
  }));
  await embedDesigns(zip, baseThemeXml);
  return zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
}

function fgC(light)  { return light ? FGLIT  : FGDRK;  }
function dimC(light) { return light ? DIMLIT : DIMDRK; }

// Deterministic autofit: largest font size (<= maxFs, >= minFs) at which `text` fits box w×h (inches).
// Mirrors Python _estimate_text_lines/_required_text_height (pptx_pattern_engines.py) so JS and the
// Python geometry agree. Emitting an already-fitting fontSize lets callers DROP pptxgenjs `shrinkText`
// (normAutofit) -- which is unreliable both for fitting and for the PNG-export "word ghosting" artifact.
function fitFontSize(text, w, h, maxFs, minFs, role = 'any', bold = true) {
  maxFs = maxFs || 18; minFs = minFs || 9;
  const s = String(text || '').trim();
  if (!s) return maxFs;
  const ew = Math.max(0.2, w - 0.2);                // the box's default insets: 0.1" left and right
  const longest = Math.max(...s.split(/\s+/).map(x => emW(x, role, bold)));
  for (let fs = Math.round(maxFs); fs >= minFs; fs--) {
    if (longest * fs / 72 > ew * WRAP_SAFE && fs > minFs) continue;  // the longest word would break mid-word
    if (wrapLines(s, ew, fs, role, bold) * fs * LINE / 72 + 0.1 <= h) return fs;   // + 0.05" top and bottom
  }
  return minFs;
}

// Relative luminance (WCAG, sRGB->linear) of a hex color, mirroring Python _hex_luminance.
function hexLum(hex) {
  const h = String(hex || '').replace('#', '');
  if (h.length !== 6) return 0.5;
  const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  const r = lin(parseInt(h.slice(0, 2), 16)), g = lin(parseInt(h.slice(2, 4), 16)), b = lin(parseInt(h.slice(4, 6), 16));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// Readable text color for text placed ON a colored fill: dark ink on light fills, white on dark fills
// -- fixes white-on-light-card contrast (cards use c1-c4 which can be light).
function textOn(bgHex) {
  if (bgHex === BGDARK) return FGDRK;
  if (bgHex === BGLIT) return FGLIT;
  return String(ROLE_XML.get(bgHex) || '').startsWith('<a:schemeClr val="accent1"') ? INK_ACC : INK;
}

// WCAG contrast ratio between two hex colors.
function contrastRatio(a, b) {
  const la = hexLum(a), lb = hexLum(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// A distinctive on-theme colour for the slide TITLE (so it has "its own colour", not plain ink). Prefers
// the theme accent; falls back to the deepest/brightest theme shade, then plain fg, keeping >=3.2:1
// contrast (big bold heading text) against the slide background.
function titleColor(isLight) {
  const bg = isLight ? BGLIT : BGDARK;
  const cands = isLight ? [ACC, C1, FGLIT] : [ACC, C4, FGDRK];
  for (const c of cands) { if (c && contrastRatio(c, bg) >= 3.2) return c; }
  return fgC(isLight);
}

// ── Gradient / tonal depth (pptxgenjs shape fills are solid-only, so we fake a smooth gradient by
// stacking many thin interpolated rects). Makes accent panels feel richer than a flat solid block. ──

// Linear-interpolate two hex colors; t in [0,1].
function lerpHex(a, b, t) {
  const pa = String(a || '').replace('#', ''), pb = String(b || '').replace('#', '');
  if (pa.length !== 6 || pb.length !== 6) return pa || pb || '000000';
  const ch = (h, i) => parseInt(h.slice(i, i + 2), 16);
  const mix = (i) => Math.round(ch(pa, i) + (ch(pb, i) - ch(pa, i)) * t).toString(16).padStart(2, '0');
  return (mix(0) + mix(2) + mix(4)).toUpperCase();
}

// ── HSL helpers for richer, hue-shifted gradients (a two-hue gradient looks far more premium than a
// mono-hue accent->dark-shade one). ──
function _hex2rgb(h) { h = String(h || '').replace('#', ''); return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]; }
function _rgb2hex(r, g, b) { const c = v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0'); return (c(r) + c(g) + c(b)).toUpperCase(); }
function _rgb2hsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b); let h = 0, s = 0, l = (mx + mn) / 2;
  if (mx !== mn) {
    const d = mx - mn; s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    if (mx === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (mx === g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
    h /= 6;
  }
  return [h * 360, s, l];
}
function _hsl2rgb(h, s, l) {
  h = (h % 360) / 360; let r, g, b;
  if (s === 0) { r = g = b = l; }
  else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
    const hue = t => { if (t < 0) t += 1; if (t > 1) t -= 1; if (t < 1 / 6) return p + (q - p) * 6 * t; if (t < 1 / 2) return q; if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6; return p; };
    r = hue(h + 1 / 3); g = hue(h); b = hue(h - 1 / 3);
  }
  return [r * 255, g * 255, b * 255];
}

// The theme's gradient color pair for accent panels: accent (top) -> a vibrant, slightly hue-rotated
// DEEP tone (bottom). Two-hue + saturated reads far richer than accent->flat dark shade.
function gradStops() { return [ACC, accShade(60)]; }

// The representative middle color of the gradient (for choosing readable ink on top via textOn()).
function gradMid() { return accShade(80); }

// Draw a gradient-filled rect as N interpolated bands. dir: 'v' (top->bottom, default) or 'h'.
// Each band OVERLAPS the next generously so no hairline seam shows at PDF/PNG export.
function gradRect(slide, x, y, w, h, cA, cB, dir, steps) {
  // FE00FE is an internal sentinel, replaced in postProcess() by a genuine
  // DrawingML gradient based on accent1/accent2.  This keeps the gradient live
  // when the user applies another embedded design.
  slide.addShape(pptx.ShapeType.rect, {
    x, y, w, h, fill: { color: 'FE00FE' }, line: { type: 'none' },
  });
}

// Layered decorative geometry for a colored/gradient panel [x,y,w,h] using ink (usually white): a soft
// corner glow, two concentric rings bleeding off a corner, a lone ring, and a small dotted column.
// Purely decorative + low-opacity so it never fights the text on the panel.
function panelMotif(slide, x, y, w, h, ink) {
  const el = (cx, cy, d, opts) => slide.addShape(pptx.ShapeType.ellipse, { x: cx - d / 2, y: cy - d / 2, w: d, h: d, ...opts });
  // soft glow bleeding off the bottom-right corner
  el(x + w + 0.1, y + h - 0.3, 3.0, { fill: { color: ink, transparency: 92 }, line: { type: 'none' } });
  // concentric outlined rings bleeding off the top-right corner
  el(x + w - 0.15, y + 0.25, 2.5, { fill: { type: 'none' }, line: { color: ink, pt: 1, transparency: 72 } });
  el(x + w - 0.15, y + 0.25, 1.7, { fill: { type: 'none' }, line: { color: ink, pt: 1, transparency: 82 } });
  // a lone outlined ring lower-left with a small filled centre dot
  el(x + 1.05, y + h - 1.35, 1.1, { fill: { type: 'none' }, line: { color: ink, pt: 1.5, transparency: 50 } });
  el(x + 1.05, y + h - 1.35, 0.14, { fill: { color: ink, transparency: 25 }, line: { type: 'none' } });
  // a small dotted column near the left edge
  for (let i = 0; i < 4; i++) el(x + 0.62, y + h - 2.35 + i * 0.24, 0.07, { fill: { color: ink, transparency: 45 }, line: { type: 'none' } });
}

// Subtle floating accent shapes for a PLAIN slide background (not a colored panel) -- for slides that
// otherwise read as sparse/empty (e.g. a centered "Thank you", a short quote). Same visual language as
// panelMotif (concentric rings + a dot-ring) but in the theme ACCENT at much lower opacity, since it
// sits directly on the page background rather than on a saturated panel. Two faint clusters bleed off
// the top-right and bottom-left corners so the eye reads "designed" without competing with the text.
function bgMotif(slide) {
  const el = (cx, cy, d, opts) => slide.addShape(pptx.ShapeType.ellipse, { x: cx - d / 2, y: cy - d / 2, w: d, h: d, ...opts });
  // top-right: two concentric faint rings bleeding off the corner
  el(W + 0.2, -0.3, 2.6, { fill: { type: 'none' }, line: { color: ACC, pt: 1, transparency: 78 } });
  el(W + 0.2, -0.3, 1.7, { fill: { type: 'none' }, line: { color: ACC, pt: 1, transparency: 85 } });
  // bottom-left: a ring with a small filled centre dot bleeding off the corner
  el(-0.5, H + 0.4, 2.1, { fill: { type: 'none' }, line: { color: ACC, pt: 1.25, transparency: 80 } });
  el(0.55, H - 0.55, 0.11, { fill: { color: ACC, transparency: 60 } });
}

// How many wrapped lines `text` takes at font size fs in a box w inches wide (default insets).
function estLines(text, w, fs, role = 'any', bold = false) {
  const s = String(text || '').trim();
  return s ? wrapLines(s, Math.max(0.2, w - 0.2), fs, role, bold) : 1;
}

// Deterministic shrink-to-fit: pick the largest font that fits box w×h; if the text still needs more
// lines than fit at minFs, keep shrinking (down to a hard floor) so the FULL text shows -- never
// truncate with an ellipsis. Returns {fs, text} (text is always the full string).
function fitClip(text, w, h, maxFs, minFs) {
  maxFs = maxFs || 16; minFs = minFs || 9;
  const HARD_MIN = 8;
  const s = String(text || '').trim();
  let fs = fitFontSize(s, w, h, maxFs, minFs);
  const fits = (f) => estLines(s, w, f) <= Math.max(1, Math.floor((h - 0.06) / (f * 1.28 / 72)));
  while (fs > HARD_MIN && !fits(fs)) fs -= 1;
  return { fs, text: s };
}

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
  '\\ell':'ℓ','\\Vert':'‖','\\vert':'|','\\top':'ᵀ','\\prime':'′','\\mid':'|','\\ast':'∗','\\star':'⋆',
  '\\circ':'∘','\\bullet':'•','\\odot':'⊙','\\otimes':'⊗','\\oplus':'⊕','\\vartheta':'ϑ','\\lfloor':'⌊',
  '\\rfloor':'⌋','\\lceil':'⌈','\\rceil':'⌉','\\max':'max','\\min':'min',
};

// Unicode superscript / subscript maps
const SUP_MAP = {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹',
                 'a':'ᵃ','b':'ᵇ','c':'ᶜ','d':'ᵈ','e':'ᵉ','f':'ᶠ','g':'ᵍ','h':'ʰ','i':'ⁱ','j':'ʲ','k':'ᵏ','l':'ˡ',
                 'm':'ᵐ','n':'ⁿ','o':'ᵒ','p':'ᵖ','r':'ʳ','s':'ˢ','t':'ᵗ','u':'ᵘ','v':'ᵛ','w':'ʷ','x':'ˣ','y':'ʸ',
                 'z':'ᶻ','A':'ᴬ','B':'ᴮ','D':'ᴰ','E':'ᴱ','G':'ᴳ','H':'ᴴ','I':'ᴵ','J':'ᴶ','K':'ᴷ','L':'ᴸ','M':'ᴹ',
                 'N':'ᴺ','O':'ᴼ','P':'ᴾ','R':'ᴿ','T':'ᵀ','U':'ᵁ','V':'ⱽ','W':'ᵂ','θ':'ᶿ','β':'ᵝ','γ':'ᵞ','δ':'ᵟ',
                 'φ':'ᵠ','χ':'ᵡ','ᵀ':'ᵀ','′':'′','*':'*','∗':'*',
                 '+':'⁺','-':'⁻','−':'⁻','=':'⁼','(':'⁽',')':'⁾'};
const SUB_MAP = {'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉',
                 'a':'ₐ','e':'ₑ','h':'ₕ','i':'ᵢ','j':'ⱼ','k':'ₖ','l':'ₗ','m':'ₘ','n':'ₙ','o':'ₒ','p':'ₚ','r':'ᵣ',
                 's':'ₛ','t':'ₜ','u':'ᵤ','v':'ᵥ','x':'ₓ','β':'ᵦ','γ':'ᵧ','ρ':'ᵨ','φ':'ᵩ','χ':'ᵪ',
                 '+':'₊','-':'₋','−':'₋','=':'₌','(':'₍',')':'₎'};

function _mapStr(str, map) {
  return str.split('').map(c => map[c] || c).join('');
}

// Named operators keep their name (\log -> log) and accents become combining marks (\hat{p} -> p̂); both would
// otherwise fall to the generic "strip unknown command" rule and silently change the formula.
const MATH_OPS = /\\(log|ln|exp|min|max|argmin|argmax|arg|sin|cos|tan|det|lim|sup|inf|Pr|softmax|sign|tr)(?![a-zA-Z])/g;
const ACCENT = {hat: '\u0302', bar: '\u0304', tilde: '\u0303', vec: '\u20D7', dot: '\u0307'};
function mathWords(text) {
  text = text.replace(/\\(?:operatorname|mathrm|text)\{(argmin|argmax|softmax|sign|tr)\}/g, '$1');
  text = text.replace(MATH_OPS, '$1 ');
  for (const [name, mark] of Object.entries(ACCENT)) {
    text = text.replace(new RegExp(`\\\\${name}\\s*\\{\\s*([a-zA-Z0-9])\\s*\\}`, 'g'), `$1${mark}`);
    text = text.replace(new RegExp(`\\\\${name}\\s+([a-zA-Z0-9])`, 'g'), `$1${mark}`);
  }
  return text;
}

// Longest command first: a naive substring replace would read "\top" as "\to"+"p" and "\simeq" as "\sim"+"eq".
const LMAP_SORTED = Object.entries(LMAP).sort((a, b) => b[0].length - a[0].length);
// A script becomes Unicode sub/superscript glyphs only when every character has one; otherwise it stays readable
// as "_θ" / "^(i)" instead of mapping some letters of a word ("\theta" -> "ₑtₐ").
function _script(group, map, mark) {
  const s = group.replace(/\s+/g, '');
  if (s && [...s].every(c => map[c])) return _mapStr(s, map);
  return mark + ([...s].length > 1 && !/^\(.*\)$/.test(s) ? `(${s})` : s);
}
// Shared by toMathText and toPlain: environments, escaped braces, OCR spacing, symbols and font commands are
// settled first, so sub/superscripts only ever see finished glyphs.
function latexPrep(text) {
  text = text.replace(/\\begin\s*\{[a-zA-Z*]+\}(\s*\{[^{}]*\})?/g, ' ').replace(/\\end\s*\{[a-zA-Z*]+\}/g, ' ');
  text = text.replace(/\\(?:left|right)\s*\./g, ' ');
  text = text.replace(/\\\{/g, '').replace(/\\\}/g, '');
  text = text.replace(/\\bf([A-Za-z])(?![A-Za-z])/g, '$1');
  text = text.replace(/\\(?:big|Big|bigg|Bigg)[lrm]?(?![a-zA-Z])/g, '');
  text = text.replace(/\\(?:scriptscript|script|display|text)style/g, ' ');
  text = text.replace(/(\d)\s*\.\s*(?=\d)/g, '$1.');
  text = text.replace(/\\(?:left|right)(?=[^a-zA-Z]|$)/g, '');
  text = text.replace(/\\(?:q?quad|[ ,;:!])/g, ' ').replace(/~/g, ' ');         // spacing commands
  text = mathWords(text);
  // a root keeps its braced radicand until the fractions inside are done (U+E001 marks it)
  text = text.replace(/\\sqrt\s*\[([^\]]+)\]\s*(?=\{)/g, (_, n) => `${_mapStr(n.trim(), SUP_MAP)}`)
    .replace(/\\sqrt\s*(?=\{)/g, '');
  for (const [k, v] of LMAP_SORTED) text = text.split(k).join(v);
  for (const [name, mark] of Object.entries(ACCENT)) {
    text = text.replace(new RegExp(`\\\\${name}\\s*\\{\\s*([^\\s{}\\\\])\\s*\\}`, 'gu'), `$1${mark}`);
  }
  text = text.replace(/([∂∇]) +(?=\S)/g, '$1');
  for (let i = 0; i < 3; i++) {
    text = text.replace(/\\(?:mathbf|mathrm|mathit|mathsf|mathtt|mathcal|mathbb|boldsymbol|textbf|textit|textrm|text|operatorname\*?)\s*\{([^{}]*)\}/g, '$1');
  }
  text = text.replace(/\\overset\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '$2^{$1}');
  text = text.replace(/\\underset\s*\{([^{}]*)\}\s*\{((?:[^{}]|\{[^{}]*\})*)\}/g, '$2_{$1}');
  const scripts = () => {
    text = text.replace(/\s+([_^])/g, '$1');
    text = text.replace(/\^\s*\{([^{}]+)\}/g, (_, g) => _script(g, SUP_MAP, '^'));
    text = text.replace(/\^\s*([a-zA-Z0-9])/g, (_, c) => SUP_MAP[c] || `^${c}`);
    text = text.replace(/\_\s*\{([^{}]+)\}/g, (_, g) => _script(g, SUB_MAP, '_'));
    // one character after _ and no more: x_i, θ_0 -- never inside an identifier (get_params, housing_median_age)
    text = text.replace(/\_\s*([a-zA-Z0-9])(?![A-Za-z0-9])/g, (_, c) => SUB_MAP[c] || `_${c}`);
  };
  scripts();                                   // a fraction's parts may hold scripts, and a fraction may carry one
  const part = x => { x = x.trim(); return /^[\p{L}\p{N}\p{M}\u2070-\u209F\u1D2C-\u1DBF\u2032.∂∇∞√]+$/u.test(x) ? x : `(${x})`; };
  const roots = () => { text = text.replace(/\s*\{([^{}]*)\}/g, (_, x) => `√${part(x)}`); };
  for (let i = 0; i < 4; i++) {
    roots();
    text = text.replace(/\\[cdt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, (_, a, b) => `${part(a)}/${part(b)}`);
  }
  scripts();
  for (let i = 0; i < 4; i++) {
    text = text.replace(/(^|[^\s])(\s*)\{([^{}]*)\}/g, '$1$2$3');     // bare grouping braces left by OCR
    roots();
  }
  return text.replace(//g, '√');
}

function toMathText(text) {
  // Full conversion of LaTeX to unicode math text (for formula boxes)
  if (!text) return '';
  text = String(text);
  // Escaped underscore (\_) means a literal underscore in an identifier (e.g. "life\_satisfaction"),
  // not a subscript trigger -- protect it from the subscript regexes below with a placeholder (a
  // Private-Use-Area char that can't collide with real formula text), then restore it as a plain
  // "_" at the end (otherwise the dangling "\" survives every later step, since none of them match
  // a backslash followed by "_").
  text = text.replace(/\\_/g, '');
  // Strip display delimiters
  text = text.replace(/\$\$(.+?)\$\$/gs, '$1');
  text = text.replace(/\$(.+?)\$/gs,     '$1');
  text = text.replace(/\\\[(.+?)\\\]/gs, '$1');
  text = text.replace(/\\\((.+?)\\\)/gs, '$1');
  text = text.replace(/\\\\/g, '\n').replace(/&/g, '  ');         // rows and cells of a table layout
  text = latexPrep(text);
  // Remove remaining LaTeX commands with braced args
  text = text.replace(/\\[a-zA-Z]+\{([^}]*)\}/g, '$1');
  text = text.replace(/\\[a-zA-Z]+/g, '');
  text = text.replace(/[{}]/g, '').replace(//g, '{').replace(//g, '}');
  text = text.replace(/[$]|\\[[\]()]/g, '');  // strip stray unpaired delimiters
  text = text.split('').join('_');
  text = text.replace(/[ \t]+/g, ' ').replace(/([(\[{⟨|‖]) /g, '$1').replace(/ ([)\]}⟩,;])/g, '$1')
    .replace(/,(?=[^\s\d])/g, ', ')
    .replace(/\b(log|ln|exp|sin|cos|tan|max|min|argmax|argmin|softmax|sign|det|tr|[A-Z]{2,}) \(/g, '$1(')
    .replace(/(^|[\s(=+·,])(\p{L}\p{M}?) \(/gu, '$1$2(')
    .replace(/\(- /g, '(-');
  return text.trim();
}

function toPlain(text) {
  // Simplified version for bullets/titles (strips math, no unicode conversion)
  if (!text) return '';
  text = String(text);
  // Escaped underscore (\_) means a literal underscore in an identifier, not a subscript
  // trigger -- protect it with a placeholder, restore as "_" at the end (see toMathText).
  text = text.replace(/\\_/g, '');
  text = text.replace(/\$\$(.+?)\$\$/gs, '$1');
  text = text.replace(/\$(.+?)\$/gs,     '$1');
  text = text.replace(/\\\[(.+?)\\\]/gs, '$1');
  text = text.replace(/\\\((.+?)\\\)/gs, '$1');
  text = text.replace(/\\\\/g, ' ').replace(/&/g, ' ');
  text = latexPrep(text);
  text = text.replace(/\\[a-zA-Z]+\{([^}]*)\}/g, '$1');
  text = text.replace(/\\[a-zA-Z]+/g, '');
  text = text.replace(/[{}]/g, '').replace(//g, '{').replace(//g, '}');
  text = text.replace(/[$]|\\[[\]()]/g, '');
  text = text.split('').join('_');
  return text.replace(/\s+/g, ' ').trim();
}

// Capitalize the first letter of a content string (bodies/bullets often arrive as lowercase fragments).
// Only touches the first alphabetic char and only if it is lowercase -- leaves acronyms/proper nouns and
// already-capitalized text untouched.
function capFirst(text) {
  const s = String(text == null ? '' : text);
  const m = s.match(/[A-Za-z]/);
  if (!m) return s;
  const i = s.indexOf(m[0]);
  if (s[i] !== s[i].toLowerCase()) return s;   // already uppercase
  return s.slice(0, i) + s[i].toUpperCase() + s.slice(i + 1);
}

// pptxgenjs-instance
const pptx    = new PptxGenJS();
pptx.layout   = 'LAYOUT_16x9';
pptx.title    = manifest.lecture_title || '';
pptx.author   = manifest.speaker       || '';
pptx.subject  = manifest.lecture_title || '';
pptx.company  = manifest.institution   || '';
pptx.revision = '1';

// slide master layouts: one named layout per catalogue layout (Layout gallery), frames as placeholders, page furniture
// on the master. Slides are created from the layout of their layout_id; builders draw inside the same regions.
const LAYOUT_NAME = {};
const LAYOUT_SPEC = {};
const LAYOUT_CHROME = {};      // layout name -> its master carries the date (the running header is per slide)
let CURRENT_LAYOUT = null;
let CURRENT_LAYOUT_ID = null;
let CURRENT_SECTION = '';      // the chapter of the slide being built: its running header
function defineLayouts() {
  const isLight = (T.default_mode || 'dark') === 'light';
  const bg = isLight ? BGLIT : BGDARK, dim = dimC(isLight);
  for (const L of (manifest.layouts || [])) {
    const objects = [];
    if (!L.structural) {
      // Keep title first so it receives the same placeholder index in every
      // layout and every embedded design.
      objects.push({ placeholder: { options: { name: 'title', type: 'title', x: MX, y: TTL_Y, w: BW, h: TTL_H, fontSize: 22, bold: true,
        fontFace: FDISP, color: titleColor(isLight), valign: 'bottom', align: 'left' }, text: 'Slide title' } });
    }
    for (const f of (L.frames || [])) {
      objects.push({ placeholder: { options: { name: f.name, type: f.kind === 'pic' ? 'pic' : 'body', x: f.x, y: f.y, w: f.w, h: f.h,
        fontSize: 12, fontFace: FBODY, color: dim }, text: f.prompt } });
    }
    if (!L.structural && manifest.date) objects.push({ text: { text: manifest.date, options: { x: MX, y: CBOT_Y, w: 2.25, h: CBOT_H, fontSize: 8, color: dim, fontFace: FMONO, valign: 'middle' } } });
    pptx.defineSlideMaster({ title: L.name, background: { color: bg }, objects });
    LAYOUT_NAME[L.id] = L.name;
    LAYOUT_SPEC[L.id] = L;
    LAYOUT_CHROME[L.name] = !L.structural;
  }
  const addSlide = pptx.addSlide.bind(pptx);
  pptx.addSlide = (opts) => {
    SLIDE_NO += 1;
    return fitGuard(addSlide(opts || (CURRENT_LAYOUT ? { masterName: CURRENT_LAYOUT } : undefined)));
  };
}

// ── the fit guard: no text ever leaves its box ─────────────────────────────────────────────────────────────────────
// Every text box and table a builder draws is measured as PowerPoint will lay it out (real glyph widths, its insets,
// bullet indents, paragraph spacing, each run's size); one its text would overflow -- a plan that found no fit, an
// estimate that ran short -- is set at the largest smaller size that fits it. Text never asks PowerPoint to shrink it
// (normAutofit is recomputed when the design changes). Every box set smaller is reported (FIT_LOG).
const FIT_LOG = [];
let SLIDE_NO = 0, FIT_NEED = null;
const DEF_FS = 18;                                   // pptxgenjs's size for a run that names none
const half = (x) => Math.floor(x * 2) / 2;

function fitGuard(slide) {
  const addText = slide.addText.bind(slide), addTable = slide.addTable.bind(slide);
  slide.addText = (text, opts) => {
    if (opts && opts.shrinkText) { opts = { ...opts }; delete opts.shrinkText; }
    const sc = opts ? textScale(text, opts) : 1;
    if (sc >= 1) return addText(text, opts);
    const fit = scaleText(text, opts, sc);
    logFit(text, opts, sc);
    return addText(fit.text, fit.opts);
  };
  slide.addTable = (rows, opts) => addTable(rows, fitTableRows(rows, opts || {}));
  return slide;
}

// Text insets (inches) of a box: pptxgenjs `margin` is in points ([left, right, bottom, top]); none = PowerPoint's.
function insetsOf(o) {
  const m = o.margin;
  if (Array.isArray(m)) return { l: (m[0] || 0) / 72, r: (m[1] || 0) / 72, b: (m[2] || 0) / 72, t: (m[3] || 0) / 72 };
  if (typeof m === 'number') return { l: m / 72, r: m / 72, b: m / 72, t: m / 72 };
  return { l: 0.1, r: 0.1, t: 0.05, b: 0.05 };
}

// The text as pptxgenjs writes it (genXmlTextBody): paragraphs of runs. A run with a bullet, or whose alignment
// differs from the run before, starts a paragraph; "\n" and a run's breakLine end one.
function paragraphsOf(text, opts) {
  const items = Array.isArray(text) ? text : [{ text, options: {} }];
  const paras = [[]];
  let prevAlign;
  items.forEach((it, i) => {
    const own = (it && it.options) || {};
    const o = { ...opts, ...own };
    const cur = paras[paras.length - 1];
    if (cur.length && ((o.align && o.align !== prevAlign) || own.bullet)) paras.push([]);
    prevAlign = o.align;
    String(it && it.text !== undefined && it.text !== null ? it.text : '').split(/\r?\n/).forEach((part, k) => {
      if (k > 0) paras.push([]);
      paras[paras.length - 1].push({ text: part, o });
    });
    if (own.breakLine && i < items.length - 1) paras.push([]);
  });
  return paras.filter(p => p.length);
}

const runFs = (r, sc) => half((r.o.fontSize || DEF_FS) * sc);

// Lines a paragraph of runs takes in w inches (runs of other sizes and faces wrap together, words never split
// across a run boundary unless a space is there).
function runLines(runs, w, sc) {
  const words = [];                                  // [width of the word, width of the spaces after it]
  let open = false;
  for (const r of runs) {
    const fs = runFs(r, sc), role = roleOf(r.o.fontFace), bold = !!r.o.bold;
    for (const m of String(r.text).matchAll(/(\S*)(\s*)/g)) {
      if (!m[0]) continue;
      const ww = textW(m[1], fs, role, bold), sw = textW(m[2], fs, role, bold);
      if (open && m[1]) { words[words.length - 1][0] += ww; words[words.length - 1][1] = sw; }
      else if (m[1]) words.push([ww, sw]);
      else if (words.length) words[words.length - 1][1] += sw;
      else words.push([0, sw]);
      open = !m[2];
    }
  }
  const avail = Math.max(0.05, w) * WRAP_SAFE;
  let lines = 1, cur = 0;
  for (const [ww, sw] of words) {
    if (cur > 0 && cur + ww > avail) { lines++; cur = 0; }
    if (ww > avail) { lines += Math.ceil(ww / avail) - 1; cur = ww % avail; } else cur += ww;
    cur += sw;
  }
  return lines;
}

// Height a paragraph needs (inches) at scale sc; for an unwrapped box, Infinity when its line is wider than w.
function paraNeed(runs, w, sc, wrap) {
  const o = runs[0].o;
  const fs = Math.max(...runs.map(r => runFs(r, sc)));
  // a bullet's hanging indent, once per list level (pptxgenjs marL = indent x (level + 1))
  const indent = o.bullet ? (typeof o.bullet === 'object' && o.bullet.indent !== undefined ? o.bullet.indent : 27)
                            * (1 + (o.indentLevel || 0)) / 72 : 0;
  const width = w - indent;
  let lines;
  if (wrap === false) {
    const lineW = runs.reduce((t, r) => t + textW(r.text, runFs(r, sc), roleOf(r.o.fontFace), !!r.o.bold), 0);
    if (lineW > width * WRAP_SAFE) return Infinity;
    lines = 1;
  } else {
    lines = runLines(runs, width, sc);
  }
  const lineH = o.lineSpacing ? o.lineSpacing * sc / 72 : LINE * fs / 72 * (o.lineSpacingMultiple || 1);
  return lines * lineH + ((o.paraSpaceBefore || 0) + (o.paraSpaceAfter || 0)) * sc / 72;
}

// The largest scale (<= 1) of the box's sizes at which its text fits it.
function textScale(text, opts) {
  if (!(opts.w > 0) || !(opts.h > 0) || opts.fit || opts.autoFit) return 1;
  const ins = insetsOf(opts);
  // text leaves the box only past its edge: top-anchored text starts below the top inset, bottom-anchored above the
  // bottom one, centred text spreads both ways from the middle
  const v = opts.valign === 'middle' || opts.valign === 'ctr' ? 0 : opts.valign === 'bottom' ? ins.b : ins.t;
  const w = opts.w - ins.l - ins.r, h = opts.h - v + 0.01;
  const paras = paragraphsOf(text, opts);
  if (!paras.some(p => p.some(r => r.text.trim()))) return 1;
  const fits = (sc) => paras.reduce((t, p) => t + paraNeed(p, w, sc, opts.wrap), 0) <= h;
  if (fits(1)) return 1;
  FIT_NEED = { need: paras.reduce((t, p) => t + paraNeed(p, w, 1, opts.wrap), 0), h };
  let sc = 1;
  while (sc > 0.2 && !fits(sc)) sc *= 0.97;
  return sc;
}

function scaleText(text, opts, sc) {
  const scaleOpts = (o) => {
    const out = { ...o };
    if (o === opts || o.fontSize !== undefined) out.fontSize = half((o.fontSize || DEF_FS) * sc);
    for (const k of ['paraSpaceBefore', 'paraSpaceAfter', 'lineSpacing']) if (o[k]) out[k] = o[k] * sc;
    return out;
  };
  const items = Array.isArray(text) ? text.map(it => ({ ...it, options: scaleOpts((it && it.options) || {}) })) : text;
  return { text: items, opts: scaleOpts(opts) };
}

function logFit(text, opts, sc) {
  const s = (Array.isArray(text) ? text.map(t => t && t.text).join(' ') : String(text)).replace(/\s+/g, ' ');
  const n = FIT_NEED || {};
  FIT_LOG.push({ slide: SLIDE_NO, from: opts.fontSize || DEF_FS, to: half((opts.fontSize || DEF_FS) * sc),
                 text: `${s.slice(0, 50)}" (needs ${(n.need || 0).toFixed(2)}" of ${(n.h || 0).toFixed(2)}")` });
}

// A table keeps the height its builder planned (the sum of rowH): rows are as tall as their cells' text needs, the
// room left shared out; cells whose text cannot fit that height get the largest smaller size that fits.
function fitTableRows(rows, opts) {
  if (!rows.length || !Array.isArray(opts.colW) || opts.rowH === undefined) return opts;
  const given = Array.isArray(opts.rowH) ? opts.rowH : rows.map(() => opts.rowH);
  const total = given.reduce((a, b) => a + b, 0);
  const inch = (v) => (v >= 1 ? v / 72 : v);
  const marg = (o) => {
    const m = o.margin !== undefined ? o.margin : opts.margin;
    if (m === undefined) return { t: 0.05, r: 0.1, b: 0.05, l: 0.1 };
    const a = Array.isArray(m) ? m.map(inch) : [inch(m), inch(m), inch(m), inch(m)];
    return { t: a[0], r: a[1], b: a[2], l: a[3] };
  };
  const need = (sc) => rows.map(row => Math.max(0.05, ...row.map((cell, j) => {
    const o = { ...opts, ...((cell && cell.options) || {}) };
    const m = marg(o);
    const text = typeof cell === 'object' && cell !== null ? cell.text : cell;
    const paras = paragraphsOf(text === undefined ? '' : text, o);
    return paras.reduce((t, p) => t + paraNeed(p, (opts.colW[j] || 1) - m.l - m.r, sc), 0) + m.t + m.b;
  })));
  let sc = 1, needs = need(1);
  while (sc > 0.3 && needs.reduce((a, b) => a + b, 0) > total + 0.01) { sc *= 0.97; needs = need(sc); }
  if (sc < 1) {
    for (const row of rows) for (const cell of row) {
      if (cell && cell.options) cell.options = { ...cell.options, fontSize: half((cell.options.fontSize || opts.fontSize || DEF_FS) * sc) };
    }
    FIT_LOG.push({ slide: SLIDE_NO, from: opts.fontSize || 'table', to: `x${sc.toFixed(2)}`, text: 'table' });
  }
  const spare = Math.max(0, total - needs.reduce((a, b) => a + b, 0));
  return { ...opts, rowH: needs.map((n, i) => n + spare * (given[i] / total)) };
}

const PLACEHOLDER_CLAIMS = new WeakMap();
function placeholderFor(slide, kind, x, y, w, h) {
  const L = LAYOUT_SPEC[CURRENT_LAYOUT_ID];
  if (!L) return null;
  const wanted = kind === 'title' ? 'title' : kind === 'pic' ? 'pic' : 'body';
  const frames = wanted === 'title'
    ? [{ name: 'title', x: MX, y: TTL_Y, w: BW, h: TTL_H }]
    : (L.frames || []).filter(f => (f.kind === 'pic' ? 'pic' : 'body') === wanted);
  if (!frames.length) return null;
  if (![x, y, w, h].every(Number.isFinite)) return null;
  // Bind only when the renderer's box is the layout frame itself.  A loose
  // nearest-frame match is unsafe: multi-column text and captions can then be
  // assigned the same placeholder index and PowerPoint stacks them on Reset.
  // A slide has exactly one title, so it can safely inherit the native title
  // placeholder even when the renderer made a small content-fit adjustment.
  // Body/picture frames stay strict because several freeform objects may share
  // the same broad region.
  const best = wanted === 'title' ? frames[0] : frames.find(f =>
    Math.abs(x - f.x) <= 0.08 && Math.abs(y - f.y) <= 0.08 &&
    Math.abs(w - f.w) <= 0.12 && Math.abs(h - f.h) <= 0.12);
  if (!best) return null;
  let claims = PLACEHOLDER_CLAIMS.get(slide);
  if (!claims) { claims = new Set(); PLACEHOLDER_CLAIMS.set(slide, claims); }
  if (claims.has(best.name)) return null;
  claims.add(best.name);
  return best.name;
}

// shared-helpers
function addChrome(slide, isLight, pageNum, minimal, ink) {
  const dim = dimC(isLight);
  const lt  = CURRENT_SECTION || manifest.lecture_title || '';
  const lbl = lt.length > 58 ? lt.slice(0, 58) + '…' : lt;

  // `minimal` (used by full-statement slides like section dividers) draws only the page number so the
  // top chapter header + date don't collide with a full-bleed panel / oversized number.
  if (!minimal) {
    // Accent dot
    slide.addShape(pptx.ShapeType.ellipse, {
      x: MX, y: CTOP_Y + 0.07, w: 0.08, h: 0.08,
      fill: { color: ACC }, line: { type: 'none' },
    });
  }
  if (!minimal && lbl) {                               // running header: the slide's chapter, else the deck title
    slide.addText(lbl, {
      x: MX + 0.14, y: CTOP_Y, w: BW - 0.14 - 1.13, h: CTOP_H,
      fontSize: 9, color: dim, fontFace: FMONO, valign: 'middle', charSpacing: 1.5,
    });
  }
  if (!minimal && !LAYOUT_CHROME[CURRENT_LAYOUT]) {   // a content layout's master carries the date
    // Date
    if (manifest.date) {
      slide.addText(manifest.date, {
        x: MX, y: CBOT_Y, w: 2.25, h: CBOT_H,
        fontSize: 8, color: dim, fontFace: FMONO, valign: 'middle',
      });
    }
  }
  // Page number
  if (pageNum) {
    slide.addText(String(pageNum), {
      x: W - MX - 0.68, y: CBOT_Y, w: 0.68, h: CBOT_H,
      fontSize: 8, color: ink || ACC, fontFace: FMONO, valign: 'middle', align: 'right',
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
  // Big, bold, theme-display heading with a per-theme accent treatment (not just bold). Deterministic
  // fit so a 2-line title shrinks instead of spilling.
  const style = T.title_style || 'underline';
  const fg = titleColor(isLight);   // title gets its own on-theme colour (accent, contrast-safe)
  const t  = toPlain(title || '');
  const placeholder = placeholderFor(slide, 'title', x, y, w, h);
  const bind = (bx, by, bw, bh, fs) => {
    if (!placeholder) return {};
    TITLE_BOXES.set(pptx._slides.indexOf(slide) + 1, [bx, by, bw, bh, fs]);
    return { placeholder };
  };

  if (style === 'bar-left') {
    const barW = 0.13, gap = 0.18, tw = w - barW - gap;
    slide.addShape(pptx.ShapeType.rect, { x, y: y + 0.05, w: barW, h: h - 0.04, fill: { color: ACC }, line: { type: 'none' } });
    const fs = fontSize || fitFontSize(t, 0.88 * tw, h, 28, 17);
    slide.addText(t, {
      x: x + barW + gap, y, w: tw, h, fontSize: fs,
      fontFace: FDISP, color: fg, bold: true, valign: 'bottom', wrap: true, align: 'left',
      ...bind(x + barW + gap, y, tw, h, fs),
    });
    return;
  }
  if (style === 'overline') {
    slide.addShape(pptx.ShapeType.rect, { x, y: y + 0.03, w: 0.95, h: 0.045, fill: { color: ACC }, line: { type: 'none' } });
    const fs = fontSize || fitFontSize(t, 0.88 * w, h - 0.10, 27, 17);
    slide.addText(t, {
      x, y: y + 0.16, w, h: h - 0.10, fontSize: fs,
      fontFace: FDISP, color: fg, bold: true, valign: 'bottom', wrap: true, align: 'left',
      ...bind(x, y + 0.16, w, h - 0.10, fs),
    });
    return;
  }
  if (style === 'accent-lead') {
    // Single-colour title with a small accent square marker at the start + underline (the emphasis is the
    // accent decoration, NOT a two-tone title).
    const sq = 0.17, gap = 0.15, tw = w - sq - gap;
    slide.addShape(pptx.ShapeType.rect, { x, y: y + h - 0.40, w: sq, h: sq, fill: { color: ACC }, line: { type: 'none' } });
    const fs = fontSize || fitFontSize(t, 0.88 * tw, h, 28, 17);
    slide.addText(t, {
      x: x + sq + gap, y, w: tw, h, fontSize: fs,
      fontFace: FDISP, color: fg, bold: true, valign: 'bottom', wrap: true, align: 'left',
      ...bind(x + sq + gap, y, tw, h, fs),
    });
    slide.addShape(pptx.ShapeType.rect, { x: x + sq + gap, y: y + h + 0.03, w: ULN_W, h: ULN_H, fill: { color: ACC }, line: { type: 'none' } });
    return;
  }
  // default: accent underline bar below title
  const fs = fontSize || fitFontSize(t, 0.88 * w, h, 28, 17);
  slide.addText(t, {
    x, y, w, h, fontSize: fs,
    fontFace: FDISP, color: fg, bold: true, valign: 'bottom', wrap: true, align: 'left',
    ...bind(x, y, w, h, fs),
  });
  slide.addShape(pptx.ShapeType.rect, { x, y: y + h + 0.03, w: ULN_W, h: ULN_H, fill: { color: ACC }, line: { type: 'none' } });
}

// Largest font (<= maxFs, >= 10) at which all bullets fit w×h, plus the estimated text-block height at
// that size, measured with the body fonts' real glyph widths (wrapLines) so the
// result never overflows on its own -- addBullets then distributes the leftover height as paragraph
// spacing so the list still fills the box. (normAutofit/shrinkText proved unreliable at clamping, so
// fitting is fully deterministic here.)
function fitBullets(bullets, w, h, maxFs, dense) {
  maxFs = maxFs || 22;
  const sizes = [22, 20, 18, 16, 15, 14, 13, 12, 11, 10, 9].filter(f => f <= maxFs);
  if (!sizes.length) sizes.push(Math.min(maxFs, 10));
  for (const fs of sizes) {
    const textH = bulletsH(bullets, w, fs, dense);
    if (textH <= h * 0.98) return { fs, textH };
  }
  const fs = sizes[sizes.length - 1];
  return { fs, textH: bulletsH(bullets, w, fs, dense) };
}

// Height a flat list needs at fs in a box w wide (a dense list: tighter lines and paragraph gaps).
function bulletsH(bullets, w, fs, dense, role = 'body', bold = false) {
  const ew = Math.max(0.4, w - BULLET_INSET);
  const lineH = fs * BODY_LINE * (dense ? DENSE_LINE : 1) / 72, paraGap = (fs * (dense ? 0.25 : 0.5)) / 72;
  const lines = bullets.reduce((n, b) => n + wrapLines(String(b || '').trim(), ew, fs, role, bold), 0);
  return lines * lineH + bullets.length * paraGap + 0.06;
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

// bulletFontSize already computes a size that fits w×h deterministically, so no pptxgenjs autofit is
// needed on the default path -- `shrinkText`(normAutofit) is what produced the PNG "word ghosting", so
// it is dropped. autoFit=true still opts into fit:'resize' (spAutoFit) for callers that want it.
function addBullets(slide, bullets, x, y, w, h, isLight, fontSize, autoFit, dense, forceFs) {
  if (!bullets || !bullets.length) return;
  const { fs, textH } = forceFs ? { fs: forceFs, textH: bulletsH(bullets, w, forceFs, dense) }
                                : fitBullets(bullets, w, h, fontSize || 22, dense);
  const n = bullets.length;
  // Distribute leftover vertical space as paragraph spacing so the list fills the box instead of
  // floating as a small block at the top (capped so gaps stay tasteful). Deterministic fit above means
  // no overflow, so no shrinkText needed.
  const extra = Math.max(0, h - textH);
  const gapIn = n > 1 ? Math.min(0.28, extra / n) : 0;
  const paraSpaceAfterPt = Math.round(fs * (dense ? 0.2 : 0.45) + gapIn * 72);
  const items = bullets.map(b => ({
    text: capFirst(toPlain(b)),
    options: {
      bullet:        { code: '25B8', color: ACC, indent: BULLET_IND_PT },
      paraSpaceAfter: paraSpaceAfterPt,
      color:         fgC(isLight),
      fontFace:      FBODY,
      ...(dense ? { lineSpacingMultiple: DENSE_LINE } : {}),
    },
  }));
  const fitOpt = autoFit ? { fit: 'resize' } : {};
  const placeholder = placeholderFor(slide, 'body', x, y, w, h);
  slide.addText(items, { x, y, w, h, fontSize: fs, valign: 'top', margin: BULLET_MARGIN,
                         ...(placeholder ? { placeholder } : {}), ...fitOpt });
}

// ── two-level lists: a group title is a parent bullet, its items are child bullets one level in ──────────
const NEST_IND_PT = 18;                 // bullet hanging indent per level (pt)
const NEST_IND = NEST_IND_PT / 72;

function nestedGroups(s) {
  const groups = (s.groups || []).map(g => ({
    title: capFirst(toPlain(g.title || '')),
    items: (g.items || []).map(t => capFirst(toPlain(t))).filter(t => t.trim()),
  })).filter(g => g.title.trim() || g.items.length);
  if (groups.length) return groups;
  const flat = (s.bullets || []).map(b => capFirst(toPlain(b))).filter(t => t.trim());
  return flat.length ? [{ title: '', items: flat }] : [];
}

function hasHierarchy(groups) { return (groups || []).some(g => g.title && g.items.length); }

function childFs(fs) { return fs >= 14 ? fs - 2 : fs; }

function _nestLines(text, width, fs) {
  return wrapLines(String(text || '').trim(), Math.max(0.4, width), fs, 'body');
}

// Height one group needs at parent size fs in a box w wide (mirrored by fit_model.nested_group_h).
function nestedGroupH(g, w, fs, dense) {
  const cfs = childFs(fs), ew = Math.max(0.4, w - 0.08 - NEST_IND);
  const line = BODY_LINE * (dense ? DENSE_LINE : 1), gap = dense ? 0.15 : 0.3;
  let h = 0;
  if (g.title) h += _nestLines(g.title, ew, fs) * fs * line / 72 + fs * gap / 72;
  // items under a title (or continuing a titled group in the next column) are sub-bullets; others are top-level
  const child = !!(g.title || g.cont), ifs = child ? cfs : fs, cw = child ? ew - NEST_IND : ew;
  g.items.forEach((it, i) => {                      // the last child's gap is the group gap below
    h += _nestLines(it, cw, ifs) * ifs * line / 72 + (i < g.items.length - 1 ? ifs * gap / 72 : 0);
  });
  return h + fs * (dense ? 0.2 : 0.4) / 72;
}

// Largest parent size (<= maxFs) at which every group fits w×h; children run childFs(fs).
function fitNested(groups, w, h, maxFs, dense) {
  const sizes = [22, 20, 18, 16, 15, 14, 13, 12, 11, 10, 9].filter(f => f <= (maxFs || 20));
  const need = (fs) => nestedH(groups, w, fs, dense);
  for (const fs of sizes) { const t = need(fs); if (t <= h * 0.98) return { fs, cfs: childFs(fs), textH: t }; }
  const fs = sizes[sizes.length - 1];
  return { fs, cfs: childFs(fs), textH: need(fs) };
}

function nestedH(groups, w, fs, dense) { return groups.reduce((a, g) => a + nestedGroupH(g, w, fs, dense), 0.06); }

function nestedParas(groups, fs, isLight, groupGapPt, opts) {
  opts = opts || {};
  const cfs = childFs(fs), paras = [];
  const tight = opts.dense ? { lineSpacingMultiple: DENSE_LINE } : {};
  const gap = opts.dense ? 0.15 : 0.3;
  groups.forEach((g, gi) => {
    const last = gi === groups.length - 1;
    const after = last ? 0 : groupGapPt;
    if (g.title) {
      paras.push({ text: g.title, options: {
        bullet: opts.noParentBullet ? false : { code: '25B8', color: ACC, indent: NEST_IND_PT },
        bold: true, fontSize: fs, fontFace: FBODY, color: opts.parentColor || fgC(isLight), ...tight,
        paraSpaceAfter: g.items.length ? Math.round(fs * gap) : after } });
    }
    g.items.forEach((t, ii) => {
      const child = !!(g.title || g.cont);
      paras.push({ text: t, options: {
        bullet: child ? { code: '2013', color: ACC, indent: NEST_IND_PT } : { code: '25B8', color: ACC, indent: NEST_IND_PT },
        indentLevel: child && !opts.noParentBullet ? 1 : 0,
        fontSize: child ? cfs : fs, fontFace: FBODY, color: fgC(isLight), ...tight,
        paraSpaceAfter: ii === g.items.length - 1 ? after : Math.round(cfs * gap) } });
    });
  });
  return paras;
}

// Two-level list in a box; `forceFs` lets sibling boxes share one size (uniform sizing).
function addNested(slide, groups, x, y, w, h, isLight, maxFs, forceFs, dense) {
  if (!groups.length) return;
  const fit = forceFs ? { fs: forceFs, textH: nestedH(groups, w, forceFs, dense) }
                      : fitNested(groups, w, h, maxFs || 20, dense);
  const extra = Math.max(0, h - fit.textH);
  const gap = Math.round(fit.fs * (dense ? 0.2 : 0.4) + (groups.length > 1 ? Math.min(0.22, extra / groups.length) : 0) * 72);
  const placeholder = placeholderFor(slide, 'body', x, y, w, h);
  slide.addText(nestedParas(groups, fit.fs, isLight, gap, { dense }),
                { x, y, w, h, valign: 'top', margin: [0.05, 0.08, 0.05, 0],
                  ...(placeholder ? { placeholder } : {}) });
}

// ── elastic text: a list grows to hold its content -- smaller, denser, then two columns; never cut ─────────
// Mirrored by fit_model.plan_list. The first plan whose every column fits at >= FLOOR_FS wins; a list that fits
// no plan still shows in full with the roomiest one.
const FLOOR_FS = 12;                    // readability floor of body text
const DENSE_LINE = 0.9;                 // line spacing of a dense list (0.9 of the normal line)
const COL_GAP = 0.3;                    // gap between the two columns of a list
const TWO_COL_MIN_W = 6.0;              // a list splits into two columns only in a box at least this wide

function listUnits(s, bullets) {
  const groups = nestedGroups(s);
  if (s.groups && hasHierarchy(groups)) return { nested: true, groups };
  return { nested: false, groups: [{ title: '', items: (bullets || []).map(b => String(b || '')).filter(t => t.trim()) }] };
}

// Two columns of about equal height: cut between groups (between the items of a single group or of a flat list).
function splitColumns(u, w) {
  let parts;
  if (u.nested && u.groups.length >= 2) parts = u.groups.map(g => [g]);
  else {
    const g = u.groups[0];
    parts = g.items.map((it, i) => [{ title: i === 0 ? g.title : '', cont: i > 0 && !!g.title, items: [it] }]);
  }
  const hOf = (part) => u.nested ? nestedH(part, w, 14, false) : bulletsH(part[0].items, w, 14, false);
  const hs = parts.map(hOf);
  const total = hs.reduce((a, b) => a + b, 0);
  let best = 1, bestMax = Infinity, run = 0;
  for (let k = 1; k < parts.length; k++) {
    run += hs[k - 1];
    const m = Math.max(run, total - run);
    if (m < bestMax - 1e-9) { bestMax = m; best = k; }
  }
  const join = (ps) => {
    if (u.nested && u.groups.length >= 2) return { nested: true, groups: ps.flat() };
    const first = ps[0][0];
    return { nested: u.nested, groups: [{ title: first.title, cont: first.cont, items: ps.map(pp => pp[0].items[0]) }] };
  };
  return [join(parts.slice(0, best)), join(parts.slice(best))];
}

function unitsH(c, w, fs, dense) { return c.nested ? nestedH(c.groups, w, fs, dense) : bulletsH(c.groups[0].items, w, fs, dense); }

function unitsFit(c, w, h, maxFs, dense) {
  return c.nested ? fitNested(c.groups, w, h, Math.min(maxFs, 20), dense) : fitBullets(c.groups[0].items, w, h, maxFs, dense);
}

function planList(s, bullets, w, h, maxFs, maxCols) {
  maxFs = maxFs || 22;
  const u = listUnits(s, bullets);
  const count = u.nested && u.groups.length >= 2 ? u.groups.length : u.groups[0].items.length;
  const tries = [[1, false], [1, true]];
  if ((maxCols || 2) >= 2 && w >= TWO_COL_MIN_W && count >= 2) tries.push([2, false], [2, true]);
  let last = null;
  for (const [cols, dense] of tries) {
    const cw = cols === 1 ? w : (w - COL_GAP) / 2;
    const columns = cols === 1 ? [u] : splitColumns(u, cw);
    const fs = Math.min(...columns.map(c => unitsFit(c, cw, h, maxFs, dense).fs));
    const textH = Math.max(...columns.map(c => unitsH(c, cw, fs, dense)));
    const ok = Math.min(fs, u.nested ? childFs(fs) : fs) >= FLOOR_FS && textH <= h * 0.98;
    last = { cols, dense, fs, textH, ok, cw, columns, nested: u.nested };
    if (ok) return last;
  }
  return last;
}

function drawList(slide, s, x, y, h, isLight, plan) {
  const fs = listFsFor(plan, h);
  plan.columns.forEach((c, i) => {
    const cx = x + i * (plan.cw + COL_GAP);
    if (c.nested) addNested(slide, c.groups, cx, y, plan.cw, h, isLight, 20, fs, plan.dense);
    else addBullets(slide, c.groups[0].items, cx, y, plan.cw, h, isLight, fs, false, plan.dense, fs);
  });
}

// The plan's size, or -- when its text is taller than h (no plan fitted) -- the largest smaller size at which every
// column fits, so the columns stay one size (the fit guard would set each overflowing box smaller on its own).
function listFsFor(plan, h) {
  const fits = (fs) => plan.columns.every(c => unitsH(c, plan.cw, fs, plan.dense) <= h * 0.98);
  if (fits(plan.fs)) return plan.fs;
  for (let fs = plan.fs - 0.5; fs >= 6; fs -= 0.5) if (fits(fs)) return fs;
  return 6;
}

// The slide's text box, elastic: sub-bullets under their parent when its groups carry a hierarchy, the flat list
// otherwise; `maxCols` 1 keeps it in one column (beside a figure).
function addList(slide, s, bullets, x, y, w, h, isLight, fontSize, maxCols) {
  const plan = planList(s, bullets, w, h, fontSize || 22, maxCols);
  drawList(slide, s, x, y, h, isLight, plan);
  return plan;
}

// ── room for the body: a title that fits one line gives the body 0.25" more when the text needs it ──────
const COMPACT_TTL_H = 0.55;
function titleCompactable(title, w) { return estLines(toPlain(title || ''), 0.88 * (w || BW), 20, 'display', true) <= 1; }
function frame(compact) {
  const lift = compact ? TTL_H - COMPACT_TTL_H : 0;
  return { ty: TTL_Y, th: TTL_H - lift, by: BODY_Y - lift, bh: BODY_H + lift };
}
// The first frame whose plan fits (the compact one only when the title allows it and it helps).
function roomFor(title, titleW, planFor) {
  const F = frame(false), plan = planFor(F);
  if (plan.ok || !titleCompactable(title, titleW)) return { F, plan };
  const G = frame(true), p = planFor(G);
  return (p.ok || p.fs > plan.fs) ? { F: G, plan: p } : { F, plan };
}

// Height the text needs at 10pt (with bulletsMinH's safety factor), so a figure beside it is placed clear of it.
function listMinH(s, bullets, w) {
  const groups = nestedGroups(s);
  if (!(s.groups && hasHierarchy(groups))) return bulletsMinH(bullets, w);
  return fitNested(groups, w, 99, 10).textH * 1.3;
}

// A card/cell body: its items as – sub-bullets when it has several, else the one paragraph it always was.
function cardItems(block) {
  return (block.items || []).map(t => String(t || '').trim()).filter(Boolean);
}

// Largest size (<= maxFs, >= minFs) at which the body fits w×h; one size across equal cards is taken by the caller.
// The largest size (half points) at which a card's body fits w x h as the fit guard measures it, so cards that share
// the smallest of these sizes are drawn at it unshrunk and stay uniform.
function cardBodyFs(block, text, w, h, maxFs, minFs) {
  // below minFs only when the text needs it: the guard would shrink that card alone, and cards share one size
  for (let fs = maxFs; fs > 6; fs -= 0.5) {
    const [t, o] = cardBodyText(block, text, 0, 0, w, h, fs, '', 'left');
    if (!t || textScale(t, o) >= 1) return fs;
  }
  return Math.min(minFs || 9, 6);
}

// A card's body as one text box: a paragraph, or its items as dash bullets.
function cardBodyText(block, text, x, y, w, h, fs, color, align) {
  const items = cardItems(block);
  if (items.length < 2) {
    return [text ? capFirst(toPlain(text)) : '', { x, y, w, h, fontSize: fs, fontFace: FBODY, color,
                                                   align: align || 'left', valign: 'top', wrap: true }];
  }
  return [items.map((t, i) => ({ text: capFirst(toPlain(t)), options: {
    bullet: { code: '2013', color: ACC, indent: NEST_IND_PT }, fontSize: fs, fontFace: FBODY, color,
    breakLine: i < items.length - 1, paraSpaceAfter: i === items.length - 1 ? 0 : Math.round(fs * 0.35) } })),
    { x, y, w, h, valign: 'top', align: 'left', margin: [0.02, 0.04, 0.02, 0] }];
}

function addCardBody(slide, block, text, x, y, w, h, fs, color, align) {
  const [t, o] = cardBodyText(block, text, x, y, w, h, fs, color, align);
  if (t) slide.addText(t, o);
}

// Read intrinsic pixel dimensions straight from the PNG/JPEG/GIF header (no deps) so we can preserve the
// real aspect ratio ourselves -- pptxgenjs `sizing:'contain'` proved unreliable and let wide figures get
// stretched to fill a short band.
function imgDims(p) {
  try {
    const fd = fs.openSync(p, 'r');
    const buf = Buffer.alloc(30000);
    const n = fs.readSync(fd, buf, 0, buf.length, 0);
    fs.closeSync(fd);
    if (n >= 24 && buf[0] === 0x89 && buf[1] === 0x50) {            // PNG
      return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) };
    }
    if (n >= 10 && buf[0] === 0x47 && buf[1] === 0x49) {            // GIF
      return { w: buf.readUInt16LE(6), h: buf.readUInt16LE(8) };
    }
    if (buf[0] === 0xFF && buf[1] === 0xD8) {                       // JPEG: scan SOF markers
      let o = 2;
      while (o + 9 < n) {
        if (buf[o] !== 0xFF) { o++; continue; }
        const m = buf[o + 1];
        if (m >= 0xC0 && m <= 0xCF && m !== 0xC4 && m !== 0xC8 && m !== 0xCC) {
          return { h: buf.readUInt16BE(o + 5), w: buf.readUInt16BE(o + 7) };
        }
        o += 2 + buf.readUInt16BE(o + 2);
      }
    }
  } catch (e) { /* fall through to box-as-is */ }
  return null;
}

// Fit an image inside the x,y,w,h box preserving its real aspect ratio, centered. Passing the exact
// contained w/h (no `sizing`) means pptxgenjs draws it undistorted -- never stretched to fill the box.
function addImage(slide, imgPath, x, y, w, h) {
  if (!imgPath || !fs.existsSync(imgPath)) return;
  const placeholder = placeholderFor(slide, 'pic', x, y, w, h);
  const d = imgDims(imgPath);
  if (d && d.w > 0 && d.h > 0) {
    const scale = Math.min(w / d.w, h / d.h);
    const iw = d.w * scale, ih = d.h * scale;
    slide.addImage({ path: imgPath, x: x + (w - iw) / 2, y: y + (h - ih) / 2, w: iw, h: ih,
                     ...(placeholder ? { placeholder } : {}) });
  } else {
    slide.addImage({ path: imgPath, x, y, w, h, sizing: { type: 'contain', w, h },
                     ...(placeholder ? { placeholder } : {}) });
  }
}

// Image + a small centered caption underneath. Every caption is set at CAP_FS and its strip is as tall as its
// wrapped text, so the (aspect-preserved) image takes the rest of the box and never overlaps it.
const CAP_FS = 10;
function captionH(caption, w) {
  const cap = caption ? toPlain(String(caption).trim()) : '';
  if (!cap) return 0;
  const ins = insetsOf({});
  return wrapLines(cap, w - ins.l - ins.r, CAP_FS, 'body') * CAP_FS * LINE / 72 + ins.t + ins.b;
}

function addImageCap(slide, imgPath, x, y, w, h, caption, isLight) {
  if (!imgPath || !fs.existsSync(imgPath)) return;
  const cap = caption ? String(caption).trim() : '';
  const capH = captionH(cap, w);
  addImage(slide, imgPath, x, y, w, h - capH);
  if (cap) {
    slide.addText(toPlain(cap), {
      x, y: y + h - capH, w, h: capH,
      fontSize: CAP_FS, fontFace: FBODY, italic: true, color: dimC(isLight),
      align: 'center', valign: 'top', wrap: true,
    });
  }
}

// cover
function buildCover(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);

  const title   = toPlain(s.title || manifest.lecture_title || '');
  const speaker = s.speaker || manifest.speaker || '';
  const date    = manifest.date || '';

  // Bold accent GRADIENT band on the left as a strong color anchor, dressed with layered geometric
  // motifs (concentric rings, a soft glow, a dotted column) so it reads designed, not like a flat block.
  const bandW = 3.05;
  const ink = textOn(gradMid());
  gradRect(slide, 0, 0, bandW, H, gradStops()[0], gradStops()[1], 'v');
  panelMotif(slide, 0, 0, bandW, H, ink);
  // kicker + a short underline
  slide.addText('LECTURE', {
    x: 0.5, y: 0.72, w: bandW - 0.9, h: 0.3, wrap: false,
    fontSize: 12, fontFace: FMONO, color: ink, charSpacing: 3,
  });
  slide.addShape(pptx.ShapeType.rect, { x: 0.5, y: 1.06, w: 0.55, h: 0.028, fill: { color: ink, transparency: 25 }, line: { type: 'none' } });

  const rx = bandW + 0.55, rw = W - rx - MX;
  slide.addText(title, {
    x: rx, y: 1.15, w: rw, h: 2.55,
    fontSize: fitFontSize(title, rw, 2.55, 40, 22), fontFace: FDISP,
    color: fgC(isLight), valign: 'middle', wrap: true, bold: true,
  });
  slide.addShape(pptx.ShapeType.rect, { x: rx, y: 3.95, w: 1.25, h: 0.05, fill: { color: ACC }, line: { type: 'none' } });
  const footer = [speaker, date].filter(Boolean).join('    ·    ');
  if (footer) {
    slide.addText(footer, {
      x: rx, y: 4.25, w: rw, h: 0.5,
      fontSize: 13, fontFace: FBODY, color: dimC(isLight), valign: 'middle',
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
    fontSize: 42, fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'bottom', shrinkText: true,
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
  const bs = (s.bullets || []).filter(b => String(b || '').trim());
  const { F, plan } = roomFor(s.title, BW, (f) => planList(s, bs, BW - 0.5, f.bh - 0.24, 22));
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  if (!bs.length) return;  // no content -> don't draw an empty panel (was rendering a blank block)
  if (bs.length < 4) bgMotif(slide);  // sparse list -> add corner motif so the slide doesn't read empty
  // Soft tinted panel + accent side-stripe so the content area reads as a designed block, not bare
  // text floating on the background.
  slide.addShape(pptx.ShapeType.roundRect, {
    x: MX, y: F.by, w: BW, h: F.bh, rectRadius: 0.06,
    fill: { color: ACC, transparency: isLight ? 93 : 88 }, line: { type: 'none' },
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: F.by, w: 0.07, h: F.bh, fill: { color: ACC }, line: { type: 'none' },
  });
  drawList(slide, s, MX + 0.28, F.by + 0.12, F.bh - 0.24, isLight, plan);
}

// formula
function _drawFormulaBox(slide, isLight, boxY, boxH) {
  const BAR_W  = 0.06;  // left accent bar width
  const boxBg  = tintC(isLight, 8);
  // Background panel (slight tint)
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: boxY, w: BW, h: boxH,
    fill: { color: boxBg },
    line: { type: 'none' },
  });
  // Accent left bar — mirrors HTML border-left: 4px solid var(--accent)
  slide.addShape(pptx.ShapeType.rect, {
    x: MX, y: boxY, w: BAR_W, h: boxH,
    fill: { color: ACC }, line: { type: 'none' },
  });
}

// Height of the formula box: the drawn equation at the full body width, never below what its lines need to stay
// legible (mirrored by layout_select.formula_min_h).
function formulaMinH(lines) { return 0.25 + 0.4 * Math.max(1, lines); }
// lines of every stacked formula plus the blank line between two (mirrored by slide_writer.extent)
function formulaLines(s) {
  const text = String(s.formula_text || s.formula_latex || '');
  const eqs = text.split(/\n\s*\n/).filter(e => e.trim()).length;
  return Math.max(1, text.split(/\n/).filter(t => t.trim()).length + Math.max(0, eqs - 1));
}

function buildFormula(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);

  const bullets  = (s.bullets || []).filter(b => String(b || '').trim());
  const hasBulls = bullets.length > 0;
  const natural = equationH(s, BW - 0.23, 24);
  const minBox = formulaMinH(formulaLines(s));
  // the box takes what the equation needs (up to 60% of the body); text that needs more takes it back
  const planFor = (f) => {
    const boxes = [Math.max(minBox, Math.min(natural, f.bh * 0.6))];
    if (boxes[0] > minBox) boxes.push(minBox);
    let last = null;
    for (const box of boxes) {
      const p = planList(s, bullets, BW, f.bh - box - 0.15, 20);
      last = { ...p, box };
      if (p.ok) break;
    }
    // what the text does not need goes to the equation (up to 60% of the body)
    const spare = (f.bh - last.box - 0.15) - (last.textH / 0.98 + 0.06);
    if (last.ok && spare > 0) last.box = Math.min(f.bh * 0.6, last.box + spare);
    return last;
  };
  const { F, plan } = hasBulls ? roomFor(s.title, BW, planFor) : { F: frame(false), plan: null };
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  const boxH = hasBulls ? plan.box : F.bh * 0.60;
  const boxY = hasBulls ? F.by : F.by + (F.bh - boxH) / 2;
  _drawFormulaBox(slide, isLight, boxY, boxH);
  drawEquation(slide, s, isLight, MX + 0.15, boxY + 0.06, BW - 0.23, boxH - 0.12, 24);
  if (hasBulls) drawList(slide, s, MX, boxY + boxH + 0.11, F.bh - boxH - 0.15, isLight, plan);
}

// image-right
const TEXT_SHARES = [0.55, 0.60, 0.65, 0.70, 0.74];
function sidePlan(s, bs, f) {
  let last = null;
  for (const r of TEXT_SHARES) {
    const p = planList(s, bs, BW * r, f.bh, 22, 1);
    last = { ...p, tw: BW * r };
    if (p.ok) break;
  }
  return last;
}

function buildImgRight(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);

  const bs  = s.bullets || [];
  const img = (s.images || [])[0];
  const { F, plan } = roomFor(s.title, BW, (f) => sidePlan(s, bs, f));
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  drawList(slide, s, MX, F.by, F.bh, isLight, plan);
  const ix = MX + plan.tw + 0.15;
  addImageCap(slide, img, ix, F.by, MX + BW - ix, F.bh, s.caption, isLight);
}

// image-left
function buildImgLeft(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);

  const bs  = s.bullets || [];
  const img = (s.images || [])[0];
  const { F, plan } = roomFor(s.title, BW * TEXT_SHARES[TEXT_SHARES.length - 1], (f) => sidePlan(s, bs, f));
  const iw = BW - plan.tw - 0.15;

  // Image on left
  addImageCap(slide, img, MX, F.by - 0.38, iw, F.bh + 0.38, s.caption, isLight);

  // Title and bullets on right
  const rx = MX + iw + 0.15;
  slide.addText(toPlain(s.title), {
    x: rx, y: F.ty, w: plan.tw, h: F.th,
    fontSize: fitFontSize(s.title, plan.tw, F.th, 26, 16), fontFace: FDISP, color: titleColor(isLight),
    bold: true, valign: 'bottom', wrap: true,
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: rx, y: F.ty + F.th + 0.03, w: ULN_W, h: ULN_H,
    fill: { color: ACC }, line: { type: 'none' },
  });
  drawList(slide, s, rx, F.by, F.bh, isLight, plan);
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

// Content-aware table geometry.  Equal-width columns and one fixed row height
// made a long header consume half the table while short numeric columns wasted
// space.  Search 18..12pt, allocate widths by measured content, and compute one
// height per wrapped row.  Returning fits=false lets callers reserve the whole
// body instead of overlapping a takeaway.
function tableFit(headers, rows, width, maxHeight, maxFs = 18, minFs = 12) {
  const clean = [headers, ...rows].map(row => headers.map((_, j) => toPlain((row || [])[j] || '')));
  const n = headers.length || 1;
  const geometry = (fs) => {
    const words = Array.from({ length: n }, (_, j) => Math.max(0.1, ...clean.map((row, ri) => row[j] || '')
      .flatMap((v, ri) => v.split(/\s+/).map(w => textW(w, fs, 'body', ri === 0)))) + 0.16);
    const raw = Array.from({ length: n }, (_, j) => {
      const typical = Math.max(textW('abcd', fs, 'body'), ...clean.map(row => textW((row[j] || '').slice(0, 30), fs, 'body')));
      return Math.max(0.72, words[j] + 0.06, typical / 2 + 0.34);
    });
    const floor = Math.min(0.82, width / n * 0.72);
    const base = raw.map(v => Math.max(floor, v));
    const scale = width / base.reduce((a, b) => a + b, 0);
    const colW = base.map(v => v * scale);
    const rowH = clean.map((row, ri) => {
      const lines = Math.max(1, ...row.map((cell, j) => wrapLines(cell, Math.max(0.2, colW[j] - 0.12), fs, 'body', ri === 0)));
      return lines * fs * LINE / 72 + (ri === 0 ? 0.16 : 0.12);           // cell insets 0.05" + room
    });
    // a column narrower than its longest word would break that word (numbers split mid-digit)
    const whole = colW.every((w, j) => w >= words[j]);
    return { fs, headFs: fs, cellFs: fs, colW, rowH, height: rowH.reduce((a, b) => a + b, 0), whole };
  };
  for (let fs = maxFs; fs >= minFs; fs--) {
    const g = geometry(fs);
    if (g.whole && g.height <= maxHeight * 0.98) return { fits: true, ...g };
  }
  // the smallest size, reported as not fitting: a caller may give it a larger region
  return { fits: false, ...geometry(minFs) };
}

function tableData(headers, rows, isLight, fit) {
  const headBg = ACC, evenBg = tintC(isLight, 8), oddBg = isLight ? BGLIT : BGDARK;
  return [
    headers.map(h => ({ text: toPlain(h), options: { bold: true, color: INK_ACC, fill: { color: headBg },
      align: 'center', valign: 'middle', fontFace: FBODY, fontSize: fit.headFs, margin: 0.05 } })),
    ...rows.map((row, i) => Array.from({ length: headers.length }, (_, j) => ({ text: toPlain(row[j] || ''),
      options: { fill: { color: i % 2 === 0 ? evenBg : oddBg }, color: isLight ? FGLIT : FGDRK,
        align: j === 0 ? 'left' : 'center', valign: 'middle', fontFace: FBODY,
        fontSize: fit.cellFs, margin: 0.05 } }))),
  ];
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

  const fit = tableFit(headers, rows, BW, BODY_H - 0.35, 18, 7);
  const tblH   = fit.height;
  const tblTop = BODY_Y + Math.max(0, (BODY_H - tblH) / 2);

  const borderC  = dimC(isLight);
  const tblData = tableData(headers, rows, isLight, fit);

  slide.addTable(tblData, {
    x: MX, y: tblTop, w: BW, rowH: fit.rowH,
    colW: fit.colW,
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
    addTitle(slide, s.title, isLight);
    addList(slide, s, bullets, MX, BODY_Y, BW, BODY_H, isLight);
    return;
  }

  // the table's size and the text's are chosen together: the text as large as it can be with the table at most 2pt
  // smaller, the table above the text or, when that leaves the text less, beside it
  const F = frame(titleCompactable(s.title, BW));
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  const borderC = dimC(isLight);
  if (!hasBulls) {
    const fit = tableFit(headers, rows, BW, F.bh - 0.05, 16, 7);
    slide.addTable(tableData(headers, rows, isLight, fit), {
      x: MX, y: F.by, w: BW, rowH: fit.rowH, colW: fit.colW, border: { pt: 0.5, color: borderC } });
    return;
  }
  const best = tableTextPlan(s, bullets, headers, rows, F);
  slide.addTable(tableData(headers, rows, isLight, best.fit), {
    x: MX, y: F.by, w: best.tw, rowH: best.fit.rowH, colW: best.fit.colW, border: { pt: 0.5, color: borderC } });
  if (best.side) drawList(slide, s, MX + best.tw + 0.3, F.by, F.bh, isLight, best.p);
  else drawList(slide, s, MX, F.by + best.fit.height + 0.10, best.room, isLight, best.p);
}

// The size a list plan's text is really shown at: its own, or what the fit guard leaves of it when it overflows h.
function shownFs(p, h) {
  const fs = p.nested ? childFs(p.fs) : p.fs;
  return p.textH <= h * 0.98 ? fs : fs * h * 0.98 / p.textH;
}

function tableTextPlan(s, bullets, headers, rows, F) {
  let best = null;
  const consider = (c) => {
    c.score = Math.min(c.textFs, c.fit.fs + 2);
    if (!best || c.score > best.score + 1e-6 || (Math.abs(c.score - best.score) < 1e-6 && c.textFs > best.textFs)) best = c;
  };
  for (let tfs = 14; tfs >= 7; tfs--) {                       // table above, text below
    const fit = tableFit(headers, rows, BW, 0, tfs, tfs);
    const room = F.bh - fit.height - 0.10;
    if (room < 0.35 || (!fit.whole && tfs > 7)) continue;
    const p = planList(s, bullets, BW, room, 16);
    consider({ fit, p, room, tw: BW, side: false, textFs: shownFs(p, room) });
  }
  for (const share of [0.34, 0.42, 0.50, 0.58]) {             // table beside the text
    const tw = BW * share;
    const p = planList(s, bullets, BW - tw - 0.3, F.bh, 16, 1);
    for (let tfs = 12; tfs >= 7; tfs--) {
      const fit = tableFit(headers, rows, tw, 0, tfs, tfs);
      if (fit.height > F.bh || !fit.whole) continue;
      consider({ fit, p, room: F.bh, tw, side: true, textFs: shownFs(p, F.bh) });
      break;
    }
  }
  if (!best) {                                                 // nothing leaves the text room: the smallest table
    const fit = tableFit(headers, rows, BW, 0, 7, 7);
    const room = Math.max(0.35, F.bh - fit.height - 0.10);
    best = { fit, p: planList(s, bullets, BW, room, 16), room, tw: BW, side: false };
  }
  return best;
}

// end
// end — HTML: large display title (320px), 3-col footer with border-top separator
function buildEnd(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  bgMotif(slide);
  const title = toPlain(s.title || 'Thank You');
  // Centered large title with an accent rule above -- theme-consistent, clean closing.
  slide.addShape(pptx.ShapeType.rect, { x: W / 2 - 0.55, y: 1.75, w: 1.1, h: 0.06, fill: { color: ACC }, line: { type: 'none' } });
  slide.addText(title, {
    x: MX, y: 2.0, w: BW, h: 1.5,
    fontSize: fitFontSize(title, BW, 1.5, 54, 30), fontFace: FDISP,
    color: fgC(isLight), align: 'center', valign: 'middle', wrap: true, bold: true,
  });
  const meta = manifest.speaker || '';
  if (meta) {
    slide.addText(meta, {
      x: MX, y: 3.65, w: BW, h: 0.45,
      fontSize: 13, fontFace: FBODY, color: dimC(isLight), align: 'center', valign: 'middle',
    });
  }
}

// ── NEW chrome/content builders ────────────────────────────────────────────────────────────────

// cover variant: editorial / magazine — slim accent edge, eyebrow, big left title, baseline meta
function buildCoverEditorial(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  const title  = toPlain(s.title || manifest.lecture_title || '');
  const inst   = s.institution || '';
  const author = s.author || manifest.speaker || '';
  const date   = s.date || manifest.date || '';
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: 0.16, h: H, fill: { color: ACC }, line: { type: 'none' } });
  const lx = 0.92, lw = W - lx - MX;
  const eyebrow = [ 'LECTURE', inst ].filter(Boolean).join('    ·    ').toUpperCase();
  slide.addText(eyebrow, { x: lx, y: 0.72, w: lw, h: 0.3, wrap: false, fontSize: 12, fontFace: FMONO, color: ACC, charSpacing: 3 });
  slide.addShape(pptx.ShapeType.rect, { x: lx, y: 1.08, w: lw, h: 0.02, fill: { color: fgC(isLight), transparency: 80 }, line: { type: 'none' } });
  slide.addText(title, {
    x: lx, y: 1.35, w: lw, h: 2.55, fontSize: fitFontSize(title, lw, 2.55, 46, 24),
    fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'middle', wrap: true,
  });
  slide.addShape(pptx.ShapeType.rect, { x: lx, y: 4.14, w: 1.4, h: 0.05, fill: { color: ACC }, line: { type: 'none' } });
  const foot = [ author, date ].filter(Boolean).join('    ·    ');
  if (foot) slide.addText(foot, { x: lx, y: 4.42, w: lw, h: 0.4, fontSize: 13, fontFace: FBODY, color: dimC(isLight), valign: 'middle' });
}

// cover variant: minimal — centered, thin full-width rules, lots of whitespace
function buildCoverMinimal(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  const title  = toPlain(s.title || manifest.lecture_title || '');
  const inst   = s.institution || '';
  const author = s.author || manifest.speaker || '';
  const date   = s.date || manifest.date || '';
  slide.addShape(pptx.ShapeType.rect, { x: MX, y: 0.82, w: BW, h: 0.02, fill: { color: fgC(isLight), transparency: 82 }, line: { type: 'none' } });
  slide.addShape(pptx.ShapeType.rect, { x: MX, y: H - 0.82, w: BW, h: 0.02, fill: { color: fgC(isLight), transparency: 82 }, line: { type: 'none' } });
  slide.addText((inst || 'LECTURE').toUpperCase(), {
    x: MX, y: 1.4, w: BW, h: 0.3, wrap: false, fontSize: 12, fontFace: FMONO, color: ACC, align: 'center', charSpacing: 4,
  });
  slide.addText(title, {
    x: MX, y: 1.95, w: BW, h: 1.65, fontSize: fitFontSize(title, BW, 1.65, 46, 26),
    fontFace: FDISP, color: titleColor(isLight), bold: true, align: 'center', valign: 'middle', wrap: true,
  });
  slide.addShape(pptx.ShapeType.rect, { x: W / 2 - 0.6, y: 3.74, w: 1.2, h: 0.05, fill: { color: ACC }, line: { type: 'none' } });
  const foot = [ author, date ].filter(Boolean).join('    ·    ');
  if (foot) slide.addText(foot, { x: MX, y: 4.02, w: BW, h: 0.4, fontSize: 13, fontFace: FBODY, color: dimC(isLight), align: 'center', valign: 'middle' });
}

// ending variant: credits — left-aligned big title + institution/author/acknowledgment lines
function buildEndCredits(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  const title  = toPlain(s.title || 'Thank you');
  const inst   = s.institution || '';
  const author = s.author || manifest.speaker || '';
  const ack    = s.acknowledgment || '';
  slide.addShape(pptx.ShapeType.rect, { x: MX, y: 1.68, w: 0.95, h: 0.06, fill: { color: ACC }, line: { type: 'none' } });
  slide.addText(title, {
    x: MX, y: 1.92, w: BW, h: 1.3, fontSize: fitFontSize(title, BW, 1.3, 52, 28),
    fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'top', wrap: true,
  });
  const line1 = [ author, inst ].filter(Boolean).join('    ·    ');
  let y = 3.45;
  if (line1) { slide.addText(line1, { x: MX, y, w: BW, h: 0.36, fontSize: 14, fontFace: FBODY, color: fgC(isLight), valign: 'middle' }); y += 0.42; }
  if (ack)   { slide.addText(capFirst(toPlain(ack)), { x: MX, y, w: BW * 0.82, h: 0.7, fontSize: 12, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true }); }
}

// big-statement: one large declarative line + optional small supporting bullets
function buildBigStatement(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  const statement = capFirst(toPlain(s.statement || s.title || ''));
  const subs = (s.bullets || []).filter(b => String(b || '').trim());
  if (!subs.length) bgMotif(slide);  // just a headline, no support list -> sparse, add corner motif
  slide.addShape(pptx.ShapeType.rect, { x: MX, y: BODY_Y + 0.12, w: 0.9, h: 0.07, fill: { color: ACC }, line: { type: 'none' } });
  const hasSubs = subs.length > 0;
  const stH = hasSubs ? BODY_H * 0.58 : BODY_H - 0.5;
  slide.addText(statement, {
    x: MX, y: BODY_Y + 0.38, w: BW, h: stH, fontSize: fitFontSize(statement, BW, stH, 40, 22),
    fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'top', wrap: true,
  });
  if (hasSubs) addBullets(slide, subs, MX, BODY_Y + 0.38 + stH + 0.12, BW, BODY_H - stH - 0.6, isLight, 16);
}

// definition callout: accent-tinted panel + accent left border, term + definition text
function buildDefinition(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  const term = toPlain(s.term || s.title || '');
  const defText = (s.bullets || []).map(b => capFirst(toPlain(b))).filter(Boolean).join('   ');
  if (defText.length < 90) bgMotif(slide);  // short definition -> a lot of empty space around the panel
  const px = MX, py = BODY_Y + 0.2, pw = BW, ph = BODY_H - 0.5;
  slide.addShape(pptx.ShapeType.roundRect, { x: px, y: py, w: pw, h: ph, rectRadius: 0.09, fill: { color: ACC, transparency: isLight ? 90 : 86 }, line: { type: 'none' } });
  slide.addShape(pptx.ShapeType.rect, { x: px, y: py, w: 0.13, h: ph, fill: { color: ACC }, line: { type: 'none' } });
  const ix = px + 0.5, iw = pw - 0.85;
  slide.addText(term, {
    x: ix, y: py + 0.32, w: iw, h: 0.72, fontSize: fitFontSize(term, iw, 0.72, 30, 18),
    fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'top', wrap: true,
  });
  const df = fitClip(defText, iw, ph - 1.35, 20, 12);
  slide.addText(df.text, { x: ix, y: py + 1.12, w: iw, h: ph - 1.4, fontSize: df.fs, fontFace: FBODY, color: fgC(isLight), valign: 'top', wrap: true });
}

// vertical timeline: spine + dots, each node a bold lead + body (uniform sizes)
function buildTimelineVertical(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const rows = (s.rows || []).filter(r => cardItems(r).length);
  const raw = (s.bullets || []).filter(b => String(b || '').trim());
  // split "Lead: body" into head/desc where a short prefix exists
  const parts = rows.length
    ? rows.map(r => ({ h: capFirst(toPlain(r.title || '')), d: cardItems(r).join(' '), items: cardItems(r) }))
    : raw.map(b => splitLabelBody(capFirst(toPlain(b))));
  const n = parts.length; if (!n) return;
  // up to 5 rows in one column; more run down two columns (mirrored by layout_select.timeline_geometry)
  const colsN = n <= 5 ? 1 : 2, perCol = Math.ceil(n / colsN), COLG = 0.3;
  const colW = (BW - COLG * (colsN - 1)) / colsN;
  const topY = BODY_Y + 0.12, availH = BODY_H - 0.24, rowH = availH / perCol;
  const DOT = 0.19;
  const spineOf = (c) => MX + c * (colW + COLG) + 0.42;
  for (let c = 0; c < colsN; c++) {
    const inCol = Math.min(perCol, n - c * perCol);
    if (inCol > 1) slide.addShape(pptx.ShapeType.rect, { x: spineOf(c) - 0.011, y: topY + rowH * 0.5, w: 0.022, h: rowH * (inCol - 1), fill: { color: ACC, transparency: 45 }, line: { type: 'none' } });
  }
  const tw = colW - 0.82;
  const headBoxH = 0.32, bodyBoxH = Math.max(0.1, rowH - headBoxH - 0.12);
  const minFit = (vals, dflt) => vals.length ? Math.min(...vals) : dflt;
  const uHead = minFit(parts.filter(p => p.h).map(p => fitClip(p.h, tw, headBoxH, 16, 11).fs), 14);
  const uBody = minFit(parts.filter(p => p.d).map(p => cardBodyFs(p, p.d, tw, p.h ? bodyBoxH : rowH - 0.14, 13, 9)), 11);
  parts.forEach((p, i) => {
    const c = Math.floor(i / perCol), r = i % perCol;
    const spineX = spineOf(c), tx = spineX + 0.4;
    const cy = topY + rowH * r + rowH * 0.5;
    slide.addShape(pptx.ShapeType.ellipse, { x: spineX - DOT / 2, y: cy - DOT / 2, w: DOT, h: DOT, fill: { color: ACC }, line: { color: isLight ? BGLIT : BGDARK, pt: 2 } });
    const ny = topY + rowH * r + 0.05;
    if (p.h) {
      slide.addText(p.h, { x: tx, y: ny, w: tw, h: headBoxH, fontSize: uHead, fontFace: FDISP, color: fgC(isLight), bold: true, valign: 'top', wrap: true });
      addCardBody(slide, p, p.d, tx, ny + headBoxH + 0.02, tw, bodyBoxH, uBody, dimC(isLight));
    } else {
      slide.addText(p.d, { x: tx, y: ny, w: tw, h: rowH - 0.1, fontSize: uBody, fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true });
    }
  });
}

// spotlight: one hero point (accent panel, left) + up to 3 secondary points (right)
function buildSpotlight(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = (s.bullets || []).map(b => capFirst(toPlain(b))).filter(Boolean);
  if (!items.length) return;
  const hero = items[0], rest = items.slice(1, 4);
  const heroW = BW * 0.52, gap = 0.3;
  const hx = MX, hy = BODY_Y, hh = BODY_H;
  // hero panel (accent gradient for depth)
  gradRect(slide, hx, hy, heroW, hh, gradStops()[0], gradStops()[1], 'v');
  const ink = textOn(gradMid());
  slide.addText('01', { x: hx + 0.32, y: hy + 0.3, w: 1.0, h: 0.5, wrap: false, fontSize: 22, fontFace: FDISP, color: ink, bold: true, valign: 'top' });
  const hf = fitClip(hero, heroW - 0.7, hh - 1.2, 24, 14);
  slide.addText(hf.text, { x: hx + 0.34, y: hy + 0.92, w: heroW - 0.68, h: hh - 1.2, fontSize: hf.fs, fontFace: FDISP, color: ink, bold: true, valign: 'top', wrap: true });
  // secondary stacked cards
  const rx = hx + heroW + gap, rw = W - rx - MX, m = rest.length || 1;
  const rGap = 0.16, rh = (BODY_H - rGap * (m - 1)) / m;
  const minFit = (vals, dflt) => vals.length ? Math.min(...vals) : dflt;
  const uFs = minFit(rest.map(t => fitClip(t, rw - 0.7, rh - 0.24, 16, 10).fs), 13);
  rest.forEach((t, i) => {
    const ry = BODY_Y + i * (rh + rGap);
    slide.addShape(pptx.ShapeType.roundRect, { x: rx, y: ry, w: rw, h: rh, rectRadius: 0.06, fill: { color: ACC, transparency: isLight ? 90 : 85 }, line: { type: 'none' } });
    slide.addShape(pptx.ShapeType.ellipse, { x: rx + 0.22, y: ry + rh / 2 - 0.12, w: 0.24, h: 0.24, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(String(i + 2).padStart(2, '0'), { x: rx + 0.22, y: ry + rh / 2 - 0.12, w: 0.24, h: 0.24, wrap: false, fontSize: 10, fontFace: FMONO, color: textOn(ACC), bold: true, align: 'center', valign: 'middle' });
    slide.addText(t, { x: rx + 0.62, y: ry + 0.1, w: rw - 0.78, h: rh - 0.2, fontSize: uFs, fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true });
  });
}

// icon-matrix: up to 6 short items in a 2×3 / 3×2 grid, each accent dot + label (uniform)
function buildIconMatrix(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const parts = (s.bullets || []).map(b => splitLabelBody(capFirst(toPlain(b)))).filter(p => p.h || p.d);
  const n = parts.length; if (!n) return;
  const hasBody = parts.some(p => p.h && p.d);
  const cols = n <= 4 ? 2 : 3, rows = Math.ceil(n / cols);
  const GAP = 0.18;
  const cw = (BW - GAP * (cols - 1)) / cols;
  // Cap cell height so a short grid reads as a COMPACT centered block instead of stretching (and looking
  // empty) across the whole body.
  const chMax = (BODY_H - GAP * (rows - 1)) / rows;
  const ch = Math.min(chMax, hasBody ? 1.5 : 1.05);
  const blockH = rows * ch + GAP * (rows - 1);
  const startY = BODY_Y + Math.max(0, (BODY_H - blockH) / 2);
  const DOT = 0.44, tx0 = 0.22 + DOT + 0.18, tW = cw - tx0 - 0.18;
  const uHead = minFitArr(parts.map(p => fitClip(p.h || p.d, tW, hasBody ? 0.5 : ch - 0.24, 19, 12).fs), 15);
  const uBody = minFitArr(parts.filter(p => p.h && p.d).map(p => fitClip(p.d, tW, ch - 0.72, 12, 9).fs), 11);
  parts.forEach((p, i) => {
    const col = i % cols, row = Math.floor(i / cols);
    const x = MX + col * (cw + GAP), y = startY + row * (ch + GAP);
    slide.addShape(pptx.ShapeType.roundRect, { x, y, w: cw, h: ch, rectRadius: 0.07, fill: { color: ACC, transparency: isLight ? 91 : 86 }, line: { type: 'none' } });
    slide.addShape(pptx.ShapeType.roundRect, { x: x + 0.22, y: y + ch / 2 - DOT / 2, w: DOT, h: DOT, rectRadius: 0.06, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(String(i + 1).padStart(2, '0'), { x: x + 0.22, y: y + ch / 2 - DOT / 2, w: DOT, h: DOT, wrap: false, fontSize: 13, fontFace: FMONO, color: textOn(ACC), bold: true, align: 'center', valign: 'middle' });
    const tx = x + tx0;
    if (p.h && p.d) {
      slide.addText(p.h, { x: tx, y: y + 0.16, w: tW, h: 0.5, fontSize: uHead, fontFace: FBODY, color: fgC(isLight), bold: true, valign: 'bottom', wrap: true });
      slide.addText(p.d, { x: tx, y: y + 0.16 + 0.52, w: tW, h: ch - 0.72, fontSize: uBody, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true });
    } else {
      slide.addText(p.h || p.d, { x: tx, y, w: tW, h: ch, fontSize: uHead, fontFace: FBODY, color: fgC(isLight), bold: true, valign: 'middle', wrap: true });
    }
  });
}

// Split "Label: body" (or "Label — body") into {h, d}; heading must be 1-7 words else no split.
function splitLabelBody(text) {
  const t = String(text || '').trim();
  for (const sep of [': ', ' — ', ' – ', ' - ']) {
    const i = t.indexOf(sep);
    if (i > 0) {
      const h = t.slice(0, i).trim(), d = t.slice(i + sep.length).trim();
      // capitalize BOTH parts -- the body after the separator is often a lowercase fragment.
      if (h && d && h.split(/\s+/).length <= 7) return { h: capFirst(h), d: capFirst(d) };
    }
  }
  return { h: '', d: capFirst(t) };
}

// A straight connector line from (x1,y1) to (x2,y2).
function connector(slide, x1, y1, x2, y2, color, w) {
  slide.addShape(pptx.ShapeType.line, {
    x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1) || 0.001, h: Math.abs(y2 - y1) || 0.001,
    line: { color: color || ACC, width: w || 1, transparency: 35 }, flipH: x2 < x1, flipV: y2 < y1,
  });
}

const minFitArr = (vals, dflt) => vals.length ? Math.min(...vals) : dflt;

// comparison-columns: two labelled sides (A vs B), each a header + its own bullets (uniform sizes).
function buildComparison(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const sides = (s.sides || []).slice(0, 2);
  if (sides.length < 2) return;
  const GAP = 0.4, colW = (BW - GAP) / 2;
  const hdrH = 0.5, hdrY = BODY_Y, listY = hdrY + hdrH + 0.18;
  const listH = (BODY_Y + BODY_H) - listY;
  const ink = textOn(gradMid());
  const allItems = sides.flatMap(sd => sd.items || []);
  const uFs = minFitArr(allItems.map(t => fitClip(capFirst(toPlain(t)), colW - 0.7, 0.6, 15, 10).fs), 12);
  sides.forEach((sd, c) => {
    const x = MX + c * (colW + GAP);
    // header band (gradient) with the side label
    gradRect(slide, x, hdrY, colW, hdrH, gradStops()[0], gradStops()[1], 'h');
    slide.addText(toPlain(sd.label || (c === 0 ? 'A' : 'B')), {
      x: x + 0.18, y: hdrY, w: colW - 0.36, h: hdrH, fontSize: fitFontSize(sd.label || '', colW - 0.36, hdrH, 18, 12),
      fontFace: FDISP, color: ink, bold: true, valign: 'middle', wrap: false,
    });
    const items = (sd.items || []).map(t => capFirst(toPlain(t))).filter(Boolean);
    const n = Math.max(items.length, 1), rowH = listH / n;
    items.forEach((t, i) => {
      const ry = listY + i * rowH;
      slide.addShape(pptx.ShapeType.rect, { x: x + 0.02, y: ry + rowH / 2 - 0.05, w: 0.1, h: 0.1, fill: { color: ACC }, line: { type: 'none' } });
      slide.addText(t, { x: x + 0.28, y: ry, w: colW - 0.36, h: rowH, fontSize: uFs, fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true });
    });
  });
}

// comparison-matrix: 2-3 objects compared on the SAME criteria (columns = objects, rows = criteria, a sentence per cell).
function buildComparisonMatrix(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const m = s.matrix || {};
  const ents = (m.entities || []).slice(0, 3).map(e => toPlain(e));
  const rows = m.rows || [];
  if (ents.length < 2 || !rows.length) return;
  const critW = 1.7, GAP = 0.08, colW = (BW - critW - GAP * ents.length) / ents.length;
  const hdrH = 0.42, top = BODY_Y, ink = textOn(gradMid());
  ents.forEach((e, i) => {
    const x = MX + critW + GAP + i * (colW + GAP);
    gradRect(slide, x, top, colW, hdrH, gradStops()[0], gradStops()[1], 'h');
    slide.addText(e, { x: x + 0.1, y: top, w: colW - 0.2, h: hdrH, fontSize: fitFontSize(e, colW - 0.2, hdrH, 16, 11),
      fontFace: FDISP, color: ink, bold: true, valign: 'middle', wrap: false });
  });
  const y0 = top + hdrH + 0.08, n = rows.length, RG = 0.06;
  const rowH = Math.min(1.25, (BODY_Y + BODY_H - y0 - RG * (n - 1)) / n);
  const cellTexts = rows.flatMap(r => (r.cells || []).map(t => capFirst(toPlain(t))));
  const cellFs = minFitArr(cellTexts.map(t => fitClip(t, colW - 0.26, rowH - 0.1, 14, 9).fs), 11);
  const critFs = minFitArr(rows.map(r => fitClip(toPlain(r.criterion || ''), critW - 0.2, rowH - 0.1, 14, 9).fs), 11);
  rows.forEach((r, ri) => {
    const y = y0 + ri * (rowH + RG);
    slide.addText(capFirst(toPlain(r.criterion || '')), { x: MX, y, w: critW, h: rowH, fontSize: critFs, fontFace: FDISP,
      color: ACC, bold: true, valign: 'middle', wrap: true });
    (r.cells || []).slice(0, ents.length).forEach((t, ci) => {
      const x = MX + critW + GAP + ci * (colW + GAP);
      slide.addShape(pptx.ShapeType.rect, { x, y, w: colW, h: rowH, fill: { color: fgC(isLight), transparency: 96 }, line: { type: 'none' } });
      slide.addShape(pptx.ShapeType.rect, { x, y, w: 0.04, h: rowH, fill: { color: ACC }, line: { type: 'none' } });
      slide.addText(capFirst(toPlain(t)), { x: x + 0.12, y: y + 0.03, w: colW - 0.2, h: rowH - 0.06, fontSize: cellFs,
        fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true });
    });
  });
}

// pyramid: hierarchy, base (widest, content[0]) -> apex (narrowest). Uniform text size.
function buildPyramid(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = (s.bullets || []).map(b => splitLabelBody(capFirst(toPlain(b))));
  const n = items.length; if (!n) return;
  const bandH = (BODY_H - (n - 1) * 0.08) / n;
  const topW = BW * 0.34, botW = BW * 0.94;
  const uFs = minFitArr(items.map(p => fitClip(p.h || p.d, botW - 0.6, bandH - 0.1, 16, 10).fs), 12);
  items.forEach((p, i) => {
    // i=0 base (bottom); larger i => higher, narrower
    const frac = n === 1 ? 1 : i / (n - 1);
    const width = botW - (botW - topW) * frac;
    const y = BODY_Y + BODY_H - (i + 1) * bandH - i * 0.08;
    const x = MX + (BW - width) / 2;
    const cA = accShade(60 + 40 * frac);  // base darker -> apex accent
    slide.addShape(pptx.ShapeType.rect, { x, y, w: width, h: bandH, fill: { color: cA }, line: { type: 'none' } });
    const label = p.h ? `${p.h}${p.d ? ' — ' + p.d : ''}` : p.d;
    slide.addText(label, { x: x + 0.2, y, w: width - 0.4, h: bandH, fontSize: uFs, fontFace: FBODY, color: textOn(cA), bold: !!p.h, align: 'center', valign: 'middle', wrap: true });
  });
}

// funnel: narrowing stages, top widest (content[0]) -> bottom narrowest. Uniform text size.
function buildFunnel(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = (s.bullets || []).map(b => splitLabelBody(capFirst(toPlain(b))));
  const n = items.length; if (!n) return;
  const bandH = (BODY_H - (n - 1) * 0.08) / n;
  const topW = BW * 0.94, botW = BW * 0.34;
  const uFs = minFitArr(items.map(p => fitClip(p.h || p.d, botW + 0.5, bandH - 0.1, 16, 10).fs), 12);
  items.forEach((p, i) => {
    const frac = n === 1 ? 0 : i / (n - 1);
    const width = topW - (topW - botW) * frac;
    const y = BODY_Y + i * (bandH + 0.08);
    const x = MX + (BW - width) / 2;
    const cA = accShade(100 - 40 * frac);  // top accent -> bottom deeper
    slide.addShape(pptx.ShapeType.rect, { x, y, w: width, h: bandH, fill: { color: cA }, line: { type: 'none' } });
    const label = p.h ? `${p.h}${p.d ? ' — ' + p.d : ''}` : p.d;
    slide.addText(label, { x: x + 0.2, y, w: width - 0.4, h: bandH, fontSize: uFs, fontFace: FBODY, color: textOn(cA), bold: !!p.h, align: 'center', valign: 'middle', wrap: true });
  });
}

// cycle: 3-6 stages around a ring (repeating process). Numbered nodes + labels, uniform size.
function buildCycle(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = (s.bullets || []).map(b => capFirst(toPlain(b))).filter(Boolean);
  const n = items.length; if (!n) return;
  const cx = W / 2, cy = BODY_Y + BODY_H / 2 + 0.05;
  const R = Math.min(BODY_H / 2 - 0.35, 1.55);
  // faint guide ring
  slide.addShape(pptx.ShapeType.ellipse, { x: cx - R, y: cy - R, w: 2 * R, h: 2 * R, fill: { type: 'none' }, line: { color: ACC, width: 1.25, transparency: 60 } });
  const NODE = 0.5, labW = 2.0, labH = 0.7;
  const uFs = minFitArr(items.map(t => fitClip(t, labW, labH, 13, 9).fs), 11);
  items.forEach((t, i) => {
    const ang = -Math.PI / 2 + i * (2 * Math.PI / n);   // start at top, clockwise
    const nx = cx + R * Math.cos(ang), ny = cy + R * Math.sin(ang);
    slide.addShape(pptx.ShapeType.ellipse, { x: nx - NODE / 2, y: ny - NODE / 2, w: NODE, h: NODE, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(String(i + 1), { x: nx - NODE / 2, y: ny - NODE / 2, w: NODE, h: NODE, fontSize: 16, fontFace: FDISP, color: textOn(ACC), bold: true, align: 'center', valign: 'middle', wrap: false });
    // label placed radially outward
    const lx = cx + (R + 0.5) * Math.cos(ang), ly = cy + (R + 0.5) * Math.sin(ang);
    const onRight = Math.cos(ang) >= 0;
    slide.addText(t, {
      x: onRight ? lx : lx - labW, y: ly - labH / 2, w: labW, h: labH,
      fontSize: uFs, fontFace: FBODY, color: fgC(isLight), align: onRight ? 'left' : 'right', valign: 'middle', wrap: true,
    });
  });
}

// hub-spoke: central idea (content[0]) + 3-6 branches around, connected by lines. Uniform size.
function buildHubSpoke(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const all = (s.bullets || []).map(b => capFirst(toPlain(b))).filter(Boolean);
  if (all.length < 2) return;
  const center = splitLabelBody(all[0]).h || all[0];
  const spokes = all.slice(1, 7);
  const n = spokes.length;
  const cx = W / 2, cy = BODY_Y + BODY_H / 2 + 0.05;
  // Elliptical placement (wide horizontally, since the slide is 10x5.6) so 2-inch spoke boxes never overlap.
  const Rx = 3.25, Ry = Math.min(BODY_H / 2 - 0.15, 1.5);
  const HUB = 1.6, HUBH = 1.0, SPW = 2.0, SPH = 0.62;
  const uFs = minFitArr(spokes.map(t => fitClip(t, SPW - 0.3, SPH - 0.1, 13, 9).fs), 11);
  // connectors first (under nodes)
  spokes.forEach((t, i) => {
    const ang = -Math.PI / 2 + i * (2 * Math.PI / n);
    connector(slide, cx, cy, cx + Rx * Math.cos(ang), cy + Ry * Math.sin(ang), ACC, 1.25);
  });
  spokes.forEach((t, i) => {
    const ang = -Math.PI / 2 + i * (2 * Math.PI / n);
    const nx = cx + Rx * Math.cos(ang), ny = cy + Ry * Math.sin(ang);
    slide.addShape(pptx.ShapeType.roundRect, { x: nx - SPW / 2, y: ny - SPH / 2, w: SPW, h: SPH, rectRadius: 0.06, fill: { color: isLight ? BGLIT : BGDARK }, line: { type: 'none' } });   // opaque base: the connector must not show through
    slide.addShape(pptx.ShapeType.roundRect, { x: nx - SPW / 2, y: ny - SPH / 2, w: SPW, h: SPH, rectRadius: 0.06, fill: { color: ACC, transparency: isLight ? 88 : 82 }, line: { color: ACC, width: 1 } });
    slide.addText(t, { x: nx - SPW / 2 + 0.12, y: ny - SPH / 2, w: SPW - 0.24, h: SPH, fontSize: uFs, fontFace: FBODY, color: fgC(isLight), align: 'center', valign: 'middle', wrap: true });
  });
  // hub on top (gradient)
  gradRect(slide, cx - HUB / 2, cy - HUBH / 2, HUB, HUBH, gradStops()[0], gradStops()[1], 'v');
  slide.addText(center, { x: cx - HUB / 2 + 0.1, y: cy - HUBH / 2, w: HUB - 0.2, h: HUBH, fontSize: fitFontSize(center, HUB - 0.2, HUBH, 16, 10), fontFace: FDISP, color: textOn(gradMid()), bold: true, align: 'center', valign: 'middle', wrap: true });
}

// checklist: criteria / requirements as an accent-ticked vertical list. Uniform size.
function buildChecklist(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = (s.bullets || []).map(b => splitLabelBody(capFirst(toPlain(b))));
  const n = items.length; if (!n) return;
  const rowH = BODY_H / n, BOX = 0.34, tx = MX + BOX + 0.34, tw = W - tx - MX;
  const uHead = minFitArr(items.filter(p => p.h).map(p => fitClip(p.h, tw, rowH * 0.5, 16, 11).fs), 14);
  const uBody = minFitArr(items.filter(p => p.d && p.h).map(p => fitClip(p.d, tw, rowH * 0.5, 13, 9).fs), 11);
  const uPlain = minFitArr(items.filter(p => !p.h).map(p => fitClip(p.d, tw, rowH - 0.14, 15, 10).fs), 13);
  items.forEach((p, i) => {
    const y = BODY_Y + i * rowH;
    // accent check box
    slide.addShape(pptx.ShapeType.roundRect, { x: MX, y: y + rowH / 2 - BOX / 2, w: BOX, h: BOX, rectRadius: 0.05, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText('✓', { x: MX, y: y + rowH / 2 - BOX / 2, w: BOX, h: BOX, fontSize: 15, fontFace: FBODY, color: textOn(ACC), bold: true, align: 'center', valign: 'middle', wrap: false });
    if (p.h) {
      slide.addText(p.h, { x: tx, y: y + 0.06, w: tw, h: rowH * 0.5, fontSize: uHead, fontFace: FDISP, color: fgC(isLight), bold: true, valign: 'bottom', wrap: true });
      if (p.d) slide.addText(p.d, { x: tx, y: y + rowH * 0.5 + 0.02, w: tw, h: rowH * 0.5 - 0.06, fontSize: uBody, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true });
    } else {
      slide.addText(p.d, { x: tx, y, w: tw, h: rowH, fontSize: uPlain, fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true });
    }
    if (i < n - 1) slide.addShape(pptx.ShapeType.rect, { x: tx, y: y + rowH - 0.01, w: tw, h: 0.01, fill: { color: fgC(isLight), transparency: 88 }, line: { type: 'none' } });
  });
}

// highlight-list: a dense annotated list (6-10 items) -- inline bold accent label + detail on one line,
// with faint ALTERNATING row tints so a long list reads with rhythm instead of one flat block of text.
function buildHighlightList(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = (s.bullets || []).map(b => splitLabelBody(capFirst(toPlain(b)))).filter(p => p.h || p.d);
  const n = items.length; if (!n) return;
  const rowH = BODY_H / n;
  const tx = MX + 0.24, tw = BW - 0.48;
  const uFs = minFitArr(items.map(p => fitClip((p.h ? p.h + ': ' : '') + p.d, tw, rowH - 0.1, 16, 10).fs), 13);
  items.forEach((p, i) => {
    const y = BODY_Y + i * rowH;
    if (i % 2 === 0) slide.addShape(pptx.ShapeType.rect, { x: MX, y, w: BW, h: rowH, fill: { color: ACC, transparency: isLight ? 95 : 90 }, line: { type: 'none' } });
    if (p.h) {
      const runs = [{ text: p.h + ':  ', options: { color: titleColor(isLight), bold: true } }, { text: p.d, options: { color: fgC(isLight) } }];
      slide.addText(runs, { x: tx, y, w: tw, h: rowH, fontSize: uFs, fontFace: FBODY, valign: 'middle', wrap: true });
    } else {
      slide.addText(p.d, { x: tx, y, w: tw, h: rowH, fontSize: uFs, fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true });
    }
  });
}

// takeaway-panel: one standout LEAD statement (pull-quote scale) + smaller supporting bullets below it,
// all inside one panel -- for "one main point + several supporting points" where the point must dominate
// but the supporting list still needs to stay fully visible (unlike big_statement, which is near-bare).
function buildTakeawayPanel(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const all = (s.bullets || []).map(b => capFirst(toPlain(b))).filter(Boolean);
  if (!all.length) return;
  const lead = all[0], support = all.slice(1, 6);
  const px = MX, py = BODY_Y, pw = BW, ph = BODY_H;
  slide.addShape(pptx.ShapeType.roundRect, { x: px, y: py, w: pw, h: ph, rectRadius: 0.08, fill: { color: ACC, transparency: isLight ? 94 : 89 }, line: { type: 'none' } });
  slide.addShape(pptx.ShapeType.rect, { x: px, y: py, w: 0.11, h: ph, fill: { color: ACC }, line: { type: 'none' } });
  const ix = px + 0.42, iw = pw - 0.75;
  const leadH = Math.min(ph * 0.42, 1.6);
  slide.addText(lead, { x: ix, y: py + 0.26, w: iw, h: leadH, fontSize: fitFontSize(lead, iw, leadH, 30, 18), fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'top', wrap: true });
  if (support.length) {
    slide.addShape(pptx.ShapeType.rect, { x: ix, y: py + leadH + 0.36, w: iw, h: 0.016, fill: { color: ACC, transparency: 55 }, line: { type: 'none' } });
    addBullets(slide, support, ix, py + leadH + 0.55, iw, ph - leadH - 0.8, isLight, 16);
  }
}

// concept-breakdown: 2-4 concepts, each = term (accent, bold) + short definition, in accent-tint panels.
function buildConceptBreakdown(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  // accept structured s.concepts=[{term,def}] or fall back to "Term: def" bullets
  let items = (s.concepts || []).map(c => ({ h: capFirst(toPlain(c.term || '')), d: capFirst(toPlain(c.def || '')),
                                              items: c.items || [] }));
  if (!items.length) items = (s.bullets || []).map(b => splitLabelBody(capFirst(toPlain(b)))).filter(p => p.h && p.d);
  items = items.filter(p => p.h || p.d);   // an untitled concept still shows its content
  const n = items.length; if (!n) return;
  const cols = n <= 2 ? 1 : n <= 4 ? 2 : 3, rows = Math.ceil(n / cols);
  const GAP = 0.2, cw = (BW - GAP * (cols - 1)) / cols, ch = (BODY_H - GAP * (rows - 1)) / rows;
  const pad = 0.26, tW = cw - pad * 2;
  const uTerm = minFitArr(items.map(p => fitClip(p.h, tW, 0.5, 22, 14).fs), 18);
  const uDef = minFitArr(items.map(p => cardBodyFs(p, p.d, tW, ch - 1.05, 15, 10)), 12);
  items.forEach((p, i) => {
    const col = i % cols, row = Math.floor(i / cols);
    const x = MX + col * (cw + GAP), y = BODY_Y + row * (ch + GAP);
    slide.addShape(pptx.ShapeType.roundRect, { x, y, w: cw, h: ch, rectRadius: 0.08, fill: { color: ACC, transparency: isLight ? 91 : 86 }, line: { type: 'none' } });
    slide.addShape(pptx.ShapeType.rect, { x, y, w: 0.1, h: ch, fill: { color: ACC }, line: { type: 'none' } });
    if (p.h) slide.addText(p.h, { x: x + pad, y: y + 0.22, w: tW, h: 0.55, fontSize: uTerm, fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'top', wrap: true });
    addCardBody(slide, p, p.d, x + pad, y + 0.85, tW, ch - 1.05, uDef, fgC(isLight));
  });
}

// Shared table renderer (used by buildTable + table_highlight). Returns table height.
function _drawTable(slide, headers, rows, x, y, w, isLight, headFs, cellFs, maxH = BODY_H) {
  const nCols = headers.length; if (!nCols) return 0;
  const fit = tableFit(headers, rows, w, maxH, headFs || 15, 7);
  const borderC = dimC(isLight);
  const tblData = tableData(headers, rows, isLight, fit);
  slide.addTable(tblData, { x, y, w, rowH: fit.rowH, colW: fit.colW, border: { pt: 0.5, color: borderC } });
  return fit.height;
}

function _tableFrom(s) {
  if (s.parsed_table) return { headers: s.parsed_table.headers || [], rows: s.parsed_table.rows || [] };
  if (s.table) return parseMdTable(s.table.table_markdown || '');
  return { headers: [], rows: [] };
}

function _formulaEqs(s) {
  return String(s.formula_text || s.formula_latex || '').split(/\n\s*\n/).map(e => toMathText(e.trim()))
    .filter(e => /[a-zA-Z0-9]/.test(e));
}

// Largest size (<= maxFs) at which the equation's lines fit w x h; a line wider than w wraps.
function equationFs(lines, w, h, maxFs) {
  for (let fs = maxFs; fs > 10; fs--) {
    const rows = lines.reduce((n, l) => n + wrapLines(l, w - 0.2, fs, 'math'), 0);
    if (rows * fs * 1.35 / 72 + 0.12 <= h) return fs;
  }
  return 10;
}

// The slide's equation as Unicode text (Cambria Math), in the theme's text colour, centred in its box.
function drawEquation(slide, s, isLight, x, y, w, h, maxFs) {
  const eqs = _formulaEqs(s);
  const text = eqs.join('\n\n') || toPlain(s.title || '');
  const lines = text.split('\n');
  slide.addText(text, { x, y, w, h, fontSize: equationFs(lines, w, h, maxFs), fontFace: 'Cambria Math',
    color: fgC(isLight), align: 'center', valign: 'middle', wrap: true });
}

// Height the equation needs at a comfortable size (the box never takes more).
function equationH(s, w, fs) {
  const lines = (_formulaEqs(s).join('\n\n') || ' ').split('\n');
  return lines.reduce((n, l) => n + wrapLines(l, w - 0.2, fs, 'math'), 0) * fs * 1.35 / 72 + 0.3;
}

// formula-hero: one big formula centred in an accent-tint panel
function buildFormulaHero(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  bgMotif(slide);  // just a formula panel, lots of surrounding whitespace -> add corner motif
  const boxH = BODY_H * 0.62, boxY = BODY_Y + (BODY_H - boxH) / 2;
  slide.addShape(pptx.ShapeType.roundRect, { x: MX, y: boxY, w: BW, h: boxH, rectRadius: 0.1, fill: { color: ACC, transparency: isLight ? 92 : 88 }, line: { type: 'none' } });
  slide.addShape(pptx.ShapeType.rect, { x: MX, y: boxY, w: 0.1, h: boxH, fill: { color: ACC }, line: { type: 'none' } });
  drawEquation(slide, s, isLight, MX + 0.35, boxY, BW - 0.7, boxH, 32);
}

// formula-annotated: formula on top + a "where: sym = meaning" list
function buildFormulaAnnotated(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = (s.bullets || []).map(b => splitLabelBody(capFirst(toPlain(b)))).filter(p => p.h || p.d);
  // the term column is as wide as its longest term needs (terms wrap beyond 40% of the width)
  const termW = Math.min(BW * 0.4, Math.max(0.5, ...items.map(p => p.h ? textW(p.h, 15, 'body', true) + 0.25 : 0)));
  const bodyW = (p) => p.h ? BW - 0.5 - termW : BW - 0.4;
  const rowFits = (bh) => {
    const rh = ((BODY_Y + BODY_H) - (BODY_Y + bh + 0.22) - 0.34) / Math.max(1, items.length);
    return items.every(p => fitClip(p.d, bodyW(p), rh, 14, 10).fs >= FLOOR_FS);
  };
  const boxY = BODY_Y;
  const boxH = rowFits(BODY_H * 0.4) ? BODY_H * 0.4 : Math.min(BODY_H * 0.4, formulaMinH(formulaLines(s)));
  _drawFormulaBox(slide, isLight, boxY, boxH);
  drawEquation(slide, s, isLight, MX + 0.2, boxY, BW - 0.4, boxH, 24);
  const listY = boxY + boxH + 0.22, listH = (BODY_Y + BODY_H) - listY;
  slide.addText('WHERE', { x: MX, y: listY, w: 2, h: 0.26, wrap: false, fontSize: 11, fontFace: FMONO, color: ACC, charSpacing: 3 });
  const n = items.length || 1, rowH = (listH - 0.34) / n;
  const uFs = minFitArr(items.map(p => fitClip(p.d, bodyW(p), rowH, 14, 10).fs), 12);
  const termFs = minFitArr(items.filter(p => p.h).map(p => fitClip(p.h, termW, rowH, uFs + 1, 10).fs), uFs + 1);
  items.forEach((p, i) => {
    const y = listY + 0.34 + i * rowH;
    if (p.h) {
      slide.addText(p.h, { x: MX + 0.2, y, w: termW, h: rowH, fontSize: termFs, fontFace: 'Cambria Math', color: titleColor(isLight), bold: true, valign: 'top', wrap: true });
      slide.addText('= ' + p.d, { x: MX + 0.3 + termW, y, w: bodyW(p), h: rowH, fontSize: uFs, fontFace: FBODY, color: fgC(isLight), valign: 'top', wrap: true });
    } else {
      slide.addText(p.d, { x: MX + 0.2, y, w: bodyW(p), h: rowH, fontSize: uFs, fontFace: FBODY, color: fgC(isLight), valign: 'top', wrap: true });
    }
  });
}

// table-highlight: table on the left + an accent callout (key takeaway) on the right
function buildTableHighlight(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const { headers, rows } = _tableFrom(s);
  if (!headers.length) { addBullets(slide, s.bullets || [], MX, BODY_Y, BW, BODY_H, isLight, 22); return; }
  const tblW = BW * 0.62;
  const th = _drawTable(slide, headers, rows, MX, BODY_Y, tblW, isLight, 14, 12, BODY_H);
  const cx = MX + tblW + 0.35, cw = W - cx - MX;
  const call = (s.bullets && s.bullets[0]) ? capFirst(toPlain(s.bullets[0])) : (s.caption ? capFirst(toPlain(s.caption)) : '');
  if (call) {
    slide.addShape(pptx.ShapeType.roundRect, { x: cx, y: BODY_Y + 0.2, w: cw, h: BODY_H - 0.4, rectRadius: 0.08, fill: { color: ACC, transparency: isLight ? 90 : 85 }, line: { type: 'none' } });
    slide.addShape(pptx.ShapeType.rect, { x: cx, y: BODY_Y + 0.2, w: 0.1, h: BODY_H - 0.4, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(call, { x: cx + 0.28, y: BODY_Y + 0.45, w: cw - 0.55, h: BODY_H - 0.9, fontSize: fitFontSize(call, cw - 0.55, BODY_H - 0.9, 20, 12), fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'middle', wrap: true });
  }
}

// image-comparison: 2 images side by side with A/B badges + captions
function buildImageCompare(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const imgs = (s.images || []).filter(p => p && fs.existsSync(p)).slice(0, 2);
  const caps = s.captions || [];
  if (imgs.length < 2) { if (imgs[0]) addImageCap(slide, imgs[0], MX, BODY_Y, BW, BODY_H, caps[0], isLight); return; }
  const GAP = 0.35, iW = (BW - GAP) / 2, BADGE = 0.5;
  ['A', 'B'].forEach((lab, i) => {
    const x = MX + i * (iW + GAP);
    slide.addShape(pptx.ShapeType.roundRect, { x, y: BODY_Y, w: BADGE, h: 0.42, rectRadius: 0.06, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(lab, { x, y: BODY_Y, w: BADGE, h: 0.42, wrap: false, fontSize: 18, fontFace: FDISP, color: textOn(ACC), bold: true, align: 'center', valign: 'middle' });
    addImageCap(slide, imgs[i], x, BODY_Y + 0.52, iW, BODY_H - 0.52, caps[i], isLight);
  });
}

// Parse "Label: 55%" / "42%: Label" bullets -> [{value, label, num}] (num = numeric part or null).
function parseStatData(bullets) {
  const out = [];
  for (const b of (bullets || [])) {
    const t = toPlain(b).trim(); const i = t.indexOf(': ');
    let value = '', label = t;
    if (i > 0) {
      const a = t.slice(0, i).trim(), c = t.slice(i + 2).trim();
      if (/[0-9]/.test(a) && a.length <= 14) { value = a; label = c; }
      else if (/[0-9]/.test(c) && c.length <= 14) { value = c; label = a; }
    }
    const num = parseFloat((value || '').replace(/[^0-9.\-]/g, ''));
    out.push({ value: value, label: capFirst(label), num: isNaN(num) ? null : num });
  }
  return out;
}

function _chartColors() { return [ACC, C1, C2, C3, C4]; }

// big-stat: 1-3 oversized numbers (accent) + labels — a dominant-metric slide.
function buildBigStat(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = parseStatData(s.bullets).filter(x => x.value).slice(0, 3);
  if (!items.length) { addBullets(slide, s.bullets || [], MX, BODY_Y, BW, BODY_H, isLight); return; }
  const n = items.length, GAP = 0.3, cw = (BW - GAP * (n - 1)) / n;
  items.forEach((it, i) => {
    const x = MX + i * (cw + GAP), cy = BODY_Y + BODY_H / 2;
    slide.addText(it.value, { x, y: cy - 1.0, w: cw, h: 1.1, wrap: false, fontSize: fitFontSize(it.value, cw, 1.1, 72, 34), fontFace: FDISP, color: titleColor(isLight), bold: true, align: 'center', valign: 'bottom' });
    slide.addShape(pptx.ShapeType.rect, { x: x + cw / 2 - 0.3, y: cy + 0.14, w: 0.6, h: 0.045, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(it.label, { x, y: cy + 0.3, w: cw, h: 1.0, fontSize: fitFontSize(it.label, cw, 1.0, 18, 12), fontFace: FBODY, color: fgC(isLight), align: 'center', valign: 'top', wrap: true });
  });
}

// chart-stat: a pie/bar chart (left) + the dominant stat & a callout (right).
function buildChartStat(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = parseStatData(s.bullets).filter(x => x.num != null);
  if (items.length < 2) { buildBigStat(s); return; }
  const sum = items.reduce((a, x) => a + x.num, 0);
  const type = Math.abs(sum - 100) < 8 ? pptx.ChartType.doughnut : pptx.ChartType.bar;
  const chartW = BW * 0.5, chartH = BODY_H - 0.1;
  slide.addChart(type, [{ name: toPlain(s.title || 'Data'), labels: items.map(x => x.label), values: items.map(x => x.num) }], {
    x: MX, y: BODY_Y, w: chartW, h: chartH,
    chartColors: _chartColors(), showLegend: true, legendPos: 'b', legendFontSize: 10,
    showValue: true, dataLabelColor: type === pptx.ChartType.bar ? fgC(isLight) : INK,
    dataLabelFontSize: 10, showTitle: false, holeSize: 55,
    barDir: 'bar', valAxisHidden: true, catAxisHidden: false,
  });
  // right: hero stat + callout
  const rx = MX + chartW + 0.5, rw = W - rx - MX;
  const hero = items.reduce((m, x) => x.num > m.num ? x : m, items[0]);
  slide.addText(hero.value, { x: rx, y: BODY_Y + 0.1, w: rw, h: 1.1, wrap: false, fontSize: fitFontSize(hero.value, rw, 1.1, 60, 32), fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'bottom' });
  slide.addText(hero.label, { x: rx, y: BODY_Y + 1.25, w: rw, h: 0.7, fontSize: fitFontSize(hero.label, rw, 0.7, 17, 12), fontFace: FBODY, color: fgC(isLight), valign: 'top', wrap: true });
  const call = (s.callout || '').trim();
  if (call) {
    const cy = BODY_Y + 2.2, ch = (BODY_Y + BODY_H) - cy;
    slide.addShape(pptx.ShapeType.roundRect, { x: rx, y: cy, w: rw, h: ch, rectRadius: 0.07, fill: { color: ACC, transparency: isLight ? 90 : 85 }, line: { type: 'none' } });
    slide.addShape(pptx.ShapeType.rect, { x: rx, y: cy, w: 0.09, h: ch, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(capFirst(toPlain(call)), { x: rx + 0.24, y: cy + 0.12, w: rw - 0.4, h: ch - 0.24, fontSize: fitFontSize(call, rw - 0.4, ch - 0.24, 15, 11), fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true });
  }
}

// kpi-cards: 2-4 KPI cards (big number + label) in a row.
function buildKpiCards(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = parseStatData(s.bullets).filter(x => x.value).slice(0, 4);
  if (!items.length) { addBullets(slide, s.bullets || [], MX, BODY_Y, BW, BODY_H, isLight); return; }
  const n = items.length, GAP = 0.22, cw = (BW - GAP * (n - 1)) / n;
  const ch = Math.min(BODY_H, 2.6), y = BODY_Y + (BODY_H - ch) / 2;
  items.forEach((it, i) => {
    const x = MX + i * (cw + GAP);
    slide.addShape(pptx.ShapeType.roundRect, { x, y, w: cw, h: ch, rectRadius: 0.08, fill: { color: ACC, transparency: isLight ? 91 : 86 }, line: { type: 'none' } });
    slide.addShape(pptx.ShapeType.rect, { x, y, w: cw, h: 0.08, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(it.value, { x: x + 0.18, y: y + 0.35, w: cw - 0.36, h: 1.1, wrap: false, fontSize: fitFontSize(it.value, cw - 0.36, 1.1, 48, 26), fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'middle' });
    slide.addText(it.label, { x: x + 0.18, y: y + 1.5, w: cw - 0.36, h: ch - 1.65, fontSize: fitFontSize(it.label, cw - 0.36, ch - 1.65, 15, 11), fontFace: FBODY, color: fgC(isLight), valign: 'top', wrap: true });
  });
}

// Dark scrim (gradient of semi-transparent black) so overlaid text stays readable on a photo.
// dir: 'b'(dark bottom, default) 'l'(dark left) 't' 'r'. darkT = transparency at the dark end (lower=darker).
function scrim(slide, x, y, w, h, dir, darkT) {
  dir = dir || 'b'; darkT = darkT == null ? 30 : darkT;
  const horiz = (dir === 'l' || dir === 'r'), steps = 16;
  const span = horiz ? w : h, step = span / steps, band = step * 1.9;
  const atEndDark = (dir === 'b' || dir === 'r');
  for (let i = 0; i < steps; i++) {
    const frac = i / (steps - 1), darkness = atEndDark ? frac : (1 - frac);
    const tr = Math.round(100 - (100 - darkT) * darkness);
    const off = step * i;
    slide.addShape(pptx.ShapeType.rect, {
      x: horiz ? x + off : x, y: horiz ? y : y + off,
      w: horiz ? Math.min(band, span - off) : w, h: horiz ? h : Math.min(band, span - off),
      fill: { color: '000000', transparency: tr }, line: { type: 'none' },
    });
  }
}

// A styled empty image FRAME (for the user to insert their own picture when no image is available and
// AI-gen is off). Muted panel + dashed-look border + a picture glyph + a hint label.
function imagePlaceholder(slide, x, y, w, h, isLight) {
  slide.addShape(pptx.ShapeType.rect, { x, y, w, h, fill: { color: tintC(isLight, 6) }, line: { color: ACC, width: 1.25, dashType: 'dash', transparency: 40 } });
  slide.addText('🖼', { x: x + w / 2 - 0.6, y: y + h / 2 - 0.7, w: 1.2, h: 0.8, fontSize: 40, color: ACC, align: 'center', valign: 'middle', transparency: 30 });
  slide.addText('Add image', { x, y: y + h / 2 + 0.15, w, h: 0.4, fontSize: 13, fontFace: FMONO, color: dimC(isLight), align: 'center', valign: 'middle', charSpacing: 2 });
}

// Full-bleed background photo; if none, draw a full-slide image placeholder frame instead of a photo.
// Returns { hasImg, ink } so the caller picks white text on a photo, or theme ink on the placeholder.
function _fullBleedImage(slide, img, isLight) {
  if (img && fs.existsSync(img)) { slide.addImage({ path: img, x: 0, y: 0, w: W, h: H, sizing: { type: 'cover', w: W, h: H } }); return { hasImg: true, ink: 'FFFFFF' }; }
  imagePlaceholder(slide, 0, 0, W, H, isLight);
  return { hasImg: false, ink: fgC(isLight) };
}

function _imageChrome(slide, pageNum) {
  // white chapter header + page number over the photo (top scrim keeps them readable)
  const lt = CURRENT_SECTION || manifest.lecture_title || '';
  if (lt) slide.addText(lt.length > 58 ? lt.slice(0, 58) : lt, { x: MX, y: CTOP_Y, w: BW - 1.2, h: CTOP_H, fontSize: 9, color: 'FFFFFF', fontFace: FMONO, valign: 'middle', charSpacing: 1.5, transparency: 15 });
  if (pageNum) slide.addText(String(pageNum), { x: W - MX - 0.68, y: CTOP_Y, w: 0.68, h: CTOP_H, fontSize: 9, color: 'FFFFFF', fontFace: FMONO, valign: 'middle', align: 'right', transparency: 15 });
}

// image-stat-hero: full-bleed photo (or placeholder) + scrim + a big stat/headline overlay + credit.
function buildImageStatHero(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  const img = s.bg_image_path || (s.images || [])[0];
  const { hasImg, ink } = _fullBleedImage(slide, img, isLight);
  if (hasImg) { scrim(slide, 0, 0, W, H, 'b', 22); _imageChrome(slide, s.page_num); }
  else addChrome(slide, isLight, s.page_num, true);
  const stats = parseStatData(s.bullets);
  const hero = stats.find(x => x.value);
  const stat = (hero && hero.value) || toPlain(s.title || '');
  slide.addText(stat, { x: MX, y: H - 2.5, w: BW, h: 1.1, wrap: false, fontSize: fitFontSize(stat, BW, 1.1, 56, 30), fontFace: FDISP, color: hasImg ? 'FFFFFF' : titleColor(isLight), bold: true, valign: 'bottom' });
  const sup = (hero && hero.label) || (s.bullets && s.bullets[0] ? capFirst(toPlain(s.bullets[0])) : '');
  if (sup) slide.addText(sup, { x: MX, y: H - 1.35, w: BW * 0.7, h: 0.8, fontSize: fitFontSize(sup, BW * 0.7, 0.8, 18, 12), fontFace: FBODY, color: ink, valign: 'top', wrap: true });
  if (s.credit && hasImg) slide.addText(toPlain(s.credit), { x: W - 5.0, y: H - 0.5, w: 4.5, h: 0.3, fontSize: 9, fontFace: FBODY, color: 'FFFFFF', align: 'right', valign: 'middle', transparency: 25 });
}

// image-quote-hero: full-bleed (usually dark) photo (or placeholder) + big accent quote + supporting + credit.
function buildImageQuoteHero(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  const img = s.bg_image_path || (s.images || [])[0];
  const { hasImg, ink } = _fullBleedImage(slide, img, isLight);
  if (hasImg) { scrim(slide, 0, 0, W, H, 'l', 22); scrim(slide, 0, 0, W, H, 'b', 40); _imageChrome(slide, s.page_num); }
  else addChrome(slide, isLight, s.page_num, true);
  const quote = toPlain(s.quote || s.title || '');
  slide.addText(quote, { x: MX, y: 1.9, w: BW * 0.78, h: 1.6, fontSize: fitFontSize(quote, BW * 0.78, 1.6, 46, 26), fontFace: FDISP, color: ACC, bold: true, valign: 'middle', wrap: true });
  const sup = (s.bullets && s.bullets[0]) ? capFirst(toPlain(s.bullets[0])) : (s.lead ? capFirst(toPlain(s.lead)) : '');
  if (sup) slide.addText(sup, { x: MX, y: 3.7, w: BW * 0.7, h: 0.9, fontSize: fitFontSize(sup, BW * 0.7, 0.9, 20, 13), fontFace: FBODY, color: ink, valign: 'top', wrap: true });
  if (s.credit && hasImg) slide.addText(toPlain(s.credit), { x: W - 5.0, y: H - 0.5, w: 4.5, h: 0.3, fontSize: 9, fontFace: FBODY, color: 'FFFFFF', align: 'right', valign: 'middle', transparency: 25 });
}

// ── A2-A5 chrome builders (TOC sidebar/timeline, hero cover, contact/gradient endings, progress divider) ──

// toc-sidebar: gradient panel left ("Contents" + motif) + numbered list on the right. Up to TOC_ROWS entries take one
// column; more take 2-3 columns of up to TOC_ROWS_MULTI rows beside a narrower panel, so a long contents stays on one page
const TOC_ROWS = 7, TOC_ROWS_MULTI = 10, TOC_COLS = 3, TOC_COL_GAP = 0.3;
function buildTocSidebar(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num, true);
  const items = (s.toc_items || []).slice(0, TOC_ROWS_MULTI * TOC_COLS);
  const cols = items.length <= TOC_ROWS ? 1 : Math.min(TOC_COLS, Math.ceil(items.length / TOC_ROWS_MULTI));
  const panelW = cols > 1 ? 2.4 : 3.15, ink = textOn(gradMid());
  gradRect(slide, 0, 0, panelW, H, gradStops()[0], gradStops()[1], 'v');
  panelMotif(slide, 0, 0, panelW, H, ink);
  slide.addText('Contents', { x: 0.5, y: 1.4, w: panelW - 0.8, h: 1.2,
    fontSize: fitFontSize('Contents', panelW - 0.8, 0.6, 34, 16),           // one line: a word never breaks
    fontFace: FDISP, color: ink, bold: true, valign: 'top', wrap: false });
  slide.addShape(pptx.ShapeType.rect, { x: 0.55, y: 2.5, w: 0.7, h: 0.05, fill: { color: ink, transparency: 20 }, line: { type: 'none' } });
  const lx = panelW + (cols > 1 ? 0.4 : 0.55), lw = W - lx - MX;
  const rows = Math.max(1, Math.ceil(items.length / cols));
  const cw = (lw - TOC_COL_GAP * (cols - 1)) / cols;
  const rowH = (H - 1.0) / rows;
  const NUM_W = cols > 1 ? 0.45 : 0.66, PAGE_W = cols > 2 ? 0 : 0.5;   // three columns: no page column
  const textW = cw - NUM_W - PAGE_W - (PAGE_W ? 0.1 : 0.05);
  const uFs = minFitArr(items.map(it => fitClip(toPlain(it.t || ''), textW, rowH - 0.14, cols > 1 ? 15 : 19, 10).fs), 15);
  const top = 0.5 + ((H - 1.0) - rows * rowH) / 2;
  items.forEach((it, i) => {
    const c = Math.floor(i / rows), r = i % rows;
    const x = lx + c * (cw + TOC_COL_GAP), y = top + r * rowH;
    slide.addText(it.n || String(i + 1).padStart(2, '0'), { x, y, w: NUM_W, h: rowH, wrap: false, fontSize: Math.min(cols > 1 ? 18 : 24, uFs + 6), fontFace: FDISP, color: ACC, bold: true, valign: 'middle' });
    slide.addText(toPlain(it.t || ''), { x: x + NUM_W, y, w: textW, h: rowH, fontSize: uFs, fontFace: FBODY, color: fgC(isLight), bold: true, valign: 'middle', wrap: true });
    if (it.p && PAGE_W) slide.addText(String(it.p), { x: x + cw - PAGE_W, y, w: PAGE_W, h: rowH, fontSize: cols > 1 ? 10 : 11, fontFace: FMONO, color: dimC(isLight), align: 'right', valign: 'middle', wrap: false });
    if (r < rows - 1 && i < items.length - 1) slide.addShape(pptx.ShapeType.rect, { x: x + NUM_W, y: y + rowH - 0.01, w: cw - NUM_W, h: 0.012, fill: { color: fgC(isLight), transparency: 85 }, line: { type: 'none' } });
  });
}

// toc-timeline: vertical spine with dots + numbered titles
function buildTocTimeline(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title || 'Contents', isLight);
  const items = (s.toc_items || []).slice(0, 8);
  const n = items.length; if (!n) return;
  const spineX = MX + 0.9, topY = BODY_Y + 0.1, availH = BODY_H - 0.2, rowH = availH / n, DOT = 0.34;
  slide.addShape(pptx.ShapeType.rect, { x: spineX - 0.014, y: topY + rowH * 0.5, w: 0.028, h: rowH * (n - 1), fill: { color: ACC, transparency: 45 }, line: { type: 'none' } });
  const tx = spineX + 0.55, tw = W - tx - MX;
  const uFs = minFitArr(items.map(it => fitClip(toPlain(it.t || ''), tw, rowH - 0.14, 20, 12).fs), 16);
  items.forEach((it, i) => {
    const cy = topY + rowH * i + rowH * 0.5;
    slide.addShape(pptx.ShapeType.ellipse, { x: spineX - DOT / 2, y: cy - DOT / 2, w: DOT, h: DOT, fill: { color: ACC }, line: { color: isLight ? BGLIT : BGDARK, pt: 2 } });
    slide.addText((it.n || String(i + 1).padStart(2, '0')).replace(/^0/, '') || String(i + 1), { x: spineX - DOT / 2, y: cy - DOT / 2, w: DOT, h: DOT, wrap: false, fontSize: 12, fontFace: FMONO, color: textOn(ACC), bold: true, align: 'center', valign: 'middle' });
    slide.addText(toPlain(it.t || ''), { x: tx, y: cy - rowH / 2, w: tw, h: rowH, fontSize: uFs, fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true });
  });
}

// cover-hero: full-bleed gradient with an oversized centred title (a bold statement opening)
function buildCoverHero(s) {
  const slide = pptx.addSlide();
  slide.background = { color: gradStops()[1] };
  gradRect(slide, 0, 0, W, H, gradStops()[0], gradStops()[1], 'v', 26);
  const ink = textOn(gradMid());
  panelMotif(slide, 0, 0, W, H, ink);
  const title = toPlain(s.title || manifest.lecture_title || '');
  const inst = s.institution || '';
  slide.addText((inst || 'LECTURE').toUpperCase(), { x: MX, y: 1.35, w: BW, h: 0.32, wrap: false, fontSize: 13, fontFace: FMONO, color: ink, align: 'center', charSpacing: 4 });
  slide.addText(title, { x: MX, y: 1.9, w: BW, h: 1.9, fontSize: fitFontSize(title, BW, 1.9, 54, 28), fontFace: FDISP, color: ink, bold: true, align: 'center', valign: 'middle', wrap: true });
  slide.addShape(pptx.ShapeType.rect, { x: W / 2 - 0.65, y: 3.95, w: 1.3, h: 0.055, fill: { color: ink, transparency: 15 }, line: { type: 'none' } });
  const foot = [ s.author || manifest.speaker || '', s.date || manifest.date || '' ].filter(Boolean).join('    ·    ');
  if (foot) slide.addText(foot, { x: MX, y: 4.25, w: BW, h: 0.4, fontSize: 13, fontFace: FBODY, color: ink, align: 'center', valign: 'middle' });
}

// ending: contact / next-steps — left gradient stripe + Thank you + contact lines
function buildEndContact(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  const stripeW = 0.55;
  gradRect(slide, 0, 0, stripeW, H, gradStops()[0], gradStops()[1], 'v');
  const title = toPlain(s.title || 'Thank you');
  slide.addText(title, { x: MX + 0.4, y: 1.7, w: BW - 0.4, h: 1.2, fontSize: fitFontSize(title, BW - 0.4, 1.2, 50, 28), fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'top', wrap: true });
  slide.addShape(pptx.ShapeType.rect, { x: MX + 0.42, y: 2.95, w: 0.95, h: 0.05, fill: { color: ACC }, line: { type: 'none' } });
  const rows = [ s.author || manifest.speaker || '', s.institution || '', s.acknowledgment || '' ].filter(Boolean);
  let y = 3.25;
  rows.forEach((t, i) => {
    slide.addText(capFirst(toPlain(t)), { x: MX + 0.42, y, w: BW - 0.6, h: i === rows.length - 1 ? 0.7 : 0.36, fontSize: i === 0 ? 15 : 12, fontFace: FBODY, color: i === 0 ? fgC(isLight) : dimC(isLight), valign: 'top', wrap: true });
    y += (i === rows.length - 1 ? 0.7 : 0.42);
  });
}

// ending: full-bleed gradient "Thank you" (a striking closing)
function buildEndGradient(s) {
  const slide = pptx.addSlide();
  slide.background = { color: gradStops()[1] };
  gradRect(slide, 0, 0, W, H, gradStops()[0], gradStops()[1], 'v', 26);
  const ink = textOn(gradMid());
  panelMotif(slide, 0, 0, W, H, ink);
  const title = toPlain(s.title || 'Thank you');
  slide.addShape(pptx.ShapeType.rect, { x: W / 2 - 0.6, y: 2.0, w: 1.2, h: 0.06, fill: { color: ink, transparency: 15 }, line: { type: 'none' } });
  slide.addText(title, { x: MX, y: 2.25, w: BW, h: 1.4, fontSize: fitFontSize(title, BW, 1.4, 60, 32), fontFace: FDISP, color: ink, bold: true, align: 'center', valign: 'middle', wrap: true });
  const meta = [ s.author || manifest.speaker || '', s.institution || '' ].filter(Boolean).join('    ·    ');
  if (meta) slide.addText(meta, { x: MX, y: 3.8, w: BW, h: 0.4, fontSize: 13, fontFace: FBODY, color: ink, align: 'center', valign: 'middle' });
}

// image-above
const MIN_IMG_H = 1.1, IMG_GAP = 0.16;
// The figure comes first: the text takes the least height at which it still reads (>= 12pt) and the figure takes all
// the rest.
function stackPlan(s, bullets, f) {
  const most = f.bh - MIN_IMG_H - IMG_GAP;
  let last = null;
  for (let th = Math.min(most, 0.6); ; th = Math.min(most, th + 0.1)) {
    const p = planList(s, bullets, BW, th, 20);
    last = { ...p, th };
    if (p.ok || th >= most) break;
  }
  return { ...last, th: Math.min(last.th, last.textH / 0.98 + 0.06) };
}

function buildImgAbove(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  const img = (s.images || [])[0];
  const bullets = s.bullets || [];
  const hasImg = img && fs.existsSync(img);
  const hasBulls = bullets.length > 0;
  const { F, plan } = hasImg && hasBulls ? roomFor(s.title, BW, (f) => stackPlan(s, bullets, f))
                                         : { F: frame(false), plan: null };
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  if (hasImg && hasBulls) {
    const imgH = F.bh - plan.th - IMG_GAP;
    addImageCap(slide, img, MX, F.by, BW, imgH, s.caption, isLight);
    drawList(slide, s, MX, F.by + imgH + IMG_GAP, plan.th, isLight, plan);
  } else if (hasImg) {
    addImageCap(slide, img, MX, F.by, BW, F.bh, s.caption, isLight);
  } else if (hasBulls) {
    addList(slide, s, bullets, MX, F.by, BW, F.bh, isLight);
  }
}

// image-below
function buildImgBelow(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  const img = (s.images || [])[0];
  const bullets = s.bullets || [];
  const hasImg = img && fs.existsSync(img);
  const hasBulls = bullets.length > 0;
  const { F, plan } = hasImg && hasBulls ? roomFor(s.title, BW, (f) => stackPlan(s, bullets, f))
                                         : { F: frame(false), plan: null };
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  if (hasImg && hasBulls) {
    drawList(slide, s, MX, F.by, plan.th, isLight, plan);
    addImageCap(slide, img, MX, F.by + plan.th + IMG_GAP, BW, F.bh - plan.th - IMG_GAP, s.caption, isLight);
  } else if (hasImg) {
    addImageCap(slide, img, MX, F.by, BW, F.bh, s.caption, isLight);
  } else if (hasBulls) {
    addList(slide, s, bullets, MX, F.by, BW, F.bh, isLight);
  }
}

// ── two figures: the same room planning as one figure; the pair sits side by side or stacked, whichever shows both larger
const PAIR_GAP = 0.15;
function pairArrangement(images, w, h) {
  const shown = (bw, bh) => images.reduce((sum, im) => {
    const d = imgDims(im);
    if (!d || !d.w || !d.h) return sum + bw * bh / 2;
    const k = Math.min(bw / d.w, bh / d.h);
    return sum + d.w * k * d.h * k;
  }, 0);
  return shown((w - PAIR_GAP) / 2, h) >= shown(w, (h - PAIR_GAP) / 2) ? 'row' : 'column';
}

function drawPair(slide, images, caps, x, y, w, h, isLight) {
  const imgs = images.filter(im => im && fs.existsSync(im)).slice(0, 2);
  if (imgs.length < 2) {
    if (imgs.length) addImageCap(slide, imgs[0], x, y, w, h, caps[0], isLight);
    return;
  }
  if (pairArrangement(imgs, w, h) === 'row') {
    const iw = (w - PAIR_GAP) / 2;
    imgs.forEach((im, i) => addImageCap(slide, im, x + i * (iw + PAIR_GAP), y, iw, h, caps[i], isLight));
  } else {
    const ih = (h - PAIR_GAP) / 2;
    imgs.forEach((im, i) => addImageCap(slide, im, x, y + i * (ih + PAIR_GAP), w, ih, caps[i], isLight));
  }
}

function buildTwoImgRight(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  const bs = s.bullets || [];
  const { F, plan } = roomFor(s.title, BW, (f) => sidePlan(s, bs, f));
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  drawList(slide, s, MX, F.by, F.bh, isLight, plan);
  const ix = MX + plan.tw + 0.15;
  drawPair(slide, s.images || [], s.captions || [], ix, F.by, MX + BW - ix, F.bh, isLight);
}

function buildTwoImgLeft(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  const bs = s.bullets || [];
  const { F, plan } = roomFor(s.title, BW * TEXT_SHARES[TEXT_SHARES.length - 1], (f) => sidePlan(s, bs, f));
  const iw = BW - plan.tw - 0.15;
  drawPair(slide, s.images || [], s.captions || [], MX, F.by - 0.38, iw, F.bh + 0.38, isLight);
  const rx = MX + iw + 0.15;
  addTitle(slide, s.title, isLight, rx, F.ty, plan.tw, F.th);
  drawList(slide, s, rx, F.by, F.bh, isLight, plan);
}

function buildTwoImgStack(s, above) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  const bullets = s.bullets || [];
  const images = s.images || [], caps = s.captions || [];
  const { F, plan } = bullets.length ? roomFor(s.title, BW, (f) => stackPlan(s, bullets, f))
                                     : { F: frame(false), plan: null };
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  if (!plan) {
    drawPair(slide, images, caps, MX, F.by, BW, F.bh, isLight);
    return;
  }
  const imgH = F.bh - plan.th - IMG_GAP;
  const imgY = above ? F.by : F.by + plan.th + IMG_GAP;
  drawPair(slide, images, caps, MX, imgY, BW, imgH, isLight);
  drawList(slide, s, MX, above ? F.by + imgH + IMG_GAP : F.by, plan.th, isLight, plan);
}
function buildTwoImgAbove(s) { buildTwoImgStack(s, true); }
function buildTwoImgBelow(s) { buildTwoImgStack(s, false); }

// ── code listing: the book's code as editable monospace text in a panel tinted from the theme ─────────────────────
// width per character per point of the widest monospace face (Courier New 0.6 em), with the wrap model's margin
const MONO_CW = emW('M', 'mono') / 72 / WRAP_SAFE;
const CODE_LINE = 1.2, CODE_PAD = 0.14, CODE_MAX_FS = 16, CODE_MIN_FS = 10;
const CODE_SHARES = [0.35, 0.42, 0.50, 0.60];    // the listing's share of the width beside its explanation
const MIN_CODE_H = 1.2;

function codeLines(code) {
  return String(code || '').replace(/\t/g, '    ').split('\n').map(l => l.replace(/\s+$/, ''));
}

// Where a listing may stop (2 > 1 > 0.5): after a blank line, before a statement at the listing's outer indentation,
// before any other statement; never inside a bracket left open or before a continuation line (0).
function codeBreak(lines, k) {
  if (k >= lines.length) return 2;
  const prev = lines[k - 1], next = lines[k];
  if (!prev.trim()) return 2;
  const indent = (l) => l.length - l.trimStart().length;
  const outer = Math.min(...lines.filter(l => l.trim()).map(indent));
  const head = lines.slice(0, k).join(' ');
  const open = (head.match(/[([{]/g) || []).length - (head.match(/[)\]}]/g) || []).length;
  if (open > 0 || /[,\\([{:]\s*$/.test(prev) || /^\s*[)\]}.]/.test(next)) return 0;
  return indent(next) <= outer ? 1 : 0.5;
}

// The largest size (16 -> 10pt) at which the listing fits w x h without wrapping; below the floor it shows the
// listing's opening part that fits, ending where a block or statement ends -- so its length follows the code.
function codePlan(lines, w, h) {
  const longest = Math.max(1, ...lines.map(l => l.length));
  const lineH = (fs) => fs * CODE_LINE / 72;
  for (let fs = CODE_MAX_FS; fs >= CODE_MIN_FS; fs--) {
    const needH = lines.length * lineH(fs) + 2 * CODE_PAD;
    if (longest * fs * MONO_CW <= w - 2 * CODE_PAD && needH <= h) return { fs, lines, h: needH, wrap: false };
  }
  const fs = CODE_MIN_FS;
  const perRow = Math.max(1, Math.floor((w - 2 * CODE_PAD) / (fs * MONO_CW)));
  const rowsOf = (l) => Math.max(1, Math.ceil(l.length / perRow));      // a line too long for the width wraps
  const room = Math.max(2, Math.floor((h - 2 * CODE_PAD) / lineH(fs)));
  let fit = 0, used = 0;
  while (fit < lines.length && used + rowsOf(lines[fit]) <= room) used += rowsOf(lines[fit++]);
  fit = Math.max(1, fit);
  let cut = fit;                                  // the latest block end, else statement end, in the second half
  for (const want of [2, 1, 0.5]) {
    const k = [...Array(fit).keys()].map(i => fit - i).find(k => k >= Math.ceil(fit / 2) && codeBreak(lines, k) >= want);
    if (k) { cut = k; break; }
  }
  const shown = lines.slice(0, cut);
  while (shown.length > 1 && !shown[shown.length - 1].trim()) shown.pop();
  const rows = shown.reduce((n, l) => n + rowsOf(l), 0);
  return { fs, lines: shown, h: Math.min(h, rows * lineH(fs) + 2 * CODE_PAD), wrap: longest > perRow };
}

// The listing beside its explanation reads only when it fits unwrapped at the floor size and the text fits its column.
function codeSideFits(lines, F, plan, cw) {
  const longest = Math.max(1, ...lines.map(l => l.length));
  return plan.ok && longest * CODE_MIN_FS * MONO_CW <= cw - 2 * CODE_PAD && !codePlan(lines, cw, F.bh).wrap;
}

function drawCode(slide, plan, x, y, w, isLight) {
  slide.addShape(pptx.ShapeType.rect, { x, y, w, h: plan.h, fill: { color: tintC(isLight, 8) }, line: { type: 'none' } });
  slide.addShape(pptx.ShapeType.rect, { x, y, w: 0.05, h: plan.h, fill: { color: ACC }, line: { type: 'none' } });
  slide.addText(plan.lines.map(l => l.length ? l : ' ').join('\n'), {
    x: x + CODE_PAD, y: y + CODE_PAD / 2, w: w - 1.5 * CODE_PAD, h: plan.h - CODE_PAD,
    fontFace: FMONO, fontSize: plan.fs, color: fgC(isLight), align: 'left', valign: 'top',
    wrap: !!plan.wrap, lineSpacingMultiple: 1.0, paraSpaceBefore: 0, paraSpaceAfter: 0, margin: 0,
  });
}

// code-beside: the explanation on the left, the listing on the narrowest share that shows it at a comfortable size; a
// listing too wide for it, or text too long for its column, is laid out code-above instead
function buildCodeSide(s) {
  const bs = s.bullets || [];
  const lines = codeLines(s.code);
  const want = Math.max(1, ...lines.map(l => l.length)) * 13 * MONO_CW + 2 * CODE_PAD;
  const cw = BW * (CODE_SHARES.find(r => BW * r >= want) || CODE_SHARES[CODE_SHARES.length - 1]);
  const tw = BW - cw - 0.2;
  const { F, plan } = roomFor(s.title, BW, (f) => planList(s, bs, tw, f.bh, 22, 1));
  if (!codeSideFits(lines, F, plan, cw)) return buildCodeAbove(s);
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  drawList(slide, s, MX, F.by, F.bh, isLight, plan);
  drawCode(slide, codePlan(lines, cw, F.bh), MX + tw + 0.2, F.by, cw, isLight);
}

// code-above: the listing takes the height it needs (never below MIN_CODE_H), the explanation everything else
function codeStackPlan(s, bullets, lines, f) {
  const most = f.bh - MIN_CODE_H - IMG_GAP;
  let th = Math.min(most, 0.6), p = planList(s, bullets, BW, th, 20);
  while (!p.ok && th < most) {
    th = Math.min(most, th + 0.1);
    p = planList(s, bullets, BW, th, 20);
  }
  const code = codePlan(lines, BW, f.bh - Math.min(th, p.textH / 0.98 + 0.06) - IMG_GAP);
  const room = f.bh - code.h - IMG_GAP;
  return { ...planList(s, bullets, BW, room * 0.9, 20), code, room };      // 10% slack: nested lists measure short
}

function buildCodeAbove(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  const bullets = s.bullets || [];
  const lines = codeLines(s.code);
  if (!bullets.length) {
    const F = frame(false);
    addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
    drawCode(slide, codePlan(lines, BW, F.bh), MX, F.by, BW, isLight);
    return;
  }
  const { F, plan } = roomFor(s.title, BW, (f) => codeStackPlan(s, bullets, lines, f));
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  drawCode(slide, plan.code, MX, F.by, BW, isLight);
  drawList(slide, s, MX, F.by + plan.code.h + IMG_GAP, plan.room, isLight, plan);
}

// two-contents — HTML: subtitle has border-bottom:2px accent; no vertical divider
function buildTwoContents(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const blocks = (s.blocks || []).slice(0, 2);
  const GAP  = 0.38;
  const colW = (BW - GAP) / 2;
  // both column headings at one size, the heading row as tall as the longer one wraps to, so the lists start level
  const HEAD_FS = 21, ins = insetsOf({});
  const headH = Math.max(0, ...blocks.filter(b => b.subtitle).map(b =>
    wrapLines(toPlain(b.subtitle), colW - ins.l - ins.r, HEAD_FS, 'display') * HEAD_FS * LINE / 72 + ins.t + ins.b));
  const RULE = 0.017, RULE_GAP = 0.06;
  blocks.forEach((block, i) => {
    const x       = i === 0 ? MX : MX + colW + GAP;
    const subtitle = block.subtitle || '';
    const bullets  = block.bullets || [];
    if (subtitle) {
      slide.addText(toPlain(subtitle), {
        x, y: BODY_Y, w: colW, h: headH,
        fontSize: HEAD_FS, fontFace: FDISP, color: fgC(isLight), bold: false, valign: 'bottom',
      });
      // Accent underline — mirrors border-bottom: 2px solid var(--accent)
      slide.addShape(pptx.ShapeType.rect, {
        x, y: BODY_Y + headH, w: colW, h: RULE,
        fill: { color: ACC }, line: { type: 'none' },
      });
      const top = headH + RULE + RULE_GAP;
      addBullets(slide, bullets, x, BODY_Y + top, colW, BODY_H - top, isLight);
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
  const n = steps.length;
  if (!n) return;
  const rowsN = n <= 5 ? 1 : 2, perRow = Math.ceil(n / rowsN);
  const g = stepGeometry(rowsN, perRow);
  // Uniform font sizes across all steps: size to fit the LONGEST title/body so every step matches AND
  // shows its full text (no ellipsis).
  const minFit = (vals, dflt) => vals.length ? Math.min(...vals) : dflt;
  const uTitleFs = minFit(steps.filter(s2 => s2.title).map(s2 => fitClip(s2.title, g.textW, g.titleBoxH, 18, 8).fs), 14);
  const uBodyFs  = minFit(steps.filter(s2 => s2.body).map(s2 => cardBodyFs(s2, s2.body, g.textW, g.bodyBoxH, 13, 8)), 10);
  for (let r = 0; r < rowsN; r++) {
    const row = steps.slice(r * perRow, (r + 1) * perRow);
    const bandY = BODY_Y + r * (g.bandH + g.rowGap);
    const stepY = bandY + g.top;
    const panelTop = stepY + g.cir + g.below;
    const panelH = (bandY + g.bandH) - panelTop - 0.02;
    // Connecting line through circle centers
    if (row.length > 1) slide.addShape(pptx.ShapeType.rect, {
      x: MX + g.cw / 2, y: stepY + g.cir / 2 - 0.008, w: (row.length - 1) * (g.cw + g.gap), h: 0.017,
      fill: { color: ACC, transparency: 60 }, line: { type: 'none' },
    });
    row.forEach((step, j) => {
      const i = r * perRow + j;
      const x  = MX + j * (g.cw + g.gap);
      const cx = x + g.cw / 2 - g.cir / 2;
      // Tinted panel behind text -> fills the space + adds color, consistent across steps
      slide.addShape(pptx.ShapeType.roundRect, {
        x, y: panelTop, w: g.cw, h: panelH, rectRadius: 0.06,
        fill: { color: ACC, transparency: isLight ? 91 : 84 }, line: { type: 'none' },
      });
      // Number circle (accent) on the timeline
      slide.addShape(pptx.ShapeType.ellipse, { x: cx, y: stepY, w: g.cir, h: g.cir, fill: { color: ACC }, line: { type: 'none' } });
      slide.addText(toPlain(step.num || String(i + 1)), {
        x: cx, y: stepY, w: g.cir, h: g.cir, wrap: false,
        fontSize: rowsN > 1 ? 12 : 14, fontFace: FDISP, color: textOn(ACC), bold: true, align: 'center', valign: 'middle',
      });
      if (step.title) {
        slide.addText(toPlain(step.title), {
          x: x + 0.12, y: panelTop + g.pad, w: g.textW, h: g.titleBoxH,
          fontSize: uTitleFs, fontFace: FDISP, color: fgC(isLight), bold: true, align: 'center', valign: 'top', wrap: true,
        });
      }
      addCardBody(slide, step, step.body, x + 0.12, panelTop + g.pad + g.titleBoxH + 0.04, g.textW, g.bodyBoxH,
                  uBodyFs, dimC(isLight), 'center');
    });
  }
}

// Geometry of the step cards: one row of up to 5, or two rows (mirrored by layout_select.step_geometry).
function stepGeometry(rowsN, perRow) {
  const gap = 0.12, rowGap = rowsN > 1 ? 0.12 : 0;
  const bandH = (BODY_H - rowGap * (rowsN - 1)) / rowsN;
  const one = rowsN === 1;
  const top = one ? 0.08 : 0.04, cir = one ? 0.42 : 0.34, below = one ? 0.12 : 0.08, pad = one ? 0.16 : 0.1;
  const titleBoxH = one ? 0.62 : 0.4;
  const cw = (BW - gap * (perRow - 1)) / perRow;
  const panelH = bandH - top - cir - below - 0.02;
  const bodyBoxH = panelH - pad - titleBoxH - 0.04 - (one ? 0.14 : 0.08);
  return { gap, rowGap, bandH, top, cir, below, pad, titleBoxH, cw, textW: cw - 0.24, bodyBoxH };
}

// key-points — HTML: each .pt has border-left:4px accent + rgba bg + border-radius
// Measured card layout: the largest body font (title = body + 3) at which every card's text fits, with each
// row given the height its own text needs (the rest is shared out evenly). Mirrors fit_model.fit_cards().
const KP_GAP = 0.06, KP_TEXT_X = 0.68, KP_PAD = 0.05;
function fitCards(points, textW, availH) {
  const lineH = (fs) => fs * 1.28 / 72;
  const bodyLines = (p, bFs) => cardItems(p).length >= 2
    ? cardItems(p).reduce((a, t) => a + estLines(t, textW - NEST_IND, bFs), 0) + 0.35 * (cardItems(p).length - 1)
    : estLines(p.body, textW, bFs);
  const need = (p, tFs, bFs) => KP_PAD + estLines(p.title || '', textW, tFs) * lineH(tFs) + 0.04
                                + (String(p.body || '').trim() ? bodyLines(p, bFs) * lineH(bFs) + 0.02 : 0) + KP_PAD;
  const gaps = KP_GAP * (points.length - 1);
  let pick = null;
  for (const bFs of [13, 12, 11, 10, 9]) {
    const tFs = Math.min(18, bFs + 3);
    const rows = points.map(p => need(p, tFs, bFs));
    if (rows.reduce((a, b) => a + b, 0) + gaps <= availH) { pick = { tFs, bFs, rows, fits: true }; break; }
    if (bFs === 9) pick = { tFs, bFs, rows, fits: false };
  }
  const total = pick.rows.reduce((a, b) => a + b, 0) + gaps;
  const scale = total > availH ? (availH - gaps) / (total - gaps) : 1;
  const extra = total < availH ? Math.min(0.25, (availH - total) / points.length) : 0;
  pick.heights = pick.rows.map(h => h * scale + extra);
  return pick;
}

function buildKeyPoints(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const points = s.points || [];
  const n = points.length;
  if (!n) return;
  const textW = BW - KP_TEXT_X - 0.03;
  const fit = fitCards(points, textW, BODY_H);
  let y = BODY_Y;
  points.forEach((pt, i) => {
    const rowH = fit.heights[i];
    slide.addShape(pptx.ShapeType.rect, {
      x: MX, y, w: BW, h: rowH, fill: { color: fgC(isLight), transparency: 96 }, line: { type: 'none' },
    });
    slide.addShape(pptx.ShapeType.rect, { x: MX, y, w: 0.04, h: rowH, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(toPlain(pt.ix || String(i + 1).padStart(2, '0')), {
      x: MX + 0.11, y: y + 0.05, w: 0.5, h: Math.min(rowH - 0.1, 0.4),
      fontSize: Math.max(10, fit.tFs - 2), fontFace: FMONO, color: ACC, valign: 'top', wrap: false,
    });
    const tH = estLines(pt.title || '', textW, fit.tFs) * fit.tFs * 1.28 / 72 + 0.04;
    slide.addText(toPlain(pt.title || ''), {
      x: MX + KP_TEXT_X, y: y + KP_PAD, w: textW, h: tH,
      fontSize: fit.tFs, fontFace: FBODY, color: fgC(isLight), bold: true, valign: 'top',
    });
    addCardBody(slide, pt, pt.body || '', MX + KP_TEXT_X, y + KP_PAD + tH + 0.02, textW,
                Math.max(0.2, rowH - tH - 2 * KP_PAD - 0.02), fit.bFs, dimC(isLight));
    y += rowH + KP_GAP;
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
      fill: { color: fgC(isLight), transparency: 96 },
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
        fontSize: fitFontSize(col.title, colW - 0.30, 0.60, 24, 14), fontFace: FBODY, color: fgC(isLight),
        bold: true, valign: 'top', wrap: true,
      });
      y += 0.64;
    }
    if (col.body) {
      const tbf = fitClip(col.body, colW - 0.30, 0.90, 14, 9);
      slide.addText(toPlain(tbf.text), {
        x: x + 0.15, y, w: colW - 0.30, h: 0.90,
        fontSize: tbf.fs, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true, shrinkText: true,
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
    const bgColor = isDark ? BGDARK : surfC(true, 4);
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
    fill: { color: fgC(isLight), transparency: 80 }, line: { type: 'none' },
  });
  const cardsN = cards.slice(0, n);
  // UNIFORM geometry + font sizes so every card matches (headings/bodies same size, aligned) -- the
  // per-card fit made "Spam" huge and "Spam Filter" small, which looked inconsistent.
  const textW  = cardW - 0.20;
  const numD   = 0.30;
  const headY  = CARD_Y + 0.52;                 // heading block starts just below the number
  const headBoxH = 0.66;
  const bodyY  = headY + headBoxH + 0.04;
  const bodyBoxH = (CARD_Y + cardH) - bodyY - 0.14;
  // Uniform font sizes across all cards: take the size that fits the LONGEST heading/body (each measured
  // by shrink-to-fit), so every card shares one size AND shows its full text (no ellipsis).
  const minFit = (vals, dflt) => vals.length ? Math.min(...vals) : dflt;
  const uHeadFs = minFit(cardsN.filter(c => c.heading).map(c => fitClip(c.heading, textW, headBoxH, 20, 8).fs), 16);
  const uBodyFs = minFit(cardsN.filter(c => c.body).map(c => cardBodyFs(c, c.body, textW, bodyBoxH, 14, 8)), 11);
  cardsN.forEach((card, i) => {
    const x  = MX + i * (cardW + GAP);
    const cc = CARD_COLORS[i % CARD_COLORS.length];
    const cx = x + cardW / 2 - DOT_D / 2;
    const ink = FGLIT;                            // dark ink on the light tint of the card's colour, in any design
    slide.addShape(pptx.ShapeType.ellipse, { x: cx, y: DOT_Y, w: DOT_D, h: DOT_D, fill: { color: isLight ? BGLIT : BGDARK }, line: { color: cc, pt: 2 } });
    slide.addShape(pptx.ShapeType.rect, { x, y: CARD_Y, w: cardW, h: cardH, fill: { color: CARD_TINTS[i % CARD_TINTS.length] }, line: { type: 'none' } });
    slide.addShape(pptx.ShapeType.rect, { x, y: CARD_Y, w: cardW, h: 0.07, fill: { color: cc }, line: { type: 'none' } });
    // Number chip (centered, single line): a ring in the card's full colour
    const numX = x + cardW / 2 - numD / 2;
    slide.addShape(pptx.ShapeType.ellipse, { x: numX, y: CARD_Y + 0.16, w: numD, h: numD, fill: { color: CARD_TINTS[i % CARD_TINTS.length] }, line: { color: cc, pt: 1.5 } });
    slide.addText(toPlain(card.num || String(i + 1)), {
      x: numX, y: CARD_Y + 0.16, w: numD, h: numD, wrap: false, margin: 0,
      fontSize: 12, fontFace: FDISP, color: ink, bold: true, align: 'center', valign: 'middle',
    });
    // Heading -- uniform size across cards, top-aligned so every heading starts on the same line.
    if (card.heading) {
      slide.addText(toPlain(card.heading), {
        x: x + 0.10, y: headY, w: textW, h: headBoxH,
        fontSize: uHeadFs, fontFace: FBODY, color: ink, bold: true, align: 'center', valign: 'top', wrap: true,
      });
    }
    // Body -- uniform size across cards; full text always shows.
    addCardBody(slide, card, card.body, x + 0.10, bodyY, textW, bodyBoxH, uBodyFs, ink, 'center');
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
  const { F, plan } = roomFor(s.title, BW, (f) => numConclPlan(rows, f.bh));
  addTitle(slide, s.title, isLight, MX, F.ty, BW, F.th);
  const { cols, rpc, colW, bfs, hfs, heights } = plan;
  const txtW = colW - NUM_TXT_X;
  const numFs = Math.min(26, Math.round(Math.min(...heights) * 40));
  const border = { color: fgC(isLight), transparency: 78 };
  for (let c = 0; c < cols; c++) {
    const x = MX + c * (colW + NUM_COL_GAP);
    const mine = rows.slice(c * rpc, (c + 1) * rpc);
    // every row as tall as its own text, the room left shared out so the rows fill the body
    const spare = Math.max(0, F.bh - mine.reduce((t, _, r) => t + heights[c * rpc + r], 0)) / Math.max(1, mine.length);
    let y = F.by;
    mine.forEach((row, r) => {
      const i = c * rpc + r, rowH = heights[i] + spare;
      const headH = numConclHeadH(row, txtW, hfs);
      slide.addShape(pptx.ShapeType.rect, { x, y, w: colW, h: 0.014, fill: border, line: { type: 'none' } });
      slide.addText(toPlain(row.n || String(i + 1).padStart(2, '0')), {
        x, y: y + 0.04, w: NUM_W_CONCL, h: rowH - 0.08, wrap: false,
        fontSize: numFs, fontFace: FDISP, color: ACC, valign: 'middle',
      });
      slide.addText(toPlain(row.heading || ''), {
        x: x + NUM_TXT_X, y: y + NUM_PAD, w: txtW, h: headH, wrap: true, valign: 'top',
        fontSize: hfs, fontFace: FDISP, color: fgC(isLight), margin: 0,
      });
      const bY = y + NUM_PAD + headH + 0.03;
      if (row.body) addCardBody(slide, row, row.body, x + NUM_TXT_X, bY, txtW, y + rowH - bY - NUM_PAD / 2, bfs, dimC(isLight));
      y += rowH;
    });
    slide.addShape(pptx.ShapeType.rect, { x, y, w: colW, h: 0.014, fill: border, line: { type: 'none' } });
  }
}

// numbered conclusions: one column while the body keeps a comfortable size, two columns before it gets small; every
// row as tall as its heading and body need at one size shared by the slide
const NUM_W_CONCL = 0.62, NUM_TXT_X = 0.78, NUM_PAD = 0.07, NUM_COL_GAP = 0.45;
const NUM_CONCL_TRIES = [[1, 14], [1, 13], [1, 12], [2, 14], [2, 13], [2, 12], [2, 11], [1, 11], [2, 10], [1, 10],
                         [2, 9], [1, 9], [2, 8], [1, 8]];

function numConclHeadH(row, w, fs) {
  return row.heading ? wrapLines(toPlain(row.heading), w, fs, 'display') * fs * BODY_LINE / 72 : 0;
}

// a row's body: its items as sub-bullets, or its one paragraph
function numConclRowH(row, w, hfs, bfs) {
  const items = cardItems(row).length >= 2 ? cardItems(row) : [toPlain(row.body || '')];
  const body = row.body || items[0] ? bulletsH(items, w + BULLET_INSET - 0.08 - (items.length >= 2 ? NEST_IND : 0), bfs) : 0;
  return Math.max(0.5, NUM_PAD * 1.5 + numConclHeadH(row, w, hfs) + 0.03 + body);
}

// The first (columns, size) at which every column of rows fits h; when none does, the one that overflows least.
function numConclPlan(rows, h) {
  let best = null;
  for (const [cols, bfs] of NUM_CONCL_TRIES) {
    if (cols > 1 && rows.length < 3) continue;
    const rpc = Math.ceil(rows.length / cols);
    const colW = (BW - NUM_COL_GAP * (cols - 1)) / cols;
    const hfs = Math.min(20, bfs + 4);
    const heights = rows.map(r => numConclRowH(r, colW - NUM_TXT_X, hfs, bfs));
    const need = Math.max(...Array.from({ length: cols }, (_, c) =>
      heights.slice(c * rpc, (c + 1) * rpc).reduce((t, x) => t + x, 0)));
    const plan = { cols, rpc, colW, bfs, hfs, heights, need, fs: bfs, ok: need <= h };
    if (plan.ok) return plan;
    if (!best || need < best.need) best = plan;
  }
  return best;
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
  const borderColor = dimC(isLight);
  const cN = cells.slice(0, n);
  const padX = 0.22;
  const tW = cellW - padX * 2, bW = cellW - padX * 2;
  const TITLE_TOP = 0.30, GAPTB = 0.16;
  // Title: modest cap so it doesn't dwarf the body. Uniform across cells.
  const minFit = (vals, dflt) => vals.length ? Math.min(...vals) : dflt;
  const uTitleFs = minFit(cN.filter(c => c.title).map(c => fitClip(c.title, tW, 0.56, 18, 12).fs), 15);
  const tLineH = uTitleFs * 1.3 / 72;
  // Body starts under the tallest title (uniform bodyY across cells) and fills the rest of the cell, so
  // the body font can be sized against a LARGE box -> body is big, not tiny under a huge title.
  const maxTitleLines = Math.max(1, ...cN.filter(c => c.title).map(c => estLines(c.title, tW, uTitleFs)));
  const bodyOff = TITLE_TOP + maxTitleLines * tLineH + GAPTB;
  const bodyBoxH = cellH - bodyOff - 0.16;
  const uBodyFs = minFit(cN.filter(c => c.body).map(c => cardBodyFs(c, c.body, bW, bodyBoxH, 18, 11)), 13);
  cN.forEach((cell, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = MX + col * (cellW + 0.15);
    const y = BODY_Y + row * (cellH + 0.11);
    slide.addShape(pptx.ShapeType.rect, {
      x, y, w: cellW, h: cellH,
      fill: { color: tintC(isLight, 8) },
      line: { color: borderColor, pt: 0.5 },
    });
    slide.addShape(pptx.ShapeType.rect, {
      x: x + padX, y: y + 0.13, w: 0.20, h: 0.045,
      fill: { color: ACC }, line: { type: 'none' },
    });
    if (cell.title) {
      slide.addText(toPlain(cell.title), {
        x: x + padX, y: y + TITLE_TOP, w: tW, h: maxTitleLines * tLineH + 0.06,
        fontSize: uTitleFs, fontFace: FBODY, color: fgC(isLight),
        bold: true, valign: 'top', wrap: true,
      });
    }
    addCardBody(slide, cell, cell.body, x + padX, y + bodyOff, bW, bodyBoxH, uBodyFs, dimC(isLight));
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
    fill: { color: fgC(isLight), transparency: 96 },
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
        fill: { color: fgC(isLight), transparency: 96 }, line: { type: 'none' },
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
// Agenda / section opener — 3 deterministic variants (picked by s.variant).
function buildAgenda(s) {
  const v = ((s.variant | 0) % 3 + 3) % 3;
  if (v === 1) return _agendaTwoCol(s);
  if (v === 2) return _agendaChips(s);
  return _agendaPanel(s);
}

// v0: solid accent panel left with the section title + numbered list right
function _agendaPanel(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  // Left: a solid accent panel with the section title in contrasting ink -- a strong block of color
  // that stops the slide reading as empty. Sits below the header band.
  const panelW = 2.95, panelY = BODY_Y, panelH = BODY_H;
  gradRect(slide, MX, panelY, panelW, panelH, gradStops()[0], gradStops()[1], 'v');
  const tf = fitClip(s.title || '', panelW - 0.5, panelH - 0.5, 30, 16);
  slide.addText(toPlain(tf.text), {
    x: MX + 0.28, y: panelY + 0.2, w: panelW - 0.56, h: panelH - 0.4,
    fontSize: tf.fs, fontFace: FDISP, color: textOn(gradMid()), valign: 'middle', wrap: true, bold: true,
  });
  const items = (s.items || []).slice(0, 8);
  const listX = MX + panelW + 0.4;
  const listW = W - listX - MX;
  const rowH  = BODY_H / Math.max(items.length, 1);
  const BADGE = 0.42;
  items.forEach((item, i) => {
    const iy = BODY_Y + i * rowH;
    const cy = iy + rowH / 2;
    // Filled accent number badge
    slide.addShape(pptx.ShapeType.roundRect, {
      x: listX, y: cy - BADGE / 2, w: BADGE, h: BADGE, rectRadius: 0.06,
      fill: { color: ACC }, line: { type: 'none' },
    });
    slide.addText(String(i + 1).padStart(2, '0'), {
      x: listX, y: cy - BADGE / 2, w: BADGE, h: BADGE,
      fontSize: 13, fontFace: FMONO, color: textOn(ACC), bold: true, align: 'center', valign: 'middle',
    });
    const tx = listX + BADGE + 0.22, tw = listW - BADGE - 0.22;
    slide.addText(toPlain(item.title || ''), {
      x: tx, y: iy + 0.04, w: tw, h: rowH - 0.08,
      fontSize: fitFontSize(item.title, tw, rowH - 0.08, 18, 12),
      fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true,
    });
    // Accent divider under each item (except last)
    if (i < items.length - 1) {
      slide.addShape(pptx.ShapeType.rect, {
        x: tx, y: iy + rowH - 0.012, w: tw, h: 0.012,
        fill: { color: ACC, transparency: 62 }, line: { type: 'none' },
      });
    }
  });
}

// v1: title on top + a 2-column grid of accent-numbered items on the theme background
function _agendaTwoCol(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = (s.items || []).slice(0, 8);
  const n = items.length; if (!n) return;
  const cols = n > 4 ? 2 : 1, rows = Math.ceil(n / cols);
  const GAP = 0.4, colW = (BW - GAP * (cols - 1)) / cols;
  const rowH = (BODY_H - 0.1) / rows, BADGE = 0.44;
  const tw = colW - BADGE - 0.24;
  const minFit = (vals, dflt) => vals.length ? Math.min(...vals) : dflt;
  const uFs = minFit(items.map(it => fitClip(it.title || '', tw, rowH - 0.14, 18, 11).fs), 14);
  items.forEach((item, i) => {
    const col = Math.floor(i / rows), row = i % rows;
    const x = MX + col * (colW + GAP), y = BODY_Y + row * rowH;
    slide.addShape(pptx.ShapeType.roundRect, { x, y: y + rowH / 2 - BADGE / 2, w: BADGE, h: BADGE, rectRadius: 0.07, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(String(i + 1).padStart(2, '0'), { x, y: y + rowH / 2 - BADGE / 2, w: BADGE, h: BADGE, wrap: false, fontSize: 13, fontFace: FMONO, color: textOn(ACC), bold: true, align: 'center', valign: 'middle' });
    slide.addText(toPlain(item.title || ''), { x: x + BADGE + 0.24, y: y + 0.06, w: tw, h: rowH - 0.12, fontSize: uFs, fontFace: FBODY, color: fgC(isLight), valign: 'middle', wrap: true });
    slide.addShape(pptx.ShapeType.rect, { x: x + BADGE + 0.24, y: y + rowH - 0.02, w: tw, h: 0.012, fill: { color: ACC, transparency: 68 }, line: { type: 'none' } });
  });
}

// v2: title on top + a horizontal row of numbered chips with labels below (for up to 5 items)
function _agendaChips(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  addTitle(slide, s.title, isLight);
  const items = (s.items || []).slice(0, 5);
  const n = items.length; if (!n) return;
  const GAP = 0.2, cw = (BW - GAP * (n - 1)) / n;
  const CHIP = 0.62, chipY = BODY_Y + 0.35;
  const labY = chipY + CHIP + 0.22, labH = (BODY_Y + BODY_H) - labY - 0.1;
  const minFit = (vals, dflt) => vals.length ? Math.min(...vals) : dflt;
  const uFs = minFit(items.map(it => fitClip(it.title || '', cw - 0.1, labH, 16, 10).fs), 12);
  items.forEach((item, i) => {
    const x = MX + i * (cw + GAP), cx = x + cw / 2 - CHIP / 2;
    slide.addShape(pptx.ShapeType.roundRect, { x: cx, y: chipY, w: CHIP, h: CHIP, rectRadius: 0.1, fill: { color: ACC }, line: { type: 'none' } });
    slide.addText(String(i + 1).padStart(2, '0'), { x: cx, y: chipY, w: CHIP, h: CHIP, wrap: false, fontSize: 18, fontFace: FDISP, color: textOn(ACC), bold: true, align: 'center', valign: 'middle' });
    slide.addText(toPlain(item.title || ''), { x, y: labY, w: cw, h: labH, fontSize: uFs, fontFace: FBODY, color: fgC(isLight), align: 'center', valign: 'top', wrap: true });
  });
}

// quote — HTML: blockquote 96px display font, .who::before = 80px accent line + mono dim text
function buildQuote(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  addChrome(slide, isLight, s.page_num);
  bgMotif(slide);  // a quote slide is naturally sparse (short text, lots of whitespace)
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

// Full-canvas accent tint (edge-to-edge, including margins) -- no content layout ever paints outside the
// margin, so this wash is a visual signature reserved for section dividers alone, making v1/v2/v3
// (which otherwise reuse the same flat setBg() content slides use) instantly identifiable as dividers.
function _dividerWash(slide, isLight) {
  slide.addShape(pptx.ShapeType.rect, {
    x: 0, y: 0, w: W, h: H, fill: { color: ACC, transparency: isLight ? 92 : 88 }, line: { type: 'none' },
  });
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.08, fill: { color: ACC }, line: { type: 'none' } });
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: H - 0.08, w: W, h: 0.08, fill: { color: ACC }, line: { type: 'none' } });
}

// Consistent "SECTION" kicker label so every divider variant shares one recognizable tell regardless of
// its individual layout.
function _dividerKicker(slide, x, y, color) {
  slide.addText('SECTION', { x, y, w: 2.2, h: 0.3, fontSize: 10, fontFace: FMONO, color, bold: true, charSpacing: 3, valign: 'middle' });
}

// section-divider — HTML always uses accent-color background with dark panel text
// Section divider — 3 deterministic design variants (picked by s.variant so consecutive topic changes in
// a deck don't all look identical).
function buildSectionDivider(s) {
  const v = ((s.variant | 0) % 4 + 4) % 4;
  if (v === 1) return _sectionDividerSplit(s);
  if (v === 2) return _sectionDividerNumber(s);
  if (v === 3) return _sectionDividerProgress(s);
  return _sectionDividerFull(s);
}

// v3: title + a segmented PROGRESS bar (current section highlighted among the deck's sections, no digits)
function _sectionDividerProgress(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  _dividerWash(slide, isLight);
  addChrome(slide, isLight, s.page_num, true);
  _dividerKicker(slide, MX, 0.55, ACC);
  const idx = parseInt(String(s.section_num || '').replace(/\D/g, ''), 10) || 1;
  const total = Math.max(idx, s.section_total || idx);
  const title = toPlain(s.title || '');
  slide.addText(title, { x: MX, y: 1.5, w: BW, h: 1.5, fontSize: fitFontSize(title, BW, 1.5, 52, 28), fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'middle', wrap: true });
  if (s.lead) slide.addText(capFirst(toPlain(s.lead)), { x: MX, y: 3.05, w: BW * 0.7, h: 0.6, fontSize: 12, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true });
  // progress segments (no numeric labels -- purely a visual "how far into the deck" indicator)
  const segY = H - 1.15, segGap = 0.1, segW = (BW - segGap * (total - 1)) / total, segH = 0.16;
  for (let i = 0; i < total; i++) {
    const x = MX + i * (segW + segGap), filled = i < idx;
    slide.addShape(pptx.ShapeType.roundRect, { x, y: segY, w: segW, h: segH, rectRadius: 0.04, fill: { color: filled ? ACC : fgC(isLight), transparency: filled ? 0 : 84 }, line: { type: 'none' } });
  }
}

// v0: full-bleed accent GRADIENT background, number + big title + lead
function _sectionDividerFull(s) {
  const slide = pptx.addSlide();
  slide.background = { color: gradStops()[1] };
  gradRect(slide, 0, 0, W, H, gradStops()[0], gradStops()[1], 'v', 22);
  const fg = textOn(gradMid());
  const inkIsDark = hexLum(fg) < 0.5;
  addChrome(slide, inkIsDark, s.page_num, true, fg);
  _dividerKicker(slide, MX, 0.55, fg);
  const title = toPlain(s.title || '');
  slide.addText(title, {
    x: MX, y: 0.83, w: BW, h: H - 2.85,
    fontSize: fitFontSize(title, BW, H - 2.85, 60, 30), fontFace: FDISP, color: fg, bold: true, valign: 'middle', wrap: true,
  });
  slide.addShape(pptx.ShapeType.rect, { x: MX, y: H - 1.19, w: BW, h: 0.014, fill: { color: fg, transparency: 70 }, line: { type: 'none' } });
  if (s.lead) slide.addText(toPlain(s.lead), { x: MX, y: H - 1.07, w: BW * 0.7, h: 0.38, fontSize: 10, fontFace: FBODY, color: fg, valign: 'middle' });
}

// v1: split — accent gradient panel left with decorative motif, title on the theme background at right
function _sectionDividerSplit(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  _dividerWash(slide, isLight);
  addChrome(slide, isLight, s.page_num, true);
  const panelW = 3.4;
  gradRect(slide, 0, 0, panelW, H, gradStops()[0], gradStops()[1], 'v');
  const ink = textOn(gradMid());
  panelMotif(slide, 0, 0, panelW, H, ink);
  slide.addText('SECTION', { x: 0.5, y: 0.7, w: panelW - 0.8, h: 0.4, wrap: false, fontSize: 12, fontFace: FMONO, color: ink, bold: true, charSpacing: 3 });
  const rx = panelW + 0.55, rw = W - rx - MX;
  const title = toPlain(s.title || '');
  slide.addText(title, {
    x: rx, y: 1.0, w: rw, h: H - 2.4, fontSize: fitFontSize(title, rw, H - 2.4, 46, 26),
    fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'middle', wrap: true,
  });
  if (s.lead) slide.addText(capFirst(toPlain(s.lead)), { x: rx, y: H - 1.25, w: rw, h: 0.6, fontSize: 12, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true });
}

// v2: light/theme bg with a faint decorative accent motif behind the title (no numbering)
function _sectionDividerNumber(s) {
  const isLight = !!s.is_light;
  const slide = pptx.addSlide();
  setBg(slide, isLight);
  _dividerWash(slide, isLight);
  bgMotif(slide);
  addChrome(slide, isLight, s.page_num, true);
  _dividerKicker(slide, MX, 0.55, ACC);
  slide.addShape(pptx.ShapeType.rect, { x: MX, y: 1.5, w: 0.95, h: 0.06, fill: { color: ACC }, line: { type: 'none' } });
  const title = toPlain(s.title || '');
  slide.addText(title, {
    x: MX, y: 1.75, w: BW, h: 2.2, fontSize: fitFontSize(title, BW, 2.2, 54, 28),
    fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'middle', wrap: true,
  });
  if (s.lead) slide.addText(capFirst(toPlain(s.lead)), { x: MX, y: H - 1.15, w: BW * 0.8, h: 0.5, fontSize: 12, fontFace: FBODY, color: dimC(isLight), valign: 'top', wrap: true });
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
      fontSize: fitFontSize(s.title, BW, 0.90, 28, 17), fontFace: FDISP, color: FGDRK, bold: true,
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
    const bgColor = tintC(isLight, 5);
    slide.addShape(pptx.ShapeType.rect, {
      x, y: BODY_Y, w: cardW, h: BODY_H,
      fill: { color: bgColor },
      line: { color: isLight ? dimC(isLight) : ACC, pt: 0.5 },
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
    fontSize: 27, fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'bottom', shrinkText: true,
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
    fontSize: 42, fontFace: FDISP, color: titleColor(isLight), bold: true,
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
      fill: { color: tintC(isLight, 6) },
      line: { color: fgC(isLight), transparency: 85, pt: 0.5 },
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
      const descColor = dimC(isLight);
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
    fontSize: 27, fontFace: FDISP, color: titleColor(isLight), bold: true, valign: 'bottom', shrinkText: true,
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
      fill: { color: fgC(isLight), transparency: 92 }, line: { type: 'none' },
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

// composed (EXPERIMENTAL, opt-in only — see compose_pptx_layout() in deck_pptx_exporter.py). When a
// slide carries `composed_shapes` (an LLM freely composed this slide's layout instead of using one of
// the fixed build<Type> templates below), draw straight from those positions — still real native
// pptxgenjs shapes (addText/addShape/addImage), never rasterized, so the "must stay editable" requirement
// holds exactly like every other layout here.
// Safe preset shapes the composer may request (kept in sync with _SAFE_ACCENT_SHAPES in
// pptx_patterns.py) — any other/unknown name falls back to plain 'rect' rather than throwing.
// Full pptxgenjs ShapeType vocabulary minus actionButton*/flowChart*; 'oval' was a latent bug (not a
// real ShapeType -- only 'ellipse' is) fixed while expanding this list.
const SAFE_ACCENT_SHAPES = new Set([
  'accentBorderCallout1', 'accentBorderCallout2', 'accentBorderCallout3', 'accentCallout1',
  'accentCallout2', 'accentCallout3', 'arc', 'bentArrow', 'bentUpArrow', 'bevel', 'blockArc',
  'borderCallout1', 'borderCallout2', 'borderCallout3', 'bracePair', 'bracketPair', 'callout1',
  'callout2', 'callout3', 'can', 'chartPlus', 'chartStar', 'chartX', 'chevron', 'chord',
  'circularArrow', 'cloud', 'cloudCallout', 'corner', 'cornerTabs', 'cube', 'curvedDownArrow',
  'curvedLeftArrow', 'curvedRightArrow', 'curvedUpArrow', 'decagon', 'diagStripe', 'diamond',
  'dodecagon', 'donut', 'doubleWave', 'downArrow', 'downArrowCallout', 'ellipse', 'ellipseRibbon',
  'ellipseRibbon2', 'folderCorner', 'frame', 'funnel', 'gear6', 'gear9', 'halfFrame', 'heart',
  'heptagon', 'hexagon', 'homePlate', 'horizontalScroll', 'irregularSeal1', 'irregularSeal2',
  'leftArrow', 'leftArrowCallout', 'leftBrace', 'leftBracket', 'leftCircularArrow', 'leftRightArrow',
  'leftRightArrowCallout', 'leftRightCircularArrow', 'leftRightRibbon', 'leftRightUpArrow',
  'leftUpArrow', 'lightningBolt', 'line', 'lineInv', 'mathDivide', 'mathEqual', 'mathMinus',
  'mathMultiply', 'mathNotEqual', 'mathPlus', 'moon', 'noSmoking', 'nonIsoscelesTrapezoid',
  'notchedRightArrow', 'octagon', 'parallelogram', 'pentagon', 'pie', 'pieWedge', 'plaque',
  'plaqueTabs', 'plus', 'quadArrow', 'quadArrowCallout', 'rect', 'ribbon', 'ribbon2', 'rightArrow',
  'rightArrowCallout', 'rightBrace', 'rightBracket', 'round1Rect', 'round2DiagRect', 'round2SameRect',
  'roundRect', 'rtTriangle', 'smileyFace', 'snip1Rect', 'snip2DiagRect', 'snip2SameRect',
  'snipRoundRect', 'squareTabs', 'star10', 'star12', 'star16', 'star24', 'star32', 'star4', 'star5',
  'star6', 'star7', 'star8', 'stripedRightArrow', 'sun', 'swooshArrow', 'teardrop', 'trapezoid',
  'triangle', 'upArrow', 'upArrowCallout', 'upDownArrow', 'upDownArrowCallout', 'uturnArrow',
  'verticalScroll', 'wave', 'wedgeEllipseCallout', 'wedgeRectCallout', 'wedgeRoundRectCallout',
]);
const SOFT_SHADOW = { type: 'outer', color: '000000', opacity: 0.35, blur: 6, offset: 3, angle: 90 };

function buildComposed(s) {
  const isLight = !!s.is_light;
  const slide   = pptx.addSlide();
  setBg(slide, isLight);
  // No addChrome() here: buildComposed is only reached for slides carrying composed_shapes, which the
  // active experimental path no longer produces (it now renders via the default deterministic builders,
  // which draw their own chrome). Kept for a possible future opt-in composed path.
  // Decorative shapes render first (bottom z-order) so they can never visually cover text, even if
  // their bounding boxes overlap — real testing showed an accent bar hiding column text behind it
  // when shape order was left to the LLM's own array ordering.
  // z-order tiers: images/accent shapes draw first, a scrim draws above an image but below text,
  // text (title/bullet) always draws last so it is never hidden.
  const zTier = (t) => (t === 'accent_shape' || t === 'image' || t === 'freeform' ? 0 : t === 'scrim' ? 1 : 2);
  const shapes = [...(s.composed_shapes || [])];
  shapes.sort((a, b) => zTier(a.type) - zTier(b.type));
  for (const shape of shapes) {
    const x = Number(shape.x) || 0;
    const y = Number(shape.y) || 0;
    const w = Math.max(0.1, Number(shape.w) || 1);
    const h = Math.max(0.1, Number(shape.h) || 0.3);
    const fill = (shape.fill || ACC).replace('#', '');
    switch (shape.type) {
      case 'title':
      case 'bullet': {
        const opts = {
          x, y, w, h,
          fontSize: shape.font_size_hint || (shape.type === 'title' ? titleFontSize(shape.text || '') : 14),
          color: (shape.color || fgC(isLight)).replace('#', ''),
          align: shape.align || 'left', valign: shape.valign || 'top', wrap: true,
          margin: shape.margin === undefined ? 0 : shape.margin,
          bold: !!shape.bold,
          fontFace: shape.font_face || (shape.type === 'title' ? FDISP : FBODY),
        };
        if (typeof shape.rotate === 'number' && isFinite(shape.rotate)) opts.rotate = shape.rotate;
        if (shape.shadow) opts.shadow = { ...SOFT_SHADOW };
        if (shape.glow) opts.glow = { color: fill, opacity: 0.6, size: 8 };
        // Real native PPTX bullet glyph (same style as the legacy addBullets() helper) -- opt-in via
        // list_marker so single-line captions/kickers/labels (also type:'bullet') stay marker-free.
        if (shape.type === 'bullet' && shape.list_marker) {
          opts.bullet = { code: '25B8', color: ACC, indent: BULLET_IND_PT };
          opts.paraSpaceAfter = 5;
        }
        slide.addText(shape.preserve_text ? String(shape.text || '') : toPlain(shape.text || ''), opts);
        break;
      }
      case 'image':
        addImage(slide, shape.path, x, y, w, h);
        break;
      case 'scrim': {
        const opacity = Math.min(0.9, Math.max(0.1, Number(shape.opacity) || 0.45));
        slide.addShape(pptx.ShapeType.rect, {
          x, y, w, h, fill: { color: fill, transparency: (1 - opacity) * 100 }, line: { type: 'none' },
        });
        break;
      }
      case 'connector': {
        const from = shape.from || [x, y];
        const to = shape.to || [x + w, y + h];
        slide.addShape(pptx.ShapeType.line, {
          x: from[0], y: from[1], w: to[0] - from[0], h: to[1] - from[1],
          line: { color: String(shape.color || ACC).replace('#', ''), width: 1.4,
                  endArrowType: shape.arrow === false ? 'none' : 'triangle' },
        });
        break;
      }
      case 'accent_shape': {
        const shapeName = SAFE_ACCENT_SHAPES.has(shape.shape) ? shape.shape : 'rect';
        const opts = { x, y, w, h, fill: { color: fill }, line: shape.line_color ? {color: shape.line_color, width: 1} : { type: 'none' } };
        if (shape.shadow) opts.shadow = { ...SOFT_SHADOW };
        if (typeof shape.rotate === 'number' && isFinite(shape.rotate)) opts.rotate = shape.rotate;
        if (shape.rectRadius) opts.rectRadius = shape.rectRadius;
        slide.addShape(pptx.ShapeType[shapeName], opts);
        break;
      }
      case 'freeform': {
        // CUSTOM_GEOMETRY: `points` are LOCAL to this shape's own x/y/w/h box (verified against real
        // generated OOXML, not the slide's absolute coordinates) -- Python already re-bases them, so
        // shape.points are passed through as-is.
        if (!Array.isArray(shape.points) || !shape.points.length) break;
        const opts = { x, y, w, h, points: shape.points };
        opts.fill = shape.fill ? { color: fill } : { type: 'none' };
        opts.line = shape.line_color
          ? { color: String(shape.line_color).replace('#', ''), width: 1 }
          : { type: 'none' };
        if (shape.shadow) opts.shadow = { ...SOFT_SHADOW };
        if (typeof shape.rotate === 'number' && isFinite(shape.rotate)) opts.rotate = shape.rotate;
        slide.addShape(pptx.ShapeType.custGeom, opts);
        break;
      }
      default:
        break;
    }
  }
  // Title is rendered LAST (topmost z-order) using the same addTitle() every fixed template calls --
  // buildComposed() previously never rendered it at all, leaving title-only slides (e.g. section
  // dividers) looking empty. Skip if `shapes` already has a title (the render_narrative fallback
  // path draws its own via _title_block()) to avoid drawing it twice.
  const hasTitleShape = shapes.some((sh) => sh.type === 'title');
  if (s.title && !hasTitleShape) addTitle(slide, s.title, isLight);
}

// A layout whose meaning allows at most N items (a pyramid of levels, a cycle of stages...) never drops the rest:
// such a slide is drawn as an elastic list instead. Python picks another layout first; this is the last guard.
const ITEM_CAP = { iconmatrix: 6, pyramid: 5, funnel: 5, cycle: 6, hubspoke: 7, checklist: 8, highlight_list: 10,
                   concepts: 6, timeline: 8, steps: 8, formula_annot: 99 };
function overCap(s) {
  const cap = ITEM_CAP[s.type];
  if (!cap) return false;
  const n = Math.max((s.bullets || []).length, (s.rows || []).length, (s.steps || []).length, (s.concepts || []).length);
  return n > cap;
}

// Speaker notes as the presenter reads them: what to say, the passages kept for the presenter, the code, and the
// source sentences no bullet uses. Provenance of each bullet stays in the lecture JSON and the pptx audit.
function notesText(s) {
  const say = [], passages = [], code = [], formulas = [], unused = [];
  for (const n of s.speaker_notes || []) {
    if (!n || typeof n !== 'object') continue;
    if (n.presenter) say.push(toPlain(String(n.presenter)));
    if (n.passage) passages.push(toPlain(String(n.passage)));
    if (n.code) code.push(String(n.code));
    if (n.formula) formulas.push(toMathText(String(n.formula).replace(/^\s*\$\$|\$\$\s*$/g, '').trim()));
    for (const src of n.sources || []) if (src && src.quote) unused.push(toPlain(String(src.quote)));
  }
  const parts = [];
  if (say.length) parts.push(say.join('\n\n'));
  if (passages.length) parts.push(passages.join('\n\n'));
  if (code.length) parts.push('Code:\n' + code.join('\n\n'));
  if (formulas.length) parts.push('Formulas:\n' + formulas.join('\n'));
  if (unused.length) parts.push('Not on the slide:\n' + unused.map(q => '- ' + q).join('\n'));
  return parts.join('\n\n').trim();
}

// dispatch
defineLayouts();
for (const s of manifest.slides || []) {
  const before = pptx._slides.length;
  CURRENT_LAYOUT_ID = s.layout_id || null;
  CURRENT_LAYOUT = LAYOUT_NAME[s.layout_id] || null;
  CURRENT_SECTION = toPlain(s.section || '');
  if (s.composed_shapes) {
    buildComposed(s);
  } else if (overCap(s)) {
    buildBullets(s);
  } else switch (s.type) {
    case 'cover':            buildCover(s);             break;
    case 'toc':              buildToc(s);               break;
    case 'toc_vertical':     buildTocVertical(s);       break;
    case 'toc_described':    buildTocDescribed(s);      break;
    case 'toc_cards':        buildTocCards(s);          break;
    case 'bullets':          buildBullets(s);           break;
    case 'formula':          buildFormula(s);           break;
    case 'codeside':         buildCodeSide(s);          break;
    case 'codeabove':        buildCodeAbove(s);         break;
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
    case 'end_credits':      buildEndCredits(s);        break;
    case 'cover_editorial':  buildCoverEditorial(s);    break;
    case 'cover_minimal':    buildCoverMinimal(s);      break;
    case 'bigstatement':     buildBigStatement(s);      break;
    case 'definition':       buildDefinition(s);        break;
    case 'timeline':         buildTimelineVertical(s);  break;
    case 'spotlight':        buildSpotlight(s);         break;
    case 'iconmatrix':       buildIconMatrix(s);        break;
    case 'cmpmatrix':        buildComparisonMatrix(s);  break;
    case 'comparison2':      buildComparison(s);        break;
    case 'pyramid':          buildPyramid(s);           break;
    case 'cycle':            buildCycle(s);             break;
    case 'funnel':           buildFunnel(s);            break;
    case 'hubspoke':         buildHubSpoke(s);          break;
    case 'checklist':        buildChecklist(s);         break;
    case 'concepts':         buildConceptBreakdown(s);  break;
    case 'highlight_list':   buildHighlightList(s);     break;
    case 'takeaway_panel':   buildTakeawayPanel(s);     break;
    case 'formula_hero':     buildFormulaHero(s);       break;
    case 'formula_annot':    buildFormulaAnnotated(s);  break;
    case 'table_highlight':  buildTableHighlight(s);    break;
    case 'imgcompare':       buildImageCompare(s);      break;
    case 'chart_stat':       buildChartStat(s);         break;
    case 'kpi_cards':        buildKpiCards(s);          break;
    case 'big_stat':         buildBigStat(s);           break;
    case 'img_stat_hero':    buildImageStatHero(s);     break;
    case 'img_quote_hero':   buildImageQuoteHero(s);    break;
    case 'toc_sidebar':      buildTocSidebar(s);        break;
    case 'toc_timeline':     buildTocTimeline(s);       break;
    case 'cover_hero':       buildCoverHero(s);         break;
    case 'end_contact':      buildEndContact(s);        break;
    case 'end_gradient':     buildEndGradient(s);       break;
    default:                 buildBullets(s);           break;
  }
  const created = pptx._slides.slice(before);
  if (s.source_slide_number != null && created.length !== 1) {
    throw new Error(`Semantic slide ${s.source_slide_number} rendered ${created.length} PPTX pages`);
  }
  const notes = notesText(s);
  if (notes) for (const slide of created) slide.addNotes(notes);
}

(async () => {
  try {
    const n = (manifest.slides || []).length;
    let out = await pptx.write({ outputType: 'nodebuffer' });
    if (GRAD_STOPS.length >= 2) out = await applyGradBg(out, GRAD_STOPS, BGDARK);
    fs.writeFileSync(outputPath, await postProcess(out));
    console.log(`[pptx] ${n} slides → ${outputPath}`);
    if (FIT_LOG.length) {
      console.log(`[fit] ${FIT_LOG.length} box(es) set smaller than planned so their text fits (slides ${[...new Set(FIT_LOG.map(f => f.slide))].join(', ')})`);
      for (const f of FIT_LOG) console.log(`[fit]   slide ${f.slide}: ${f.from} -> ${f.to}pt  "${f.text}"`);
    }
    process.exit(0);
  } catch (err) {
    console.error('[pptx] Error: ' + err.message);
    process.exit(1);
  }
})();
