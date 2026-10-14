"""Local web UI to ask questions of one KB, answered only from a chosen part of the document.

Retrieval is the slide pipeline's own: `EvidenceStore.retrieve_scoped` (meaning + keyword + KG mentions + MiniRAG chunk and
entity hits) restricted to the headings under the selected roots, plus MiniRAG's entity/chunk context filtered to the chunks
of those headings. A model then answers from that context only and cites the pages; the retrieved sentences are shown so
retrieval can be judged separately from the answer.

    python -m src.rag.query_server --kb <kb dir name> --root_heading "Chapter 3. Classification;Chapter 4. Training Models"
"""
from __future__ import annotations

import argparse
import json
import time

import uvicorn
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.rag import build_kg as B
from src.rag.evidence_store import EvidenceStore
from src.rag.idea_candidates import scope_sentences
from src.rag.retrieve_content import load_structural_index
from src.utils.config import Config
from src.utils.llm import chat
from src.workflow.scope import ScopeOrchestrator

app = FastAPI(title="RAG Query UI")
STATE: dict = {}

_ANSWER = (
    "Answer the QUESTION using ONLY the CONTEXT: numbered source sentences from a textbook (with their page and "
    "section) and short descriptions of knowledge-graph entities. Cite the sentences you use as [n] or [n, m]. "
    "If the context does not contain the answer, say so plainly instead of guessing. Answer in the language of the "
    "question, concisely, in markdown.")


class QueryIn(BaseModel):
    question: str
    k: int = 12
    only_context: bool = False
    section: str | None = None          # a section id from /sections: search only that section's own headings


def _setup(kb: str, root_heading: str) -> None:
    kb_dir = B.KB_DIR / kb
    if not (kb_dir / "structural_index.json").exists():
        raise SystemExit(f"no KB named {kb!r} under {B.KB_DIR}")
    index = load_structural_index(kb_dir)
    orchestrator = ScopeOrchestrator(index, kb_dir)
    visible = orchestrator.visible
    roots = orchestrator.resolve_roots(root_heading, visible)
    ordered = orchestrator.ordered_scope(roots, visible)
    store = EvidenceStore(kb_dir, roots)
    store.mentions                                   # build the KG index once, before the first question
    # sections: each chapter's introduction, then one per direct sub-heading (with its own sub-headings)
    by_heading = scope_sentences(kb_dir, index, set(ordered))
    sections = []
    for n, heading_ids in enumerate(s for s in orchestrator.sections(roots, ordered, visible) if s):
        rows = [s for h in heading_ids for s in (by_heading.get(h) or {}).get("sentences", [])]
        path = []
        cur = heading_ids[0]
        while cur is not None and cur in set(ordered):
            path.insert(0, orchestrator.display(cur))
            parent = (orchestrator.headings.get(str(cur)) or {}).get("parent")
            cur = int(parent) if parent is not None else None
        title = orchestrator.display(heading_ids[0])
        sections.append({"id": f"S{n + 1}", "title": title, "path": " > ".join(path),
                         "heading_ids": list(heading_ids), "sentences": len(rows),
                         "keywords": [store._concept_names.get(c, c) for c in store.query_concepts(title)],
                         "pages": sorted({(s.get("ref") or {}).get("page") for s in rows} - {None})})
    chunks = {s.get("ref", {}).get("chunk_id") for s in store.sentences if s["tier"] == "scope"}
    STATE.update(kb=kb, store=store, roots=[orchestrator.title(r) for r in roots], chunks=chunks - {None},
                 scope_sentences=sum(s["tier"] == "scope" for s in store.sentences),
                 sections={s["id"]: s for s in sections})


