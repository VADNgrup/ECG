import argparse
import json
import re
from pathlib import Path

import networkx as nx

from src.rag.kg_schema import infer_node_kind, normalize_graph_schema, validate_graph_schema

BASE_DIR = Path(__file__).resolve().parents[2]
KB_DIR = BASE_DIR / "data" / "cache" / "kb"

_PALETTE = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1",
    "#76b7b2", "#edc948", "#ff9da7", "#9c755f", "#bab0ac",
    "#8cd17d", "#b6992d", "#86bcb6", "#d37295", "#fabfd2",
]
# Heading hierarchy ramp: level 0 (document title) darkest -> deeper headings lighter, so headings read
# as a hierarchy AND stand apart from concept colors (which use _PALETTE).
_HEADING_RAMP = ["#08306b", "#2171b5", "#4292c6", "#6baed6", "#9ecae1", "#c6dbef", "#deebf7"]


def _load_graph(graphml: Path) -> nx.Graph:
    graph = nx.read_graphml(graphml)
    normalize_graph_schema(graph)
    validate_graph_schema(graph)
    return graph


def _heading_level(desc: str) -> int | None:
    m = re.search(r"level=(\d+)", desc or "")
    return int(m.group(1)) if m else None


def _struct_order(desc: str) -> int | None:
    mp = re.search(r"pages=(\d+)", desc)
    if not mp:
        return None
    ml = re.search(r"level=(\d+)", desc)
    return int(mp.group(1)) * 100 + (int(ml.group(1)) if ml else 0)


def _to_payload(g: nx.Graph) -> dict:
    types: dict[str, int] = {}
    nodes = []
    for nid, data in g.nodes(data=True):
        etype = (data.get("entity_type") or "concept").strip().strip('"') or "concept"
        kind = infer_node_kind(data)
        desc = (data.get("description") or "").strip().strip('"')
        is_heading = etype == "heading"
        types.setdefault(etype, len(types))
        node = {
            "id": str(nid),
            "label": str(nid).strip('"').replace("⁣", ""),
            "type": etype,
            "kind": kind,
            "desc": desc,
            "deg": g.degree(nid),
            "order": _struct_order(desc) if is_heading else None,
        }
        if is_heading:  # per-node colour by LEVEL (still ONE 'heading' type/legend entry)
            lvl = _heading_level(desc)
            if lvl is not None:
                node["color"] = _HEADING_RAMP[min(lvl, len(_HEADING_RAMP) - 1)]
        nodes.append(node)
    edges = []
    for u, v, data in g.edges(data=True):
        edges.append({
            "source": str(u),
            "target": str(v),
            "desc": (data.get("description") or "").strip().strip('"'),
            "kw": (data.get("keywords") or "").strip().strip('"'),
            "weight": float(data.get("weight", 1) or 1),
        })
    color_by_type = {t: _PALETTE[i % len(_PALETTE)] for t, i in types.items()}
    if "heading" in color_by_type:  # legend swatch = a representative mid-ramp blue
        color_by_type["heading"] = _HEADING_RAMP[2]
    return {"nodes": nodes, "edges": edges, "colors": color_by_type}


