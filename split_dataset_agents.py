from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parent

BASE_CSV = REPO_DIR / "combined_births_population_marriages.csv"
AGENT1_CSV = REPO_DIR / "agent1_db.csv"
AGENT2_CSV = REPO_DIR / "agent2_db.csv"
META_JSON = REPO_DIR / "split_metadata.json"


FIELDS = ["LiveBirths", "Marriages", "Population"]


def pick_lands_partition() -> tuple[set[str], set[str], set[str]]:
    """Hard-coded Länder partition for the two agents.

    Agent 1 Länder (as requested):
      Brandenburg, Bremen, Hamburg, Hessen, Mecklenburg-Vorpommern, Niedersachsen,
      Nordrhein-Westfalen, Rheinland-Pfalz, Saarland, Sachsen, Sachsen-Anhalt,
      Schleswig-Holstein, Thüringen

    Agent 2 Länder (as requested):
      Baden-Württemberg, Bayern, Berlin, Brandenburg, Bremen, Hamburg, Hessen,
      Mecklenburg-Vorpommern, Niedersachsen, Nordrhein-Westfalen, Rheinland-Pfalz

    Overlap rule:
    - We define a deterministic set of 4 common Länder = Agent1∩Agent2.
      These Länder will be eligible for field-level attribute-missingness mixing.

    Returns:
      (common, agent1_only, agent2_only)
    """

    agent1 = {
        "Brandenburg",
        "Bremen",
        "Hamburg",
        "Hessen",
        "Mecklenburg-Vorpommern",
        "Niedersachsen",
        "Nordrhein-Westfalen",
        "Rheinland-Pfalz",
        "Saarland",
        "Sachsen",
        "Sachsen-Anhalt",
        "Schleswig-Holstein",
        "Thüringen",
    }

    agent2 = {
        "Baden-Württemberg",
        "Bayern",
        "Berlin",
        "Brandenburg",
        "Bremen",
        "Hamburg",
        "Hessen",
        "Mecklenburg-Vorpommern",
        "Niedersachsen",
        "Nordrhein-Westfalen",
        "Rheinland-Pfalz",
    }

    all_lands = agent1 | agent2

    # Overlap: deterministically choose 4 common Länder from the overlap set.
    overlap = sorted(agent1 & agent2)
    if len(overlap) < 4:
        raise ValueError(f"Expected at least 4 overlapping Länder, got {len(overlap)}: {overlap}")
    common = set(overlap[:4])

    agent1_only = agent1 - agent2
    agent2_only = agent2 - agent1

    return common, agent1_only, agent2_only




