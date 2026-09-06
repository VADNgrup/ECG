from langgraph.graph import StateGraph, END
from typing import Dict, Any
import json
from pathlib import Path
from src.utils.config import Config
from src.multimodal.state import MultimodalState
from src.multimodal.agents.visual_aggregation import VisualAggregation
from src.multimodal.agents.image_distribution import ImageDistribution
from src.multimodal.agents.table_chart_distribution import TableChartDistribution

def create_multimodal_workflow() -> StateGraph:
    workflow = StateGraph(MultimodalState)
    aggregation_agent = VisualAggregation()
    image_distribution_agent = ImageDistribution()
    table_distribution_agent = TableChartDistribution(Config.LLM_MODEL_NAME)

    def load_lecture_node(state: MultimodalState) -> Dict[str, Any]:
        lecture_id = state['lecture_id']
        lecture_path = Path(f'data/lectures/{lecture_id}/{lecture_id}.json')
        if not lecture_path.exists():
            raise FileNotFoundError(f'Lecture file not found: {lecture_path}')
        with open(lecture_path, 'r', encoding='utf-8') as f:
            lecture_dict = json.load(f)
        print(f'Loaded lecture: {lecture_id}')
        print(f"Total slides: {lecture_dict.get('metadata', {}).get('total_slides', 'N/A')}")
        return {'lecture_dict': lecture_dict}

    def aggregate_media_node(state: MultimodalState) -> Dict[str, Any]:
        lecture_dict = state['lecture_dict']
        print('\nAggregating media (tables and images)...')
        aggregated_media = aggregation_agent.aggregate_media_from_lecture(lecture_dict)
        print(f'Aggregated media:')
        print(f"   - Tables: {aggregated_media['total_tables']}")
        print(f"   - Images: {aggregated_media['total_images']}")
        return {'aggregated_media': aggregated_media}

    def distribute_images_node(state: MultimodalState) -> Dict[str, Any]:
        lecture_id = state['lecture_id']
        lecture_dict = state['lecture_dict']
        aggregated_media = state['aggregated_media']
        used_images = state.get('used_images', set())
        print('\nDistributing images to slides...')
        distributions = image_distribution_agent.distribute_images(lecture_id=lecture_id, lecture_dict=lecture_dict, aggregated_media=aggregated_media, used_images=used_images)
        print(f'\nDistributed {len(distributions)} images to slides')
        print(f"   aggregated_media total_images after distribution: {aggregated_media.get('total_images', 0)}")
        return {'image_distributions': distributions, 'aggregated_media': aggregated_media, 'used_images': used_images}

    def distribute_tables_node(state: MultimodalState) -> Dict[str, Any]:
        lecture_id = state['lecture_id']
        lecture_dict = state['lecture_dict']
        aggregated_media = state['aggregated_media']
        used_tables = state.get('used_tables', set())
        print('\nDistributing tables to slides...')
        distributions = table_distribution_agent.distribute_tables(lecture_id=lecture_id, lecture_dict=lecture_dict, aggregated_media=aggregated_media, used_tables=used_tables)
        print(f'\nDistributed {len(distributions)} tables to slides')
        return {'table_distributions': distributions, 'used_tables': used_tables}
    workflow.add_node('load_lecture', load_lecture_node)
    workflow.add_node('aggregate_media', aggregate_media_node)
    workflow.add_node('distribute_images', distribute_images_node)
    workflow.add_node('distribute_tables', distribute_tables_node)
    workflow.set_entry_point('load_lecture')
    workflow.add_edge('load_lecture', 'aggregate_media')
    workflow.add_edge('aggregate_media', 'distribute_images')
    workflow.add_edge('aggregate_media', 'distribute_tables')
    workflow.add_edge('distribute_images', END)
    workflow.add_edge('distribute_tables', END)
    return workflow.compile()