"""Generate the synthetic datasets used by the tests and examples.

Deterministic (fixed seeds), so re-running reproduces identical files:
    uv run python scripts/generate_messy_data.py

Writes:
    tests/fixtures/messy_sample.csv        housing data with deliberate issues
    tests/fixtures/clean_sample.csv        small dataset with no issues
    examples/sample_datasets/messy_data.csv  copy of the messy sample
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def generate_messy_housing(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Housing data with missing values, mixed types, inconsistent dates and
    booleans, duplicate rows, and extreme price outliers."""
    rng = np.random.default_rng(seed)

    # Dates in three different formats, rotating by row
    date_formats = ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y")
    dates = [
        (pd.Timestamp("2020-01-01") + pd.Timedelta(days=int(i * 365 / n))).strftime(
            date_formats[i % 3]
        )
        for i in range(n)
    ]

    df = pd.DataFrame(
        {
            "id": range(1, n + 1),
            "price": rng.normal(350_000, 80_000, n).round(),
            "sqft": rng.normal(1800, 400, n).round(),
            "bedrooms": rng.integers(1, 6, n),
            "bathrooms": rng.choice([1.0, 1.5, 2.0, 2.5, 3.0], n),
            "age_years": rng.integers(0, 80, n),
            "neighborhood": rng.choice(["Downtown", "Suburbs", "Rural", "Midtown"], n),
            "sale_date": dates,
            "garage": rng.choice(
                ["Yes", "No", "yes", "no", "Y", "N", "TRUE", "FALSE"], n
            ),
            "school_rating": rng.uniform(1, 10, n).round(1),
        }
    )

    # Random missing values
    for col in ("price", "sqft", "school_rating"):
        df.loc[rng.random(n) < 0.08, col] = np.nan
    # Structured missing values: older homes lack a school rating
    df.loc[df["age_years"] > 60, "school_rating"] = np.nan

    # 20 exact duplicate rows
    dup_idx = rng.choice(n, size=20, replace=False)
    df = pd.concat([df, df.iloc[dup_idx]], ignore_index=True)

    # Mixed types: placeholder strings inside a numeric column
    df["sqft"] = df["sqft"].astype(object)
    for idx in rng.choice(len(df), size=15, replace=False):
        df.at[idx, "sqft"] = str(rng.choice(["N/A", "unknown", "TBD"]))

    # Outliers: extreme and impossible prices
    for idx in rng.choice(len(df), size=10, replace=False):
        df.at[idx, "price"] = float(rng.choice([5_000, 9_999_999, -1_000]))

    return df.sample(frac=1, random_state=seed).reset_index(drop=True)


def generate_clean_sample(n: int = 50, seed: int = 0) -> pd.DataFrame:
    """A small, well-formed dataset with no quality issues."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "id": range(1, n + 1),
            "name": [f"Person_{i}" for i in range(1, n + 1)],
            "age": rng.integers(20, 65, n),
            "salary": rng.normal(60_000, 15_000, n).round(),
            "department": rng.choice(["Engineering", "Marketing", "Sales", "HR"], n),
        }
    )


def main() -> None:
    fixtures = ROOT / "tests" / "fixtures"
    examples = ROOT / "examples" / "sample_datasets"
    fixtures.mkdir(parents=True, exist_ok=True)
    examples.mkdir(parents=True, exist_ok=True)

    messy = generate_messy_housing()
    clean = generate_clean_sample()
    outputs = {
        fixtures / "messy_sample.csv": messy,
        examples / "messy_data.csv": messy,
        fixtures / "clean_sample.csv": clean,
    }
    for path, frame in outputs.items():
        frame.to_csv(path, index=False, lineterminator="\n")
        print(f"Wrote {len(frame)} rows -> {path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