def apply_row_level_missingness(
    df: pd.DataFrame,
    common: set[str],
    agent1_only: set[str],
    agent2_only: set[str],
    seed: int = 42,
    p_agent1_missing_one: float = 0.25,
    p_agent1_missing_two_or_three: float = 0.10,
    p_agent1_missing_all_three: float = 0.10,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create agent1/agent2 datasets with row-level missingness.

    Länder availability (requested overlap split):
    - common: both agents keep all fields
    - agent1_only: agent1 has all fields; agent2 is eligible to have NA via attribute-missingness mix
    - agent2_only: agent2 has all fields; agent1 is eligible to have NA via attribute-missingness mix

    Option Y (your last instruction): apply row-level attribute-missingness mix even for exclusive Länder.

    Complement rule inside the mix:
    - if Agent 1 is set to NA for a field on a row => Agent 2 gets the original value for that row/field
    - and vice versa is handled by construction (we start from original df for both, then only apply NA to one side).
    """


    rng = np.random.default_rng(seed)

    # Work on copies; we will set NA in-place.
    agent1 = df.copy()
    agent2 = df.copy()

    # Ensure stable row indexing by (Land,Year)
    if not df.index.is_unique:
        df = df.reset_index(drop=True)
        agent1 = agent1.reset_index(drop=True)
        agent2 = agent2.reset_index(drop=True)

    # Apply Länder-level availability mask first (so the owning agent is the one that has data).
    # - common: both have all fields
    # - agent1_only: Agent 2 has NA for all fields on those Länder
    # - agent2_only: Agent 1 has NA for all fields on those Länder
    agent1.loc[df["Land"].isin(list(agent2_only)).values, FIELDS] = pd.NA
    agent2.loc[df["Land"].isin(list(agent1_only)).values, FIELDS] = pd.NA

    # IMPORTANT: we only apply the attribute-missingness mix *on rows where both agents are allowed
    # to have values for the base availability (common Länder).*
    # For exclusive Länder, we keep the partition-enforced NA as-is.
    allowed_mask = df["Land"].isin(list(common)).values


    n = len(df)


    r = rng.random(n)

    # Pattern definitions (exclusive partitions)
    # - missing_one: next p_agent1_missing_one
    # - missing_all_three: next p_agent1_missing_all_three
    # - missing_two: remaining from p_agent1_missing_two_or_three - missing_all_three
    p_one = p_agent1_missing_one
    p_all3 = p_agent1_missing_all_three
    p_two = max(0.0, p_agent1_missing_two_or_three - p_all3)

    # Base: no missing (both have all fields)
    missing_one_mask = r < p_one
    missing_all3_mask = (r >= p_one) & (r < p_one + p_all3)
    missing_two_mask = (r >= p_one + p_all3) & (r < p_one + p_all3 + p_two)

    # Helper to set NA for specified fields in agent1, and set non-NA in agent2
    def set_missing_for_agent1(rows_mask: np.ndarray, missing_fields: list[str]):
        for col in missing_fields:
            agent1.loc[rows_mask, col] = pd.NA
            # ensure agent2 has the original value (in case it was edited earlier)
            agent2.loc[rows_mask, col] = df.loc[rows_mask, col]

    # missing_one: pick which field is missing for Agent1
    idx_one = np.where(missing_one_mask)[0]
    if len(idx_one) > 0:
        # random choice per row
        for i in idx_one:
            missing_field = rng.choice(FIELDS)
            set_missing_for_agent1(np.array([i]), [missing_field])

    # missing_all_three: set all fields missing in agent1
    if missing_all3_mask.any():
        set_missing_for_agent1(np.array(missing_all3_mask), FIELDS)

    # missing_two: pick 2 fields missing for agent1 (random combination)
    idx_two = np.where(missing_two_mask)[0]
    if len(idx_two) > 0:
        for i in idx_two:
            miss = rng.choice(FIELDS, size=2, replace=False).tolist()
            set_missing_for_agent1(np.array([i]), miss)

    # Final sanity: ensure complement property holds
    # If agent1 has NA for a field, agent2 should have non-NA (original value).
    for col in FIELDS:
        m = agent1[col].isna()
        agent2.loc[m, col] = df.loc[m, col]

    return agent1, agent2


def main() -> int:
    df = pd.read_csv(BASE_CSV)

    expected_cols = ["Land", "Year"] + FIELDS
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Base CSV missing columns: {missing}")

    common, agent1_only, agent2_only = pick_lands_partition()

    agent1, agent2 = apply_row_level_missingness(
        df,
        common=common,
        agent1_only=agent1_only,
        agent2_only=agent2_only,
        seed=42,
        p_agent1_missing_one=0.25,
        p_agent1_missing_two_or_three=0.15,
        p_agent1_missing_all_three=0.10,
    )


    agent1.to_csv(AGENT1_CSV, index=False)
    agent2.to_csv(AGENT2_CSV, index=False)

    meta = {
        "base_csv": str(BASE_CSV),
        "agent1_csv": str(AGENT1_CSV),
        "agent2_csv": str(AGENT2_CSV),
        "seed": 42,
        "fields": FIELDS,
        "lands": {
            "agent1": sorted(agent1_only | common),
            "agent2": sorted(agent2_only | common),
            "common": sorted(common),
            "agent1_only": sorted(agent1_only),
            "agent2_only": sorted(agent2_only),
        },
        "missingness": {
            "pattern_support": ["missing_one", "missing_two", "missing_all_three"],
            "p_agent1_missing_one": 0.25,
            "p_agent1_missing_two_or_three": 0.15,
            "p_agent1_missing_all_three": 0.10,
        },
        "complement_rule": "If Agent1 field is NA for a row, Agent2 has the original value for that field (and vice versa is default via construction).",
        "counts": {
            "rows_base": int(len(df)),
            "agent1_na_counts": {c: int(agent1[c].isna().sum()) for c in FIELDS},
            "agent2_na_counts": {c: int(agent2[c].isna().sum()) for c in FIELDS},
            "agent1_rows_missing_all3": int(agent1[FIELDS].isna().all(axis=1).sum()),
            "agent1_rows_missing_one": int((agent1[FIELDS].isna().sum(axis=1) == 1).sum()),
            "agent1_rows_missing_two": int((agent1[FIELDS].isna().sum(axis=1) == 2).sum()),
        },
    }


    with open(META_JSON, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    summary = {
        "rows_base": int(len(df)),
        "agent1_na_counts": {c: int(agent1[c].isna().sum()) for c in FIELDS},
        "agent2_na_counts": {c: int(agent2[c].isna().sum()) for c in FIELDS},
        "agent1_rows_missing_all3": int(agent1[FIELDS].isna().all(axis=1).sum()),
        "agent1_rows_missing_one": int((agent1[FIELDS].isna().sum(axis=1) == 1).sum()),
        "agent1_rows_missing_two": int((agent1[FIELDS].isna().sum(axis=1) == 2).sum()),
    }
    print(json.dumps(summary, indent=2))
    print("Wrote:", AGENT1_CSV)
    print("Wrote:", AGENT2_CSV)
    print("Wrote:", META_JSON)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


