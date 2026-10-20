import shutil
from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# The cleaning plan quoted in planning/milestone-3.md (Schema changes)
SPEC_PLAN = [
    {"action": "remove_duplicates", "keep": "first"},
    {"action": "fix_type", "column": "sqft", "target_type": "numeric"},
    {"action": "fix_type", "column": "sale_date", "target_type": "datetime"},
    {"action": "fix_type", "column": "garage", "target_type": "boolean"},
    {"action": "handle_missing", "column": "price", "strategy": "fill_median"},
    {"action": "handle_outliers", "column": "price", "strategy": "remove"},
]


@pytest.fixture
def messy_path() -> Path:
    return FIXTURES / "messy_sample.csv"


@pytest.fixture
def messy_copy(messy_path: Path, tmp_path: Path) -> Path:
    """A copy of the messy fixture in a temp folder, for tests that write files."""
    return Path(shutil.copy(messy_path, tmp_path / messy_path.name))


@pytest.fixture
def spec_plan() -> list[dict]:
    return [dict(step) for step in SPEC_PLAN]


@pytest.fixture
def clean_path() -> Path:
    return FIXTURES / "clean_sample.csv"


@pytest.fixture
def messy_df(messy_path: Path) -> pd.DataFrame:
    return pd.read_csv(messy_path)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
