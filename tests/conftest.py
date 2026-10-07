from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def messy_path() -> Path:
    return FIXTURES / "messy_sample.csv"


@pytest.fixture
def clean_path() -> Path:
    return FIXTURES / "clean_sample.csv"


@pytest.fixture
def messy_df(messy_path: Path) -> pd.DataFrame:
    return pd.read_csv(messy_path)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
