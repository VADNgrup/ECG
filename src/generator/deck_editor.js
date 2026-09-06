(function(){
'use strict';

/* ── State ── */
var editing=false, stage=null, _selEl=null, _multiSel=[], _targetImg=null, _clipboard=null;
var _imgMode='replace', _mode='select';
var _origScale=null, _origShow=null;
var _undoStack=[], _redoStack=[], MAX_UNDO=30;
var TOOL_H=144, PANEL_W=200;
var _fmtPaint=false, _fmtStyles=null, _fmtPaintBtn=null;
var _prevTab='home', _colorCmd='', _savedSel=null;
var _dragCleanup=null;
var _guideLines=[], _guidesEl=null;
var _rubberBandEl=null;
var _slRect=null, _slideFrameEl=null;
var _resizeHandler=null;
var EDIT_SEL='h1,h2,h3,h4,h5,h6,p,li,figcaption,blockquote,td,th,.ix,.kicker,.eyebrow,.section-num,.num,.tag,.badge,.lbl,.lede,.ttl,.body,.b,.t,.q,.big,.who,.chrome-top span,.chrome-bot span';
var MOVE_SEL='.block,.step,.pt,.card,.col,.side,.cell,.lhs,.rhs,.ed-textbox,.ed-shape,.ed-group,figure,h1,h2,h3,h4,h5,h6,p,ul,ol,li,table,blockquote,.chrome-top,.chrome-bot,.eq-wrap,.toc-row,.meta-row,.eyebrow,.kicker,.who,.big,.lede,.q,.ix,.section-num,.num,.tag,.badge,.lbl,.ttl,.body,.by,.inst';

/* ── DOM vars ── */
var header, slidePanel, outlinePanel, selBox, groupBox, imgPicker;

/* ── THEMES ── */
var THEMES = {
  frankfurt: {cover_bg:'linear-gradient(135deg,#0f172a 0%,#172554 46%,#1d4ed8 100%)',end_bg:'radial-gradient(circle at top,#1e3a8a 0%,#0f172a 72%)',accent:'#e2b96f',text:'#f8fafc',dim:'rgba(248,250,252,0.55)',cards:['#5b9bd5','#e07b6a','#7b68c8','#f0a050'],panel:'dark',font:{display:"'Montserrat',system-ui,sans-serif",body:"'Open Sans',system-ui,sans-serif",mono:"'IBM Plex Mono',monospace"}},
  umn:       {cover_bg:'linear-gradient(135deg,#7a0019 0%,#500014 55%,#330009 100%)',end_bg:'radial-gradient(circle at top,#7a0019 0%,#330009 72%)',accent:'#ffcc33',text:'#ffffff',dim:'rgba(255,255,255,0.55)',cards:['#900021','#2c6fad','#2a7a6f','#b8860b'],panel:'dark',font:{display:"'Playfair Display',Georgia,serif",body:"'Source Sans 3',system-ui,sans-serif",mono:"'IBM Plex Mono',monospace"}},
  seriph:    {cover_bg:'linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%)',end_bg:'radial-gradient(circle at top,#16213e 0%,#0a0a1a 72%)',accent:'#e94560',text:'#f8fafc',dim:'rgba(248,250,252,0.55)',cards:['#c0415e','#4a6fa5','#7a5c8a','#5a8a5a'],panel:'light',font:{display:"'Cormorant Garamond',Georgia,serif",body:"'Work Sans',system-ui,sans-serif",mono:"'JetBrains Mono',monospace"}},
  scholarly: {cover_bg:'linear-gradient(135deg,#1e2a3a 0%,#2d3e50 50%,#3a5068 100%)',end_bg:'radial-gradient(circle at top,#2d3e50 0%,#0f1a25 72%)',accent:'#f0a500',text:'#f0f0f0',dim:'rgba(240,240,240,0.55)',cards:['#3a7abf','#bf5a3a','#5a7a3a','#7a5abf'],panel:'dark',font:{display:"'Lora',Georgia,serif",body:"'IBM Plex Sans',system-ui,sans-serif",mono:"'IBM Plex Mono',monospace"}},
  improving: {cover_bg:'linear-gradient(135deg,#0f0c29 0%,#302b63 50%,#24243e 100%)',end_bg:'radial-gradient(circle at top,#302b63 0%,#0f0c29 72%)',accent:'#a855f7',text:'#f8fafc',dim:'rgba(248,250,252,0.55)',cards:['#9333ea','#06b6d4','#f59e0b','#10b981'],panel:'light',font:{display:"'Crimson Pro',Georgia,serif",body:"'Space Grotesk',system-ui,sans-serif",mono:"'Space Mono',monospace"}},
  meetup:    {cover_bg:'linear-gradient(135deg,#1a5276 0%,#1b4f72 50%,#1a252f 100%)',end_bg:'radial-gradient(circle at top,#1a5276 0%,#1a252f 72%)',accent:'#5dade2',text:'#f8fafc',dim:'rgba(248,250,252,0.55)',cards:['#2980b9','#e57373','#5a9a5a','#f0a050'],panel:'light',font:{display:"'Newsreader',Georgia,serif",body:"'DM Sans',system-ui,sans-serif",mono:"'JetBrains Mono',monospace"}},
  bricks:    {cover_bg:'linear-gradient(135deg,#c0392b 0%,#a93226 50%,#922b21 100%)',end_bg:'radial-gradient(circle at top,#c0392b 0%,#6e2016 72%)',accent:'#f1c40f',text:'#ffffff',dim:'rgba(255,255,255,0.6)',cards:['#c0392b','#d4891a','#8a4a2f','#6a7a8a'],panel:'dark',font:{display:"'Spectral',Georgia,serif",body:"'Archivo',system-ui,sans-serif",mono:"'IBM Plex Mono',monospace"}},
  ivory:     {cover_bg:'linear-gradient(135deg,#f5f3ee 0%,#e8e4da 50%,#d4cdc0 100%)',end_bg:'radial-gradient(circle at top,#e8e4da 0%,#c8c0b0 72%)',accent:'#8b6914',text:'#1a1a1a',dim:'rgba(26,26,26,0.55)',cards:['#6b8f71','#8b6914','#5a6b8a','#8a4a2f'],panel:'dark',font:{display:"'Crimson Pro',Georgia,serif",body:"'Source Sans 3',system-ui,sans-serif",mono:"'IBM Plex Mono',monospace"}},
};

function applyTheme(key){
  var t=THEMES[key]; if(!t)return;
  var s=stage;
  var accentLight=t.accentLight||t.accent||'#b45309';
  var lightBg=t.lightBg||'#f5f3ee';
  [['--cover-bg',t.cover_bg],['--end-bg',t.end_bg],['--accent',t.accent],
   ['--accent-light',accentLight],['--light-bg',lightBg],
   ['--text',t.text],['--dim',t.dim],['--c1',t.cards[0]],['--c2',t.cards[1]],
   ['--c3',t.cards[2]],['--c4',t.cards[3]],
   ['--panel-text',t.panel==='dark'?'#0b0d12':'#fff'],
   ['--font-display',t.font.display],['--font-body',t.font.body],['--font-mono',t.font.mono]
  ].forEach(function(kv){ s.style.setProperty(kv[0],kv[1]); });
}

function detectTheme(){
  var acc=(getComputedStyle(stage).getPropertyValue('--accent')||'').trim();
  for(var k in THEMES){ if((THEMES[k].accent||'').trim()===acc) return k; }
  return 'frankfurt';
}

/* ── Layout helpers ── */
var IMG_PH='<div style="position:absolute;inset:0;background:rgba(255,255,255,.05);display:flex;align-items:center;justify-content:center;border:2px dashed rgba(255,255,255,.15)"><span style="color:#666;font-size:48px">+</span></div>';
function h2t(t){ return '<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">'+t+'</h2>'; }
function ulItems(items){ return items.length?'<ul>'+items.map(function(i){return '<li>'+i+'</li>';}).join('')+'</ul>':''; }
function imgSlot(src){ return '<div class="img-slot">'+(src||IMG_PH)+'</div>'; }

/* ── LAYOUT_BUILDERS ── */
var LAYOUT_BUILDERS = {
  bullets: function(t,items){
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+ulItems(items)+'</div>';
  },
  twocols: function(t,items){
    var m=Math.ceil(items.length/2);
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+'<div class="grid">'+ulItems(items.slice(0,m))+ulItems(items.slice(m))+'</div></div>';
  },
  twocontents: function(t,items){
    var m=Math.ceil(items.length/2);
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+'<div class="pair"><div class="block"><h3>Part 1</h3>'+ulItems(items.slice(0,m))+'</div><div class="block"><h3>Part 2</h3>'+ulItems(items.slice(m))+'</div></div></div>';
  },
  quote: function(t,items){
    return '<div class="body-wrap"><div class="mark">“</div><blockquote><p>'+(items[0]||'Your quote here')+'</p></blockquote><div class="who"><b>'+(items[1]||'— Attribution')+'</b></div></div>';
  },
  splitcontrast: function(t,items){
    var m=Math.ceil(items.length/2);
    function side(tag,h3,its,cls){ return '<div class="side '+cls+'"><div class="tag">'+tag+'</div><h3>'+h3+'</h3>'+ulItems(its)+'</div>'; }
    return '<div class="pair">'+side('Before',t||'Before',items.slice(0,m),'before')+side('After',items[m]||'After',items.slice(m),'after')+'</div>';
  },
  'section-divider': function(t){
    return '<div class="body-wrap"><div class="section-num">Part · 01</div>'+h2t(t)+'</div>';
  },
  steps: function(t,items){
    var s=items.slice(0,5).map(function(it,i){ return '<div class="step"><div class="num">'+(i+1)+'</div><h3>Step '+(i+1)+'</h3><p>'+it+'</p></div>'; }).join('');
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+'<div class="track">'+s+'</div></div>';
  },
  keypoints: function(t,items){
    var s=items.slice(0,6).map(function(it,i){ var n=i<9?'P·0'+(i+1):'P·'+(i+1); return '<div class="pt"><div class="ix">'+n+'</div><div><h3 class="ttl">Point '+(i+1)+'</h3><p class="body">'+it+'</p></div></div>'; }).join('');
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70"><div class="head">'+h2t(t)+'</div><div class="list">'+s+'</div></div>';
  },
  threecol: function(t,items){
    var s=['#01','#02','#03'].map(function(tag,i){ return '<div class="col"><div class="tag">'+tag+'</div><h3>Topic '+(i+1)+'</h3><p>'+(items[i]||'Content here')+'</p></div>'; }).join('');
    return '<div class="body-wrap">'+h2t(t)+'<div class="grid">'+s+'</div></div>';
  },
  conclcards: function(t,items){
    var colors=['var(--c1)','var(--c2)','var(--c3)','var(--c4)']; var dots='',cards='';
    items.slice(0,4).forEach(function(it,i){ var c=colors[i%4]; dots+='<div><div class="dot" style="--card-color:'+c+'"></div></div>'; cards+='<div class="card" style="background:'+c+'"><div class="card-content" data-fit-block style="display:flex;flex-direction:column;gap:10px;height:100%"><div class="num">0'+(i+1)+'</div><h3>Conclusion '+(i+1)+'</h3><p>'+it+'</p></div></div>'; });
    return '<div class="body-wrap">'+h2t(t)+'<div class="timeline"><div class="timeline-line"></div><div class="timeline-dots">'+dots+'</div></div><div class="cards">'+cards+'</div></div>';
  },
  numconcl: function(t,items){
    var s=items.map(function(it,i){ return '<div class="row"><div class="n">0'+(i+1)+'</div><div><h3 class="t">Point '+(i+1)+'</h3><p class="b">'+it+'</p></div></div>'; }).join('');
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+'<div class="list">'+s+'</div></div>';
  },
  grid2x2: function(t,items){
    var s=[0,1,2,3].map(function(i){ return '<div class="cell"><div class="cell-content" data-fit-block style="display:flex;flex-direction:column;gap:12px;height:100%"><div class="dash"></div><h3>Topic '+(i+1)+'</h3><p>'+(items[i]||'Content')+'</p></div></div>'; }).join('');
    return '<div class="body-wrap">'+h2t(t)+'<div class="cells">'+s+'</div></div>';
  },
  rquestion: function(t,items){
    var subs=items.slice(0,3).map(function(it,i){ return '<div class="sub"><div class="lbl">Sub-Q 0'+(i+1)+'</div><p class="q">'+it+'</p></div>'; }).join('');
    return '<div class="body-wrap">'+h2t(t)+'<div class="main-rq"><div class="lbl">Main RQ</div><div class="q">'+(items[0]||'Research question')+'</div></div><div class="subs">'+subs+'</div></div>';
  },
  agenda: function(t,items){
    var s=items.map(function(it){ return '<li><span class="ttl">'+it+'</span></li>'; }).join('');
    return '<div class="body-wrap"><div class="lhs">'+h2t(t)+'</div><ol>'+s+'</ol></div>';
  },
  stat: function(t,items){
    var s=items.slice(0,4).map(function(it){ return '<div class="n"><div class="big">—</div><div class="lbl">'+it+'</div></div>'; }).join('');
    return '<div class="body-wrap"><div class="kicker">'+t+'</div><div class="nums">'+s+'</div></div>';
  },
  editorial: function(t,items){
    return '<div class="body-wrap"><div class="eyebrow"></div>'+h2t(t)+'<p class="lede">'+(items[0]||'Editorial content here')+'</p></div>';
  },
  cmptable: function(t,items){
    var m=Math.ceil(items.length/2);
    var lRows=items.slice(0,m).map(function(i){return '<tr><td>'+i+'</td></tr>';}).join('');
    var rRows=items.slice(m).map(function(i){return '<tr><td>'+i+'</td></tr>';}).join('');
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+'<div class="pair"><table><thead><tr><th>Before</th></tr></thead><tbody>'+lRows+'</tbody></table><table><thead><tr><th>After</th></tr></thead><tbody>'+rRows+'</tbody></table></div></div>';
  },
  tblabove: function(t,items){
    var s=items.map(function(it){return '<tr><td>'+it+'</td></tr>';}).join('');
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+'<table style="width:100%;border-collapse:collapse"><tbody>'+s+'</tbody></table></div>';
  },
  imgleft: function(t,items,imgs){
    return '<div class="body-wrap"><figure>'+imgSlot(imgs[0])+'</figure><div class="rhs" data-fit-block>'+h2t(t)+ulItems(items)+'</div></div>';
  },
  imgright: function(t,items,imgs){
    return '<div class="body-wrap"><div class="lhs" data-fit-block>'+h2t(t)+ulItems(items)+'</div><figure>'+imgSlot(imgs[0])+'</figure></div>';
  },
  imgabove: function(t,items,imgs){
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+'<figure>'+imgSlot(imgs[0])+'</figure>'+ulItems(items)+'</div>';
  },
  imgbelow: function(t,items,imgs){
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+ulItems(items)+'<figure>'+imgSlot(imgs[0])+'</figure></div>';
  },
  twoimgright: function(t,items,imgs){
    return '<div class="body-wrap"><div class="lhs">'+h2t(t)+ulItems(items)+'</div><div class="imgs"><figure>'+imgSlot(imgs[0])+'</figure><figure>'+imgSlot(imgs[1])+'</figure></div></div>';
  },
  twoimgleft: function(t,items,imgs){
    return '<div class="body-wrap"><div class="imgs"><figure>'+imgSlot(imgs[0])+'</figure><figure>'+imgSlot(imgs[1])+'</figure></div><div class="rhs">'+h2t(t)+ulItems(items)+'</div></div>';
  },
  twoimgabove: function(t,items,imgs){
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+'<div class="imgs"><figure>'+imgSlot(imgs[0])+'</figure><figure>'+imgSlot(imgs[1])+'</figure></div>'+ulItems(items)+'</div>';
  },
  twoimgbelow: function(t,items,imgs){
    return '<div class="body-wrap" data-fit-block data-fit-reserve="70">'+h2t(t)+ulItems(items)+'<div class="imgs"><figure>'+imgSlot(imgs[0])+'</figure><figure>'+imgSlot(imgs[1])+'</figure></div></div>';
  },
  imgfull: function(t,items,imgs){
    var bg=imgs[0]?imgs[0].replace(/style="[^"]*"/,'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"'):'<div style="position:absolute;inset:0;background:#222;display:flex;align-items:center;justify-content:center"><span style="color:#666;font-size:64px">+</span></div>';
    return '<div class="img-slot-full">'+bg+'</div><div class="overlay"></div><div class="body-wrap"><div class="accent-bar"></div>'+h2t(t)+'<p>'+(items[0]||'')+'</p></div>';
  },
};

/* ── extractContent(sl) ── */
function extractContent(sl){
  var title=(sl.querySelector('h1,h2')||{}).textContent||'Slide Title';
  var items=Array.from(sl.querySelectorAll('.body-wrap li')).map(function(l){return l.textContent;});
  if(!items.length){
    var bq=sl.querySelector('blockquote p'); var who=sl.querySelector('.who b,.who');
    if(bq) items.push(bq.textContent);
    if(who && who.textContent!==( bq ? bq.textContent : null)) items.push(who.textContent);
  }
  if(!items.length) items=Array.from(sl.querySelectorAll('.body-wrap p,.body-wrap h3')).map(function(el){return el.textContent;}).filter(Boolean);
  var imgs=Array.from(sl.querySelectorAll('.img-slot img,.img-slot-full img')).map(function(img){
    return '<img src="'+img.src+'" alt="'+img.alt+'" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain">';
  });
  return {title:title, items:items.length?items:['Edit this content'], imgs:imgs};
}

/* ── Undo / Redo ── */
function pushUndo(){
  _undoStack.push({h:stage.innerHTML,i:stage._idx});
  if(_undoStack.length>MAX_UNDO) _undoStack.shift();
  _redoStack=[];
  if(typeof scheduleAutosave==='function') scheduleAutosave();
}

function reloadStage() {
  if (stage && stage.querySelectorAll) {
    stage._slides = Array.from(stage.querySelectorAll('section.slide'));
    stage._total = stage._slides.length;
  }
}


function addSlide() {
  if(!stage) return;
  var sl = document.createElement('section');
  sl.className = 'slide';
  sl.innerHTML = '<h2>New Slide</h2>';
  stage.appendChild(sl);
  saveState();
  reloadStage();
  stage._show(stage._total - 1);
}
function duplicateSlide() {
  if(!stage || stage._idx < 0) return;
  var clone = stage._slides[stage._idx].cloneNode(true);
  stage.insertBefore(clone, stage._slides[stage._idx].nextSibling);
  saveState();
  reloadStage();
  stage._show(stage._idx + 1);
}
function deleteSlide() {
  if(!stage || stage._total <= 1 || stage._idx < 0) return;
  stage._slides[stage._idx].remove();
  saveState();
  reloadStage();
  stage._show(Math.max(0, stage._idx - 1));
}
function insertTextBox() {
  if(!stage || stage._idx < 0) return;
  var sl = stage._slides[stage._idx];
  var div = document.createElement('div');
  div.className = 'ed-textbox';
  div.style.position = 'absolute';
  div.style.left = '100px';
  div.style.top = '100px';
  div.style.fontSize = '24px';
  div.innerHTML = 'New Text';
  div.contentEditable = 'true';
  sl.appendChild(div);
  saveState();
}
function insertImage() {
  if(typeof imgPicker !== 'undefined' && imgPicker) {
    imgPicker.dataset.mode = 'insert';
    imgPicker.click();
  }
}
function insertTable() {
  if(!stage || stage._idx < 0) return;
  var sl = stage._slides[stage._idx];
  var table = document.createElement('table');
  table.style.cssText='position:absolute;left:200px;top:200px;border-collapse:collapse;min-width:600px;font-size:36px;color:var(--text,#fff)';
  var rows=['Row 1','Row 2','Row 3'];
  var cols=['Column A','Column B','Column C'];
  var thead='<thead><tr>'+cols.map(function(c){return '<th style="padding:16px 24px;border:2px solid rgba(255,255,255,0.3);background:rgba(255,255,255,0.1);font-weight:600;min-width:160px">'+c+'</th>';}).join('')+'</tr></thead>';
  var tbody='<tbody>'+rows.map(function(r){return '<tr>'+cols.map(function(c,ci){return '<td style="padding:16px 24px;border:1px solid rgba(255,255,255,0.2)">'+r+' / '+String.fromCharCode(65+ci)+'</td>';}).join('')+'</tr>';}).join('')+'</tbody>';
  table.innerHTML=thead+tbody;
  sl.appendChild(table);
  selectEl(table);
  saveState();
}
function addLink() {
  var url = prompt("Enter URL:", "https://");
  if (url) {
    document.execCommand('createLink', false, url);
    saveState();
  }
}
function previewTransition() {
  if(!stage || stage._total <= 1) return;
  var cur = stage._idx;
  if(cur > 0) {
    stage._show(cur - 1);
    setTimeout(function() { stage._show(cur); }, 500);
  } else {
    stage._show(1);
    setTimeout(function() { stage._show(0); }, 500);
  }
}
function tableDeleteRow() {
  if (typeof _selEl !== 'undefined' && _selEl) {
    var tr = _selEl.closest('tr');
    if (tr) { tr.remove(); saveState(); }
  }
}
function tableDeleteCol() {
  if (typeof _selEl !== 'undefined' && _selEl) {
    var td = _selEl.closest('td, th');
    if (td) {
      var idx = Array.from(td.parentNode.children).indexOf(td);
      var table = td.closest('table');
      if (table) {
        table.querySelectorAll('tr').forEach(function(tr) {
          if (tr.children[idx]) tr.children[idx].remove();
        });
        saveState();
      }
    }
  }
}

function applyState(state){
  stage.innerHTML=state.h;
  reloadStage(); stage._show(Math.min(state.i,stage._total-1));
  buildSlideList();
  clearSelection(); clearGroupBox();
  _updateSlRect();
}
function undo(){ if(!_undoStack.length)return; _redoStack.push({h:stage.innerHTML,i:stage._idx}); applyState(_undoStack.pop()); }
function redo(){ if(!_redoStack.length)return; _undoStack.push({h:stage.innerHTML,i:stage._idx}); applyState(_redoStack.pop()); }

/* ── buildUI() ── */
function buildUI(){
  if(!document.getElementById('ed-icons-link')){
    var link = document.createElement('link');
    link.id = 'ed-icons-link';
    link.rel = 'stylesheet';
    link.href = 'https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200';
    document.head.appendChild(link);
  }
  if(!document.getElementById('ed-extra-fonts')){
    var fl=document.createElement('link'); fl.id='ed-extra-fonts'; fl.rel='stylesheet';
    fl.href='https://fonts.googleapis.com/css2?family=Inter:wght@300;400;700&family=Roboto:wght@300;400;700&family=Nunito:wght@300;400;700&family=Raleway:wght@300;400;700&family=Poppins:wght@300;400;700&family=Outfit:wght@300;400;700&family=Figtree:wght@300;400;700&family=Work+Sans:wght@300;400;700&family=Merriweather:wght@300;400;700&family=EB+Garamond:ital,wght@0,400;0,700;1,400&family=Crimson+Pro:ital,wght@0,400;0,700;1,400&family=PT+Serif:ital,wght@0,400;0,700;1,400&family=Oswald:wght@300;400;700&family=Bebas+Neue&family=JetBrains+Mono:wght@400;700&family=Source+Code+Pro:wght@400;700&family=Fira+Code:wght@400;700&family=Noto+Sans:wght@300;400;700&display=swap';
    document.head.appendChild(fl);
  }

  stage=document.querySelector('deck-stage'); if(!stage)return;

  header=document.createElement('div'); header.id='ed-header';

  var tabbar=document.createElement('div'); tabbar.id='ed-tabbar';
  tabbar.innerHTML=[
    '<button class="ed-tab" id="ed-done-btn" style="color:#c43e1c;font-weight:600;"><span class="material-symbols-outlined" style="font-size:16px;margin-right:4px;">close</span>Close</button>',
    '<button class="ed-tab active" id="ed-tab-home" data-tab="home">Home</button>',
    '<button class="ed-tab" id="ed-tab-insert" data-tab="insert">Insert</button>',
    '<button class="ed-tab" id="ed-tab-design" data-tab="design">Design</button>',
    '<button class="ed-tab" id="ed-tab-animate" data-tab="animate">Transitions</button>',
    '<button class="ed-ctx-tab" data-ctx-tab="table-design" data-ctx-group="table" style="display:none;background:#70AD47;color:#fff;border-radius:4px 4px 0 0;padding:4px 10px;font-size:12px;border:none;cursor:pointer;font-weight:600;margin-left:12px">Table Design</button>',
    '<button class="ed-ctx-tab" data-ctx-tab="table-layout" data-ctx-group="table" style="display:none;background:#70AD47;color:#fff;border-radius:4px 4px 0 0;padding:4px 10px;font-size:12px;border:none;cursor:pointer;font-weight:600;margin-left:2px">Table Layout</button>',
    '<button class="ed-ctx-tab" data-ctx-tab="picture" style="display:none;background:#ED7D31;color:#fff;border-radius:4px 4px 0 0;padding:4px 10px;font-size:12px;border:none;cursor:pointer;font-weight:600;margin-left:12px">Picture Format</button>',
    '<button class="ed-ctx-tab" data-ctx-tab="shape" style="display:none;background:#2B579A;color:#fff;border-radius:4px 4px 0 0;padding:4px 10px;font-size:12px;border:none;cursor:pointer;font-weight:600;margin-left:12px">Shape Format</button>',
    '<button id="ed-view-btn" style="margin-left:auto" title="Slide Show"><span class="material-symbols-outlined" style="font-size:14px;margin-right:4px;">slideshow</span>Slide Show</button>',
    '<button id="ed-toggle-panel" title="Toggle Slide Panel"><span class="material-symbols-outlined" style="font-size:14px;">view_sidebar</span></button>',
    '<button id="ed-collapse-ribbon" title="Collapse Ribbon"><span class="material-symbols-outlined" style="font-size:14px;">keyboard_arrow_up</span></button>',
    '<button id="ed-save-btn" style="background:#c43e1c;color:#fff;border-radius:4px;padding:4px 12px;margin-left:8px;font-weight:600;">Save</button>',
  ].join('');
  header.appendChild(tabbar);

  var ribbon=document.createElement('div'); ribbon.id='ed-ribbon';

  var FONTS=[
    ['Montserrat',"'Montserrat',system-ui,sans-serif"],['Open Sans',"'Open Sans',system-ui,sans-serif"],
    ['Inter',"'Inter',system-ui,sans-serif"],['Roboto',"'Roboto',system-ui,sans-serif"],
    ['Poppins',"'Poppins',system-ui,sans-serif"],['Nunito',"'Nunito',system-ui,sans-serif"],
    ['Raleway',"'Raleway',system-ui,sans-serif"],['Outfit',"'Outfit',system-ui,sans-serif"],
    ['Figtree',"'Figtree',system-ui,sans-serif"],['Work Sans',"'Work Sans',system-ui,sans-serif"],
    ['DM Sans',"'DM Sans',system-ui,sans-serif"],['Space Grotesk',"'Space Grotesk',system-ui,sans-serif"],
    ['IBM Plex Sans',"'IBM Plex Sans',system-ui,sans-serif"],['Noto Sans',"'Noto Sans',system-ui,sans-serif"],
    ['Oswald',"'Oswald',system-ui,sans-serif"],['Bebas Neue',"'Bebas Neue',system-ui,sans-serif"],
    ['Playfair Display',"'Playfair Display',Georgia,serif"],['Lora',"'Lora',Georgia,serif"],
    ['Merriweather',"'Merriweather',Georgia,serif"],['EB Garamond',"'EB Garamond',Georgia,serif"],
    ['Crimson Pro',"'Crimson Pro',Georgia,serif"],['PT Serif',"'PT Serif',Georgia,serif"],
    ['Cormorant Garamond',"'Cormorant Garamond',Georgia,serif"],
    ['JetBrains Mono',"'JetBrains Mono',monospace"],['Source Code Pro',"'Source Code Pro',monospace"],
    ['Fira Code',"'Fira Code',monospace"],['IBM Plex Mono',"'IBM Plex Mono',monospace"]
  ];
  var fontOpts=FONTS.map(function(f){return '<option value="'+f[1]+'">'+f[0]+'</option>';}).join('');

  ribbon.innerHTML = `
    <div class="ed-ribbon-group" data-tab="home">
      <div class="ed-group-content" style="flex-wrap:nowrap">
        <button class="ed-btn-big" id="ed-paste-btn" title="Paste">
          <span class="material-symbols-outlined" style="color:#d94214">content_paste</span>Paste
        </button>
        <div class="ed-group-content-col">
          <button class="ed-btn" id="ed-cut-btn" title="Cut"><span class="material-symbols-outlined" style="color:#D94214">content_cut</span><span style="font-size:11px;margin-left:4px">Cut</span></button>
          <button class="ed-btn" id="ed-copy-btn" title="Copy"><span class="material-symbols-outlined" style="color:#D94214">content_copy</span><span style="font-size:11px;margin-left:4px">Copy</span></button>
          <button class="ed-btn" id="ed-format-paint" title="Format Painter"><span class="material-symbols-outlined" style="color:#70AD47">format_paint</span><span style="font-size:11px;margin-left:4px">Format</span></button>
        </div>
      </div>
      <div class="ed-group-title">Clipboard</div>
    </div>

    <div class="ed-ribbon-group" data-tab="home">
      <div class="ed-group-content" style="flex-wrap:nowrap">
        <button class="ed-btn-big" id="ed-add-btn" title="New Slide">
          <span class="material-symbols-outlined" style="color:#d94214">add_to_queue</span>New Slide
        </button>
        <div class="ed-group-content-col">
          <div style="position:relative;">
            <button class="ed-btn" id="ed-layout-btn" title="Layout"><span class="material-symbols-outlined">dashboard</span><span style="font-size:11px;margin-left:4px">Layout</span></button>
            <select id="ed-layout-sel" class="ed-select" style="position:absolute;top:0;left:0;opacity:0;width:100%;height:100%;cursor:pointer;">
              <option value="">-- Layout --</option>
              <optgroup label="── Text ──">
                <option value="bullets">Bullets</option>
                <option value="twocols">Two Columns</option>
                <option value="twocontents">Two Content</option>
                <option value="quote">Quote</option>
                <option value="editorial">Editorial</option>
              </optgroup>
              <optgroup label="── Structure ──">
                <option value="steps">Steps</option>
                <option value="keypoints">Key Points</option>
                <option value="agenda">Agenda</option>
                <option value="section-divider">Section Divider</option>
                <option value="rquestion">Research Q</option>
              </optgroup>
              <optgroup label="── Cards ──">
                <option value="threecol">3 Columns</option>
                <option value="grid2x2">Grid 2×2</option>
                <option value="conclcards">Concl Cards</option>
                <option value="numconcl">Numbered List</option>
                <option value="splitcontrast">Split Contrast</option>
                <option value="stat">Stats</option>
              </optgroup>
              <optgroup label="── Tables ──">
                <option value="cmptable">Compare Table</option>
                <option value="tblabove">Table Below</option>
              </optgroup>
              <optgroup label="── Image ──">
                <option value="imgleft">Image Left</option>
                <option value="imgright">Image Right</option>
                <option value="imgabove">Image Above</option>
                <option value="imgbelow">Image Below</option>
                <option value="twoimgleft">2 Imgs Left</option>
                <option value="twoimgright">2 Imgs Right</option>
                <option value="twoimgabove">2 Imgs Above</option>
                <option value="twoimgbelow">2 Imgs Below</option>
                <option value="imgfull">Full Image</option>
              </optgroup>
            </select>
          </div>
          <button class="ed-btn" id="ed-dup-btn" title="Duplicate"><span class="material-symbols-outlined">file_copy</span><span style="font-size:11px;margin-left:4px">Duplicate</span></button>
          <button class="ed-btn" id="ed-del-btn" title="Delete"><span class="material-symbols-outlined" style="color:#C00000">delete</span><span style="font-size:11px;margin-left:4px">Delete</span></button>
        </div>
      </div>
      <div class="ed-group-title">Slides</div>
    </div>

    <div class="ed-ribbon-group" data-tab="home">
      <div class="ed-group-content-col">
        <div class="ed-group-content" style="justify-content:flex-start">
          <select class="ed-select" id="ed-font-sel" style="width:120px">${fontOpts}</select>
          <input type="number" class="ed-input" id="ed-sz-input" value="24" style="width:40px; margin-left:4px;">
          <button class="ed-btn" id="ed-sz-up" title="Increase Font Size"><span class="material-symbols-outlined">text_increase</span></button>
          <button class="ed-btn" id="ed-sz-dn" title="Decrease Font Size"><span class="material-symbols-outlined">text_decrease</span></button>
          <button class="ed-btn" id="ed-clear-format" title="Clear All Formatting"><span class="material-symbols-outlined">format_clear</span></button>
        </div>
        <div class="ed-group-content" style="justify-content:flex-start">
          <button class="ed-btn" id="ed-bold" title="Bold (Ctrl+B)"><span class="material-symbols-outlined">format_bold</span></button>
          <button class="ed-btn" id="ed-italic" title="Italic (Ctrl+I)"><span class="material-symbols-outlined">format_italic</span></button>
          <button class="ed-btn" id="ed-underline" title="Underline (Ctrl+U)"><span class="material-symbols-outlined">format_underlined</span></button>
          <button class="ed-btn" id="ed-strike" title="Strikethrough"><span class="material-symbols-outlined">strikethrough_s</span></button>
          <button class="ed-btn" onclick="document.execCommand('superscript')" title="Superscript (Ctrl+Shift++)"><span class="material-symbols-outlined">superscript</span></button>
          <button class="ed-btn" onclick="document.execCommand('subscript')" title="Subscript (Ctrl+Shift+-)"><span class="material-symbols-outlined">subscript</span></button>
          <div style="width:4px;"></div>
          <button class="ed-btn" id="ed-text-shadow" title="Text Shadow"><span style="font-weight:bold;font-size:13px;text-shadow:1px 2px 3px rgba(0,0,0,0.6);display:inline-block;padding:0 2px;line-height:1">T</span></button>
          <button class="ed-btn" id="ed-change-case" title="Change Case"><span class="material-symbols-outlined">match_case</span></button>
          <button class="ed-btn" onclick="edToggleColorPicker(this, 'hiliteColor')" title="Highlight Color"><span class="material-symbols-outlined">format_ink_highlighter</span></button>
          <button class="ed-btn" onclick="edToggleColorPicker(this, 'foreColor')" title="Font Color"><span class="material-symbols-outlined">format_color_text</span></button>
        </div>
      </div>
      <div class="ed-group-title">Font</div>
    </div>

    <div class="ed-ribbon-group" data-tab="home">
      <div class="ed-group-content-col">
        <div class="ed-group-content" style="justify-content:flex-start">
          <button class="ed-btn" onclick="edToggleList('ul')" title="Bullets"><span class="material-symbols-outlined">format_list_bulleted</span></button>
          <button class="ed-btn" onclick="edToggleList('ol')" title="Numbering"><span class="material-symbols-outlined">format_list_numbered</span></button>
          <div style="width:4px;"></div>
          <button class="ed-btn" id="ed-outdent" title="Decrease Indent"><span class="material-symbols-outlined">format_indent_decrease</span></button>
          <button class="ed-btn" id="ed-indent" title="Increase Indent"><span class="material-symbols-outlined">format_indent_increase</span></button>
          <button class="ed-btn" id="ed-wrap-text" title="Toggle Word Wrap"><span class="material-symbols-outlined">wrap_text</span></button>
        </div>
        <div class="ed-group-content" style="justify-content:flex-start">
          <button class="ed-btn" id="ed-align-l" title="Align Left"><span class="material-symbols-outlined">format_align_left</span></button>
          <button class="ed-btn" id="ed-align-c" title="Center"><span class="material-symbols-outlined">format_align_center</span></button>
          <button class="ed-btn" id="ed-align-r" title="Align Right"><span class="material-symbols-outlined">format_align_right</span></button>
          <button class="ed-btn" id="ed-align-j" title="Justify"><span class="material-symbols-outlined">format_align_justify</span></button>
        </div>
      </div>
      <div class="ed-group-title">Paragraph</div>
    </div>

    <div class="ed-ribbon-group" data-tab="home">
      <div class="ed-group-content">
        <button class="ed-btn-big" id="ed-edit-text-btn" title="Text Edit Mode">
          <span class="material-symbols-outlined" style="color:#2B579A">edit_document</span>Text Mode
        </button>
        <div class="ed-group-content-col">
          <button class="ed-btn" id="ed-move-up-btn" title="Move Up"><span class="material-symbols-outlined">arrow_upward</span><span style="font-size:11px;margin-left:4px">Move Up</span></button>
          <button class="ed-btn" id="ed-move-dn-btn" title="Move Down"><span class="material-symbols-outlined">arrow_downward</span><span style="font-size:11px;margin-left:4px">Move Down</span></button>
          <button class="ed-btn" id="ed-link-btn" title="Add Link"><span class="material-symbols-outlined" style="color:#0563C1">link</span><span style="font-size:11px;margin-left:4px">Link</span></button>
        </div>
      </div>
      <div class="ed-group-title">Editing</div>
    </div>

    <div class="ed-ribbon-group" data-tab="home">
      <div class="ed-group-content" style="gap:6px;align-items:flex-start">
        <div class="ed-group-content-col">
          <div class="ed-group-content">
            <button class="ed-btn" id="ed-bring-front" title="Bring to Front"><span class="material-symbols-outlined" style="color:#ED7D31">flip_to_front</span><span style="font-size:10px;margin-left:2px">Front</span></button>
            <button class="ed-btn" id="ed-bring-fwd" title="Bring Forward"><span class="material-symbols-outlined" style="color:#ED7D31">expand_less</span><span style="font-size:10px;margin-left:2px">Fwd</span></button>
          </div>
          <div class="ed-group-content">
            <button class="ed-btn" id="ed-send-back" title="Send to Back"><span class="material-symbols-outlined" style="color:#ED7D31">flip_to_back</span><span style="font-size:10px;margin-left:2px">Back</span></button>
            <button class="ed-btn" id="ed-send-bwd" title="Send Backward"><span class="material-symbols-outlined" style="color:#ED7D31">expand_more</span><span style="font-size:10px;margin-left:2px">Bwd</span></button>
          </div>
          <div class="ed-group-content" style="margin-top:2px">
            <button class="ed-btn" id="ed-group" title="Group (Ctrl+G)"><span class="material-symbols-outlined" style="color:#5B9BD5">group_work</span><span style="font-size:10px;margin-left:2px">Group</span></button>
            <button class="ed-btn" id="ed-ungroup" title="Ungroup (Ctrl+Shift+G)"><span class="material-symbols-outlined">splitscreen</span><span style="font-size:10px;margin-left:2px">Ungrp</span></button>
            <button class="ed-btn" id="ed-flip-h" title="Flip Horizontal"><span class="material-symbols-outlined">flip</span><span style="font-size:10px;margin-left:2px">H</span></button>
            <button class="ed-btn" id="ed-flip-v" title="Flip Vertical"><span class="material-symbols-outlined" style="transform:rotate(90deg)">flip</span><span style="font-size:10px;margin-left:2px">V</span></button>
            <button class="ed-btn" id="ed-free-layout" title="Snapshot current positions as independent absolute layout — elements become freely moveable"><span class="material-symbols-outlined" style="color:#70ad47">lock_open</span><span style="font-size:10px;margin-left:2px">Free</span></button>
          </div>
        </div>
        <div class="ed-group-content-col" style="gap:2px">
          <div style="display:flex;gap:2px">
            <button class="ed-btn" id="ed-obj-al" title="Align Left" style="min-width:22px;padding:2px"><span class="material-symbols-outlined" style="font-size:14px">align_horizontal_left</span></button>
            <button class="ed-btn" id="ed-obj-ac" title="Center H" style="min-width:22px;padding:2px"><span class="material-symbols-outlined" style="font-size:14px">align_horizontal_center</span></button>
            <button class="ed-btn" id="ed-obj-ar" title="Align Right" style="min-width:22px;padding:2px"><span class="material-symbols-outlined" style="font-size:14px">align_horizontal_right</span></button>
          </div>
          <div style="display:flex;gap:2px">
            <button class="ed-btn" id="ed-obj-at" title="Align Top" style="min-width:22px;padding:2px"><span class="material-symbols-outlined" style="font-size:14px">align_vertical_top</span></button>
            <button class="ed-btn" id="ed-obj-am" title="Center V" style="min-width:22px;padding:2px"><span class="material-symbols-outlined" style="font-size:14px">align_vertical_center</span></button>
            <button class="ed-btn" id="ed-obj-ab" title="Align Bottom" style="min-width:22px;padding:2px"><span class="material-symbols-outlined" style="font-size:14px">align_vertical_bottom</span></button>
          </div>
          <div style="display:flex;gap:4px;align-items:center;margin-top:2px">
            <span style="font-size:10px;color:#aaa">Opacity</span>
            <input type="range" id="ed-opacity" min="10" max="100" value="100" style="width:60px;height:4px;accent-color:#c43e1c">
          </div>
        </div>
      </div>
      <div class="ed-group-title">Arrange</div>
    </div>

    <!-- INSERT TAB -->
    <div class="ed-ribbon-group" data-tab="insert" style="display:none">
      <div class="ed-group-content">
        <button class="ed-btn-big" id="ed-add-textbox" title="Text Box"><span class="material-symbols-outlined">text_fields</span>Text Box</button>
        <button class="ed-btn-big" id="ed-add-img" title="Image"><span class="material-symbols-outlined">image</span>Image</button>
        <button class="ed-btn-big" id="ed-add-table" title="Table"><span class="material-symbols-outlined">table</span>Table</button>
        <button class="ed-btn-big" id="ed-add-bullet" title="List"><span class="material-symbols-outlined">format_list_bulleted</span>List</button>
      </div>
      <div class="ed-group-title">Objects</div>
    </div>
    <div class="ed-ribbon-group" data-tab="insert" style="display:none">
      <div class="ed-group-content">
        <button class="ed-btn-big ed-shape-btn" data-shape="rect" title="Rectangle"><span class="material-symbols-outlined">rectangle</span>Rect</button>
        <button class="ed-btn-big ed-shape-btn" data-shape="ellipse" title="Ellipse"><span class="material-symbols-outlined">circle</span>Ellipse</button>
        <button class="ed-btn-big ed-shape-btn" data-shape="triangle" title="Triangle"><span class="material-symbols-outlined">change_history</span>Triangle</button>
        <button class="ed-btn-big ed-shape-btn" data-shape="arrow" title="Arrow"><span class="material-symbols-outlined">arrow_forward</span>Arrow</button>
        <button class="ed-btn-big ed-shape-btn" data-shape="line" title="Line"><span class="material-symbols-outlined">horizontal_rule</span>Line</button>
      </div>
      <div class="ed-group-title">Shapes</div>
    </div>

    <!-- DESIGN TAB -->
    <div class="ed-ribbon-group" data-tab="design" style="display:none">
      <div class="ed-group-content" id="ed-theme-swatches"></div>
      <div class="ed-group-title">Theme</div>
    </div>
    <div class="ed-ribbon-group" data-tab="design" style="display:none">
      <div class="ed-group-content-col">
        <label style="font-size:11px;color:#aaa;margin-bottom:4px">Slide Background</label>
        <input type="color" class="ed-input" style="width:40px;height:26px;cursor:pointer" id="ed-bg-color" value="#1a1a2e">
      </div>
      <div class="ed-group-title">Background</div>
    </div>

    <!-- ANIMATE TAB -->
    <div class="ed-ribbon-group" data-tab="animate" style="display:none">
      <div class="ed-group-content-col">
        <div style="display:flex;gap:4px;align-items:center;margin-bottom:4px">
          <label style="font-size:11px;color:#aaa;white-space:nowrap">Transition</label>
          <select class="ed-select" id="ed-transition-sel">
            <option value="none">None</option>
            <option value="fade">Fade</option>
            <option value="slide-left">Slide Left</option>
            <option value="slide-right">Slide Right</option>
            <option value="slide-up">Slide Up</option>
            <option value="slide-down">Slide Down</option>
            <option value="zoom">Zoom</option>
            <option value="flip">Flip</option>
          </select>
        </div>
        <div style="display:flex;gap:4px;align-items:center;margin-bottom:4px">
          <label style="font-size:11px;color:#aaa;white-space:nowrap">Duration</label>
          <input type="number" class="ed-input" id="ed-transition-dur" min="0.1" max="3" step="0.05" value="0.45" style="width:56px" title="Transition duration (seconds)">
          <span style="font-size:11px;color:#aaa">s</span>
        </div>
        <div style="display:flex;gap:4px">
          <button class="ed-btn" id="ed-trans-preview" title="Preview transition"><span class="material-symbols-outlined">play_arrow</span><span style="font-size:11px;margin-left:4px">Preview</span></button>
          <button class="ed-btn" id="ed-trans-all" title="Apply this transition to all slides"><span class="material-symbols-outlined">done_all</span><span style="font-size:11px;margin-left:4px">All</span></button>
        </div>
      </div>
      <div class="ed-group-title">Slide Transition</div>
    </div>
    <div class="ed-ribbon-group" data-tab="animate" style="display:none">
      <div class="ed-group-content-col">
        <div style="display:flex;gap:4px;align-items:center;margin-bottom:4px">
          <label style="font-size:11px;color:#aaa;white-space:nowrap">Animation</label>
          <select class="ed-select" id="ed-anim-sel">
            <option value="none">None</option>
            <option value="fade-in">Fade In</option>
            <option value="fly-left">Fly Left</option>
            <option value="fly-right">Fly Right</option>
            <option value="fly-up">Fly Up</option>
            <option value="fly-down">Fly Down</option>
            <option value="zoom-in">Zoom In</option>
            <option value="zoom-out">Zoom Out</option>
            <option value="bounce">Bounce</option>
            <option value="rotate">Rotate</option>
            <option value="flip-h">Flip H</option>
            <option value="flip-v">Flip V</option>
            <option value="wipe">Wipe</option>
          </select>
        </div>
        <div style="display:flex;gap:4px;align-items:center;margin-bottom:4px">
          <label style="font-size:11px;color:#aaa;white-space:nowrap">Dur</label>
          <input type="number" class="ed-input" id="ed-anim-dur" min="0.1" max="4" step="0.05" value="0.5" style="width:50px" title="Animation duration (seconds)">
          <label style="font-size:11px;color:#aaa;white-space:nowrap">Delay</label>
          <input type="number" class="ed-input" id="ed-anim-delay" min="0" max="10" step="0.05" value="0" style="width:50px" title="Start delay (seconds)">
          <label style="font-size:11px;color:#aaa;white-space:nowrap">Order</label>
          <input type="number" class="ed-input" id="ed-anim-order" min="0" max="99" step="1" value="0" style="width:44px" title="Build order (lower plays first)">
        </div>
        <div style="display:flex;gap:4px">
          <button class="ed-btn" id="ed-anim-apply"><span class="material-symbols-outlined">animation</span><span style="font-size:11px;margin-left:4px">Apply</span></button>
          <button class="ed-btn" id="ed-anim-preview" title="Preview this slide's animations"><span class="material-symbols-outlined">play_arrow</span><span style="font-size:11px;margin-left:4px">Preview</span></button>
          <button class="ed-btn" id="ed-anim-remove" title="Remove animation from selection"><span class="material-symbols-outlined">close</span></button>
        </div>
      </div>
      <div class="ed-group-title">Object Animation</div>
    </div>

    <!-- ── TABLE DESIGN TAB ── -->
    <div class="ed-ribbon-group" data-ctx-tab="table-design" style="display:none">
      <div class="ed-group-content-col">
        <label style="font-size:10px;color:#605E5C;font-weight:600;margin-bottom:2px">Style Options</label>
        <label class="ed-chk"><input type="checkbox" id="ed-tbl-opt-header" checked> Header Row</label>
        <label class="ed-chk"><input type="checkbox" id="ed-tbl-opt-total"> Total Row</label>
        <label class="ed-chk"><input type="checkbox" id="ed-tbl-opt-banded" checked> Banded Rows</label>
        <label class="ed-chk"><input type="checkbox" id="ed-tbl-opt-firstcol"> First Col</label>
        <label class="ed-chk"><input type="checkbox" id="ed-tbl-opt-lastcol"> Last Col</label>
      </div>
      <div class="ed-group-title">Style Options</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="table-design" style="display:none">
      <div class="ed-group-content-col" style="gap:4px">
        <div class="ed-group-content" style="gap:3px">
          <div class="ed-tbl-style-swatch" data-hdr="#2B579A" data-row="rgba(43,87,154,0.12)" title="Blue" style="background:linear-gradient(to bottom,#2B579A 40%,rgba(43,87,154,0.12) 40%)"></div>
          <div class="ed-tbl-style-swatch" data-hdr="#70AD47" data-row="rgba(112,173,71,0.12)" title="Green" style="background:linear-gradient(to bottom,#70AD47 40%,rgba(112,173,71,0.12) 40%)"></div>
          <div class="ed-tbl-style-swatch" data-hdr="#ED7D31" data-row="rgba(237,125,49,0.12)" title="Orange" style="background:linear-gradient(to bottom,#ED7D31 40%,rgba(237,125,49,0.12) 40%)"></div>
          <div class="ed-tbl-style-swatch" data-hdr="#C00000" data-row="rgba(192,0,0,0.12)" title="Red" style="background:linear-gradient(to bottom,#C00000 40%,rgba(192,0,0,0.12) 40%)"></div>
          <div class="ed-tbl-style-swatch" data-hdr="#7030A0" data-row="rgba(112,48,160,0.12)" title="Purple" style="background:linear-gradient(to bottom,#7030A0 40%,rgba(112,48,160,0.12) 40%)"></div>
          <div class="ed-tbl-style-swatch" data-hdr="#404040" data-row="rgba(64,64,64,0.08)" title="Gray" style="background:linear-gradient(to bottom,#404040 40%,rgba(64,64,64,0.08) 40%)"></div>
        </div>
        <div class="ed-group-content" style="gap:4px">
          <span style="font-size:10px">Header:</span>
          <input type="color" id="ed-tbl-header-color" value="#2B579A" style="width:24px;height:20px;cursor:pointer;border:1px solid #C8C6C4;border-radius:2px">
          <span style="font-size:10px">Cell:</span>
          <input type="color" id="ed-tbl-cell-color" value="#ffffff" style="width:24px;height:20px;cursor:pointer;border:1px solid #C8C6C4;border-radius:2px">
        </div>
      </div>
      <div class="ed-group-title">Table Styles</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="table-design" style="display:none">
      <div class="ed-group-content-col" style="gap:4px">
        <div class="ed-group-content" style="gap:4px">
          <span style="font-size:10px">Color:</span>
          <input type="color" id="ed-tbl-border-color" value="#C8C6C4" style="width:24px;height:20px;cursor:pointer;border:1px solid #C8C6C4;border-radius:2px">
          <select class="ed-select" id="ed-tbl-border-width" style="width:44px">
            <option value="1">1pt</option><option value="2" selected>2pt</option>
            <option value="3">3pt</option><option value="4">4pt</option>
          </select>
        </div>
        <div class="ed-group-content" style="gap:2px">
          <button class="ed-btn" id="ed-tbl-border-all" style="font-size:10px;padding:2px 4px">All</button>
          <button class="ed-btn" id="ed-tbl-border-none" style="font-size:10px;padding:2px 4px">None</button>
          <button class="ed-btn" id="ed-tbl-border-outside" style="font-size:10px;padding:2px 4px">Outside</button>
        </div>
      </div>
      <div class="ed-group-title">Draw Borders</div>
    </div>

    <!-- ── TABLE LAYOUT TAB ── -->
    <div class="ed-ribbon-group" data-ctx-tab="table-layout" style="display:none">
      <div class="ed-group-content-col">
        <button class="ed-btn" id="ed-tbl-del-table"><span class="material-symbols-outlined" style="color:#C00000">delete</span><span style="font-size:10px;margin-left:2px">Delete Table</span></button>
        <button class="ed-btn" id="ed-tbl-select-all"><span class="material-symbols-outlined">select_all</span><span style="font-size:10px;margin-left:2px">Select All</span></button>
      </div>
      <div class="ed-group-title">Table</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="table-layout" style="display:none">
      <div class="ed-group-content-col">
        <div class="ed-group-content" style="gap:2px">
          <button class="ed-btn" id="ed-tbl-row-above" style="font-size:9px;padding:2px 4px">↑ Row Above</button>
          <button class="ed-btn" id="ed-tbl-row-below" style="font-size:9px;padding:2px 4px">↓ Row Below</button>
        </div>
        <div class="ed-group-content" style="gap:2px">
          <button class="ed-btn" id="ed-tbl-col-left" style="font-size:9px;padding:2px 4px">← Col Left</button>
          <button class="ed-btn" id="ed-tbl-col-right" style="font-size:9px;padding:2px 4px">Col Right →</button>
        </div>
        <div class="ed-group-content" style="gap:2px">
          <button class="ed-btn" id="ed-tbl-del-row" style="font-size:9px;padding:2px 4px;color:#C00000">Del Row</button>
          <button class="ed-btn" id="ed-tbl-del-col" style="font-size:9px;padding:2px 4px;color:#C00000">Del Col</button>
        </div>
      </div>
      <div class="ed-group-title">Rows & Columns</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="table-layout" style="display:none">
      <div class="ed-group-content-col">
        <button class="ed-btn" id="ed-tbl-merge"><span class="material-symbols-outlined">merge</span><span style="font-size:10px;margin-left:2px">Merge Cells</span></button>
        <button class="ed-btn" id="ed-tbl-split"><span class="material-symbols-outlined">call_split</span><span style="font-size:10px;margin-left:2px">Split Cell</span></button>
      </div>
      <div class="ed-group-title">Merge</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="table-layout" style="display:none">
      <div class="ed-group-content-col">
        <div class="ed-group-content" style="gap:3px;margin-bottom:2px">
          <span style="font-size:10px;min-width:12px">H:</span>
          <input type="number" class="ed-input" id="ed-tbl-row-h" value="40" style="width:44px">
          <button class="ed-btn" id="ed-tbl-dist-rows" style="font-size:9px;padding:2px 3px">Dist</button>
        </div>
        <div class="ed-group-content" style="gap:3px">
          <span style="font-size:10px;min-width:12px">W:</span>
          <input type="number" class="ed-input" id="ed-tbl-col-w" value="160" style="width:44px">
          <button class="ed-btn" id="ed-tbl-dist-cols" style="font-size:9px;padding:2px 3px">Dist</button>
        </div>
      </div>
      <div class="ed-group-title">Cell Size</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="table-layout" style="display:none">
      <div class="ed-group-content-col">
        <div class="ed-group-content" style="gap:2px;margin-bottom:2px">
          <button class="ed-btn" id="ed-cell-al"><span class="material-symbols-outlined" style="font-size:14px">format_align_left</span></button>
          <button class="ed-btn" id="ed-cell-ac"><span class="material-symbols-outlined" style="font-size:14px">format_align_center</span></button>
          <button class="ed-btn" id="ed-cell-ar"><span class="material-symbols-outlined" style="font-size:14px">format_align_right</span></button>
        </div>
        <div class="ed-group-content" style="gap:2px">
          <button class="ed-btn" id="ed-cell-vt"><span class="material-symbols-outlined" style="font-size:14px">vertical_align_top</span></button>
          <button class="ed-btn" id="ed-cell-vm"><span class="material-symbols-outlined" style="font-size:14px">vertical_align_center</span></button>
          <button class="ed-btn" id="ed-cell-vb"><span class="material-symbols-outlined" style="font-size:14px">vertical_align_bottom</span></button>
        </div>
      </div>
      <div class="ed-group-title">Alignment</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="table-layout" style="display:none">
      <div class="ed-group-content-col">
        <div class="ed-group-content" style="gap:3px;margin-bottom:2px">
          <span style="font-size:10px">W:</span>
          <input type="number" class="ed-input" id="ed-ctx-width" style="width:60px">
        </div>
        <div class="ed-group-content" style="gap:3px">
          <span style="font-size:10px">H:</span>
          <input type="number" class="ed-input" id="ed-ctx-height" style="width:60px">
        </div>
      </div>
      <div class="ed-group-title">Table Size</div>
    </div>

    <!-- ── PICTURE FORMAT TAB ── -->
    <div class="ed-ribbon-group" data-ctx-tab="picture" style="display:none">
      <div class="ed-group-content-col">
        <button class="ed-btn" id="ed-img-replace"><span class="material-symbols-outlined">image</span><span style="font-size:10px;margin-left:2px">Edit Picture</span></button>
        <button class="ed-btn" id="ed-img-remove-bg"><span class="material-symbols-outlined">hide_image</span><span style="font-size:10px;margin-left:2px">Remove Bg</span></button>
      </div>
      <div class="ed-group-title">Edit</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="picture" style="display:none">
      <div class="ed-group-content-col" style="gap:3px">
        <div class="ed-group-content" style="gap:4px">
          <span style="font-size:10px;min-width:60px">Brightness</span>
          <input type="range" id="ed-img-brightness" min="0" max="200" value="100" style="width:80px;accent-color:#ED7D31">
        </div>
        <div class="ed-group-content" style="gap:4px">
          <span style="font-size:10px;min-width:60px">Contrast</span>
          <input type="range" id="ed-img-contrast" min="0" max="200" value="100" style="width:80px;accent-color:#ED7D31">
        </div>
        <div class="ed-group-content" style="gap:4px">
          <span style="font-size:10px;min-width:60px">Opacity</span>
          <input type="range" id="ed-img-opacity" min="0" max="100" value="100" style="width:80px;accent-color:#ED7D31">
        </div>
        <div class="ed-group-content" style="gap:4px">
          <select class="ed-select" id="ed-img-fit" style="width:76px">
            <option value="contain">Contain</option>
            <option value="cover">Cover</option>
            <option value="fill">Fill</option>
          </select>
          <button class="ed-btn" id="ed-img-reset" style="font-size:10px;padding:2px 4px">Reset</button>
        </div>
      </div>
      <div class="ed-group-title">Adjust</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="picture" style="display:none">
      <div class="ed-group-content-col" style="gap:3px">
        <div class="ed-group-content" style="gap:2px">
          <button class="ed-btn" id="ed-img-border-none" style="font-size:10px;padding:2px 4px">No Border</button>
          <button class="ed-btn" id="ed-img-border-thin" style="font-size:10px;padding:2px 4px">Thin</button>
          <button class="ed-btn" id="ed-img-border-thick" style="font-size:10px;padding:2px 4px">Thick</button>
        </div>
        <div class="ed-group-content" style="gap:2px">
          <button class="ed-btn" id="ed-img-round" style="font-size:10px;padding:2px 4px">Rounded</button>
          <button class="ed-btn" id="ed-img-shadow" style="font-size:10px;padding:2px 4px">Shadow</button>
        </div>
      </div>
      <div class="ed-group-title">Picture Styles</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="picture" style="display:none">
      <div class="ed-group-content-col">
        <div class="ed-group-content" style="gap:2px;margin-bottom:2px">
          <button class="ed-btn" id="ed-img-front"><span class="material-symbols-outlined" style="font-size:14px">flip_to_front</span></button>
          <button class="ed-btn" id="ed-img-back"><span class="material-symbols-outlined" style="font-size:14px">flip_to_back</span></button>
        </div>
        <div class="ed-group-content" style="gap:2px">
          <button class="ed-btn" id="ed-img-al"><span class="material-symbols-outlined" style="font-size:14px">align_horizontal_left</span></button>
          <button class="ed-btn" id="ed-img-ac"><span class="material-symbols-outlined" style="font-size:14px">align_horizontal_center</span></button>
          <button class="ed-btn" id="ed-img-ar"><span class="material-symbols-outlined" style="font-size:14px">align_horizontal_right</span></button>
        </div>
      </div>
      <div class="ed-group-title">Arrange</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="picture" style="display:none">
      <div class="ed-group-content-col">
        <div class="ed-group-content" style="gap:3px;margin-bottom:2px">
          <span style="font-size:10px">W:</span>
          <input type="number" class="ed-input" id="ed-ctx-width-pic" style="width:60px">
        </div>
        <div class="ed-group-content" style="gap:3px">
          <span style="font-size:10px">H:</span>
          <input type="number" class="ed-input" id="ed-ctx-height-pic" style="width:60px">
        </div>
      </div>
      <div class="ed-group-title">Size</div>
    </div>

    <!-- ── SHAPE FORMAT TAB ── -->
    <div class="ed-ribbon-group" data-ctx-tab="shape" style="display:none">
      <div class="ed-group-content">
        <button class="ed-btn-big ed-shape-btn" data-shape="rect"><span class="material-symbols-outlined">rectangle</span>Rect</button>
        <button class="ed-btn-big ed-shape-btn" data-shape="ellipse"><span class="material-symbols-outlined">circle</span>Circle</button>
        <button class="ed-btn-big ed-shape-btn" data-shape="triangle"><span class="material-symbols-outlined">change_history</span>Triangle</button>
        <button class="ed-btn-big ed-shape-btn" data-shape="arrow"><span class="material-symbols-outlined">arrow_forward</span>Arrow</button>
      </div>
      <div class="ed-group-title">Insert Shapes</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="shape" style="display:none">
      <div class="ed-group-content-col" style="gap:3px">
        <div class="ed-group-content" style="gap:4px">
          <span style="font-size:10px;min-width:42px">Fill</span>
          <input type="color" id="ed-shape-fill" value="#2B579A" style="width:28px;height:20px;cursor:pointer;border:1px solid #C8C6C4;border-radius:2px">
          <button class="ed-btn" id="ed-shape-no-fill" style="font-size:9px;padding:2px 3px">None</button>
        </div>
        <div class="ed-group-content" style="gap:4px">
          <span style="font-size:10px;min-width:42px">Outline</span>
          <input type="color" id="ed-shape-stroke" value="#ffffff" style="width:28px;height:20px;cursor:pointer;border:1px solid #C8C6C4;border-radius:2px">
          <input type="number" class="ed-input" id="ed-shape-sw" value="2" min="0" max="20" style="width:30px">
        </div>
        <div class="ed-group-content" style="gap:4px">
          <span style="font-size:10px;min-width:42px">Dash</span>
          <select class="ed-select" id="ed-stroke-dash" style="width:70px">
            <option value="">Solid</option>
            <option value="8,4">Dashed</option>
            <option value="2,4">Dotted</option>
            <option value="8,4,2,4">Dash-Dot</option>
          </select>
        </div>
        <div class="ed-group-content" style="gap:4px">
          <span style="font-size:10px;min-width:42px">Radius</span>
          <input type="number" class="ed-input" id="ed-border-radius" value="0" min="0" max="100" style="width:30px">
          <button class="ed-btn" id="ed-shadow-btn" style="font-size:9px;padding:2px 3px">Shadow</button>
        </div>
      </div>
      <div class="ed-group-title">Shape Styles</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="shape" style="display:none">
      <div class="ed-group-content-col">
        <div class="ed-group-content" style="gap:2px;margin-bottom:2px">
          <button class="ed-btn" id="ed-shape-front"><span class="material-symbols-outlined" style="font-size:14px">flip_to_front</span></button>
          <button class="ed-btn" id="ed-shape-back"><span class="material-symbols-outlined" style="font-size:14px">flip_to_back</span></button>
          <button class="ed-btn" id="ed-shape-group"><span class="material-symbols-outlined" style="font-size:14px">group_work</span></button>
          <button class="ed-btn" id="ed-shape-ungroup"><span class="material-symbols-outlined" style="font-size:14px">splitscreen</span></button>
        </div>
        <div class="ed-group-content" style="gap:2px">
          <button class="ed-btn" id="ed-shape-al"><span class="material-symbols-outlined" style="font-size:14px">align_horizontal_left</span></button>
          <button class="ed-btn" id="ed-shape-ac"><span class="material-symbols-outlined" style="font-size:14px">align_horizontal_center</span></button>
          <button class="ed-btn" id="ed-shape-ar"><span class="material-symbols-outlined" style="font-size:14px">align_horizontal_right</span></button>
          <button class="ed-btn" id="ed-flip-h-ctx"><span class="material-symbols-outlined" style="font-size:14px">flip</span></button>
          <button class="ed-btn" id="ed-flip-v-ctx"><span class="material-symbols-outlined" style="transform:rotate(90deg);font-size:14px">flip</span></button>
        </div>
      </div>
      <div class="ed-group-title">Arrange</div>
    </div>
    <div class="ed-ribbon-group" data-ctx-tab="shape" style="display:none">
      <div class="ed-group-content-col">
        <div class="ed-group-content" style="gap:3px;margin-bottom:2px">
          <span style="font-size:10px">W:</span>
          <input type="number" class="ed-input" id="ed-ctx-width-shape" style="width:60px">
        </div>
        <div class="ed-group-content" style="gap:3px">
          <span style="font-size:10px">H:</span>
          <input type="number" class="ed-input" id="ed-ctx-height-shape" style="width:60px">
        </div>
      </div>
      <div class="ed-group-title">Size</div>
    </div>
  `;

  header.appendChild(ribbon);
  
  // Color Picker popup
  var colorPicker = document.createElement('div');
  colorPicker.id = 'ed-color-picker';
  colorPicker.className = 'ed-color-picker';
  
  // PPTX-like color grid
  var baseColors = ['#ffffff', '#000000', '#eeece1', '#1f497d', '#4f81bd', '#c0504d', '#9bbb59', '#8064a2', '#4bacc6', '#f79646'];
  var stdColors = ['#c00000', '#ff0000', '#ffc000', '#ffff00', '#92d050', '#00b050', '#00b0f0', '#0070c0', '#002060', '#7030a0'];
  
  var html = '<div class="ed-color-section"><div class="ed-color-title">Theme Colors</div><div class="ed-color-grid">';
  baseColors.forEach(c => { html += '<div class="ed-color-swatch" style="background:'+c+';" data-color="'+c+'"></div>'; });
  html += '</div><div class="ed-color-grid" style="margin-top:4px;">';
  // 5 rows of shade variations
  var shades = [
    [0.9, 0.5, -0.1, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8],
    [0.75, 0.35, -0.25, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6],
    [0.5, 0.25, -0.5, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],
    [0.25, 0.15, -0.75, -0.25, -0.25, -0.25, -0.25, -0.25, -0.25, -0.25],
    [0.1, 0.05, -0.9, -0.5, -0.5, -0.5, -0.5, -0.5, -0.5, -0.5]
  ];
  shades.forEach(row => {
    baseColors.forEach((c, i) => {
      // Fake shades for now, just varying opacity for simplicity in this demo, real PPTX mixes with white/black
      html += '<div class="ed-color-swatch" style="background:'+c+'; opacity:'+Math.abs(row[i])+';" data-color="'+c+'"></div>';
    });
  });
  html += '</div></div>';
  
  html += '<div class="ed-color-section"><div class="ed-color-title">Standard Colors</div><div class="ed-color-grid">';
  stdColors.forEach(c => { html += '<div class="ed-color-swatch" style="background:'+c+';" data-color="'+c+'"></div>'; });
  html += '</div></div>';
  
  // Custom color section appended to color picker
  html += '<div class="ed-color-section" style="margin-top:8px"><div class="ed-color-title">Custom Color</div><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px"><input type="color" id="ed-custom-color-native" value="#ff0000" style="width:32px;height:28px;cursor:pointer;border:1px solid #C8C6C4;border-radius:2px;padding:1px"><input type="text" id="ed-custom-color-hex" placeholder="#RRGGBB" maxlength="7" style="width:72px;font-size:12px;border:1px solid #C8C6C4;border-radius:2px;padding:2px 4px;font-family:monospace"><button id="ed-custom-color-apply" style="font-size:11px;padding:3px 8px;background:#2B579A;color:#fff;border:none;border-radius:2px;cursor:pointer">Apply</button><button id="ed-eyedropper" title="Pick color from screen" style="font-size:11px;padding:3px 6px;background:#605E5C;color:#fff;border:none;border-radius:2px;cursor:pointer;display:flex;align-items:center;gap:2px"><span class="material-symbols-outlined" style="font-size:14px">colorize</span></button></div></div>';

  colorPicker.innerHTML = html;
  document.body.appendChild(colorPicker);

  // Prevent focus loss from contenteditable when clicking inside color picker
  colorPicker.addEventListener('mousedown', function(e){ e.preventDefault(); });

  function applyPickedColor(color) {
    if(_savedSel) {
      var s=window.getSelection(); s.removeAllRanges(); s.addRange(_savedSel);
    }
    document.execCommand(_colorCmd, false, color);
    colorPicker.classList.remove('active');
  }
  window.applyPickedColor = applyPickedColor;

  window.edToggleColorPicker = function(btn, cmd) {
    _colorCmd = cmd;
    var sel=window.getSelection();
    _savedSel=(sel&&sel.rangeCount)?sel.getRangeAt(0).cloneRange():null;
    var rect = btn.getBoundingClientRect();
    colorPicker.style.top = (rect.bottom + 4) + 'px';
    colorPicker.style.left = rect.left + 'px';
    colorPicker.classList.add('active');
    colorPicker.dataset.cmd = cmd;

    // Auto-close when clicking outside
    var closeColorPicker = function(e) {
      if(!colorPicker.contains(e.target) && !btn.contains(e.target)) {
        colorPicker.classList.remove('active');
        document.removeEventListener('click', closeColorPicker);
      }
    };
    setTimeout(function(){ document.addEventListener('click', closeColorPicker); }, 10);
  };

  colorPicker.addEventListener('click', function(e) {
    if(e.target.classList.contains('ed-color-swatch')) {
      applyPickedColor(e.target.dataset.color);
    }
  });
  
  // Full screen toggle
  var btnView = document.getElementById('ed-view-btn');
  if(btnView) {
    btnView.addEventListener('click', function() {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(err => {
          console.log("Error attempting to enable fullscreen:", err.message);
        });
      } else {
        document.exitFullscreen();
      }
    });
  }
  
  document.addEventListener('fullscreenchange', function() {
    var st = document.querySelector('deck-stage');
    if (document.fullscreenElement) {
      document.body.style.overflow = 'hidden';
      header.classList.add('ed-fullscreen-hide');
      document.querySelectorAll('.panel').forEach(function(p){ p.style.display='none'; });
      if(st){
        ['position','background','padding','boxSizing','left','top','width','height'].forEach(function(p){ st.style[p]=''; });
        window.removeEventListener('resize', editorScale);
        if(typeof st._scale==='function') st._scale();
      }
    } else {
      document.body.style.overflow = '';
      header.classList.remove('ed-fullscreen-hide');
      document.querySelectorAll('.panel').forEach(function(p){ p.style.display=''; });
      if(st){
        if(editing){
          editorScale();
          window.addEventListener('resize', editorScale);
        } else {
          if(typeof st._scale==='function') st._scale();
        }
      }
    }
  });

  document.body.insertBefore(header,document.body.firstChild);
  
  // Create side panels...
  slidePanel=document.createElement('div'); slidePanel.className='panel'; slidePanel.id='ed-slide-panel';
  outlinePanel=document.createElement('div'); outlinePanel.className='panel'; outlinePanel.id='ed-outline-panel';
  document.body.insertBefore(slidePanel,stage);
  document.body.insertBefore(outlinePanel,stage);

  // Create selBox
  selBox = document.createElement('div');
  selBox.id = 'ed-sel-box';
  var dirs = ['nw','n','ne','e','se','s','sw','w'];
  dirs.forEach(function(d){
    var h = document.createElement('div');
    h.className = 'ed-rh ed-rh-'+d;
    h.dataset.dir = d;
    selBox.appendChild(h);
  });
  document.body.appendChild(selBox);

  // Create groupBox
  groupBox = document.createElement('div');
  groupBox.id = 'ed-group-box';
  document.body.appendChild(groupBox);

  // Create imgPicker
  imgPicker = document.createElement('input');
  imgPicker.id = 'ed-img-picker';
  imgPicker.type = 'file';
  imgPicker.accept = 'image/*';
  imgPicker.style.display = 'none';
  document.body.appendChild(imgPicker);

  // Alignment guides overlay
  _guidesEl = document.createElement('div');
  _guidesEl.id = 'ed-guides';
  _guidesEl.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:9998;overflow:hidden;';
  document.body.appendChild(_guidesEl);

  // Rubber-band selection
  _rubberBandEl = document.createElement('div');
  _rubberBandEl.id = 'ed-rubber-band';
  document.body.appendChild(_rubberBandEl);

  // Slide frame indicator (visible in edit mode, shows 16:9 boundary)
  _slideFrameEl = document.createElement('div');
  _slideFrameEl.id = 'ed-slide-frame';
  document.body.appendChild(_slideFrameEl);

  
  buildSlideList();
  wireEvents();
}


function switchTab(tabId, isCtx) {
  if(!isCtx) _prevTab=tabId;
  document.querySelectorAll('.ed-ribbon-group[data-tab]').forEach(function(g){g.style.display='none';});
  document.querySelectorAll('.ed-ribbon-group[data-ctx-tab]').forEach(function(g){g.style.display='none';});
  document.querySelectorAll('.ed-tab[data-tab]').forEach(function(t){t.classList.toggle('active',t.dataset.tab===tabId&&!isCtx);});
  document.querySelectorAll('.ed-ctx-tab').forEach(function(t){t.classList.toggle('active',t.dataset.ctxTab===tabId&&!!isCtx);});
  if(isCtx) document.querySelectorAll('.ed-ribbon-group[data-ctx-tab="'+tabId+'"]').forEach(function(g){g.style.display='';});
  else document.querySelectorAll('.ed-ribbon-group[data-tab="'+tabId+'"]').forEach(function(g){g.style.display='';});
}

function saveState() {
  if (typeof pushUndo === 'function') pushUndo();
}
function exitTextMode() {
  setMode('select');
  if(stage) stage.querySelectorAll('[contenteditable]').forEach(function(ce){
    ce.removeAttribute('contenteditable'); ce.removeAttribute('spellcheck');
  });
  clearSelection();
}
function setMode(m) { _mode = m; }

function isLeafEl(el) {
  return el.matches('p,li,span,td,th,h1,h2,h3,h4,h5,h6,figcaption,blockquote,img,svg,canvas') || el.children.length===0;
}

function freezeSlide(sl, force) {
  if(!sl) return;
  if(!force && sl.dataset.edFrozen) return;
  sl.dataset.edFrozen='1';
  var sr=sl.getBoundingClientRect(), sc=sr.width/1920;
  function freezeChildrenOf(container) {
    var cr=container.getBoundingClientRect();
    // Lock container dims first — prevents collapse while children become absolute
    if(!container.matches('section.slide')){
      if(window.getComputedStyle(container).position==='static')
        container.style.position='relative';
      container.style.boxSizing='border-box';
      container.style.width=(cr.width/sc)+'px';
      container.style.height=(cr.height/sc)+'px';
    }
    // Snapshot ALL children before any DOM modification — prevents reflow mid-loop
    var snaps=Array.from(container.children)
      .filter(function(c){ return !c.dataset.edPh; })
      .map(function(c){
        var r=c.getBoundingClientRect();
        return {el:c,l:(r.left-cr.left)/sc,t:(r.top-cr.top)/sc,w:r.width/sc,h:r.height/sc};
      });
    // Recurse into non-leaf children (internals get frozen, snaps already captured above)
    snaps.forEach(function(s){ if(!isLeafEl(s.el)) freezeChildrenOf(s.el); });
    // Apply absolute using pre-captured snaps
    snaps.forEach(function(s){
      s.el.style.boxSizing='border-box';
      s.el.style.position='absolute';
      s.el.style.left=s.l+'px'; s.el.style.top=s.t+'px';
      s.el.style.width=s.w+'px'; s.el.style.height=s.h+'px';
      s.el.style.margin='0';
    });
  }
  Array.from(sl.children).forEach(function(top){
    if(!isLeafEl(top)) freezeChildrenOf(top);
  });
}

function _buildGuides(sl, dragEl) {
  var lines=[];
  var sr=sl.getBoundingClientRect(), sc=sr.width/1920;
  lines.push({type:'v',pos:960},{type:'h',pos:540});
  sl.querySelectorAll(MOVE_SEL).forEach(function(other){
    if(other===dragEl||dragEl.contains(other)||other.contains(dragEl)) return;
    var r=other.getBoundingClientRect();
    var l=(r.left-sr.left)/sc, t=(r.top-sr.top)/sc, w=r.width/sc, h=r.height/sc;
    lines.push({type:'v',pos:l},{type:'v',pos:l+w/2},{type:'v',pos:l+w});
    lines.push({type:'h',pos:t},{type:'h',pos:t+h/2},{type:'h',pos:t+h});
  });
  return lines;
}
function _showGuides(activeLines, sl) {
  if(!_guidesEl) return;
  _guidesEl.innerHTML='';
  if(!activeLines||!activeLines.length) return;
  var sr=sl.getBoundingClientRect(), sc=sr.width/1920;
  activeLines.forEach(function(g){
    var div=document.createElement('div');
    if(g.type==='h'){
      div.className='ed-guide ed-guide-h';
      div.style.top=(sr.top+g.pos*sc)+'px';
    } else {
      div.className='ed-guide ed-guide-v';
      div.style.left=(sr.left+g.pos*sc)+'px';
    }
    _guidesEl.appendChild(div);
  });
}

function _updateSlRect() {
  if(!stage||!stage._slides) return;
  var sl=stage._slides[stage._idx];
  if(!sl) return;
  _slRect=sl.getBoundingClientRect();
  if(_slideFrameEl){
    _slideFrameEl.style.left=_slRect.left+'px';
    _slideFrameEl.style.top=_slRect.top+'px';
    _slideFrameEl.style.width=_slRect.width+'px';
    _slideFrameEl.style.height=_slRect.height+'px';
    _slideFrameEl.style.display=editing?'block':'none';
  }
}

function showOutline() {
  var el = document.getElementById('ed-outline-panel');
  if(el) el.classList.toggle('active');
}
function hideSel() { clearSelection(); }

/* ── Stage listeners: select/drag (PPT-style) / dblclick-to-edit / draw ── */
function addStageListeners(){
  var ds=document.querySelector('deck-stage');

  // Select mode: click to select, drag to move (like PPT)
  ds.addEventListener('mousedown', function(e){
    if(!editing) return;

    // TEXT MODE: click outside active contenteditable → exit to select mode then continue
    if(_mode==='text'){
      var ae=document.activeElement;
      if(ae && ae.isContentEditable && stage.contains(ae) && !ae.contains(e.target)){
        exitTextMode();
        // Immediately enter text mode on the new target if it is also editable
        var newTxt=e.target.closest(EDIT_SEL+',.ed-textbox');
        if(newTxt && stage.contains(newTxt)){
          e.preventDefault();
          var movEl=newTxt.closest(MOVE_SEL);
          if(movEl && stage.contains(movEl)) selectEl(movEl);
          setMode('text');
          newTxt.setAttribute('contenteditable','true');
          newTxt.setAttribute('spellcheck','false');
          newTxt.focus();
          return;
        }
        // Target is not editable — fall through to select-mode handling
      } else {
        return; // still inside text element — let browser handle cursor/selection
      }
    }

    if(_mode!=='select') return;
    if(selBox.contains(e.target)||groupBox.contains(e.target)) return;
    var el=e.target.closest(MOVE_SEL);
    // Fallback: if no MOVE_SEL match, find the deepest ancestor that is a direct child of .body-wrap or slide
    if(!el){
      var sl0=stage._slides&&stage._slides[stage._idx];
      if(sl0&&sl0.contains(e.target)){
        var walker=e.target;
        while(walker.parentElement&&walker.parentElement!==sl0&&
              !walker.parentElement.classList.contains('body-wrap')){
          walker=walker.parentElement;
        }
        if(sl0.contains(walker)&&walker!==sl0&&!walker.matches('section.slide,.blobs,style,script'))
          el=walker;
      }
    }
    if(!el||!stage.contains(el)){
      clearSelection(); clearGroupBox();
      // Rubber-band multi-select: drag on empty slide area
      if(stage.contains(e.target)&&_rubberBandEl){
        var rbX=e.clientX, rbY=e.clientY;
        var rb=_rubberBandEl;
        function onRbMove(ev){
          var x=Math.min(rbX,ev.clientX), y=Math.min(rbY,ev.clientY);
          var w=Math.abs(ev.clientX-rbX), h=Math.abs(ev.clientY-rbY);
          rb.style.cssText='position:fixed;display:block;left:'+x+'px;top:'+y+'px;width:'+w+'px;height:'+h+'px;pointer-events:none;z-index:400;';
        }
        function onRbUp(){
          var rr=rb.getBoundingClientRect();
          rb.style.display='none';
          document.removeEventListener('mousemove',onRbMove);
          document.removeEventListener('mouseup',onRbUp);
          if(rr.width<4||rr.height<4) return;
          var sl2=stage._slides&&stage._slides[stage._idx];
          if(!sl2) return;
          var hits=[];
          sl2.querySelectorAll(MOVE_SEL).forEach(function(cand){
            var cr=cand.getBoundingClientRect();
            if(cr.right>rr.left&&cr.left<rr.right&&cr.bottom>rr.top&&cr.top<rr.bottom) hits.push(cand);
          });
          // Filter out zero-size / collapsed elements that cause ghost selection
          hits=hits.filter(function(h){ var r=h.getBoundingClientRect(); return r.width>2&&r.height>2; });
          if(hits.length===1){ selectEl(hits[0]); }
          else if(hits.length>1){
            hits.forEach(function(h){ _multiSel.push(h); h.style.outline='2px solid rgba(91,155,213,0.8)'; h.style.outlineOffset='2px'; });
            _selEl=hits[0]; updateGroupBox();
          }
        }
        document.addEventListener('mousemove',onRbMove);
        document.addEventListener('mouseup',onRbUp);
      }
      return;
    }
    e.preventDefault();
    if(e.shiftKey||e.ctrlKey||e.metaKey){ addToSelection(el); return; }
    selectEl(el);
    // Chrome elements are fixed decorative overlays — no drag
    if(el.classList.contains('chrome-top')||el.classList.contains('chrome-bot')) return;

    // PPT-style drag: move > 8px threshold triggers drag
    var startX=e.clientX, startY=e.clientY, dragging=false;
    var sl=stage._slides[stage._idx];
    var ox=0, oy=0, _rafId=null, _slL=0, _slT=0, _sc=1;

    // Clean up any stale drag from a previous incomplete operation
    if(_dragCleanup){ _dragCleanup(); _dragCleanup=null; }

    function onMove(ev){
      if(!dragging){
        if(Math.abs(ev.clientX-startX)<8&&Math.abs(ev.clientY-startY)<8) return;
        dragging=true;
        pushUndo();
        // Use SLIDE bounding rect (not stage) as coordinate origin — fixes vertical centering offset
        var slr=sl.getBoundingClientRect(), sc=slr.width/1920;
        _slL=slr.left; _slT=slr.top; _sc=sc;
        var er=el.getBoundingClientRect();
        // Freeze siblings: pre-snapshot ALL positions before ANY DOM modification (prevents reflow between captures)
        // Use nearest POSITIONED ancestor as origin — siblings become absolute relative to it, not the static direct parent
        var elParent=el.parentElement;
        if(elParent&&!elParent.matches('section.slide')){
          var posAnc=elParent;
          while(posAnc&&posAnc!==sl&&window.getComputedStyle(posAnc).position==='static'){
            posAnc=posAnc.parentElement;
          }
          var par=(posAnc&&posAnc!==sl)?posAnc.getBoundingClientRect():slr;
          _slL=par.left; _slT=par.top;
          // Lock parent dims before making children absolute — prevents layout collapse & ancestor shift
          var elpR=elParent.getBoundingClientRect();
          elParent.style.boxSizing='border-box';
          elParent.style.width=(elpR.width/sc)+'px';
          elParent.style.height=(elpR.height/sc)+'px';
          var sibSnaps=Array.from(elParent.children).map(function(sib){
            var r=sib.getBoundingClientRect();
            return {el:sib,l:(r.left-par.left)/sc,t:(r.top-par.top)/sc,w:r.width/sc,h:r.height/sc};
          });
          sibSnaps.forEach(function(s){
            if(s.el===el) return;
            s.el.style.boxSizing='border-box';
            s.el.style.position='absolute';
            s.el.style.left=s.l+'px'; s.el.style.top=s.t+'px';
            s.el.style.width=s.w+'px'; s.el.style.height=s.h+'px';
          });
        }
        var sx=(er.left-_slL)/sc, sy=(er.top-_slT)/sc, sw=er.width/sc, sh=er.height/sc;
        // Preserve computed text styles on the dragged element
        var _cs=window.getComputedStyle(el);
        ['fontSize','fontFamily','fontWeight','fontStyle','color','lineHeight','letterSpacing','textAlign'].forEach(function(p){
          if(!el.style[p]) el.style[p]=_cs[p];
        });
        // Keep element in its parent — no reparenting to sl
        el.style.boxSizing='border-box';
        el.style.position='absolute'; el.style.left=sx+'px'; el.style.top=sy+'px';
        el.style.width=sw+'px'; el.style.height=sh+'px'; el.style.zIndex='500';
        el.dataset.dragging='1';
        ox=(startX-_slL)/sc-sx; oy=(startY-_slT)/sc-sy;
        // Build alignment guide lines once at drag start
        _guideLines=_buildGuides(sl,el);
      }
      // rAF: batch position update with snap-to-guide
      if(_rafId) cancelAnimationFrame(_rafId);
      var cx=ev.clientX, cy=ev.clientY;
      _rafId=requestAnimationFrame(function(){
        var rawL=(cx-_slL)/_sc-ox, rawT=(cy-_slT)/_sc-oy;
        var SNAP=8, activeG=[];
        function snapAxis(raw,size,type){
          var edges=[raw,raw+size/2,raw+size], bestD=SNAP, bestSnap=raw;
          _guideLines.forEach(function(g){
            if(g.type!==type) return;
            edges.forEach(function(edge,i){
              var d=Math.abs(edge-g.pos);
              if(d<bestD){ bestD=d; bestSnap=g.pos-size*(i*0.5); activeG.push(g); }
            });
          });
          return bestSnap;
        }
        var elW=el.offsetWidth, elH=el.offsetHeight;
        var snapL=snapAxis(rawL,elW,'v'), snapT=snapAxis(rawT,elH,'h');
        el.style.left=snapL+'px'; el.style.top=snapT+'px';
        _showGuides(activeG,sl);
        updateSelBox();
        _rafId=null;
      });
    }
    function onUp(){
      if(_rafId){cancelAnimationFrame(_rafId);_rafId=null;}
      delete el.dataset.dragging;
      if(_guidesEl) _guidesEl.innerHTML='';
      _guideLines=[];
      document.removeEventListener('mousemove',onMove);
      document.removeEventListener('mouseup',onUp);
      _dragCleanup=null;
      if(dragging) updateSelBox();
    }
    _dragCleanup=function(){ onUp(); };
    document.addEventListener('mousemove',onMove);
    document.addEventListener('mouseup',onUp);
  },false);

  // Dblclick → text edit mode on that element
  ds.addEventListener('dblclick', function(e){
    if(!editing) return;
    var el=e.target.closest(EDIT_SEL+',.ed-textbox');
    if(!el){
      var cd=e.target.closest('.chrome-top,.chrome-bot');
      if(cd) el=cd.querySelector('span:not(.pn)')||cd;
    }
    if(!el||!stage.contains(el)) return;
    // NO e.preventDefault() — let browser do native word selection on dblclick
    e.stopPropagation();
    setMode('text');
    el.setAttribute('contenteditable','true'); el.setAttribute('spellcheck','false');
    el.focus();
    // Don't call caretRangeFromPoint — browser handles cursor + word selection on dblclick
    function onFocusOut(evt){
      var rt=evt.relatedTarget;
      if(rt && (header.contains(rt)||(stage.contains(rt)&&rt.isContentEditable))) return;
      el.removeEventListener('focusout', onFocusOut);
      exitTextMode();
    }
    el.addEventListener('focusout', onFocusOut);
  },false);

  // Draw mode
  ds.addEventListener('mousedown', function(e){
    if(!editing||_mode!=='draw') return;
    if(!_canvas) initDrawCanvas();
  },false);

  // Table column resize: cursor change on hover near right border
  ds.addEventListener('mousemove',function(e){
    if(!editing||_mode!=='select') return;
    var td=e.target.closest('td,th');
    if(!td){ return; }
    var r=td.getBoundingClientRect();
    td.style.cursor=Math.abs(e.clientX-r.right)<6?'col-resize':'';
  },false);

  // Table column resize: drag to resize column width
  ds.addEventListener('mousedown',function(e){
    if(!editing||_mode!=='select') return;
    var td=e.target.closest('td,th');
    if(!td) return;
    var r=td.getBoundingClientRect();
    if(Math.abs(e.clientX-r.right)>=6) return;
    e.preventDefault(); e.stopPropagation();
    var startW=r.width, startX=e.clientX;
    var sc=stage.getBoundingClientRect().width/1920;
    function onResizeMove(ev){ td.style.width=Math.max(60,(startW+(ev.clientX-startX))/sc)+'px'; }
    function onResizeUp(){ document.removeEventListener('mousemove',onResizeMove); document.removeEventListener('mouseup',onResizeUp); }
    document.addEventListener('mousemove',onResizeMove);
    document.addEventListener('mouseup',onResizeUp);
  },false);
}

/* ── Slide list ── */
function buildSlideList(){
  if(!slidePanel) return;
  slidePanel.innerHTML='';
  var slides=Array.from(stage.querySelectorAll('section.slide'));
  slides.forEach(function(sl,i){
    var th=document.createElement('div'); th.className='ed-thumb'+(i===stage._idx?' active':'');
    th.innerHTML='<div class="ed-thumb-num">'+(i+1)+'</div><div class="ed-thumb-cnt"></div>';
    th.onclick=function(){ stage._show(i); buildSlideList(); };
    slidePanel.appendChild(th);
    var clone=sl.cloneNode(true);
    // Reset styles and set explicit dimensions for CSS scale(0.08333) to work
    clone.style.cssText='width:1920px;height:1080px;position:absolute;top:0;left:0;overflow:hidden;';
    // Copy CSS vars from deck-stage so theme colors work in thumbnail clone
    Array.from(stage.style).filter(function(p){return p.startsWith('--');}).forEach(function(p){
      clone.style.setProperty(p, stage.style.getPropertyValue(p));
    });
    // Remove contenteditable from thumbnail clones
    clone.querySelectorAll('[contenteditable]').forEach(function(el){ el.removeAttribute('contenteditable'); });
    th.querySelector('.ed-thumb-cnt').appendChild(clone);
  });
}
function previewAnimation(){ applyAnimations(); }
function onSlideChange(){
  updateSlideList(); clearSelection(); clearGroupBox(); _updateSlRect();
}

/* ── Selection ── */
function clearSelection(){
  _selEl=null; selBox.classList.remove('show');
  document.querySelectorAll('.ed-ctx-tab').forEach(function(t){t.style.display='none';});
  switchTab(_prevTab);
}

function clearGroupBox(){ _multiSel.forEach(function(el){ el.style.outline=''; el.style.outlineOffset=''; }); _multiSel=[]; groupBox.classList.remove('show'); }
function updateSelBox(){
  if(!_selEl)return;
  var r=_selEl.getBoundingClientRect();
  selBox.style.left=(r.left-2)+'px'; selBox.style.top=(r.top-2)+'px';
  selBox.style.width=r.width+'px'; selBox.style.height=r.height+'px';
  selBox.classList.add('show');
}
function updateCtxSizeInputs(el){
  if(!stage||!el) return;
  var sr=stage.getBoundingClientRect(), sc=sr.width/1920, er=el.getBoundingClientRect();
  var w=Math.round(er.width/sc), h=Math.round(er.height/sc);
  ['ed-ctx-width','ed-ctx-width-pic','ed-ctx-width-shape'].forEach(function(id){var inp=document.getElementById(id);if(inp)inp.value=w;});
  ['ed-ctx-height','ed-ctx-height-pic','ed-ctx-height-shape'].forEach(function(id){var inp=document.getElementById(id);if(inp)inp.value=h;});
}

function selectEl(el){
  _multiSel.forEach(function(m){ m.style.outline=''; m.style.outlineOffset=''; });
  if(_fmtPaint&&_fmtStyles){ Object.assign(el.style,_fmtStyles); _fmtPaint=false; if(_fmtPaintBtn)_fmtPaintBtn.classList.remove('active-btn'); }
  _selEl=el; _multiSel=[el]; updateSelBox();
  var tSel=document.getElementById('ed-transition-sel');
  if(tSel){ var tr=(stage._slides[stage._idx]||{}).dataset&&stage._slides[stage._idx].dataset.transition||'none'; tSel.value=tr; }
  var aSel=document.getElementById('ed-anim-sel');
  if(aSel){ aSel.value=el.dataset.anim ? el.dataset.anim.replace('anim-','') : 'none'; }
  var aDur=document.getElementById('ed-anim-dur');   if(aDur)   aDur.value   = el.dataset.animDur   || '0.5';
  var aDel=document.getElementById('ed-anim-delay'); if(aDel)   aDel.value   = el.dataset.animDelay || '0';
  var aOrd=document.getElementById('ed-anim-order'); if(aOrd)   aOrd.value   = el.dataset.animOrder || '0';
  var opSlider=document.getElementById('ed-opacity');
  if(opSlider){ var op=parseFloat(el.style.opacity||'1'); opSlider.value=Math.round(op*100); }
  // Update font size display
  var szInp=document.getElementById('ed-sz-input');
  if(szInp){ var fs=parseFloat(getComputedStyle(el).fontSize); if(!isNaN(fs)) szInp.value=Math.round(fs); }
  // Sync shape fill/stroke inputs when a shape is selected
  if(el.classList.contains('ed-shape')){
    var svgEl=el.querySelector('rect,ellipse,polygon,polyline,path,line');
    var fillIn=document.getElementById('ed-shape-fill');
    var strokeIn=document.getElementById('ed-shape-stroke');
    var swIn=document.getElementById('ed-shape-sw');
    if(fillIn&&svgEl){
      var f=svgEl.getAttribute('fill');
      if(f&&f!=='none'&&/^#/.test(f)) fillIn.value=f;
      else { var stk=svgEl.getAttribute('stroke'); if(stk&&/^#/.test(stk)) fillIn.value=stk; }
    }
    if(strokeIn&&svgEl){ var str=svgEl.getAttribute('stroke'); if(str&&/^#/.test(str)) strokeIn.value=str; }
    if(swIn&&svgEl){ var sw=svgEl.getAttribute('stroke-width'); if(sw) swIn.value=sw; }
  }
  var dashSel=document.getElementById('ed-stroke-dash');
  if(dashSel){
    var svgEl0=el.querySelector('rect,ellipse,polygon,polyline,path,line');
    dashSel.value=svgEl0?svgEl0.getAttribute('stroke-dasharray')||'':'';
  }
  var radInp=document.getElementById('ed-border-radius');
  if(radInp){
    var br=parseFloat(el.style.borderRadius)||0;
    if(!br){var svgRect0=el.querySelector('rect');if(svgRect0)br=parseFloat(svgRect0.getAttribute('rx'))||0;}
    radInp.value=Math.round(br);
  }
  var shadBtn=document.getElementById('ed-shadow-btn');
  if(shadBtn){
    var hasSh=(el.style.filter&&el.style.filter.includes('drop-shadow'))||!!el.style.boxShadow;
    shadBtn.classList.toggle('active-btn',hasSh);
  }
  // Show context-sensitive tabs (PPT-style: Table Design/Layout, Picture Format, Shape Format)
  document.querySelectorAll('.ed-ctx-tab').forEach(function(t){t.style.display='none';});
  var ctxType=el.tagName==='TABLE'?'table':(el.tagName==='FIGURE'||el.tagName==='IMG')?'picture':el.classList.contains('ed-shape')?'shape':null;
  if(ctxType==='table'){
    document.querySelectorAll('.ed-ctx-tab[data-ctx-group="table"]').forEach(function(t){t.style.display='';});
    switchTab('table-design',true);
  } else if(ctxType==='picture'){
    var _pt=document.querySelector('.ed-ctx-tab[data-ctx-tab="picture"]'); if(_pt)_pt.style.display='';
    switchTab('picture',true);
  } else if(ctxType==='shape'){
    var _st=document.querySelector('.ed-ctx-tab[data-ctx-tab="shape"]'); if(_st)_st.style.display='';
    switchTab('shape',true);
  }
  updateCtxSizeInputs(el);
  if(ctxType==='image'||el.tagName==='FIGURE'){
    var imgEl=el.querySelector('img');
    if(imgEl){
      var fitSel=document.getElementById('ed-img-fit');
      if(fitSel) fitSel.value=imgEl.style.objectFit||'contain';
      var filt=imgEl.style.filter||'';
      var brMatch=filt.match(/brightness\((\d+)%\)/);
      var coMatch=filt.match(/contrast\((\d+)%\)/);
      var brInp=document.getElementById('ed-img-brightness');
      var coInp=document.getElementById('ed-img-contrast');
      if(brInp) brInp.value=brMatch?brMatch[1]:100;
      if(coInp) coInp.value=coMatch?coMatch[1]:100;
    }
  }
}
function addToSelection(el){
  if(_multiSel.indexOf(el)===-1){
    _multiSel.push(el);
    el.style.outline='2px solid rgba(91,155,213,0.8)';
    el.style.outlineOffset='2px';
  }
  if(_multiSel.length>1) updateGroupBox();
}
function updateGroupBox(){
  if(_multiSel.length<2){groupBox.classList.remove('show');return;}
  var minL=Infinity,minT=Infinity,maxR=-Infinity,maxB=-Infinity;
  _multiSel.forEach(function(el){
    var r=el.getBoundingClientRect();
    minL=Math.min(minL,r.left);minT=Math.min(minT,r.top);
    maxR=Math.max(maxR,r.right);maxB=Math.max(maxB,r.bottom);
  });
  groupBox.style.left=(minL-4)+'px'; groupBox.style.top=(minT-4)+'px';
  groupBox.style.width=(maxR-minL+8)+'px'; groupBox.style.height=(maxB-minT+8)+'px';
  groupBox.classList.add('show');
}

/* ── Group / Ungroup ── */
function groupSelected(){
  if(_multiSel.length<2)return;
  pushUndo();
  var sl=stage._slides[stage._idx];
  var sr=sl.getBoundingClientRect(),sc=sr.width/1920;
  var minL=Infinity,minT=Infinity,maxR=-Infinity,maxB=-Infinity;
  _multiSel.forEach(function(el){
    var r=el.getBoundingClientRect();
    var x=(r.left-sr.left)/sc,y=(r.top-sr.top)/sc,w=r.width/sc,h=r.height/sc;
    minL=Math.min(minL,x);minT=Math.min(minT,y);maxR=Math.max(maxR,x+w);maxB=Math.max(maxB,y+h);
  });
  var grp=document.createElement('div'); grp.className='ed-group';
  grp.style.cssText='position:absolute;left:'+minL+'px;top:'+minT+'px;width:'+(maxR-minL)+'px;height:'+(maxB-minT)+'px;z-index:200';
  sl.appendChild(grp);
  _multiSel.forEach(function(el){
    var r=el.getBoundingClientRect();
    var ex=(r.left-sr.left)/sc-minL, ey=(r.top-sr.top)/sc-minT;
    var ew=r.width/sc;
    el.style.position='absolute'; el.style.left=ex+'px'; el.style.top=ey+'px';
    el.style.width=ew+'px'; el.style.zIndex='1';
    grp.appendChild(el);
  });
  clearGroupBox(); selectEl(grp);
}
function ungroupSelected(){
  if(!_selEl||!_selEl.classList.contains('ed-group'))return;
  pushUndo();
  var grp=_selEl; var sl=stage._slides[stage._idx];
  var sr=sl.getBoundingClientRect(),sc=sr.width/1920;
  var gr=grp.getBoundingClientRect();
  Array.from(grp.children).forEach(function(ch){
    var r=ch.getBoundingClientRect();
    var cx=(r.left-sr.left)/sc, cy=(r.top-sr.top)/sc;
    ch.style.left=cx+'px'; ch.style.top=cy+'px';
    sl.appendChild(ch);
  });
  grp.remove(); clearSelection();
}

/* ── Z-order ── */
function bringToFront(){ if(_selEl)_selEl.style.zIndex=9999; }
function sendToBack()  { if(_selEl)_selEl.style.zIndex=1; }
function bringForward(){ if(_selEl)_selEl.style.zIndex=(parseInt(_selEl.style.zIndex)||100)+1; }
function sendBackward(){ if(_selEl)_selEl.style.zIndex=Math.max(1,(parseInt(_selEl.style.zIndex)||100)-1); }

/* ── screenToSlide ── */
function screenToSlide(cx,cy){
  var sl=stage._slides&&stage._slides[stage._idx];
  var r=(sl||stage).getBoundingClientRect(),sc=r.width/1920;
  return{x:(cx-r.left)/sc,y:(cy-r.top)/sc};
}

/* ── Save HTML ── */
/* IDs of editor-injected chrome nodes that must NOT be baked into the saved deck.
 * (The injected <style>/<script> in <head> stay — they make the file re-editable.) */
var _ED_CHROME_IDS = [
  'ed-header','ed-slide-panel','ed-outline-panel','ed-sel-box','ed-group-box',
  'ed-color-picker','ed-img-picker','ed-guides','ed-rubber-band','ed-slide-frame',
  'ed-draw-overlay'
];

/* Produce a clean, self-contained, re-editable HTML string of the current deck.
 * Detaches editor chrome, strips editing-only attributes and transient FX state,
 * serializes, then restores everything exactly as it was. */
function getCleanHTML(){
  // 1. Exit any active inline editing
  var editables=Array.from(document.querySelectorAll('[contenteditable]'));
  editables.forEach(function(el){el.removeAttribute('contenteditable'); el.removeAttribute('spellcheck');});

  // 2. Detach editor chrome (remember where each node was for restore)
  var detached=[];
  _ED_CHROME_IDS.forEach(function(id){
    var el=document.getElementById(id);
    if(el && el.parentNode){ detached.push({el:el, parent:el.parentNode, next:el.nextSibling}); el.parentNode.removeChild(el); }
  });

  // 3. Strip editing state + any transient transition/animation classes so the
  //    saved deck starts in a clean, presentable state.
  var hadEditing=document.body.hasAttribute('data-editing');
  var prevMode=document.body.getAttribute('data-mode');
  document.body.removeAttribute('data-editing');
  document.body.removeAttribute('data-mode');
  if(stage){
    stage.querySelectorAll('.tr-fade,.tr-slide-left,.tr-slide-right,.tr-slide-up,.tr-slide-down,.tr-zoom,.tr-flip').forEach(function(s){
      s.className=s.className.replace(/\btr-[\w-]+/g,'').replace(/\s{2,}/g,' ').trim();
      s.style.removeProperty('--tr-dur');
    });
    stage.querySelectorAll('[data-anim]').forEach(function(el){
      el.className=el.className.replace(/\banim-\S+/g,'').replace(/\s{2,}/g,' ').trim();
      el.style.removeProperty('--anim-dur'); el.style.removeProperty('--anim-delay');
      el.style.opacity=''; // clear any mid-animation opacity:0
    });
  }

  var html='<!doctype html>\n'+document.documentElement.outerHTML;

  // 4. Restore everything
  detached.forEach(function(d){ d.parent.insertBefore(d.el, d.next); });
  editables.forEach(function(el){el.setAttribute('contenteditable','true'); el.setAttribute('spellcheck','false');});
  if(hadEditing){ document.body.setAttribute('data-editing',''); if(prevMode!=null) document.body.setAttribute('data-mode',prevMode); }
  return html;
}

/* Save = write in place (File System Access API) or download; via editor_save.js */
function saveHTML(){
  var html=getCleanHTML();
  var name=(document.title||'slides').replace(/[\\/:*?"<>|]+/g,'_')+'.html';
  if(window.EditorSave){
    window.EditorSave.saveHtml(html, name).then(function(res){ if(_autosave) _autosave.clear(); });
  } else {
    var blob=new Blob([html],{type:'text/html'});
    var a=document.createElement('a');
    a.href=URL.createObjectURL(blob); a.download=(document.title||'slides')+'_edited.html';
    a.click(); URL.revokeObjectURL(a.href);
  }
}

/* ── Autosave (localStorage safety net) + tiny toast ── */
var _autosave=null;
function _autosaveKey(){ return 'DECK_AS_'+(document.title||'deck'); }
function scheduleAutosave(){ if(_autosave) _autosave.schedule(); }
function toastMsg(msg){
  if(window.EditorSave){ window.EditorSave.toast(msg); return; }
  console.log('[deck-editor]', msg);
}
function initAutosave(){
  if(!window.EditorSave || !stage) return;
  _autosave=window.EditorSave.makeAutosave(_autosaveKey(), function(){ return stage.innerHTML; }, 2000);
  // Offer to restore a newer autosaved draft (within 7 days)
  var saved=_autosave.load(7*24*3600*1000);
  if(saved && saved!==stage.innerHTML){
    setTimeout(function(){
      if(confirm('Khôi phục bản chỉnh sửa tự động chưa lưu?')){
        stage.innerHTML=saved;
        stage._slides=Array.from(stage.querySelectorAll(':scope > section.slide'));
        stage._total=stage._slides.length;
        stage._show(Math.min(stage._idx,stage._total-1));
        if(editing) buildSlideList();
      } else { _autosave.clear(); }
    }, 300);
  }
  window.addEventListener('beforeunload', function(){ if(_autosave) _autosave.flush(); });
}

/* ── Key handler ── */
var _lastKey='';
document.addEventListener('keydown',function(e){
  if((e.ctrlKey||e.metaKey)&&e.shiftKey&&(e.key==='e'||e.key==='E')){
    e.preventDefault();e.stopPropagation();
    editing?exitEditMode():enterEditMode(); return;
  }
  if((e.ctrlKey||e.metaKey)&&!e.shiftKey&&(e.key==='s'||e.key==='S')){
    e.preventDefault();e.stopPropagation();
    saveHTML(); return;
  }
  if(!editing)return;
  if(_mode!=='text'&&(e.ctrlKey||e.metaKey)&&!e.shiftKey&&e.key==='z'){undo();e.preventDefault();return;}
  if(_mode!=='text'&&(e.ctrlKey||e.metaKey)&&(e.key==='y'||(e.shiftKey&&e.key==='Z'))){redo();e.preventDefault();return;}
  if(e.key==='Escape'){
    if(_mode==='text'){ exitTextMode(); }
    else if(_mode==='draw'){ finalizeDraw(); setMode('select'); }
    else { clearSelection(); clearGroupBox(); }
    return;
  }
  if(_mode==='select'&&(e.key==='Delete'||e.key==='Backspace')&&_selEl){
    var ae=document.activeElement;
    if(ae && ae.isContentEditable && stage.contains(ae)) return;
    pushUndo();_selEl.remove();clearSelection();return;
  }
  _lastKey=e.key;
  var inText=document.activeElement&&document.activeElement.isContentEditable;
  if(inText){
    var navKeys=['ArrowRight','ArrowLeft','ArrowUp','ArrowDown',' ','PageUp','PageDown','Home','End'];
    if(navKeys.indexOf(e.key)!==-1)e.stopPropagation();
    if(e.key==='Tab'){e.preventDefault();e.stopPropagation();document.execCommand(e.shiftKey?'outdent':'indent');}
  }
  /* ── PPT-standard keyboard shortcuts ── */
  var mod=e.ctrlKey||e.metaKey;
  if(!mod) return;
  // Text formatting (both text mode and select mode)
  if(!e.shiftKey&&e.key==='b'){ applyToSelected(function(){document.execCommand('bold');},function(el){el.style.fontWeight=el.style.fontWeight==='bold'?'':'bold';}); e.preventDefault(); return; }
  if(!e.shiftKey&&e.key==='i'){ applyToSelected(function(){document.execCommand('italic');},function(el){el.style.fontStyle=el.style.fontStyle==='italic'?'':'italic';}); e.preventDefault(); return; }
  if(!e.shiftKey&&e.key==='u'){ applyToSelected(function(){document.execCommand('underline');},function(el){var td=el.style.textDecoration||'';el.style.textDecoration=td.includes('underline')?td.replace('underline','').trim():(td+' underline').trim();}); e.preventDefault(); return; }
  if(e.shiftKey&&(e.key==='s'||e.key==='S')){ applyToSelected(function(){document.execCommand('strikeThrough');},function(el){var td=el.style.textDecoration||'';el.style.textDecoration=td.includes('line-through')?td.replace('line-through','').trim():(td+' line-through').trim();}); e.preventDefault(); return; }
  if(e.shiftKey&&(e.key==='+'||e.key==='=')){ document.execCommand('superscript'); e.preventDefault(); return; }
  if(e.shiftKey&&e.key==='-'){ document.execCommand('subscript'); e.preventDefault(); return; }
  if(!e.shiftKey&&e.key==='l'){ applyToSelected(function(){document.execCommand('justifyLeft');},function(el){el.style.textAlign='left';}); e.preventDefault(); return; }
  if(!e.shiftKey&&e.key==='e'&&_mode!=='text'){ applyToSelected(function(){document.execCommand('justifyCenter');},function(el){el.style.textAlign='center';}); e.preventDefault(); return; }
  if(!e.shiftKey&&e.key==='r'&&_mode==='select'){ applyToSelected(function(){document.execCommand('justifyRight');},function(el){el.style.textAlign='right';}); e.preventDefault(); return; }
  if(!e.shiftKey&&e.key==='j'){ applyToSelected(function(){document.execCommand('justifyFull');},function(el){el.style.textAlign='justify';}); e.preventDefault(); return; }
  // Element operations (select mode only)
  if(_mode==='select'){
    if(!e.shiftKey&&e.key==='d'&&_selEl){ e.preventDefault(); pushUndo(); var cl=_selEl.cloneNode(true); cl.style.left=(parseFloat(cl.style.left)||0)+20+'px'; cl.style.top=(parseFloat(cl.style.top)||0)+20+'px'; if(cl.style.position!=='absolute'){cl.style.position='absolute';} _selEl.parentNode.appendChild(cl); selectEl(cl); return; }
    if(!e.shiftKey&&(e.key==='g'||e.key==='G')&&!e.shiftKey){ e.preventDefault(); groupSelected(); return; }
    if(e.shiftKey&&(e.key==='g'||e.key==='G')){ e.preventDefault(); ungroupSelected(); return; }
    if(!e.shiftKey&&e.key===']'){ e.preventDefault(); bringForward(); return; }
    if(!e.shiftKey&&e.key==='['){ e.preventDefault(); sendBackward(); return; }
    if(e.shiftKey&&e.key===']'){ e.preventDefault(); bringToFront(); return; }
    if(e.shiftKey&&e.key==='['){ e.preventDefault(); sendToBack(); return; }
    // Element clipboard (select mode, no contenteditable active)
    if(!e.shiftKey&&e.key==='c'&&_selEl){ _clipboard=_selEl.cloneNode(true); e.preventDefault(); return; }
    if(!e.shiftKey&&e.key==='x'&&_selEl){ pushUndo(); _clipboard=_selEl.cloneNode(true); _selEl.remove(); clearSelection(); e.preventDefault(); return; }
    if(!e.shiftKey&&e.key==='v'&&_clipboard){ pushUndo(); var _cl=_clipboard.cloneNode(true); var _hasPos=(_cl.style.left&&parseFloat(_cl.style.left)>0); _cl.style.left=(_hasPos?parseFloat(_cl.style.left)+20:920)+'px'; _cl.style.top=(_hasPos?parseFloat(_cl.style.top)+20:460)+'px'; _cl.style.position='absolute'; stage._slides[stage._idx].appendChild(_cl); selectEl(_cl); e.preventDefault(); return; }
    // Arrow nudge (select mode only, when NOT inside contenteditable)
    if(_selEl&&(e.key==='ArrowLeft'||e.key==='ArrowRight'||e.key==='ArrowUp'||e.key==='ArrowDown')){
      e.preventDefault();
      if(_selEl.style.position!=='absolute'){
        var sl2=stage._slides[stage._idx];
        var sr2=sl2.getBoundingClientRect(),sc2=sr2.width/1920;
        var er2=_selEl.getBoundingClientRect();
        var sx2=(er2.left-sr2.left)/sc2,sy2=(er2.top-sr2.top)/sc2,sw2=er2.width/sc2,sh2=er2.height/sc2;
        var ph2=document.createElement('div');
        ph2.style.cssText='flex-shrink:0;width:'+sw2+'px;height:'+sh2+'px;visibility:hidden;pointer-events:none';
        if(_selEl.parentElement&&!_selEl.parentElement.matches('section.slide')) _selEl.parentElement.insertBefore(ph2,_selEl);
        _selEl.style.position='absolute';_selEl.style.left=sx2+'px';_selEl.style.top=sy2+'px';
        _selEl.style.width=sw2+'px';_selEl.style.height=sh2+'px';
        sl2.appendChild(_selEl);
        requestAnimationFrame(function(){ph2.remove();});
      }
      if(e.key==='ArrowLeft')  _selEl.style.left=(parseFloat(_selEl.style.left)||0)-2+'px';
      if(e.key==='ArrowRight') _selEl.style.left=(parseFloat(_selEl.style.left)||0)+2+'px';
      if(e.key==='ArrowUp')    _selEl.style.top=(parseFloat(_selEl.style.top)||0)-2+'px';
      if(e.key==='ArrowDown')  _selEl.style.top=(parseFloat(_selEl.style.top)||0)+2+'px';
      updateSelBox(); return;
    }
  }
  // Tab in list → indent (multilevel); Shift+Tab → outdent
  if(e.key==='Tab'){
    var ae2=document.activeElement;
    if(ae2&&ae2.isContentEditable&&stage.contains(ae2)){
      var sel2=window.getSelection();
      if(sel2&&sel2.rangeCount){
        var node2=sel2.anchorNode;
        var li2=(node2.nodeType===3?node2.parentElement:node2).closest('li');
        if(li2){
          e.preventDefault();
          if(e.shiftKey){ document.execCommand('outdent'); }
          else { document.execCommand('indent'); }
          return;
        }
      }
    }
  }
},true);
// Arrow nudge without modifier (select mode, no Ctrl)
document.addEventListener('keydown',function(e){
  if(!editing||_mode!=='select'||!_selEl) return;
  if(document.activeElement&&document.activeElement.isContentEditable) return;
  if(e.key==='ArrowLeft'||e.key==='ArrowRight'||e.key==='ArrowUp'||e.key==='ArrowDown'){
    e.preventDefault();
    var step=e.shiftKey?20:2;
    if(_selEl.style.position!=='absolute'){
      var _sl3=stage._slides[stage._idx];
      var _sr3=_sl3.getBoundingClientRect(),_sc3=_sr3.width/1920;
      var _er3=_selEl.getBoundingClientRect();
      var _sx3=(_er3.left-_sr3.left)/_sc3,_sy3=(_er3.top-_sr3.top)/_sc3,_sw3=_er3.width/_sc3,_sh3=_er3.height/_sc3;
      var _ph3=document.createElement('div');
      _ph3.style.cssText='flex-shrink:0;width:'+_sw3+'px;height:'+_sh3+'px;visibility:hidden;pointer-events:none';
      if(_selEl.parentElement&&!_selEl.parentElement.matches('section.slide')) _selEl.parentElement.insertBefore(_ph3,_selEl);
      _selEl.style.position='absolute';_selEl.style.left=_sx3+'px';_selEl.style.top=_sy3+'px';
      _selEl.style.width=_sw3+'px';_selEl.style.height=_sh3+'px';
      _sl3.appendChild(_selEl);
      requestAnimationFrame(function(){_ph3.remove();});
    }
    if(e.key==='ArrowLeft')  _selEl.style.left=(parseFloat(_selEl.style.left)||0)-step+'px';
    if(e.key==='ArrowRight') _selEl.style.left=(parseFloat(_selEl.style.left)||0)+step+'px';
    if(e.key==='ArrowUp')    _selEl.style.top=(parseFloat(_selEl.style.top)||0)-step+'px';
    if(e.key==='ArrowDown')  _selEl.style.top=(parseFloat(_selEl.style.top)||0)+step+'px';
    updateSelBox();
  }
},false);

/* ── startResize: 8-direction resize like PPT ── */
function startResize(e, el, dir){
  e.preventDefault(); e.stopPropagation();
  var _rsl=stage._slides&&stage._slides[stage._idx];
  var sr=(_rsl||stage).getBoundingClientRect(), sc=sr.width/1920;
  var er=el.getBoundingClientRect();
  var startX=e.clientX, startY=e.clientY;
  var startL=(er.left-sr.left)/sc, startT=(er.top-sr.top)/sc;
  var startW=el.offsetWidth, startH=el.offsetHeight;
  var ratio=startH>0?startW/startH:1;
  pushUndo();
  if(el.style.position!=='absolute'){
    var sl2=stage._slides[stage._idx];
    sl2.appendChild(el);
    el.style.position='absolute'; el.style.left=startL+'px';
    el.style.top=startT+'px'; el.style.width=startW+'px'; el.style.height=startH+'px'; el.style.zIndex='500';
  }
  function onMove(ev){
    sr=(_rsl||stage).getBoundingClientRect(); sc=sr.width/1920;
    var dx=(ev.clientX-startX)/sc, dy=(ev.clientY-startY)/sc;
    var nw=startW, nh=startH, nl=startL, nt=startT;
    if(dir.indexOf('e')!==-1) nw=Math.max(40,startW+dx);
    if(dir.indexOf('s')!==-1) nh=Math.max(20,startH+dy);
    if(dir.indexOf('w')!==-1){ nw=Math.max(40,startW-dx); nl=startL+dx; }
    if(dir.indexOf('n')!==-1){ nh=Math.max(20,startH-dy); nt=startT+dy; }
    if(ev.shiftKey&&(dir==='se'||dir==='ne'||dir==='sw'||dir==='nw')) nh=nw/ratio;
    el.style.width=nw+'px'; el.style.height=nh+'px';
    el.style.left=nl+'px'; el.style.top=nt+'px';
    updateSelBox();
  }
  function onUp(){ document.removeEventListener('mousemove',onMove); document.removeEventListener('mouseup',onUp); }
  document.addEventListener('mousemove',onMove); document.addEventListener('mouseup',onUp);
}

/* ── applyToSelected: apply format to text selection (text mode) or ALL content of selected element (select mode) ── */
function applyToSelected(execCmdFn, styleFn){
  var ae=document.activeElement;
  if(ae&&ae.isContentEditable&&stage.contains(ae)){ execCmdFn(); return; }
  if(!_selEl||!stage.contains(_selEl)) return;
  pushUndo();
  var tgt=_selEl.isContentEditable?_selEl:(_selEl.querySelector('[contenteditable="true"]')||null);
  if(tgt){
    tgt.focus();
    var r=document.createRange(); r.selectNodeContents(tgt);
    var s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
    execCmdFn();
    s.removeAllRanges();
    tgt.blur();
  } else { styleFn(_selEl); }
}

/* ── alignToSlide / ensureAbsolute helpers ── */
function ensureAbsolute(el){
  if(el.style.position==='absolute') return;
  var _sl=stage._slides&&stage._slides[stage._idx];
  var sr=(_sl||stage).getBoundingClientRect(), sc=sr.width/1920;
  var er=el.getBoundingClientRect();
  var sx=(er.left-sr.left)/sc, sy=(er.top-sr.top)/sc, sw=er.width/sc;
  var sl=stage._slides[stage._idx]; sl.appendChild(el);
  el.style.position='absolute'; el.style.left=sx+'px'; el.style.top=sy+'px'; el.style.width=sw+'px';
}
function alignToSlide(dir){
  if(!_selEl||!stage.contains(_selEl)) return; pushUndo();
  ensureAbsolute(_selEl);
  var w=_selEl.offsetWidth, h=_selEl.offsetHeight;
  if(dir==='l') _selEl.style.left='0px';
  else if(dir==='c') _selEl.style.left=((1920-w)/2)+'px';
  else if(dir==='r') _selEl.style.left=(1920-w)+'px';
  else if(dir==='t') _selEl.style.top='0px';
  else if(dir==='m') _selEl.style.top=((1080-h)/2)+'px';
  else if(dir==='b') _selEl.style.top=(1080-h)+'px';
  updateSelBox();
}



/* ── Missing function implementations ── */

function updateSlideList() {
  buildSlideList();
}

function editorScale() {
  var panelW=(slidePanel&&slidePanel.classList.contains('active'))?PANEL_W:0;
  var hH=(header&&header.classList.contains('active'))?(header.offsetHeight||TOOL_H):0;
  var pad=24;
  var avW=window.innerWidth-panelW-pad*2, avH=window.innerHeight-hH-pad*2;
  stage._scale=function(){
    var sc=Math.min(avW/1920,avH/1080);
    var ox=panelW+pad+(avW-1920*sc)/2, oy=hH+pad+(avH-1080*sc)/2;
    this.style.width='1920px'; this.style.height='1080px';
    this.style.transform='scale('+sc.toFixed(6)+')';
    this.style.transformOrigin='top left';
    this.style.position='fixed';
    this.style.left=ox.toFixed(2)+'px'; this.style.top=oy.toFixed(2)+'px';
  };
  stage._scale();
  _updateSlRect();
}

function showNormal() {
  if (outlinePanel) outlinePanel.classList.remove('active');
  if (slidePanel) slidePanel.classList.add('active');
}

function setTransition(val) {
  if (!stage || !stage._slides || stage._idx < 0) return;
  var sl = stage._slides[stage._idx];
  if (!sl) return;
  pushUndo();
  sl.dataset.transition = val;
  var durInp = document.getElementById('ed-transition-dur');
  if (durInp && durInp.value) sl.dataset.transitionDur = durInp.value;
  var sel = document.getElementById('ed-transition-sel');
  if(sel) sel.value = val;
  scheduleAutosave();
}

/* Set the current slide's transition on ALL slides */
function setTransitionAll() {
  if (!stage || !stage._slides) return;
  var sel = document.getElementById('ed-transition-sel');
  var durInp = document.getElementById('ed-transition-dur');
  var val = sel ? sel.value : 'fade';
  pushUndo();
  stage._slides.forEach(function(sl){
    sl.dataset.transition = val;
    if (durInp && durInp.value) sl.dataset.transitionDur = durInp.value;
  });
  scheduleAutosave();
  toastMsg('Transition applied to all slides');
}

/* Preview the current slide's transition (matches runtime, honours duration) */
function applyTransition(){
  var sl=stage._slides[stage._idx];
  if(!sl) return;
  var tr=sl.dataset.transition||'fade';
  if(tr==='none')return;
  var cls='tr-'+tr;
  if(sl.dataset.transitionDur) sl.style.setProperty('--tr-dur',(parseFloat(sl.dataset.transitionDur)||0.45)+'s');
  sl.classList.remove(cls); void sl.offsetWidth; sl.classList.add(cls);
  setTimeout(function(){sl.classList.remove(cls); sl.style.removeProperty('--tr-dur');},1600);
}

/* Preview the current slide's per-object animations, in build order (matches runtime) */
function applyAnimations(){
  var sl=stage._slides[stage._idx]; if(!sl) return;
  var els=Array.prototype.slice.call(sl.querySelectorAll('[data-anim]')).filter(function(el){
    var a=el.dataset.anim; return a && a!=='none' && a!=='anim-none';
  });
  els.sort(function(a,b){return (parseInt(a.dataset.animOrder||'0',10)-parseInt(b.dataset.animOrder||'0',10));});
  els.forEach(function(el,i){
    var name=el.dataset.anim; var cls=name.indexOf('anim-')===0?name:'anim-'+name;
    var dur=parseFloat(el.dataset.animDur||'0.5')||0.5;
    var delay=(parseFloat(el.dataset.animDelay||'0')||0)+i*0.18;
    el.style.setProperty('--anim-dur',dur+'s'); el.style.setProperty('--anim-delay',delay+'s');
    el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
    setTimeout(function(){el.classList.remove(cls); el.style.removeProperty('--anim-dur'); el.style.removeProperty('--anim-delay');},(delay+dur)*1000+500);
  });
}

function setAnimation(name) {
  if (!_selEl) return;
  pushUndo();
  // Never leave an anim-* class on the element while editing (would hide it).
  _selEl.className = _selEl.className.replace(/\banim-\S+/g, '').trim();
  if (!name || name === 'none') {
    delete _selEl.dataset.anim;
    delete _selEl.dataset.animDur;
    delete _selEl.dataset.animDelay;
    delete _selEl.dataset.animOrder;
    scheduleAutosave();
    return;
  }
  _selEl.dataset.anim = name;
  var durInp=document.getElementById('ed-anim-dur');
  var delayInp=document.getElementById('ed-anim-delay');
  var orderInp=document.getElementById('ed-anim-order');
  if(durInp&&durInp.value) _selEl.dataset.animDur=durInp.value;
  if(delayInp&&delayInp.value!=='') _selEl.dataset.animDelay=delayInp.value;
  if(orderInp&&orderInp.value!=='') _selEl.dataset.animOrder=orderInp.value;
  scheduleAutosave();
  applyAnimations(); // immediate preview so the user sees what they applied
}

function changeLayout(layoutKey) {
  if (!stage || stage._idx < 0) return;
  var sl = stage._slides[stage._idx];
  if (!sl) return;
  pushUndo();
  var data = extractContent(sl);
  var builder = LAYOUT_BUILDERS[layoutKey];
  if (!builder) return;
  // Preserve chrome elements
  var chromeTop = sl.querySelector('.chrome-top');
  var chromeBot = sl.querySelector('.chrome-bot');
  var chromeTopHTML = chromeTop ? chromeTop.outerHTML : '';
  var chromeBotHTML = chromeBot ? chromeBot.outerHTML : '';
  // Determine slide class
  var slideClass = sl.className;
  sl.innerHTML = chromeTopHTML + builder(data.title, data.items, data.imgs) + chromeBotHTML;
  sl.className = slideClass;
  buildSlideList();
}

function moveSlide(dir) {
  if (!stage || stage._idx < 0) return;
  var slides = stage._slides;
  var idx = stage._idx;
  var newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= slides.length) return;
  pushUndo();
  var sl = slides[idx];
  var ref = dir > 0 ? slides[newIdx].nextSibling : slides[newIdx];
  stage.insertBefore(sl, ref);
  reloadStage();
  stage._show(newIdx);
  buildSlideList();
}

function insertShape(shapeType) {
  if (!stage || stage._idx < 0) return;
  var sl = stage._slides[stage._idx];
  pushUndo();
  var wrap = document.createElement('div');
  wrap.className = 'ed-shape';
  wrap.style.cssText = 'position:absolute;left:400px;top:300px;width:200px;height:200px;z-index:500';
  var svgNS = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', '0 0 200 200');
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');
  svg.style.overflow = 'visible';
  var el;
  switch (shapeType) {
    case 'rect':
      el = document.createElementNS(svgNS, 'rect');
      el.setAttribute('x', '2'); el.setAttribute('y', '2');
      el.setAttribute('width', '196'); el.setAttribute('height', '196');
      el.setAttribute('rx', '0');
      break;
    case 'ellipse':
      el = document.createElementNS(svgNS, 'ellipse');
      el.setAttribute('cx', '100'); el.setAttribute('cy', '100');
      el.setAttribute('rx', '98'); el.setAttribute('ry', '98');
      break;
    case 'triangle':
      el = document.createElementNS(svgNS, 'polygon');
      el.setAttribute('points', '100,2 198,198 2,198');
      break;
    case 'diamond':
      el = document.createElementNS(svgNS, 'polygon');
      el.setAttribute('points', '100,2 198,100 100,198 2,100');
      break;
    case 'arrow-right':
      el = document.createElementNS(svgNS, 'polygon');
      el.setAttribute('points', '0,40 140,40 140,0 200,100 140,200 140,160 0,160');
      break;
    case 'line':
      el = document.createElementNS(svgNS, 'line');
      el.setAttribute('x1', '0'); el.setAttribute('y1', '100');
      el.setAttribute('x2', '200'); el.setAttribute('y2', '100');
      break;
    default:
      el = document.createElementNS(svgNS, 'rect');
      el.setAttribute('x', '2'); el.setAttribute('y', '2');
      el.setAttribute('width', '196'); el.setAttribute('height', '196');
  }
  el.setAttribute('fill', 'rgba(91,155,213,0.6)');
  el.setAttribute('stroke', '#5b9bd5');
  el.setAttribute('stroke-width', '2');
  svg.appendChild(el);
  wrap.appendChild(svg);
  sl.appendChild(wrap);
  selectEl(wrap);
}

function insertImageIntoSlide(dataUrl) {
  if (!stage || stage._idx < 0) return;
  var sl = stage._slides[stage._idx];
  pushUndo();
  var fig = document.createElement('figure');
  fig.style.cssText = 'position:absolute;left:200px;top:200px;width:400px;height:300px;z-index:500;margin:0;overflow:hidden';
  var img = document.createElement('img');
  img.src = dataUrl;
  img.style.cssText = 'width:100%;height:100%;object-fit:contain';
  img.alt = 'Inserted image';
  fig.appendChild(img);
  sl.appendChild(fig);
  selectEl(fig);
}

function replaceImage(img) {
  if (!img) return;
  _targetImg = img;
  _imgMode = 'replace';
  if (imgPicker) imgPicker.click();
}

function tableInsertRow(above) {
  if (!_selEl) return;
  var tr = _selEl.closest ? _selEl.closest('tr') : null;
  if (!tr) {
    var tbl = _selEl.tagName === 'TABLE' ? _selEl : _selEl.closest('table');
    if (tbl) tr = tbl.querySelector('tr');
  }
  if (!tr) return;
  pushUndo();
  var newTr = tr.cloneNode(true);
  Array.from(newTr.cells).forEach(function(c) { c.textContent = ''; });
  if (above) tr.parentNode.insertBefore(newTr, tr);
  else tr.parentNode.insertBefore(newTr, tr.nextSibling);
}

function tableInsertCol(before) {
  if (!_selEl) return;
  var td = _selEl.closest ? _selEl.closest('td,th') : null;
  var tbl = _selEl.tagName === 'TABLE' ? _selEl : (_selEl.closest ? _selEl.closest('table') : null);
  if (!tbl) return;
  pushUndo();
  var colIdx = td ? Array.from(td.parentNode.children).indexOf(td) : 0;
  tbl.querySelectorAll('tr').forEach(function(row) {
    var cell = row.children[colIdx];
    if (!cell) return;
    var newCell = document.createElement(cell.tagName.toLowerCase() === 'th' ? 'th' : 'td');
    newCell.style.cssText = cell.style.cssText;
    newCell.textContent = '';
    if (before) row.insertBefore(newCell, cell);
    else row.insertBefore(newCell, cell.nextSibling);
  });
}

function updateShapeFill(color) {
  if (!_selEl || !stage.contains(_selEl)) return;
  pushUndo();
  var svgEls = _selEl.querySelectorAll('rect,ellipse,polygon,path');
  if (svgEls.length) {
    svgEls.forEach(function(el) { el.setAttribute('fill', color); });
  } else {
    _selEl.style.background = color;
  }
}

function updateShapeStroke(color, width, dash) {
  if (!_selEl || !stage.contains(_selEl)) return;
  pushUndo();
  var svgEls = _selEl.querySelectorAll('rect,ellipse,polygon,polyline,path,line');
  svgEls.forEach(function(el) {
    if (color !== null && color !== undefined) el.setAttribute('stroke', color);
    if (width !== null && width !== undefined) el.setAttribute('stroke-width', width);
    if (dash !== null && dash !== undefined) el.setAttribute('stroke-dasharray', dash);
  });
  if (!svgEls.length) {
    if (color) _selEl.style.borderColor = color;
    if (width) _selEl.style.borderWidth = width + 'px';
    if (dash === '') _selEl.style.borderStyle = 'solid';
    else if (dash) _selEl.style.borderStyle = 'dashed';
  }
}

var _canvas = null;
function initDrawCanvas() {
  if (_canvas) return;
  _canvas = document.createElement('canvas');
  _canvas.id = 'ed-draw-overlay';
  _canvas.width = 1920;
  _canvas.height = 1080;
  _canvas.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;z-index:9999;pointer-events:auto;cursor:crosshair';
  var sl = stage._slides[stage._idx];
  if (sl) sl.appendChild(_canvas);
  var ctx = _canvas.getContext('2d');
  ctx.strokeStyle = '#ff0000';
  ctx.lineWidth = 3;
  ctx.lineCap = 'round';
  var drawing = false;
  _canvas.addEventListener('mousedown', function(e) {
    drawing = true;
    var r = _canvas.getBoundingClientRect();
    var sx = (e.clientX - r.left) * (1920 / r.width);
    var sy = (e.clientY - r.top) * (1080 / r.height);
    ctx.beginPath();
    ctx.moveTo(sx, sy);
  });
  _canvas.addEventListener('mousemove', function(e) {
    if (!drawing) return;
    var r = _canvas.getBoundingClientRect();
    var sx = (e.clientX - r.left) * (1920 / r.width);
    var sy = (e.clientY - r.top) * (1080 / r.height);
    ctx.lineTo(sx, sy);
    ctx.stroke();
  });
  _canvas.addEventListener('mouseup', function() { drawing = false; });
}

function finalizeDraw() {
  if (_canvas) {
    _canvas.remove();
    _canvas = null;
  }
}


/* ── Image / text helpers ── */
function clearEditable(){ stage.querySelectorAll('[contenteditable]').forEach(function(el){ el.removeAttribute('contenteditable'); }); }
function attachImgListeners(){
  stage.querySelectorAll('figure').forEach(function(fig){
    var img=fig.querySelector('img');
    if(img&&!fig._edListener){
      fig._edListener=function(e){e.stopPropagation();replaceImage(img);};
      fig.addEventListener('dblclick',fig._edListener);
    }
  });
}
function detachImgListeners(){
  stage.querySelectorAll('figure').forEach(function(fig){
    if(fig._edListener){fig.removeEventListener('dblclick',fig._edListener);delete fig._edListener;}
  });
}

/* ── toggleList (module-level so inline onclick can call edToggleList) ── */
function toggleList(listType){
  var ae=document.activeElement;
  /* TEXT MODE */
  if(ae&&ae.isContentEditable&&stage.contains(ae)){
    var sel=window.getSelection();
    if(!sel||!sel.rangeCount) return;
    var node=sel.anchorNode;
    var elem=node.nodeType===3?node.parentElement:node;
    var existingLi=elem?elem.closest('li'):null;
    var existingList=existingLi?existingLi.closest('ul,ol'):null;
    if(existingList){
      pushUndo();
      if(existingList.tagName.toLowerCase()===listType){
        var frag=document.createDocumentFragment();
        Array.from(existingList.children).forEach(function(li){
          var p=document.createElement('p'); p.innerHTML=li.innerHTML; frag.appendChild(p);
        });
        existingList.parentNode.insertBefore(frag,existingList); existingList.remove();
      } else {
        var nl=document.createElement(listType);
        nl.innerHTML=existingList.innerHTML; nl.style.cssText=existingList.style.cssText;
        existingList.parentNode.replaceChild(nl,existingList);
      }
      return;
    }
    pushUndo();
    var cmd=listType==='ol'?'insertOrderedList':'insertUnorderedList';
    document.execCommand(cmd);
    return;
  }
  /* SELECT MODE */
  // If a li is selected, add a sibling li after it in the same list
  if(_selEl&&_selEl.tagName==='LI'){
    var pList=_selEl.parentElement;
    if(pList){
      pushUndo();
      var nli=document.createElement('li'); nli.textContent='New item';
      pList.insertBefore(nli,_selEl.nextSibling); selectEl(nli); return;
    }
  }
  // If a matching list is selected, append a new li to it
  if(_selEl&&_selEl.tagName.toLowerCase()===listType){
    pushUndo();
    var nli2=document.createElement('li'); nli2.textContent='New item';
    _selEl.appendChild(nli2); selectEl(nli2); return;
  }
  pushUndo();
  var sl=stage._slides[stage._idx];
  if(!_selEl||!stage.contains(_selEl)){
    var nl2=document.createElement(listType);
    nl2.style.cssText='position:absolute;left:200px;top:300px;width:600px;font-size:52px;color:var(--text,#fff)';
    var li0=document.createElement('li'); li0.textContent='Item 1';
    nl2.appendChild(li0); sl.appendChild(nl2); selectEl(nl2); return;
  }
  var tag=_selEl.tagName.toLowerCase();
  if(tag==='ul'||tag==='ol'){
    if(tag===listType){
      var par=_selEl.parentNode, firstP=null;
      Array.from(_selEl.children).forEach(function(li,i){
        var p=document.createElement('p'); p.innerHTML=li.innerHTML;
        if(i===0){p.style.cssText=_selEl.style.cssText;firstP=p;}
        par.insertBefore(p,_selEl);
      });
      _selEl.remove(); if(firstP)selectEl(firstP); else clearSelection();
    } else {
      var nl3=document.createElement(listType);
      nl3.innerHTML=_selEl.innerHTML; nl3.style.cssText=_selEl.style.cssText;
      _selEl.parentNode.replaceChild(nl3,_selEl); selectEl(nl3);
    }
    return;
  }
  var nl4=document.createElement(listType);
  if(_selEl.style.position==='absolute') nl4.style.cssText=_selEl.style.cssText;
  var lines=(_selEl.innerHTML||'')
    .replace(/<\/(p|div|li)>/gi,'\n').split('\n')
    .map(function(l){return l.replace(/<[^>]+>/g,'').trim();})
    .filter(function(l){return l;});
  if(!lines.length) lines=[_selEl.textContent||'Item'];
  lines.forEach(function(line){var li3=document.createElement('li');li3.textContent=line;nl4.appendChild(li3);});
  _selEl.parentNode.insertBefore(nl4,_selEl); _selEl.remove(); selectEl(nl4);
}
window.edToggleList = function(type){ toggleList(type); };

/* ── wireEvents ── */
function wireEvents(){
  // 8 resize handles on selBox
  selBox.querySelectorAll('.ed-rh').forEach(function(rh){
    rh.addEventListener('mousedown',function(e){
      if(!_selEl) return;
      startResize(e, _selEl, rh.dataset.dir);
    });
  });

  document.addEventListener('click',function(e){
    if(!editing)return;
    if(selBox.contains(e.target)||groupBox.contains(e.target))return;
    if(stage.contains(e.target))return;
    if(header.contains(e.target))return;
    clearSelection(); clearGroupBox();
  },false);

  setTimeout(function(){
    imgPicker?.addEventListener('change',function(){
      var f=imgPicker.files[0];if(!f)return;
      var reader=new FileReader();
      reader.onload=function(ev){
        if(_imgMode==='replace'&&_targetImg)_targetImg.src=ev.target.result;
        else insertImageIntoSlide(ev.target.result);
      };
      reader.readAsDataURL(f);
    });

    document.getElementById('ed-done-btn')?.addEventListener('click',function(){ editing?exitEditMode():enterEditMode(); });
    document.getElementById('ed-save-btn')?.addEventListener('click',saveHTML);
    // ed-view-btn (Slide Show) fullscreen is handled in buildUI — no duplicate handler here

    document.querySelectorAll('.ed-tab[data-tab]').forEach(function(btn){
      btn.addEventListener('click',function(){ switchTab(btn.dataset.tab); });
    });

    document.getElementById('ed-mode-select')?.addEventListener('click',function(){setMode('select');});
    document.getElementById('ed-mode-text')?.addEventListener('click',function(){setMode('text');});
    document.getElementById('ed-mode-draw')?.addEventListener('click',function(){setMode('draw');});
    document.getElementById('ed-edit-text-btn')?.addEventListener('click',function(){setMode('text');});
    document.getElementById('ed-bold')?.addEventListener('click',function(){
      applyToSelected(function(){ document.execCommand('bold'); },
        function(el){ el.style.fontWeight=el.style.fontWeight==='bold'?'':'bold'; });
    });
    document.getElementById('ed-italic')?.addEventListener('click',function(){
      applyToSelected(function(){ document.execCommand('italic'); },
        function(el){ el.style.fontStyle=el.style.fontStyle==='italic'?'':'italic'; });
    });
    document.getElementById('ed-underline')?.addEventListener('click',function(){
      applyToSelected(function(){ document.execCommand('underline'); },
        function(el){ var td=el.style.textDecoration||''; el.style.textDecoration=td.includes('underline')?td.replace('underline','').trim():(td+' underline').trim(); });
    });
    document.getElementById('ed-strike')?.addEventListener('click',function(){
      applyToSelected(function(){ document.execCommand('strikeThrough'); },
        function(el){ var td=el.style.textDecoration||''; el.style.textDecoration=td.includes('line-through')?td.replace('line-through','').trim():(td+' line-through').trim(); });
    });
    document.getElementById('ed-txt-color')?.addEventListener('input',function(e){
      applyToSelected(function(){ document.execCommand('foreColor',false,e.target.value); },
        function(el){ el.style.color=e.target.value; });
    });
    document.getElementById('ed-hi-color')?.addEventListener('input',function(e){
      applyToSelected(
        function(){ document.execCommand('hiliteColor',false,e.target.value)||document.execCommand('backColor',false,e.target.value); },
        function(el){ el.style.backgroundColor=e.target.value; });
    });
    document.getElementById('ed-bg-color')?.addEventListener('input',function(e){
      if(stage._slides&&stage._slides[stage._idx])stage._slides[stage._idx].style.background=e.target.value;
    });
    [['l','justifyLeft','left'],['c','justifyCenter','center'],['r','justifyRight','right'],['j','justifyFull','justify']].forEach(function(row){
      document.getElementById('ed-align-'+row[0])?.addEventListener('click',function(){
        applyToSelected(function(){ document.execCommand(row[1]); },
          function(el){ el.style.textAlign=row[2]; });
      });
    });
    document.getElementById('ed-font-sel')?.addEventListener('change',function(e){
      var active=document.activeElement;
      if(active&&active.isContentEditable){ active.style.fontFamily=e.target.value; }
      else if(_selEl){ _selEl.style.fontFamily=e.target.value; }
    });
    document.getElementById('ed-sz-input')?.addEventListener('change',function(){
      var ae=document.activeElement;
      var el=(ae&&ae.isContentEditable&&stage.contains(ae))?ae:_selEl;
      if(!el||!stage.contains(el)) return; pushUndo();
      var nz=Math.max(6,Math.min(400,parseInt(this.value)||24));
      el.style.fontSize=nz+'px'; this.value=nz;
    });
    document.getElementById('ed-sz-up')?.addEventListener('click',function(){
      var ae=document.activeElement;
      var el=(ae&&ae.isContentEditable&&stage.contains(ae))?ae:_selEl;
      if(!el||!stage.contains(el)) return;
      var sz=parseFloat(getComputedStyle(el).fontSize)||24;
      var nz=Math.round(sz+2); el.style.fontSize=nz+'px';
      var inp=document.getElementById('ed-sz-input'); if(inp)inp.value=nz;
    });
    document.getElementById('ed-sz-dn')?.addEventListener('click',function(){
      var ae=document.activeElement;
      var el=(ae&&ae.isContentEditable&&stage.contains(ae))?ae:_selEl;
      if(!el||!stage.contains(el)) return;
      var sz=parseFloat(getComputedStyle(el).fontSize)||24;
      var nz=Math.max(6,Math.round(sz-2)); el.style.fontSize=nz+'px';
      var inp=document.getElementById('ed-sz-input'); if(inp)inp.value=nz;
    });
    document.getElementById('ed-bullet-style')?.addEventListener('change',function(e){
      var sel=window.getSelection();
      var ul=null;
      if(sel&&sel.anchorNode) ul=sel.anchorNode.nodeType===1?sel.anchorNode.closest('ul,ol'):(sel.anchorNode.parentElement?sel.anchorNode.parentElement.closest('ul,ol'):null);
      if(!ul) ul=stage._slides[stage._idx].querySelector('ul,ol');
      if(ul)ul.style.listStyleType=e.target.value;
    });
    document.getElementById('ed-indent')?.addEventListener('click',function(){document.execCommand('indent');});
    document.getElementById('ed-outdent')?.addEventListener('click',function(){document.execCommand('outdent');});
    document.getElementById('ed-bring-front')?.addEventListener('click',bringToFront);
    document.getElementById('ed-send-back')?.addEventListener('click',sendToBack);
    document.getElementById('ed-no-fill')?.addEventListener('click',function(){
      if(!_selEl||!stage.contains(_selEl)) return; pushUndo();
      var svgFills=_selEl.querySelectorAll('rect,ellipse,polygon,path');
      if(svgFills.length){ svgFills.forEach(function(el){el.setAttribute('fill','none');el.removeAttribute('fill-opacity');}); }
      else { _selEl.style.background='transparent'; }
    });
    document.getElementById('ed-no-stroke')?.addEventListener('click',function(){
      if(!_selEl||!stage.contains(_selEl)) return; pushUndo();
      var svgEls=_selEl.querySelectorAll('rect,ellipse,polygon,polyline,path,line');
      if(svgEls.length){ svgEls.forEach(function(el){el.setAttribute('stroke','none');}); }
      else { _selEl.style.border='none'; }
    });
    document.getElementById('ed-stroke-dash')?.addEventListener('change',function(){
      if(!_selEl||!stage.contains(_selEl)) return; pushUndo();
      updateShapeStroke(null,undefined,this.value);
    });
    document.getElementById('ed-shadow-btn')?.addEventListener('click',function(){
      if(!_selEl||!stage.contains(_selEl)) return; pushUndo();
      var f=_selEl.style.filter||'';
      var hasShadow=f.includes('drop-shadow')||!!_selEl.style.boxShadow;
      if(hasShadow){
        _selEl.style.filter=f.replace(/drop-shadow\([^)]+\)/,'').trim();
        _selEl.style.boxShadow='';
      } else {
        if(_selEl.classList.contains('ed-shape')||_selEl.querySelector('svg')){
          _selEl.style.filter=(f+' drop-shadow(3px 5px 10px rgba(0,0,0,0.7))').trim();
        } else {
          _selEl.style.boxShadow='4px 6px 16px rgba(0,0,0,0.6)';
        }
      }
      this.classList.toggle('active-btn',!hasShadow);
    });
    document.getElementById('ed-border-radius')?.addEventListener('input',function(){
      if(!_selEl||!stage.contains(_selEl)) return;
      var r=Math.max(0,parseInt(this.value)||0);
      _selEl.style.borderRadius=r+'px';
      _selEl.querySelectorAll('rect').forEach(function(el){el.setAttribute('rx',r);el.setAttribute('ry',r);});
    });
    document.getElementById('ed-bring-fwd')?.addEventListener('click',bringForward);
    document.getElementById('ed-send-bwd')?.addEventListener('click',sendBackward);
    document.getElementById('ed-group')?.addEventListener('click',groupSelected);
    document.getElementById('ed-ungroup')?.addEventListener('click',ungroupSelected);
    document.getElementById('ed-free-layout')?.addEventListener('click',function(){
      var sl=stage._slides[stage._idx]; if(!sl) return;
      pushUndo(); freezeSlide(sl,true); updateSelBox();
    });
    document.getElementById('ed-flip-h')?.addEventListener('click',function(){
      if(!_selEl||!stage.contains(_selEl)) return; pushUndo();
      var cur=_selEl.style.transform||'';
      _selEl.style.transform=cur.includes('scaleX(-1)')?cur.replace('scaleX(-1)','').trim():(cur+' scaleX(-1)').trim();
    });
    document.getElementById('ed-flip-v')?.addEventListener('click',function(){
      if(!_selEl||!stage.contains(_selEl)) return; pushUndo();
      var cur=_selEl.style.transform||'';
      _selEl.style.transform=cur.includes('scaleY(-1)')?cur.replace('scaleY(-1)','').trim():(cur+' scaleY(-1)').trim();
    });
    ['l','c','r','t','m','b'].forEach(function(d){
      document.getElementById('ed-obj-a'+d)?.addEventListener('click',function(){ alignToSlide(d); });
    });
    document.getElementById('ed-shape-fill')?.addEventListener('input',function(e){updateShapeFill(e.target.value);});
    document.getElementById('ed-shape-stroke')?.addEventListener('input',function(e){updateShapeStroke(e.target.value,undefined,undefined);});
    document.getElementById('ed-shape-sw')?.addEventListener('change',function(e){updateShapeStroke(null,e.target.value);});
    document.getElementById('ed-opacity')?.addEventListener('input',function(e){
      if(_selEl)_selEl.style.opacity=(parseInt(e.target.value)/100).toFixed(2);
    });
    document.getElementById('ed-link-btn')?.addEventListener('click',addLink);

    /* ── Contextual: Picture ── */
    document.getElementById('ed-img-replace')?.addEventListener('click',function(){
      var img=_selEl&&_selEl.querySelector('img'); if(!img) return;
      replaceImage(img);
    });
    document.getElementById('ed-img-fit')?.addEventListener('change',function(){
      var img=_selEl&&_selEl.querySelector('img'); if(!img) return; pushUndo();
      img.style.objectFit=this.value;
    });
    function _applyImgFilter(){
      var img=_selEl&&_selEl.querySelector('img'); if(!img) return;
      var br=document.getElementById('ed-img-brightness').value||100;
      var co=document.getElementById('ed-img-contrast').value||100;
      img.style.filter='brightness('+br+'%) contrast('+co+'%)';
    }
    document.getElementById('ed-img-brightness')?.addEventListener('input',_applyImgFilter);
    document.getElementById('ed-img-contrast')?.addEventListener('input',_applyImgFilter);
    document.getElementById('ed-img-reset')?.addEventListener('click',function(){
      var img=_selEl&&_selEl.querySelector('img'); if(!img) return; pushUndo();
      img.style.filter=''; img.style.objectFit='contain';
      document.getElementById('ed-img-brightness').value=100;
      document.getElementById('ed-img-contrast').value=100;
      document.getElementById('ed-img-fit').value='contain';
    });

    /* ── Contextual: Table ── */
    document.getElementById('ed-tbl-row-above')?.addEventListener('click',function(){tableInsertRow(true);});
    document.getElementById('ed-tbl-row-below')?.addEventListener('click',function(){tableInsertRow(false);});
    document.getElementById('ed-tbl-del-row')?.addEventListener('click',tableDeleteRow);
    document.getElementById('ed-tbl-col-left')?.addEventListener('click',function(){tableInsertCol(true);});
    document.getElementById('ed-tbl-col-right')?.addEventListener('click',function(){tableInsertCol(false);});
    document.getElementById('ed-tbl-del-col')?.addEventListener('click',tableDeleteCol);
    document.getElementById('ed-tbl-header-color')?.addEventListener('input',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
      var c=this.value;
      _selEl.querySelectorAll('thead th,thead td').forEach(function(cell){cell.style.background=c;});
    });
    document.getElementById('ed-tbl-cell-color')?.addEventListener('input',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
      var c=this.value;
      _selEl.querySelectorAll('tbody td').forEach(function(cell){cell.style.background=c;});
    });

    /* ── NEW: Clipboard ── */
    document.getElementById('ed-cut-btn')?.addEventListener('click',function(){
      if(_mode==='select'&&_selEl){ pushUndo(); _clipboard=_selEl.cloneNode(true); _selEl.remove(); clearSelection(); }
      else { document.execCommand('cut'); }
    });
    document.getElementById('ed-copy-btn')?.addEventListener('click',function(){
      if(_mode==='select'&&_selEl){ _clipboard=_selEl.cloneNode(true); }
      else { document.execCommand('copy'); }
    });
    document.getElementById('ed-paste-btn')?.addEventListener('click',function(){
      if(_mode==='select'&&_clipboard){
        pushUndo(); var _cl2=_clipboard.cloneNode(true);
        var _hasPos2=(_cl2.style.left&&parseFloat(_cl2.style.left)>0);
        _cl2.style.left=(_hasPos2?parseFloat(_cl2.style.left)+20:920)+'px';
        _cl2.style.top=(_hasPos2?parseFloat(_cl2.style.top)+20:460)+'px';
        _cl2.style.position='absolute';
        stage._slides[stage._idx].appendChild(_cl2); selectEl(_cl2);
      } else { document.execCommand('paste'); }
    });
    _fmtPaintBtn=document.getElementById('ed-format-paint');
    _fmtPaintBtn?.addEventListener('click',function(){
      if(!_selEl) return;
      _fmtPaint=true;
      _fmtStyles={fontFamily:_selEl.style.fontFamily,fontSize:_selEl.style.fontSize,
        fontWeight:_selEl.style.fontWeight,fontStyle:_selEl.style.fontStyle,
        textDecoration:_selEl.style.textDecoration,color:_selEl.style.color};
      _fmtPaintBtn.classList.add('active-btn');
    });

    /* ── NEW: Font extras ── */
    document.getElementById('ed-text-shadow')?.addEventListener('click',function(){
      var ae=document.activeElement;
      var el=(ae&&ae.isContentEditable&&stage.contains(ae))?ae:_selEl;
      if(!el||!stage.contains(el)) return;
      el.style.textShadow=el.style.textShadow?'':'2px 2px 6px rgba(0,0,0,0.7)';
      this.classList.toggle('active-btn',!!el.style.textShadow);
    });
    document.getElementById('ed-change-case')?.addEventListener('click',function(){
      var ae=document.activeElement;
      var el=(ae&&ae.isContentEditable&&stage.contains(ae))?ae:_selEl;
      if(!el||!stage.contains(el)) return; pushUndo();
      var t=el.innerText||el.textContent||'';
      if(t===t.toUpperCase()) el.innerText=t.toLowerCase();
      else if(t===t.toLowerCase()) el.innerText=t.replace(/\b\w/g,function(c){return c.toUpperCase();});
      else el.innerText=t.toUpperCase();
    });
    document.getElementById('ed-clear-format')?.addEventListener('click',function(){
      var ae=document.activeElement;
      var el=(ae&&ae.isContentEditable&&stage.contains(ae))?ae:_selEl;
      if(!el||!stage.contains(el)) return; pushUndo();
      el.removeAttribute('style');
    });

    /* ── NEW: Paragraph extras ── */
    document.getElementById('ed-line-spacing')?.addEventListener('change',function(e){
      if(!_selEl||!stage.contains(_selEl)) return; pushUndo();
      _selEl.style.lineHeight=e.target.value;
    });
    document.getElementById('ed-add-numbered')?.addEventListener('click',function(){ edToggleList('ol'); });

    /* ── NEW: Editing group ── */
    document.getElementById('ed-find-replace')?.addEventListener('click',function(){
      var find=window.prompt('Find text:'); if(!find) return;
      var replace=window.prompt('Replace with:',''); if(replace===null) return;
      var sl=stage._slides[stage._idx]; pushUndo();
      (function walk(n){
        if(n.nodeType===3&&n.textContent.includes(find)){
          n.textContent=n.textContent.split(find).join(replace);
        } else { n.childNodes.forEach(walk); }
      })(sl);
    });
    document.getElementById('ed-select-all')?.addEventListener('click',function(){
      var sl=stage._slides[stage._idx];
      var els=[].slice.call(sl.querySelectorAll(MOVE_SEL));
      if(els.length){ _multiSel=els; updateGroupBox(); }
    });

    document.getElementById('ed-add-bullet')?.addEventListener('click',function(){ edToggleList('ul'); });
    document.getElementById('ed-add-table')?.addEventListener('click',insertTable);
    document.getElementById('ed-add-img')?.addEventListener('click',insertImage);
    document.getElementById('ed-add-textbox')?.addEventListener('click',insertTextBox);
    document.querySelectorAll('.ed-shape-btn').forEach(function(btn){
      btn.addEventListener('click',function(){insertShape(btn.dataset.shape);});
    });

    document.getElementById('ed-layout-sel')?.addEventListener('change',function(e){
      if(e.target.value){changeLayout(e.target.value);e.target.value='';}
    });
    document.querySelectorAll('.ed-swatch').forEach(function(sw){
      sw.addEventListener('click',function(){
        applyTheme(sw.dataset.theme);
        document.querySelectorAll('.ed-swatch').forEach(function(s){s.classList.toggle('active',s===sw);});
      });
    });
    document.getElementById('ed-transition-sel')?.addEventListener('change',function(e){setTransition(e.target.value);});
    document.getElementById('ed-transition-dur')?.addEventListener('change',function(){
      var sel=document.getElementById('ed-transition-sel'); if(sel) setTransition(sel.value);
    });
    document.getElementById('ed-trans-preview')?.addEventListener('click',previewTransition);
    document.getElementById('ed-trans-all')?.addEventListener('click',setTransitionAll);

    document.getElementById('ed-anim-apply')?.addEventListener('click',function(){
      var sel=document.getElementById('ed-anim-sel');
      if(!_selEl){ toastMsg('Select an object first'); return; }
      if(sel)setAnimation(sel.value);
    });
    document.getElementById('ed-anim-preview')?.addEventListener('click',previewAnimation);
    document.getElementById('ed-anim-remove')?.addEventListener('click',function(){
      if(_selEl) setAnimation('none');
    });

    document.getElementById('ed-add-btn')?.addEventListener('click',addSlide);
    document.getElementById('ed-dup-btn')?.addEventListener('click',duplicateSlide);
    document.getElementById('ed-move-up-btn')?.addEventListener('click',function(){moveSlide(-1);});
    document.getElementById('ed-move-dn-btn')?.addEventListener('click',function(){moveSlide(1);});
    document.getElementById('ed-del-btn')?.addEventListener('click',deleteSlide);

    document.addEventListener('focusin',function(e){
      if(e.target&&e.target.isContentEditable){
        var sz=parseFloat(getComputedStyle(e.target).fontSize)||24;
        var inp=document.getElementById('ed-sz-input');
        if(inp) inp.value=Math.round(sz);
      }
    });

    document.querySelectorAll('#ed-header button, #ed-header input[type=color]').forEach(function(btn){
      btn.addEventListener('mousedown',function(e){ e.preventDefault(); });
    });

    document.getElementById('ed-collapse-ribbon')?.addEventListener('click',function(){
      var r=document.getElementById('ed-ribbon');
      var collapsed=r.classList.toggle('collapsed');
      this.classList.toggle('collapsed',collapsed);
      this.textContent=collapsed?'▼':'▲';
      editorScale();
    });
    document.getElementById('ed-toggle-panel')?.addEventListener('click',function(){
      var wasActive=slidePanel.classList.contains('active');
      if(wasActive){ slidePanel.classList.remove('active'); outlinePanel.classList.remove('active'); }
      else { slidePanel.classList.add('active'); }
      requestAnimationFrame(function(){ editorScale(); });
    });

    document.getElementById('ed-wrap-text')?.addEventListener('click',function(){
      var ae=document.activeElement;
      var el=(_mode==='select'&&_selEl)?_selEl:(ae&&ae.isContentEditable&&stage.contains(ae)?ae:null);
      if(!el||!stage.contains(el)) return; pushUndo();
      el.style.whiteSpace=el.style.whiteSpace==='nowrap'?'normal':'nowrap';
      el.style.overflowWrap=el.style.whiteSpace==='nowrap'?'normal':'break-word';
      this.classList.toggle('active-btn', el.style.whiteSpace==='nowrap');
    });

    /* ── Context tab click handlers ── */
    document.querySelectorAll('.ed-ctx-tab').forEach(function(tab){
      tab.addEventListener('click',function(){ switchTab(tab.dataset.ctxTab,true); });
    });
    /* Update normal tab click handlers to use new switchTab signature */
    document.querySelectorAll('.ed-tab[data-tab]').forEach(function(tab){
      tab.addEventListener('click',function(){ switchTab(tab.dataset.tab); });
    });

    /* ── Fix D3: Custom color + eyedropper ── */
    document.getElementById('ed-custom-color-native')?.addEventListener('input',function(){
      var h=document.getElementById('ed-custom-color-hex'); if(h) h.value=this.value;
    });
    document.getElementById('ed-custom-color-hex')?.addEventListener('input',function(){
      if(/^#[0-9A-Fa-f]{6}$/.test(this.value)){
        var n=document.getElementById('ed-custom-color-native'); if(n) n.value=this.value;
      }
    });
    document.getElementById('ed-custom-color-apply')?.addEventListener('click',function(){
      var hex=(document.getElementById('ed-custom-color-hex').value||'').trim();
      if(!hex) hex=document.getElementById('ed-custom-color-native').value;
      if(hex) window.applyPickedColor(hex);
    });
    document.getElementById('ed-eyedropper')?.addEventListener('click',function(){
      if(!window.EyeDropper){ alert('Eyedropper chưa hỗ trợ. Dùng Chrome/Edge 95+.'); return; }
      colorPicker.classList.remove('active');
      new EyeDropper().open().then(function(r){ window.applyPickedColor(r.sRGBHex); }).catch(function(){});
    });

    /* ── Table Design handlers ── */
    ['header','total','banded','firstcol','lastcol'].forEach(function(opt){
      document.getElementById('ed-tbl-opt-'+opt)?.addEventListener('change',function(){
        if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
        _selEl.classList.toggle('tbl-'+opt,this.checked);
      });
    });
    document.querySelectorAll('.ed-tbl-style-swatch').forEach(function(sw){
      sw.addEventListener('click',function(){
        if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
        _selEl.querySelectorAll('thead th,thead td').forEach(function(c){c.style.background=sw.dataset.hdr;c.style.color='#fff';});
        _selEl.querySelectorAll('tbody tr:nth-child(even) td').forEach(function(c){c.style.background=sw.dataset.row;});
      });
    });
    document.getElementById('ed-tbl-border-all')?.addEventListener('click',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
      var c=document.getElementById('ed-tbl-border-color').value, w=document.getElementById('ed-tbl-border-width').value;
      _selEl.querySelectorAll('td,th').forEach(function(cell){cell.style.border=w+'px solid '+c;});
    });
    document.getElementById('ed-tbl-border-none')?.addEventListener('click',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
      _selEl.querySelectorAll('td,th').forEach(function(cell){cell.style.border='none';});
      _selEl.style.border='none';
    });
    document.getElementById('ed-tbl-border-outside')?.addEventListener('click',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
      var c=document.getElementById('ed-tbl-border-color').value, w=document.getElementById('ed-tbl-border-width').value;
      _selEl.querySelectorAll('td,th').forEach(function(cell){cell.style.border='none';});
      _selEl.style.border=w+'px solid '+c;
    });

    /* ── Table Layout handlers ── */
    document.getElementById('ed-tbl-del-table')?.addEventListener('click',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo(); _selEl.remove(); clearSelection();
    });
    document.getElementById('ed-tbl-select-all')?.addEventListener('click',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return;
      var r=_selEl.getBoundingClientRect(), sr=stage.getBoundingClientRect(), sc=sr.width/1920;
      selBox.style.left=(r.left-sr.left)+'px'; selBox.style.top=(r.top-sr.top)+'px';
      selBox.style.width=r.width+'px'; selBox.style.height=r.height+'px';
    });
    ['al','ac','ar'].forEach(function(a,i){
      var align=['left','center','right'][i];
      document.getElementById('ed-cell-'+a)?.addEventListener('click',function(){
        var td=document.activeElement.closest('td,th'); if(td){td.style.textAlign=align;}
        else if(_selEl&&_selEl.tagName==='TABLE'){_selEl.querySelectorAll('td,th').forEach(function(c){c.style.textAlign=align;});}
      });
    });
    ['vt','vm','vb'].forEach(function(a,i){
      var align=['top','middle','bottom'][i];
      document.getElementById('ed-cell-'+a)?.addEventListener('click',function(){
        var td=document.activeElement.closest('td,th'); if(td){td.style.verticalAlign=align;}
        else if(_selEl&&_selEl.tagName==='TABLE'){_selEl.querySelectorAll('td,th').forEach(function(c){c.style.verticalAlign=align;});}
      });
    });
    document.getElementById('ed-tbl-dist-rows')?.addEventListener('click',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
      var rows=_selEl.querySelectorAll('tr'), h=Math.round(_selEl.offsetHeight/rows.length);
      rows.forEach(function(r){r.style.height=h+'px';});
    });
    document.getElementById('ed-tbl-dist-cols')?.addEventListener('click',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
      var firstRow=_selEl.querySelector('tr'), n=firstRow?firstRow.querySelectorAll('td,th').length:0;
      if(!n) return;
      var w=Math.round(_selEl.offsetWidth/n);
      _selEl.querySelectorAll('td,th').forEach(function(c){c.style.width=w+'px';});
    });
    document.getElementById('ed-tbl-merge')?.addEventListener('click',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return;
      var sel=window.getSelection(); if(!sel||!sel.rangeCount) return; pushUndo();
      var anchor=sel.anchorNode, focus=sel.focusNode;
      var td1=(anchor.nodeType===3?anchor.parentElement:anchor).closest('td,th');
      var td2=(focus.nodeType===3?focus.parentElement:focus).closest('td,th');
      if(!td1||!td2||td1===td2||td1.parentElement!==td2.parentElement) return;
      var cells=Array.from(td1.parentElement.querySelectorAll('td,th'));
      var i1=cells.indexOf(td1), i2=cells.indexOf(td2);
      if(i1>i2){var tmp=td1;td1=td2;td2=tmp;tmp=null;}
      td1.colSpan=(td1.colSpan||1)+(td2.colSpan||1);
      td1.innerHTML+=td2.innerHTML; td2.remove();
    });
    document.getElementById('ed-tbl-split')?.addEventListener('click',function(){
      var td=document.activeElement.closest('td,th'); if(!td||(td.colSpan||1)<=1) return; pushUndo();
      var span=td.colSpan; td.colSpan=1;
      for(var i=1;i<span;i++){
        var n=document.createElement(td.tagName); n.style.cssText=td.style.cssText;
        td.parentElement.insertBefore(n,td.nextSibling);
      }
    });
    document.getElementById('ed-ctx-width')?.addEventListener('change',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
      var sc=stage.getBoundingClientRect().width/1920;
      _selEl.style.width=(this.value*sc)+'px'; updateSelBox();
    });
    document.getElementById('ed-ctx-height')?.addEventListener('change',function(){
      if(!_selEl||_selEl.tagName!=='TABLE') return; pushUndo();
      var sc=stage.getBoundingClientRect().width/1920;
      _selEl.style.height=(this.value*sc)+'px'; updateSelBox();
    });

    /* ── Picture Format new handlers ── */
    document.getElementById('ed-img-remove-bg')?.addEventListener('click',function(){
      if(!_selEl) return; pushUndo();
      var img=_selEl.querySelector('img')||(_selEl.tagName==='IMG'?_selEl:null); if(!img) return;
      img.style.mixBlendMode=img.style.mixBlendMode?'':'multiply';
    });
    document.getElementById('ed-img-opacity')?.addEventListener('input',function(){
      if(!_selEl) return;
      var img=_selEl.querySelector('img')||(_selEl.tagName==='IMG'?_selEl:null);
      if(img) img.style.opacity=(this.value/100);
    });
    document.getElementById('ed-img-border-none')?.addEventListener('click',function(){
      if(!_selEl) return; pushUndo(); _selEl.style.border='none'; _selEl.style.borderRadius='';
    });
    document.getElementById('ed-img-border-thin')?.addEventListener('click',function(){
      if(!_selEl) return; pushUndo(); _selEl.style.outline='4px solid rgba(255,255,255,0.5)';
    });
    document.getElementById('ed-img-border-thick')?.addEventListener('click',function(){
      if(!_selEl) return; pushUndo(); _selEl.style.outline='12px solid rgba(255,255,255,0.5)';
    });
    document.getElementById('ed-img-round')?.addEventListener('click',function(){
      if(!_selEl) return; pushUndo(); _selEl.style.borderRadius=_selEl.style.borderRadius?'':'16px';
    });
    document.getElementById('ed-img-shadow')?.addEventListener('click',function(){
      if(!_selEl) return; pushUndo(); _selEl.style.boxShadow=_selEl.style.boxShadow?'':'8px 12px 24px rgba(0,0,0,0.5)';
    });
    document.getElementById('ed-img-front')?.addEventListener('click',function(){ if(_selEl) bringToFront(); });
    document.getElementById('ed-img-back')?.addEventListener('click',function(){ if(_selEl) sendToBack(); });
    ['al','ac','ar'].forEach(function(a,i){
      var d=['l','c','r'][i];
      document.getElementById('ed-img-'+a)?.addEventListener('click',function(){ alignToSlide(d); });
    });
    document.getElementById('ed-ctx-width-pic')?.addEventListener('change',function(){
      if(!_selEl) return; pushUndo();
      var sc=stage.getBoundingClientRect().width/1920;
      _selEl.style.width=(this.value*sc)+'px'; updateSelBox();
    });
    document.getElementById('ed-ctx-height-pic')?.addEventListener('change',function(){
      if(!_selEl) return; pushUndo();
      var sc=stage.getBoundingClientRect().width/1920;
      _selEl.style.height=(this.value*sc)+'px'; updateSelBox();
    });

    /* ── Shape Format context handlers ── */
    document.getElementById('ed-shape-no-fill')?.addEventListener('click',function(){
      if(!_selEl) return; pushUndo();
      var svgEl=_selEl.querySelector('rect,ellipse,polygon,polyline,path'); if(svgEl) svgEl.setAttribute('fill','none');
    });
    document.getElementById('ed-shape-front')?.addEventListener('click',function(){ if(_selEl) bringToFront(); });
    document.getElementById('ed-shape-back')?.addEventListener('click',function(){ if(_selEl) sendToBack(); });
    document.getElementById('ed-shape-group')?.addEventListener('click',function(){ groupSelected(); });
    document.getElementById('ed-shape-ungroup')?.addEventListener('click',function(){ ungroupSelected(); });
    ['al','ac','ar'].forEach(function(a,i){
      var d=['l','c','r'][i];
      document.getElementById('ed-shape-'+a)?.addEventListener('click',function(){ alignToSlide(d); });
    });
    document.getElementById('ed-flip-h-ctx')?.addEventListener('click',function(){
      if(!_selEl||!stage.contains(_selEl)) return; pushUndo();
      var cur=_selEl.style.transform||'';
      _selEl.style.transform=cur.includes('scaleX(-1)')?cur.replace('scaleX(-1)','').trim():(cur+' scaleX(-1)').trim();
    });
    document.getElementById('ed-flip-v-ctx')?.addEventListener('click',function(){
      if(!_selEl||!stage.contains(_selEl)) return; pushUndo();
      var cur=_selEl.style.transform||'';
      _selEl.style.transform=cur.includes('scaleY(-1)')?cur.replace('scaleY(-1)','').trim():(cur+' scaleY(-1)').trim();
    });
    document.getElementById('ed-ctx-width-shape')?.addEventListener('change',function(){
      if(!_selEl) return; pushUndo();
      var sc=stage.getBoundingClientRect().width/1920;
      _selEl.style.width=(this.value*sc)+'px'; updateSelBox();
    });
    document.getElementById('ed-ctx-height-shape')?.addEventListener('change',function(){
      if(!_selEl) return; pushUndo();
      var sc=stage.getBoundingClientRect().width/1920;
      _selEl.style.height=(this.value*sc)+'px'; updateSelBox();
    });
  },0);

  addStageListeners();

  // Clean up stale drag if user releases mouse outside browser window
  window.addEventListener('blur',function(){ if(_dragCleanup){_dragCleanup(); _dragCleanup=null;} });

  // Fix 4f: build theme swatches in Design tab
  var swatchContainer=document.getElementById('ed-theme-swatches');
  if(swatchContainer){
    Object.keys(THEMES).forEach(function(key){
      var t=THEMES[key];
      var sw=document.createElement('div');
      sw.className='ed-btn ed-swatch';
      sw.dataset.theme=key;
      sw.title=key;
      sw.style.cssText='width:32px;height:32px;border-radius:4px;background:'+t.accent+';border:2px solid transparent;cursor:pointer;margin:2px';
      swatchContainer.appendChild(sw);
    });
  }
}


