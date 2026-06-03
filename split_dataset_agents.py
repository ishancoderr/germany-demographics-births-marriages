from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parent

BASE_CSV  = REPO_DIR / "combined_births_population_marriages.csv"
AGENT1_CSV = REPO_DIR / "agent1_db.csv"
AGENT2_CSV = REPO_DIR / "agent2_db.csv"
META_JSON  = REPO_DIR / "split_metadata.json"

FIELDS = ["LiveBirths", "Marriages", "Population"]

# ── Spatial partition (which states each agent holds rows for) ────────────────
#
# Agent 1 exclusive  →  Agent 1 has rows, Agent 2 has NO rows  (spatial gap in A2)
AGENT1_EXCLUSIVE = frozenset({
    "Sachsen", "Thüringen", "Sachsen-Anhalt",
    "Saarland", "Mecklenburg-Vorpommern", "Schleswig-Holstein",
})

# Agent 2 exclusive  →  Agent 2 has rows, Agent 1 has NO rows  (spatial gap in A1)
AGENT2_EXCLUSIVE = frozenset({
    "Baden-Württemberg", "Bayern", "Berlin", "Hamburg", "Bremen",
})

# Shared  →  both agents hold rows (different year slices + attribute missingness)
SHARED = frozenset({
    "Niedersachsen", "Nordrhein-Westfalen", "Hessen", "Brandenburg", "Rheinland-Pfalz",
})

# ── Temporal partition ────────────────────────────────────────────────────────
#
# Agent 1: years <= AGENT1_MAX_YEAR   (temporal gap for years after this)
# Agent 2: years >= AGENT2_MIN_YEAR   (temporal gap for years before this)
# Overlap window for SHARED states: AGENT2_MIN_YEAR … AGENT1_MAX_YEAR
#
AGENT1_MAX_YEAR = 2019   # A1 holds 1990-2019
AGENT2_MIN_YEAR = 2010   # A2 holds 2010-2025
# Overlap years (both hold data for SHARED states): 2010-2019


def _apply_random_attribute_na(
    row: pd.Series,
    rng: np.random.Generator,
    p_one: float = 0.20,
    p_two: float = 0.10,
) -> pd.Series:
    """Randomly null 1 or 2 fields in *row* (no complement — single-agent rows)."""
    p = rng.random()
    if p < p_one:
        row = row.copy()
        row[str(rng.choice(FIELDS))] = pd.NA
    elif p < p_one + p_two:
        row = row.copy()
        for f in rng.choice(FIELDS, size=2, replace=False).tolist():
            row[f] = pd.NA
    return row


