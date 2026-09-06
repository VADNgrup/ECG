from src.utils.config import config
from src.utils.llm import chat

class TableFilter:

    def __init__(self):
        self.model = config.LLM_MODEL_NAME

    def should_visualize_table(self, table_markdown: str, table_caption: str) -> str:
        try:
            prompt = f'\n                        Review the following data table and assess whether it SHOULD be visualized (i.e., turned into a chart).\n\n                        Evaluation criteria:\n                        - Does it contain quantitative (numeric) metrics?\n                        - Does it have dimensions for comparison or grouping?\n                        - Does it have a time-series or distributional element?\n                        - Would a visualization generate meaningful insight, or would it just add noise?\n\n                        Data table:\n                        {table_markdown}\n                        Table caption:\n                        {table_caption}\n\n                        Answer with exactly one word: "Yes" or "No".'
            answer = chat(model=self.model, messages=[{'role': 'system', 'content': 'You are a professional data analyst. Answer concisely and accurately.'}, {'role': 'user', 'content': prompt}], temperature=0.3, max_tokens=10)
            if 'yes' in answer.lower():
                return 'Yes'
            else:
                return 'No'
        except Exception as e:
            print(f'Warning: LLM evaluation failed for table visualization: {e}')
            return 'No'