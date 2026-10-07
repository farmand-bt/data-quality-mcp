"""Transport-agnostic profiling engine.

Pure Python (pandas/numpy). Must never import `mcp`: this is enforced by
tests/test_architecture.py so the same engine can sit behind stdio, HTTP, or
no protocol at all.
"""

from data_quality_mcp.profiler_core.exceptions import DataQualityError
from data_quality_mcp.profiler_core.loader import load_data
from data_quality_mcp.profiler_core.report import generate_report

__all__ = ["DataQualityError", "generate_report", "load_data"]
