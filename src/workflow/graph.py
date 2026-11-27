from typing import Any, Dict, List, Optional

from langgraph.graph import END, StateGraph

from src.models.slide import SlideContent
from src.utils.config import Config
from src.workflow.agents.content_qa import ContentQAAgent
from src.workflow.agents.slide_writer import SlideWriterAgent
from src.workflow.pipeline import ChapterPipeline
from src.workflow.state import Review, SlideEntry, WorkflowState


def create_workflow(domain: Optional[str] = None) -> StateGraph:
    """outline -> packet_builder -> slide_writer -> content_qa over the shared state W = <D, S, R> (`state.py`): each
    agent reads the slide deck S from the state and writes its part of every slide; the ChapterPipeline is the agents'
    access to the document and its KG (D). After each audit the QA Router reads R: slides QA found wrong go back to
    the Writer with what to fix (`status` failed), at most QA_MAX_ROUNDS times; then the deck is done.
    `domain`: the field of study (Config.DOMAINS); None lets the pipeline detect it once the document is open."""
    workflow = StateGraph(WorkflowState)
    pipeline = ChapterPipeline(Config.LLM_MODEL_NAME, domain)
    agents: Dict[str, Any] = {}                  # the Writer and QA, made for the field once it is known

    def banner(text: str) -> None:
        print(f"\n{'=' * 60}\n {text}\n{'=' * 60}\n")

    def entries(contents: Dict[str, SlideContent]) -> List[SlideEntry]:
        """S as the deck stands: every slide in order (chapter openers included) with its plan, s_i, e_i, evidence and
        b_i."""
        jobs = {j["spec"].slide_id: j for j in pipeline.jobs}
        plans = {s.get("slide_id"): s for state in pipeline.state.values() for s in state["slides"]}
        return [{"slide_id": spec.slide_id, "chapter": jobs[spec.slide_id]["chapter"] if spec.slide_id in jobs
                 else spec.kg_node_id, "plan": plans.get(spec.slide_id), "spec": spec, "packet": packet,
                 "built": (jobs.get(spec.slide_id) or {}).get("built"), "content": contents.get(spec.slide_id)}
                for spec, packet in zip(pipeline.specs, pipeline.packets)]

    def written(deck: List[SlideEntry]) -> Dict[str, SlideContent]:
        """The bullets written so far, by slide."""
        return {e["slide_id"]: e["content"] for e in deck if e.get("content") is not None}

    def outputs(deck: List[SlideEntry]) -> Dict[str, Any]:
        """The deck as the lecture file stores it, read from S: each chapter's opener (its agenda) and the written
        slides."""
        ledger = pipeline.ledger(written(deck))
        labels = pipeline.labels()
        for row in ledger.values():
            print(f"[coverage] {row['chapter']}: paragraphs on slides {row['paragraph_slide_rate']:.0%}, "
                  f"KG nodes {row['node_slide_rate']:.0%}, links {row['link_slide_rate']:.0%}")
        slides = []
        for e in deck:
            spec, packet = e["spec"], e["packet"]
            if spec.is_section_opener:
                notes = [{"passage": packet["notes"]}] if packet.get("notes") else []
                slides.append(SlideContent(slide=spec, content=list(packet.get("agenda_items") or []) or [spec.slide_title],
                                           layout_hint='section_divider_layout', speaker_notes=notes))
            else:
                slides.append(e["content"])
                packet['slide_title'] = spec.slide_title       # the Writer may retitle the slide
        return {'slides': slides, 'slide_specs': [e["spec"] for e in deck], 'slide_packets': [e["packet"] for e in deck],
                'scope_manifest': pipeline.manifest(ledger), 'unit_plans': pipeline.report(), 'labels': labels, 'numbered': pipeline.numbered(labels)}

    def outline_node(state: WorkflowState) -> Dict[str, Any]:
        banner("Outline — goals and slides of each chapter from its KG and text")
        title = pipeline.open(state['document_context'], state.get('root_heading'))
        agents['writer'] = SlideWriterAgent(Config.LLM_MODEL_NAME, pipeline.domain)
        agents['qa'] = ContentQAAgent(Config.LLM_MODEL_NAME, pipeline.domain)
        pipeline.plan()
        deck = [{"slide_id": None, "chapter": chapter.id, "plan": slide, "spec": None, "packet": None, "built": None,
                 "content": None} for chapter in pipeline.chapters for slide in pipeline.state[chapter.id]["slides"]]
        return {'lecture_title': title, 'domain': pipeline.domain, 'deck': deck}

    def packet_builder_node(state: WorkflowState) -> Dict[str, Any]:
        banner("Packet Builder — choosing what each slide's packets carry")
        for chapter in pipeline.chapters:                # the slides the Outline planned, as the state holds them
            pipeline.state[chapter.id]["slides"] = [e["plan"] for e in state['deck'] if e["chapter"] == chapter.id]
        pipeline.build_packets()
        if not pipeline.jobs:
            raise RuntimeError("The chosen sections hold nothing to make a slide from")
        print(f"[PacketBuilder] {len(pipeline.jobs)} slide(s) over {len(pipeline.chapters)} chapter(s) "
              f"from {pipeline.kb_dir.name}")
        deck = entries({})
        return {'deck': deck, 'planned_specs': pipeline.specs, 'slide_specs': pipeline.specs,
                'slide_packets': pipeline.packets, 'scope_manifest': pipeline.manifest(), 'unit_plans': pipeline.report()}

    def leave_out(jobs: List[dict], contents: Dict[str, SlideContent]) -> None:
        """Slides the Writer wrote nothing usable for leave the deck; their paragraphs go to the notes of the slide
        before them, so the presenter still has them."""
        order = [s.slide_id for s in pipeline.specs]
        for job in jobs:
            sid = job["spec"].slide_id
            before = next((o for o in reversed(order[:order.index(sid)]) if o in contents), None)
            if before:
                contents[before].speaker_notes += [{"passage": " ".join(t for _, t in row["sentences"])}
                                                   for row in job["built"]["pool"].values()]
        print(f"[SlideWriter] nothing usable written for {[j['spec'].slide_title for j in jobs]}: left out")
        pipeline.drop([j["spec"].slide_id for j in jobs], "the Writer wrote nothing usable")

    def slide_writer_node(state: WorkflowState) -> Dict[str, Any]:
        review = state.get('review')
        if review and review["status"] == "failed":       # sent back by the QA Router: only those slides again
            banner(f"Slide Writer — writing again the {sum(map(len, review['pending'].values()))} bullet(s) Content QA sent back")
            contents = written(state['deck'])
            agents['writer'].repair(pipeline.jobs, contents, review["pending"])
            deck = entries(contents)
            return {'deck': deck, **outputs(deck)}
        banner("Slide Writer — reading each slide's packets and writing it")
        jobs = [{"spec": e["spec"], "built": e["built"], "chapter": e["chapter"]} for e in state['deck']
                if e["built"] is not None]
        contents = {c.slide.slide_id: c for c in agents['writer'].write(jobs)}
        unwritten = [j for j in jobs if j["spec"].slide_id not in contents]
        if unwritten:
            leave_out(unwritten, contents)
        deck = entries(contents)
        return {'deck': deck, **outputs(deck)}

    def content_qa_node(state: WorkflowState) -> Dict[str, Any]:
        banner("Content QA — checking each bullet and filling thin slides")
        qa, writer, contents = agents['qa'], agents['writer'], written(state['deck'])
        before = state.get('review')
        memo = before["audit"] if before else qa.start(pipeline, writer, contents)
        feedback = qa.audit(pipeline, writer, contents, memo)
        while not feedback and memo["targets"]:          # only slides QA itself changed: checked again here
            feedback = qa.audit(pipeline, writer, contents, memo)
        report = memo["report"] if feedback else qa.finish(pipeline, writer, contents, memo)
        deck = entries(contents)
        review: Review = {"status": "failed" if feedback else report["status"], "round": memo["round"],
                          "pending": feedback, "audit": memo,
                          "feedback": [{"slide_id": sid, "slide": contents[sid].slide.slide_title, "issues": issues}
                                       for sid, issues in feedback.items()]}
        out = {'deck': deck, 'review': review}
        if not feedback:
            out.update(outputs(deck), qa_report=report)
        return out

    def qa_router(state: WorkflowState) -> str:
        """The paper's QA Router: slides QA found wrong go back to the Writer; otherwise the deck is done."""
        return 'slide_writer' if state['review']["status"] == "failed" else END

    workflow.add_node('outline', outline_node)
    workflow.add_node('packet_builder', packet_builder_node)
    workflow.add_node('slide_writer', slide_writer_node)
    workflow.set_entry_point('outline')
    workflow.add_edge('outline', 'packet_builder')
    workflow.add_node('content_qa', content_qa_node)
    workflow.add_edge('packet_builder', 'slide_writer')
    workflow.add_edge('slide_writer', 'content_qa')
    workflow.add_conditional_edges('content_qa', qa_router, {'slide_writer': 'slide_writer', END: END})
    return workflow.compile()
