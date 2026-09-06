import re
from langgraph.graph import StateGraph, END
from typing import Dict, Any
from src.workflow.state import WorkflowState
from src.workflow.agents.plan_builder import PlanBuilderAgent
from src.workflow.agents.content_quality import ContentQualityAgent
from src.workflow.agents.slide_packet_builder import SlidePacketBuilderAgent
from src.workflow.agents.direct_bullet_writer import DirectBulletWriterAgent
from src.utils.config import Config

def create_workflow() -> StateGraph:
    workflow = StateGraph(WorkflowState)
    plan_builder    = PlanBuilderAgent(Config.LLM_MODEL_NAME)
    content_quality = ContentQualityAgent(Config.LLM_MODEL_NAME)
    packet_builder  = SlidePacketBuilderAgent(Config.LLM_MODEL_NAME)
    bullet_writer   = DirectBulletWriterAgent(Config.LLM_MODEL_NAME)

    def plan_builder_node(state: WorkflowState) -> Dict[str, Any]:
        print(f"\n{'=' * 60}")
        print(f' Plan Builder — Building outline and slide specs...')
        print(f"{'=' * 60}\n")
        result = plan_builder.build(state['document_context'])
        lecture_title = result['lecture_title']
        specs         = result['slide_specs']
        if not specs:
            raise RuntimeError('PlanBuilder produced 0 slide specs.')
        try:
            print(f'\nGenerated lecture title: {lecture_title}\n')
        except UnicodeEncodeError:
            print('\nGenerated lecture title: [unicode title]\n')
        return {'lecture_title': lecture_title, 'slide_specs': specs, 'slides': []}

    def packet_builder_node(state: WorkflowState) -> Dict[str, Any]:
        slide_specs = state.get('slide_specs', [])
        print(f'  Packet Builder — creating {len(slide_specs)} source-grounded slide packet(s)...')
        packets = packet_builder.build_packets(slide_specs=slide_specs, context=state['document_context'])
        # Planner outline order is already in correct PDF reading order.
        # Only strip stale numeric prefixes from titles and assign clean sequential numbers.
        spec_by_old = {spec.slide_number: spec for spec in slide_specs}
        for new_num, pkt in enumerate(packets, 1):
            old_num = pkt["slide_number"]
            pkt["slide_number"] = new_num
            clean_title = re.sub(r'^\d+\.\s*', '', pkt.get("slide_title", ""))
            pkt["slide_title"] = f"{new_num}. {clean_title}"
            if old_num in spec_by_old:
                spec = spec_by_old[old_num]
                spec.slide_number = new_num
                spec.slide_title = pkt["slide_title"]
        return {'slide_packets': packets, 'slide_specs': slide_specs}

    def direct_bullet_writer_node(state: WorkflowState) -> Dict[str, Any]:
        packets = state.get('slide_packets', [])
        print(f'  Direct Bullet Writer — generating {len(packets)} final slide(s) from packets...')
        slides = bullet_writer.write(packets=packets, slide_specs=state.get('slide_specs', []))
        return {'slides': slides}

    def content_quality_node(state: WorkflowState) -> Dict[str, Any]:
        print(f"\n{'=' * 60}")
        print(f' Content QA — Checking slide substance and title alignment...')
        print(f"{'=' * 60}\n")
        repaired_slides, qa_report = content_quality.repair_with_report(
            state['slides'],
            state.get('slide_specs', []),
            state['document_context'],
            slide_packets=state.get('slide_packets', []),
        )
        new_rev = state.get('revision_count', 0) + 1
        return {'slides': repaired_slides, 'qa_report': qa_report, 'revision_count': new_rev}

    def qa_router(state: WorkflowState) -> str:
        qa_report = state.get("qa_report") or {}
        status = qa_report.get("status", "passed")
        revision_count = state.get("revision_count", 0)

        if status == "failed" and revision_count < Config.MAX_QA_LOOPS:
            print(f"\n[Router] ContentQA failed. Looping back to direct_bullet_writer (Attempt {revision_count}/{Config.MAX_QA_LOOPS})...")
            return "direct_bullet_writer"
        
        if status == "failed":
            print(f"\n[Router] Max revisions reached. Accepting slides with unresolved issues.")
            
        return "end"

    workflow.add_node('plan_builder', plan_builder_node)
    workflow.add_node('packet_builder', packet_builder_node)
    workflow.add_node('direct_bullet_writer', direct_bullet_writer_node)

    workflow.set_entry_point('plan_builder')
    workflow.add_edge('plan_builder', 'packet_builder')
    workflow.add_edge('packet_builder', 'direct_bullet_writer')

    if Config.ABLATION_MODE == 2:
        # Ablation 2: skip ContentQualityAgent entirely — one pass through DirectBulletWriter, no repair loop.
        workflow.add_edge('direct_bullet_writer', END)
    else:
        # --- baseline / other ablations: normal QA loop ---
        workflow.add_node('content_quality', content_quality_node)
        workflow.add_edge('direct_bullet_writer', 'content_quality')
        workflow.add_conditional_edges('content_quality', qa_router, {
            "direct_bullet_writer": "direct_bullet_writer",
            "end": END
        })
    return workflow.compile()