_HTML = r"""<title>Knowledge Graph</title>
<div id="meta"></div>
<div id="tools">
  <button id="theme" title="Light / dark">🌙</button>
  <span class="sep"></span>
  <button id="rearrange" title="Recompute the static layout">⟳ Arrange</button>
  <button id="unpin" title="Release all dragged (pinned) nodes">📌 Unpin</button>
  <label>Spacing <input id="rep" type="range" min="40" max="220" value="90"></label>
</div>
<div id="legend"></div>
<canvas id="c"></canvas>
<div id="tip"></div>
<aside id="panel" hidden></aside>
<style>
  :root{--bg:#f7f6f3;--fg:#1c1b19;--muted:#6b6b6b;--panel:rgba(20,20,20,.06);--panel-bd:rgba(20,20,20,.10);--edge:#9a9a9a;--accent:#3a6ea5;--card:#ffffffee;}
  @media (prefers-color-scheme: dark){:root{--bg:#0f1117;--fg:#e8e8e8;--muted:#9aa0a6;--panel:rgba(255,255,255,.08);--panel-bd:rgba(255,255,255,.12);--edge:#7c7c7c;--accent:#7fd1ff;--card:#171a21f2;}}
  :root[data-theme="light"]{--bg:#f7f6f3;--fg:#1c1b19;--muted:#6b6b6b;--panel:rgba(20,20,20,.06);--panel-bd:rgba(20,20,20,.10);--edge:#9a9a9a;--accent:#3a6ea5;--card:#ffffffee;}
  :root[data-theme="dark"]{--bg:#0f1117;--fg:#e8e8e8;--muted:#9aa0a6;--panel:rgba(255,255,255,.08);--panel-bd:rgba(255,255,255,.12);--edge:#7c7c7c;--accent:#7fd1ff;--card:#171a21f2;}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,sans-serif;overflow:hidden}
  #c{display:block;width:100vw;height:100vh;cursor:grab}
  #meta,#legend{position:fixed;top:12px;z-index:3;background:var(--panel);border:1px solid var(--panel-bd);padding:8px 11px;border-radius:10px;backdrop-filter:blur(6px)}
  #meta{left:12px;font-size:13px}
  #legend{right:12px;font-size:12px;max-width:230px;max-height:62vh;overflow:auto}
  #legend .lh{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;gap:8px}
  #legend .ctl{cursor:pointer;user-select:none;color:var(--accent);text-transform:none;letter-spacing:0}
  #legend .ctl:hover{text-decoration:underline}
  #legend .row{display:flex;align-items:center;gap:7px;margin:2px 0;cursor:pointer;user-select:none;border-radius:6px;padding:2px 4px}
  #legend .row:hover{background:var(--panel-bd)}
  #legend .row.off{opacity:.4}
  #legend .cb{width:13px;height:13px;flex:0 0 auto;border:1.5px solid var(--muted);border-radius:3px;display:inline-flex;align-items:center;justify-content:center;font-size:11px;line-height:1;color:var(--accent)}
  #legend .dot{width:11px;height:11px;border-radius:50%;flex:0 0 auto}
  #legend .only{font-size:9px;color:var(--accent);opacity:0;text-transform:uppercase;letter-spacing:.04em}
  #legend .row:hover .only{opacity:.9}
  #legend .cnt{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums}
  #meta b{color:var(--accent);font-variant-numeric:tabular-nums}
  #tools{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:4;display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:center;background:var(--panel);border:1px solid var(--panel-bd);padding:6px 10px;border-radius:10px;backdrop-filter:blur(6px);font-size:12px;max-width:72vw}
  #tools button{font:inherit;color:var(--fg);background:transparent;border:1px solid var(--panel-bd);border-radius:7px;padding:3px 8px;cursor:pointer;user-select:none}
  #tools button:hover{background:var(--panel-bd)}
  #tools .sep{width:1px;height:16px;background:var(--panel-bd)}
  #tools label{display:flex;align-items:center;gap:5px;color:var(--muted)}
  #tools input[type=range]{width:82px;accent-color:var(--accent)}
  #tip{position:fixed;pointer-events:none;z-index:5;max-width:340px;background:#0b0b0dea;color:#f3f3f3;padding:9px 11px;border-radius:9px;font-size:12px;line-height:1.45;display:none;border:1px solid #ffffff22;box-shadow:0 6px 24px #0006}
  #tip b{color:#7fd1ff}
  #panel{position:fixed;left:12px;bottom:12px;z-index:6;width:340px;max-width:44vw;max-height:70vh;overflow:auto;background:var(--card);border:1px solid var(--panel-bd);border-radius:12px;padding:12px 14px;box-shadow:0 10px 40px #0005;backdrop-filter:blur(8px);font-size:13px;line-height:1.5}
  #panel .x{position:absolute;top:8px;right:10px;cursor:pointer;color:var(--muted);font-size:16px;line-height:1}
  #panel .x:hover{color:var(--fg)}
  #panel h2{margin:0 22px 4px 0;font-size:15px;word-break:break-word}
  #panel .sub{color:var(--muted);font-size:12px;margin-bottom:8px;display:flex;align-items:center;gap:6px}
  #panel .desc{white-space:pre-wrap;word-break:break-word;margin:6px 0 10px;max-height:26vh;overflow:auto}
  #panel .nbh{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:8px 0 4px}
  #panel .nb{display:flex;align-items:center;gap:7px;padding:3px 5px;border-radius:6px;cursor:pointer}
  #panel .nb:hover{background:var(--panel-bd)}
  #panel .nb .lbl{flex:1 1 auto;word-break:break-word}
  #panel .nb .rel{color:var(--muted);font-size:11px;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #panel .dot{width:10px;height:10px;border-radius:50%;flex:0 0 auto}
</style>
<script>
const DATA = /*DATA*/;
const cv=document.getElementById('c'), ctx=cv.getContext('2d');
const tip=document.getElementById('tip'), panel=document.getElementById('panel');
let W,H,DPR;
function resize(){DPR=devicePixelRatio||1;W=cv.clientWidth;H=cv.clientHeight;cv.width=W*DPR;cv.height=H*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);}
addEventListener('resize',resize);resize();

const nodes=DATA.nodes, edges=DATA.edges, COL=DATA.colors;
const byId={}; nodes.forEach(n=>{byId[n.id]=n; n.x=W/2; n.y=H/2; n.pinned=false;});
const maxDeg=Math.max(1,...nodes.map(n=>n.deg));
const rad=n=>4+10*Math.sqrt(n.deg/maxDeg);
// adjacency for the property panel
const adj={}; nodes.forEach(n=>adj[n.id]=[]);
edges.forEach(e=>{if(byId[e.source]&&byId[e.target]){adj[e.source].push({o:e.target,rel:e.kw||e.desc});adj[e.target].push({o:e.source,rel:e.kw||e.desc});}});

// ── static layout: concentric rings by degree (O(n), computed once per
// visibility change -- no iterative physics, so it never blocks the main
// thread regardless of graph size). Dragged (pinned) nodes keep their spot. ──
function layoutStatic(){
  const gap=+(document.getElementById('rep')?.value||90);
  const cx=W/2, cy=H/2;
  const list=nodes.filter(n=>vis(n)&&!n.pinned).sort((a,b)=>b.deg-a.deg);
  let idx=0, ring=0;
  while(idx<list.length){
    const count=ring===0?1:Math.min(list.length-idx,Math.max(6,Math.round(2*Math.PI*ring)));
    const radius=ring*gap;
    for(let i=0;i<count;i++){
      const a=(2*Math.PI*i)/count+ring*0.4;
      const n=list[idx++];
      n.x=cx+radius*Math.cos(a); n.y=cy+radius*Math.sin(a);
    }
    ring++;
  }
}

// ── type visibility filter ─────────────────────────────────────────────
const typeCount={}; nodes.forEach(n=>typeCount[n.type]=(typeCount[n.type]||0)+1);
const kindLabel={semantic:'Semantic',structure:'Structure',asset:'Asset'};
const visible={}; Object.keys(COL).forEach(t=>visible[t]=false);
const vis=n=>n&&visible[n.type]!==false;
const meta=document.getElementById('meta'), lg=document.getElementById('legend');
function updMeta(){
  const nn=nodes.filter(vis).length;
  const ne=edges.filter(e=>{const a=byId[e.source],b=byId[e.target];return a&&b&&vis(a)&&vis(b);}).length;
  meta.innerHTML=`<b>${nn}</b> nodes · <b>${ne}</b> relations`;
}
function buildLegend(){
  lg.innerHTML='';
  const lh=document.createElement('div');lh.className='lh';
  lh.innerHTML='<span>Node types</span><span><span class="ctl" data-a="all">all</span> · <span class="ctl" data-a="none">none</span></span>';
  lg.appendChild(lh);
  lh.querySelectorAll('.ctl').forEach(el=>el.onclick=(e)=>{e.stopPropagation();const v=el.dataset.a==='all';Object.keys(COL).forEach(k=>visible[k]=v);buildLegend();updMeta();layoutStatic();});
  ['semantic','structure','asset'].forEach(kind=>{
    const ts=[...new Set(nodes.filter(n=>n.kind===kind).map(n=>n.type))].sort((a,b)=>a.localeCompare(b));
    if(!ts.length)return;
    const kh=document.createElement('div');kh.className='lh';kh.style.marginTop='7px';kh.textContent=kindLabel[kind];lg.appendChild(kh);
    ts.forEach((t)=>{
      const c=COL[t];
      const on=visible[t]!==false;
      const r=document.createElement('div');r.className='row'+(on?'':' off');
      r.innerHTML=`<span class="cb">${on?'✓':''}</span><span class="dot" style="background:${c}"></span><span>${esc(t)}</span><span class="only">only</span><span class="cnt">${typeCount[t]||0}</span>`;
      r.onclick=(e)=>{
        if(e.target.classList.contains('only')){Object.keys(COL).forEach(k=>visible[k]=(k===t));}
        else{visible[t]=!on;}
        buildLegend();updMeta();layoutStatic();
      };
      lg.appendChild(r);
    });
  });
}
buildLegend();updMeta();

// ── selection + property panel ─────────────────────────────────────────
let selected=null, hlSet=new Set();
function selectNode(n){
  selected=n||null;
  hlSet=new Set();
  if(selected){hlSet.add(selected.id);(adj[selected.id]||[]).forEach(x=>hlSet.add(x.o));}
  renderPanel();
}
function renderPanel(){
  if(!selected){panel.hidden=true;panel.innerHTML='';return;}
  const n=selected, c=COL[n.type]||'#9a9a9a';
  // Related nodes in READING/STRUCTURAL order: headings (which carry an `order` = start-page key)
  // come first sorted by document order, then the rest by degree. So a chapter's related headings
  // read top-to-bottom in book order instead of a degree-jumbled list.
  const nb=(adj[n.id]||[]).map(x=>({n:byId[x.o],rel:x.rel})).filter(x=>x.n&&vis(x.n))
            .sort((a,b)=>{
              const ao=a.n.order, bo=b.n.order;
              if(ao!=null&&bo!=null) return ao-bo||b.n.deg-a.n.deg;
              if(ao!=null) return -1;
              if(bo!=null) return 1;
              return b.n.deg-a.n.deg;
            });
  panel.hidden=false;
  panel.innerHTML=`<span class="x" id="px">✕</span>`
    +`<h2>${esc(n.label)}</h2>`
    +`<div class="sub"><span class="dot" style="background:${c}"></span>${esc(n.kind)} · ${esc(n.type)} · degree ${n.deg}</div>`
    +(n.desc?`<div class="desc">${esc(n.desc)}</div>`:'')
    +`<div class="nbh">Related (${nb.length})</div>`
    +nb.slice(0,60).map(x=>`<div class="nb" data-id="${esc(x.n.id)}"><span class="dot" style="background:${COL[x.n.type]||'#9a9a9a'}"></span><span class="lbl">${esc(x.n.label)}</span><span class="rel">${esc(x.rel||'')}</span></div>`).join('');
  panel.querySelector('#px').onclick=()=>selectNode(null);
  panel.querySelectorAll('.nb').forEach(el=>el.onclick=()=>{const t=byId[el.dataset.id];if(t){selectNode(t);centerOn(t);}});
}
function centerOn(n){view.x=W/2-n.x*view.k;view.y=H/2-n.y*view.k;}

// ── static layout, canvas-only redraw loop (cheap: no per-frame physics) ──
let view={x:0,y:0,k:1};
function draw(){
  const cs=getComputedStyle(document.body);
  ctx.clearRect(0,0,W,H);ctx.save();ctx.translate(view.x,view.y);ctx.scale(view.k,view.k);
  const edgeCol=(cs.getPropertyValue('--edge')||'#888').trim();
  const accent=(cs.getPropertyValue('--accent')||'#3a6ea5').trim();
  ctx.lineWidth=0.7;
  edges.forEach(e=>{const a=byId[e.source],b=byId[e.target];if(!a||!b||!vis(a)||!vis(b))return;
    const hot=selected&&(e.source===selected.id||e.target===selected.id);
    ctx.globalAlpha=selected?(hot?0.85:0.05):0.3;ctx.strokeStyle=hot?accent:edgeCol;
    ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();});
  ctx.globalAlpha=1;
  nodes.forEach(n=>{if(!vis(n))return;const dim=selected&&!hlSet.has(n.id);
    ctx.globalAlpha=dim?0.2:1;ctx.beginPath();ctx.arc(n.x,n.y,rad(n),0,7);ctx.fillStyle=n.color||COL[n.type]||'#9a9a9a';ctx.fill();
    if(n===selected){ctx.globalAlpha=1;ctx.lineWidth=2.5/view.k;ctx.strokeStyle=accent;ctx.stroke();}});
  ctx.globalAlpha=1;ctx.fillStyle=cs.color;ctx.font='11px system-ui';
  const labelMin=Math.max(2,maxDeg*0.15);
  nodes.forEach(n=>{if(!vis(n))return;const show=(n===selected)||(hlSet.has(n.id)&&selected)||(!selected&&n.deg>=labelMin&&view.k>0.5);
    if(show){ctx.globalAlpha=(selected&&!hlSet.has(n.id))?0.25:1;ctx.fillText(n.label.slice(0,28),n.x+rad(n)+2,n.y+3);}});
  ctx.restore();
}
function loop(){draw();requestAnimationFrame(loop);}loop();

// ── interaction: drag nodes / pan / zoom / hover ───────────────────────
function nodeAt(clientX,clientY){
  const mx=(clientX-view.x)/view.k,my=(clientY-view.y)/view.k;
  for(let i=nodes.length-1;i>=0;i--){const n=nodes[i];if(!vis(n))continue;const r=rad(n)+3;if((mx-n.x)**2+(my-n.y)**2<=r*r)return n;}
  return null;
}
let drag=null;
cv.addEventListener('mousedown',e=>{
  const n=nodeAt(e.clientX,e.clientY);
  if(n){drag={mode:'node',n,sx:e.clientX,sy:e.clientY,moved:false};n.pinned=true;}
  else{drag={mode:'pan',x:e.clientX,y:e.clientY,vx:view.x,vy:view.y,sx:e.clientX,sy:e.clientY,moved:false};}
  cv.style.cursor='grabbing';
});
addEventListener('mousemove',e=>{
  if(drag){
    if(Math.hypot(e.clientX-drag.sx,e.clientY-drag.sy)>4)drag.moved=true;
    if(drag.mode==='node'){drag.n.x=(e.clientX-view.x)/view.k;drag.n.y=(e.clientY-view.y)/view.k;}
    else{view.x=drag.vx+(e.clientX-drag.x);view.y=drag.vy+(e.clientY-drag.y);}
    tip.style.display='none';return;
  }
  const n=nodeAt(e.clientX,e.clientY);
  if(n){tip.style.display='block';tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY+14)+'px';
    tip.innerHTML=`<b>${esc(n.label)}</b> <span style="color:#aaa">(${esc(n.type)}, deg ${n.deg})</span>`+(n.desc?`<br>${esc(n.desc).slice(0,300)}`:'');
    cv.style.cursor='pointer';}
  else{tip.style.display='none';cv.style.cursor='grab';}
});
addEventListener('mouseup',()=>{
  if(drag){
    if(!drag.moved){if(drag.mode==='node')selectNode(drag.n);else selectNode(null);}
    cv.style.cursor='grab';
  }
  drag=null;
});
cv.addEventListener('wheel',e=>{e.preventDefault();const s=e.deltaY<0?1.1:0.9;
  const mx=e.clientX,my=e.clientY;view.x=mx-(mx-view.x)*s;view.y=my-(my-view.y)*s;view.k*=s;},{passive:false});

// ── theme toggle (light/dark) + layout controls ────────────────────────
const root=document.documentElement, $=id=>document.getElementById(id);
function setTheme(t){root.setAttribute('data-theme',t);try{localStorage.setItem('kg-theme',t);}catch(e){}$('theme').textContent=t==='dark'?'☀':'🌙';}
let _saved=null;try{_saved=localStorage.getItem('kg-theme');}catch(e){}
setTheme(_saved||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'));
$('theme').onclick=()=>setTheme(root.getAttribute('data-theme')==='dark'?'light':'dark');
$('rep').oninput=()=>layoutStatic();
$('rearrange').onclick=()=>{nodes.forEach(n=>n.pinned=false);selectNode(null);layoutStatic();};
$('unpin').onclick=()=>{nodes.forEach(n=>n.pinned=false);layoutStatic();};

function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
</script>
"""