def build_datasets(
    df: pd.DataFrame,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (agent1_df, agent2_df) implementing three missingness types:

    Spatial   – states in AGENT2_EXCLUSIVE are absent from agent1 (and vice versa).
                No rows exist for those states in the missing agent.

    Temporal  – Agent 1 holds years ≤ 2019; Agent 2 holds years ≥ 2010.
                For SHARED states the agents overlap in 2010-2019, giving full
                1990-2025 coverage when merged.
                For exclusive states the temporal gap leaves years with no rows
                in either agent (still_missing).

    Attribute – For rows that exist, some field values are randomly NULL.
                In the shared-state overlap window (2010-2019), the complement
                rule applies: if Agent 1 loses a field, Agent 2 keeps it.
    """
    rng = np.random.default_rng(seed)

    rows1: list[pd.Series] = []
    rows2: list[pd.Series] = []

    for _, row in df.iterrows():
        land = str(row["Land"])
        year = int(row["Year"])

        # Spatial eligibility
        a1_spatial = land in AGENT1_EXCLUSIVE or land in SHARED
        a2_spatial = land in AGENT2_EXCLUSIVE or land in SHARED

        # Temporal eligibility
        a1_temporal = year <= AGENT1_MAX_YEAR
        a2_temporal = year >= AGENT2_MIN_YEAR

        a1_has = a1_spatial and a1_temporal
        a2_has = a2_spatial and a2_temporal

        # Row absent from both agents — skip (contributes to still_missing)
        if not a1_has and not a2_has:
            continue

        if a1_has and a2_has:
            # SHARED state, overlap year (2010-2019)
            # Apply complement attribute missingness per field.
            r1, r2 = row.copy(), row.copy()
            for field in FIELDS:
                p = rng.random()
                if p < 0.20:
                    r1[field] = pd.NA   # Agent 1 loses this field value
                elif p < 0.35:
                    r2[field] = pd.NA   # Agent 2 loses this field value
                # else both keep the value
            rows1.append(r1)
            rows2.append(r2)

        elif a1_has:
            # Agent 1 only — AGENT1_EXCLUSIVE state, or SHARED state early years (1990-2009)
            rows1.append(_apply_random_attribute_na(row.copy(), rng))

        else:
            # Agent 2 only — AGENT2_EXCLUSIVE state, or SHARED state recent years (2020-2025)
            rows2.append(_apply_random_attribute_na(row.copy(), rng))

    agent1 = pd.DataFrame(rows1).reset_index(drop=True)
    agent2 = pd.DataFrame(rows2).reset_index(drop=True)
    return agent1, agent2


def main() -> int:
    df = pd.read_csv(BASE_CSV)

    missing_cols = [c for c in ["Land", "Year"] + FIELDS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Base CSV missing columns: {missing_cols}")

    # Verify partition covers every state in the data
    all_partitioned = AGENT1_EXCLUSIVE | AGENT2_EXCLUSIVE | SHARED
    data_states = set(df["Land"].unique())
    unaccounted = data_states - all_partitioned
    if unaccounted:
        raise ValueError(
            f"States in data not covered by partition: {unaccounted}\n"
            f"Add them to one of AGENT1_EXCLUSIVE, AGENT2_EXCLUSIVE, or SHARED."
        )

    agent1, agent2 = build_datasets(df, seed=42)

    agent1.to_csv(AGENT1_CSV, index=False)
    agent2.to_csv(AGENT2_CSV, index=False)

    def _stats(adf: pd.DataFrame) -> dict:
        return {
            "total_rows": int(len(adf)),
            "states": sorted(adf["Land"].unique().tolist()),
            "state_count": int(adf["Land"].nunique()),
            "year_range": [int(adf["Year"].min()), int(adf["Year"].max())],
            "na_counts": {c: int(adf[c].isna().sum()) for c in FIELDS},
        }

    meta = {
        "base_csv": str(BASE_CSV),
        "agent1_csv": str(AGENT1_CSV),
        "agent2_csv": str(AGENT2_CSV),
        "seed": 42,
        "fields": FIELDS,
        "spatial_partition": {
            "agent1_exclusive": sorted(AGENT1_EXCLUSIVE),
            "agent2_exclusive": sorted(AGENT2_EXCLUSIVE),
            "shared": sorted(SHARED),
        },
        "temporal_partition": {
            "agent1_years": f"1990 to {AGENT1_MAX_YEAR}",
            "agent2_years": f"{AGENT2_MIN_YEAR} to 2025",
            "overlap_years": f"{AGENT2_MIN_YEAR} to {AGENT1_MAX_YEAR} (SHARED states only)",
        },
        "missingness_types": {
            "spatial": (
                "States in AGENT2_EXCLUSIVE have zero rows in Agent 1. "
                "States in AGENT1_EXCLUSIVE have zero rows in Agent 2."
            ),
            "temporal": (
                f"Agent 1 has no rows for years > {AGENT1_MAX_YEAR}. "
                f"Agent 2 has no rows for years < {AGENT2_MIN_YEAR}. "
                "For EXCLUSIVE states this creates still_missing gaps."
            ),
            "attribute": (
                "Rows that exist may have NULL field values. "
                "In the SHARED overlap window complement rule applies: "
                "if Agent 1 loses a field, Agent 2 retains the value."
            ),
        },
        "agent1": _stats(agent1),
        "agent2": _stats(agent2),
    }

    with open(META_JSON, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    summary = {
        "agent1_rows": int(len(agent1)),
        "agent2_rows": int(len(agent2)),
        "agent1_states": int(agent1["Land"].nunique()),
        "agent2_states": int(agent2["Land"].nunique()),
        "agent1_year_range": [int(agent1["Year"].min()), int(agent1["Year"].max())],
        "agent2_year_range": [int(agent2["Year"].min()), int(agent2["Year"].max())],
        "agent1_na": {c: int(agent1[c].isna().sum()) for c in FIELDS},
        "agent2_na": {c: int(agent2[c].isna().sum()) for c in FIELDS},
    }
    print(json.dumps(summary, indent=2))
    print("Wrote:", AGENT1_CSV)
    print("Wrote:", AGENT2_CSV)
    print("Wrote:", META_JSON)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