function enterEditMode(){
  editing=true;
  if(header) header.classList.add('active');
  if(slidePanel) slidePanel.classList.add('active');
  document.body.setAttribute('data-editing','');
  document.body.setAttribute('data-mode', _mode);
  if(!_origScale) _origScale=stage._scale.bind(stage);
  if(stage._onResize) window.removeEventListener('resize', stage._onResize);
  editorScale();
  _resizeHandler=function(){ editorScale(); };
  window.addEventListener('resize', _resizeHandler);
  var curTheme=detectTheme();
  document.querySelectorAll('.ed-swatch').forEach(function(sw){sw.classList.toggle('active',sw.dataset.theme===curTheme);});
  /* Transitions + per-object animations are played by the runtime (deck-stage.js
   * _show → applySlideTransition/playSlideAnimations). No _show monkey-patch here;
   * object animations are auto-suppressed while `data-editing` is set so elements
   * stay visible during editing (use the Preview buttons to test them). */
  attachImgListeners();
  buildSlideList();
  document.addEventListener('slidechange', onSlideChange);
}

function exitEditMode(){
  editing=false;
  if(header) header.classList.remove('active');
  if(slidePanel) slidePanel.classList.remove('active');
  if(outlinePanel) outlinePanel.classList.remove('active');
  document.body.removeAttribute('data-editing');
  document.body.removeAttribute('data-mode');
  clearEditable();
  detachImgListeners();
  clearSelection();
  clearGroupBox();
  window.removeEventListener('resize', _resizeHandler||editorScale);
  _resizeHandler=null;
  if(_origScale){ stage._scale=_origScale; _origScale=null; }
  if(stage._onResize) window.addEventListener('resize', stage._onResize);
  ['background','padding','boxSizing'].forEach(function(p){ stage.style[p]=''; });
  stage._scale();
  _updateSlRect();
  document.removeEventListener('slidechange', onSlideChange);
  finalizeDraw();
}

/* ── Boot ── */
function _boot(){ buildUI(); initAutosave(); }
if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',_boot);
} else {
  _boot();
}

})();