def _retrieve(question: str, k: int, section: str | None = None) -> dict:
    store = STATE["store"]
    if section is not None and section not in STATE["sections"]:
        raise ValueError(f"unknown section {section!r}; see /sections")
    heading_ids = STATE["sections"][section]["heading_ids"] if section else store.scope
    keywords = [store._concept_names.get(n, n) for n in store.query_concepts(question)]
    hits = store.retrieve_scoped(question, heading_ids, keywords=keywords, k=k, role="query_ui")
    sources = [{"n": n, "id": h["id"], "text": h["text"], "page": (h.get("ref") or {}).get("page"),
                "section": " > ".join(h.get("heading_path") or []), "score": round(h["score"], 4),
                "signals": {key: round(val, 3) for key, val in (h.get("signals") or {}).items() if val}}
               for n, h in enumerate(hits, 1)]
    mini = ""
    if store.retriever.available and not section:      # entity context is chapter-wide: skip it for one section
        try:
            mini = store.retriever.mini_context(question, allowed_chunks=STATE["chunks"])
        except Exception as exc:
            mini = f"(MiniRAG context unavailable: {type(exc).__name__})"
    return {"sources": sources, "keywords": keywords, "mini_context": mini}


def _answer(question: str, found: dict) -> str:
    context = "\n".join(f"[{s['n']}] (p.{s['page']}, {s['section']}) {s['text']}" for s in found["sources"])
    if found["mini_context"]:
        context += "\n\nKnowledge-graph context:\n" + found["mini_context"]
    return chat(Config.LLM_MODEL_NAME, [{"role": "system", "content": _ANSWER},
                                        {"role": "user", "content": f"QUESTION: {question}\n\nCONTEXT:\n{context}"}],
                temperature=0, max_tokens=4000, role="query_ui")


@app.get("/sections")
def sections():
    """The scope cut into sections (each chapter's introduction, then its sub-headings), in reading order."""
    return {"kb": STATE["kb"], "roots": STATE["roots"], "sections": list(STATE["sections"].values())}


@app.get("/scope")
def scope():
    return {"kb": STATE["kb"], "roots": STATE["roots"], "sentences": STATE["scope_sentences"],
            "model": Config.LLM_MODEL_NAME}


@app.post("/query")
async def query(q: QueryIn):
    if not q.question.strip():
        return JSONResponse({"error": "empty question"}, status_code=400)
    try:
        t0 = time.perf_counter()
        found = await run_in_threadpool(_retrieve, q.question, max(1, min(q.k, 40)), q.section)
        answer = "" if q.only_context else await run_in_threadpool(_answer, q.question, found)
        return {**found, "answer": answer, "elapsed_s": round(time.perf_counter() - t0, 1)}
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.get("/", response_class=HTMLResponse)
def index():
    return _PAGE


_PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RAG Query</title>
<style>
:root{--bg:#f7f6f3;--fg:#1c1b19;--muted:#6b6b6b;--panel:#ffffff;--bd:rgba(0,0,0,.12);--accent:#3a6ea5;--code:#f0efe9}
@media (prefers-color-scheme:dark){:root{--bg:#0f1117;--fg:#e8e8e8;--muted:#9aa0a6;--panel:#171a21;--bd:rgba(255,255,255,.14);--accent:#7fd1ff;--code:#1e222b}}
*{box-sizing:border-box}body{margin:0;font:15px/1.55 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
.wrap{max-width:960px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:18px;margin:0 0 4px}.scope{color:var(--muted);font-size:13px;margin-bottom:14px}
.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:8px}
button,input[type=number]{font:inherit;color:var(--fg);background:var(--panel);border:1px solid var(--bd);border-radius:8px;padding:7px 10px}
button{cursor:pointer}button.primary{background:var(--accent);color:#fff;border-color:transparent;font-weight:600}
button:disabled{opacity:.5;cursor:default}input[type=number]{width:64px}
label{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:14px}
textarea{width:100%;min-height:74px;font:inherit;color:var(--fg);background:var(--panel);border:1px solid var(--bd);border-radius:10px;padding:10px 12px;resize:vertical}
.card{margin-top:16px;background:var(--panel);border:1px solid var(--bd);border-radius:12px;padding:14px 16px;word-break:break-word}
.card h2{font-size:14px;margin:0 0 8px;color:var(--muted);font-weight:600}
.card p{margin:.5em 0}.card ul{margin:.4em 0 .4em 1.2em}.card code{background:var(--code);padding:1px 5px;border-radius:5px}
.src{border-top:1px solid var(--bd);padding:8px 0}.src:first-of-type{border-top:0}
.src .h{font-size:12px;color:var(--muted)}.src .n{font-weight:700;color:var(--accent);margin-right:6px}
pre{white-space:pre-wrap;background:var(--code);padding:10px;border-radius:8px;font-size:13px;margin:0}
.meta{color:var(--muted);font-size:12px}.err{color:#e5534b}
.spin{display:inline-block;width:13px;height:13px;border:2px solid var(--muted);border-top-color:transparent;border-radius:50%;animation:s .7s linear infinite;vertical-align:-2px}
@keyframes s{to{transform:rotate(360deg)}}
</style></head><body><div class="wrap">
<h1>RAG Query</h1><div class="scope" id="scope">…</div>
<textarea id="q" placeholder="Nhập câu hỏi… (Ctrl+Enter để gửi)"></textarea>
<div class="bar"><button class="primary" id="ask">Ask</button>
<label>số câu nguồn <input type="number" id="k" value="12" min="1" max="40"></label>
<label><input type="checkbox" id="ctx"> chỉ lấy context (không gọi LLM trả lời)</label>
<span id="status" class="meta"></span></div>
<div id="out"></div>
<script>
const $=s=>document.querySelector(s);
const esc=t=>String(t??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function md(t){t=esc(t).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');
  const out=[];let list=false;for(const line of t.split(/\r?\n/)){const li=line.match(/^\s*[-*]\s+(.*)$/);
  if(li){if(!list){out.push('<ul>');list=true}out.push('<li>'+li[1]+'</li>');continue}
  if(list){out.push('</ul>');list=false}const h=line.match(/^#{1,6}\s+(.*)$/);
  out.push(h?'<p><b>'+h[1]+'</b></p>':line.trim()?'<p>'+line+'</p>':'')}if(list)out.push('</ul>');return out.join('')}
fetch('scope').then(r=>r.json()).then(d=>{$('#scope').textContent=`Phạm vi: ${d.roots.join(' ; ')} — ${d.sentences} câu · KB ${d.kb} · model ${d.model}`});
async function ask(){const q=$('#q').value.trim();if(!q)return;
  $('#ask').disabled=true;$('#status').innerHTML='<span class="spin"></span> đang truy vấn…';$('#out').innerHTML='';
  try{const r=await fetch('query',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:q,k:+$('#k').value||12,only_context:$('#ctx').checked})});
    const d=await r.json();
    if(d.error){$('#out').innerHTML='<div class="card err">'+esc(d.error)+'</div>'}
    else{let h='';
      if(d.answer)h+='<div class="card"><h2>Trả lời</h2>'+md(d.answer)+'<div class="meta">'+d.elapsed_s+'s</div></div>';
      h+='<div class="card"><h2>Câu nguồn tìm được ('+d.sources.length+')'+(d.keywords.length?' · khái niệm KG: '+esc(d.keywords.join(', ')):'')+'</h2>'+
        d.sources.map(s=>'<div class="src"><span class="n">['+s.n+']</span>'+esc(s.text)+
        '<div class="h">p.'+esc(s.page)+' · '+esc(s.section)+' · score '+s.score+' · '+esc(Object.entries(s.signals).map(([k,v])=>k+'='+v).join(' '))+'</div></div>').join('')+'</div>';
      if(d.mini_context)h+='<div class="card"><h2>MiniRAG context (lọc theo phạm vi)</h2><pre>'+esc(d.mini_context)+'</pre></div>';
      $('#out').innerHTML=h}}
  catch(e){$('#out').innerHTML='<div class="card err">'+esc(''+e)+'</div>'}
  $('#ask').disabled=false;$('#status').textContent=''}
$('#ask').onclick=ask;$('#q').addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter')ask()});
</script></div></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="Local web UI to ask questions of one part of a KB.")
    ap.add_argument("--kb", default="Hands_On_Machine_Learning_with_Scikit_Learn_and_TensorFlow_p102-189")
    ap.add_argument("--root_heading", default="Chapter 3. Classification;Chapter 4. Training Models",
                    help="';'-separated headings whose subtrees form the queried scope")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    args = ap.parse_args()
    _setup(args.kb, args.root_heading)
    print(f"[query-ui] http://{args.host}:{args.port}  scope: {' ; '.join(STATE['roots'])} "
          f"({STATE['scope_sentences']} sentences) · KB {args.kb}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
