"""Transport-agnostic cleaning engine.

Pure Python (pandas/numpy). Must never import `mcp`: tests/test_architecture.py
enforces this. `run_cleaning` is the single entry point the MCP tool calls.
"""

from data_quality_mcp.cleaner_core.cleaning import run_cleaning

__all__ = ["run_cleaning"]
