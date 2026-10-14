from typing import Any, Dict

from langgraph.graph import END, StateGraph

from src.models.slide import SlideContent
from src.utils.config import Config
from src.workflow.agents.content_qa import ContentQAAgent
from src.workflow.agents.slide_writer import SlideWriterAgent
from src.workflow.chapter_pipeline import ChapterPipeline
from src.workflow.state import WorkflowState


def create_workflow() -> StateGraph:
    """lecture_planner -> packet_builder -> slide_writer -> content_qa: the four agents over one ChapterPipeline."""
    workflow = StateGraph(WorkflowState)
    pipeline = ChapterPipeline(Config.LLM_MODEL_NAME)
    writer = SlideWriterAgent(Config.LLM_MODEL_NAME)
    qa = ContentQAAgent(Config.LLM_MODEL_NAME)
    written: Dict[str, SlideContent] = {}

    def banner(text: str) -> None:
        print(f"\n{'=' * 60}\n {text}\n{'=' * 60}\n")

    def deck_slides():
        """The deck in order: each chapter's opener (its agenda) and the written content slides."""
        slides = []
        for spec, packet in zip(pipeline.specs, pipeline.packets):
            if spec.is_section_opener:
                slides.append(SlideContent(slide=spec, content=list(packet.get("agenda_items") or []) or [spec.slide_title],
                                           layout_hint='section_divider_layout'))
            else:
                slides.append(written[spec.slide_id])
                packet['slide_title'] = spec.slide_title       # the Writer may retitle the slide
        return slides

    def finish() -> Dict[str, Any]:
        ledger = pipeline.ledger(written)
        for row in ledger.values():
            print(f"[chapters] {row['chapter']}: paragraphs on slides {row['paragraph_slide_rate']:.0%}, "
                  f"KG nodes {row['node_slide_rate']:.0%}, links {row['link_slide_rate']:.0%}")
        return {'slides': deck_slides(), 'slide_specs': pipeline.specs, 'slide_packets': pipeline.packets,
                'scope_manifest': pipeline.manifest(ledger), 'unit_plans': pipeline.report()}

    def lecture_planner_node(state: WorkflowState) -> Dict[str, Any]:
        banner("Lecture Planner — dividing each chapter into complete slides")
        title = pipeline.open(state['document_context'], state.get('root_heading'))
        pipeline.plan()
        return {'lecture_title': title}

    def packet_builder_node(state: WorkflowState) -> Dict[str, Any]:
        banner("Packet Builder — choosing what each slide's packets carry")
        pipeline.build_packets()
        if not pipeline.jobs:
            raise RuntimeError("The Lecture Planner produced no slides")
        print(f"[PacketBuilder] {len(pipeline.jobs)} slide(s) over {len(pipeline.chapters)} chapter(s) "
              f"from {pipeline.kb_dir.name}")
        return {'planned_specs': pipeline.specs, 'slide_specs': pipeline.specs, 'slide_packets': pipeline.packets,
                'scope_manifest': pipeline.manifest(), 'unit_plans': pipeline.report(),
                'deck_plan': {'mode': 'chapters-v2'}}

    def leave_out(jobs) -> None:
        """Slides the Writer wrote nothing usable for leave the deck; their paragraphs go to the notes of the slide
        before them, so the presenter still has them."""
        order = [s.slide_id for s in pipeline.specs]
        for job in jobs:
            sid = job["spec"].slide_id
            before = next((o for o in reversed(order[:order.index(sid)]) if o in written), None)
            if before:
                written[before].speaker_notes += [{"passage": " ".join(t for _, t in row["sentences"])}
                                                  for row in job["built"]["pool"].values()]
        print(f"[SlideWriter] nothing usable written for {[j['spec'].slide_title for j in jobs]}: left out")
        pipeline.drop([j["spec"].slide_id for j in jobs], "the Writer wrote nothing usable")

    def slide_writer_node(state: WorkflowState) -> Dict[str, Any]:
        banner("Slide Writer — reading each slide's packets and writing it")
        written.update({c.slide.slide_id: c for c in writer.write(pipeline.jobs)})
        unwritten = [j for j in pipeline.jobs if j["spec"].slide_id not in written]
        if unwritten:
            leave_out(unwritten)
        return finish()

    def content_qa_node(state: WorkflowState) -> Dict[str, Any]:
        banner("Content QA — checking each bullet and filling thin slides")
        report = qa.run(pipeline, writer, written)
        return {**finish(), 'qa_report': report}

    workflow.add_node('lecture_planner', lecture_planner_node)
    workflow.add_node('packet_builder', packet_builder_node)
    workflow.add_node('slide_writer', slide_writer_node)
    workflow.set_entry_point('lecture_planner')
    workflow.add_edge('lecture_planner', 'packet_builder')
    workflow.add_node('content_qa', content_qa_node)
    workflow.add_edge('packet_builder', 'slide_writer')
    workflow.add_edge('slide_writer', 'content_qa')
    workflow.add_edge('content_qa', END)
    return workflow.compile()
