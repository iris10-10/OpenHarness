"""Temporary environment probe for Phase 1 RAG work (will be deleted)."""

import importlib.util

for name in [
    "chromadb",
    "rank_bm25",
    "pypdf",
    "bs4",
    "lxml",
    "pytest_cov",
    "sentence_transformers",
    "numpy",
    "torch",
]:
    print(f"{name}: {bool(importlib.util.find_spec(name))}")
