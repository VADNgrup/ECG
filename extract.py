"""Root shim so `python extract.py --filepath <pdf>` works from the repo root.

Builds one PDF's knowledge graph (parse -> structural KG -> kg_view.html) without generating slides; main.py does the
same step itself. Options: --filepath/--all, --pages, --force, --ocr auto|none|cloud, --llm cloud|local.
    E:\\anaconda3\\envs\\slide\\python.exe extract.py --filepath data/raw/paper.pdf
"""
from src.rag.extract import main

if __name__ == "__main__":
    main()