def render(graphml: Path, out_html: Path) -> Path:
    g = _load_graph(graphml)
    payload = _to_payload(g)
    html = _HTML.replace("/*DATA*/", json.dumps(payload))
    out_html.write_text(html, encoding="utf-8")
    print(f"nodes={len(payload['nodes'])} edges={len(payload['edges'])} types={len(payload['colors'])}")
    print(f"wrote {out_html}")
    return out_html


def main():
    ap = argparse.ArgumentParser(description="Render a LightRAG graphml into a self-contained interactive HTML.")
    ap.add_argument("--doc", help="document_id (with --mode)")
    ap.add_argument("--mode", choices=["mineru", "gpt4o"], help="which KB to view")
    ap.add_argument("--kb-suffix", default="", help="extra suffix on the KB dir, e.g. '__struct_full'")
    ap.add_argument("--graphml", help="explicit path to a graphml file")
    ap.add_argument("--out", help="output html path")
    args = ap.parse_args()

    if args.graphml:
        graphml = Path(args.graphml)
    elif args.doc and args.mode:
        graphml = KB_DIR / f"{args.doc}__{args.mode}{args.kb_suffix}" / "graph_chunk_entity_relation.graphml"
    else:
        raise SystemExit("provide --graphml OR (--doc and --mode [--kb-suffix])")

    if not graphml.exists():
        raise SystemExit(f"graphml not found: {graphml}")

    out = Path(args.out) if args.out else graphml.parent / "kg_view.html"
    render(graphml, out)


if __name__ == "__main__":
    main()
