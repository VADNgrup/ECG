from pathlib import Path
from typing import Optional, Dict, Any, List
import pandas as pd
import io
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from src.utils.config import config
from src.utils.llm import chat

class MatplotlibChartGenerator:

    def __init__(self):
        self.model = config.LLM_MODEL_NAME
        print('Matplotlib Chart Generator initialized successfully')

    def markdown_to_dataframe(self, markdown: str) -> Optional[pd.DataFrame]:
        try:
            lines = markdown.strip().split('\n')
            cleaned_lines = [line for line in lines if not re.match('^\\s*\\|[\\s\\-:]+\\|\\s*$', line)]
            if len(cleaned_lines) < 2:
                print(f'Table has insufficient rows: {len(cleaned_lines)}')
                return None
            csv_lines = []
            for line in cleaned_lines:
                line = line.strip().strip('|')
                cells = [cell.strip() for cell in line.split('|')]
                csv_lines.append(','.join(cells))
            csv_string = '\n'.join(csv_lines)
            df = pd.read_csv(io.StringIO(csv_string))
            print(f'Converted table to DataFrame: {df.shape[0]} rows × {df.shape[1]} columns')
            return df
        except Exception as e:
            print(f'Failed to convert markdown to DataFrame: {e}')
            return None

    def generate_matplotlib_code(self, table_markdown: str, table_id: str) -> Optional[str]:
        try:
            prompt = f"Analyze this data table and generate Python matplotlib code to visualize it.\n\nTable:\n{table_markdown}\n\nRequirements:\n1. Choose the MOST APPROPRIATE chart type (bar, line, scatter, pie, grouped bar, etc.)\n2. Generate COMPLETE, EXECUTABLE Python code using matplotlib\n3. Use clear labels, title, and legend\n4. Set figure size to (10, 6)\n5. Use professional styling with good colors\n6. Save figure as PNG with: plt.savefig(output_path, dpi=150, bbox_inches='tight')\n7. Close figure with: plt.close()\n\nIMPORTANT:\n- Code must be COMPLETE and EXECUTABLE\n- Include all necessary imports (matplotlib.pyplot as plt, pandas as pd, numpy as np if needed)\n- Handle data conversion (e.g., remove % signs, convert to numeric)\n- Use Vietnamese labels if table has Vietnamese text\n- The code will receive 'output_path' variable - use it to save the chart\n\nReturn ONLY the Python code, no explanations."
            code = chat(model=self.model, messages=[{'role': 'system', 'content': 'You are an expert data visualization engineer. Generate clean, executable matplotlib code.'}, {'role': 'user', 'content': prompt}], temperature=0.3, max_tokens=2000)
            if '```python' in code:
                code = code.split('```python')[1].split('```')[0].strip()
            elif '```' in code:
                code = code.split('```')[1].split('```')[0].strip()
            print(f'[OK] Generated matplotlib code ({len(code)} chars)')
            return code
        except Exception as e:
            print(f'[ERROR] Failed to generate matplotlib code: {e}')
            return None

    def execute_matplotlib_code(self, code: str, table_markdown: str, output_path: Path) -> bool:
        try:
            exec_globals = {'plt': plt, 'pd': pd, 'output_path': str(output_path), 'table_markdown': table_markdown, '__builtins__': __builtins__}
            import numpy as np
            exec_globals['np'] = np
            exec(code, exec_globals)
            if output_path.exists():
                print(f'[OK] Chart saved: {output_path}')
                return True
            else:
                print(f'[ERROR] Chart file not created')
                return False
        except Exception as e:
            print(f'[ERROR] Failed to execute matplotlib code: {e}')
            import traceback
            traceback.print_exc()
            return False

    def generate_chart_from_table(self, table_markdown: str, table_id: str, output_dir: Path, chart_title: Optional[str]=None) -> Optional[Dict[str, str]]:
        try:
            print(f'\n  [Chart Generation] {table_id}')
            output_dir.mkdir(parents=True, exist_ok=True)
            print(f'Generating matplotlib code...')
            code = self.generate_matplotlib_code(table_markdown, table_id)
            if not code:
                return None
            chart_filename = f'{table_id}_chart.png'
            chart_path = output_dir / chart_filename
            print(f'Executing code to generate chart...')
            success = self.execute_matplotlib_code(code, table_markdown, chart_path)
            if not success:
                return None
            chart_type = 'bar'
            if 'plot(' in code or 'line' in code.lower():
                chart_type = 'line'
            elif 'scatter' in code.lower():
                chart_type = 'scatter'
            elif 'pie' in code.lower():
                chart_type = 'pie'
            elif 'bar' in code.lower():
                chart_type = 'bar'
            return {'chart_path': str(chart_path), 'chart_type': chart_type}
        except Exception as e:
            print(f'[ERROR] Chart generation failed for {table_id}: {e}')
            import traceback
            traceback.print_exc()
            return None

    def generate_charts_for_tables(self, tables: List[Dict[str, Any]], document_id: str, base_output_dir: Path=Path('data/assets')) -> List[Dict[str, Any]]:
        output_dir = base_output_dir / document_id / 'charts'
        print(f"\n{'=' * 60}")
        print(f'MATPLOTLIB CHART GENERATION')
        print(f"{'=' * 60}")
        print(f'Document: {document_id}')
        print(f'Output: {output_dir}')
        updated_tables = []
        generated_count = 0
        for table in tables:
            table_copy = table.copy()
            if table.get('should_visualize', 'No') == 'Yes':
                print(f"\n[{table['table_id']}] Generating chart...")
                result = self.generate_chart_from_table(table_markdown=table['markdown'], table_id=table['table_id'], output_dir=output_dir)
                if result:
                    table_copy['chart_path'] = result['chart_path']
                    table_copy['chart_type'] = result['chart_type']
                    generated_count += 1
                    print(f"Success: {result['chart_type']} chart")
                else:
                    print(f'Failed to generate chart')
            else:
                print(f"\n[{table['table_id']}] Skipped (should_visualize=No)")
            updated_tables.append(table_copy)
        print(f"\n{'=' * 60}")
        print(f"[OK] Generated {generated_count}/{len([t for t in tables if t.get('should_visualize') == 'Yes'])} charts")
        print(f"{'=' * 60}\n")
        return updated_tables