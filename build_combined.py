from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


REPO_DIR = Path(__file__).resolve().parent


def find_header_row(df: pd.DataFrame, needle: str) -> int:
    """Find the best header row containing `needle` in column 0."""
    mask = df.iloc[:, 0].astype(str).str.contains(needle, na=False)
    idxs = df.index[mask].tolist()
    if not idxs:
        raise ValueError(f"Could not find header row containing {needle!r}")

    # Prefer the row with most non-NA cells (works around multi-row header quirks)
    best_i = idxs[0]
    best_score = -1
    for i in idxs:
        score = df.iloc[i].notna().sum()
        if score > best_score:
            best_score = score
            best_i = i
    return int(best_i)


def prepare_live_or_marriages(xlsx_path: str, sheet_name: str, measure_name: str) -> pd.DataFrame:
    df = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=None)

    header_row_idx = find_header_row(df, "Länder")
    df.columns = df.iloc[header_row_idx].tolist()
    df = df.drop(index=df.index[: header_row_idx + 1]).reset_index(drop=True)

    df = df.rename(columns={df.columns[0]: "Land_Sex"})

    # Sex label tokens — English and German variants from Destatis Excel exports.
    SEX_TOKENS = ("Male", "Female", "Total", "männlich", "weiblich", "Insgesamt", "insgesamt")
    # Tokens that represent the "total" / aggregate row we want to keep.
    TOTAL_TOKENS = ("Total", "Insgesamt", "insgesamt")

    # Parse Land and Sex from the Land_Sex column.
    # In Destatis Excel exports the structure is:
    #   "Baden-Württemberg"  ← state name row = aggregate/total (no sex label in col 0)
    #   NaN                  ← male row
    #   NaN                  ← female row
    # So rows where col 0 is NOT NaN and NOT a sex token are the aggregate total rows.
    # If sex labels do appear (English/German), keep only the "Total/Insgesamt" labelled rows.
    land_names: list[str] = []
    sexes: list[str | None] = []
    is_total_row: list[bool] = []

    for val in df["Land_Sex"].tolist():
        if pd.isna(val):
            # NaN rows are Male/Female breakdowns — not the aggregate.
            land_names.append(land_names[-1])
            sexes.append(sexes[-1])
            is_total_row.append(False)
            continue

        sval = str(val).strip()
        if any(tok in sval for tok in SEX_TOKENS):
            # Explicit sex label row — only keep if it is a total label.
            land_names.append(land_names[-1])
            sexes.append(sval)
            is_total_row.append(any(tok in sval for tok in TOTAL_TOKENS))
        else:
            # State name row — this IS the aggregate total in Destatis format.
            land_names.append(sval)
            sexes.append(None)
            is_total_row.append(True)

    df["Land"] = land_names
    df["Sex"] = sexes
    df = df[is_total_row].copy()

    # Drop helper columns
    drop_cols = [c for c in ["Land_Sex", "Sex"] if c in df.columns]
    df = df.drop(columns=drop_cols)

    # Year columns are numeric years placed in value columns; other columns are NaN flag columns.
    cols = list(df.columns)
    year_col_indices: list[int] = []
    for i, c in enumerate(cols):
        if c == "Land":
            continue
        if pd.isna(c):
            continue
        if isinstance(c, (int, float, np.integer, np.floating)) and float(c).is_integer():
            year_col_indices.append(i)
        else:
            s = str(c).strip()
            if s.isdigit() and len(s) == 4:
                year_col_indices.append(i)

    if not year_col_indices:
        non_land_cols = [c for c in cols if c != "Land"]
        raise ValueError(
            f"No numeric year columns detected for {measure_name}. "
            f"First columns seen (excluding Land): {', '.join(map(repr, non_land_cols[:50]))}"
        )

    year_cols_original = [cols[i] for i in year_col_indices]
    df_years = df[["Land"] + year_cols_original].copy()

    # Rename headers to integer years
    rename_map: dict[object, int] = {}
    for old_col in year_cols_original:
        if isinstance(old_col, (int, float, np.integer, np.floating)) and float(old_col).is_integer():
            rename_map[old_col] = int(old_col)
        else:
            rename_map[old_col] = int(str(old_col).strip())
    df_years = df_years.rename(columns=rename_map)

    df_long = df_years.melt(id_vars=["Land"], var_name="Year", value_name=measure_name)
    df_long["Year"] = df_long["Year"].astype(int)
    df_long["Land"] = df_long["Land"].astype(str).str.strip()
    df_long = df_long.dropna(subset=[measure_name])

    return df_long


def prepare_population(xlsx_path: str, sheet_name: str) -> pd.DataFrame:
    df_raw = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=None)

    # Population sheet has a multi-row header. We want:
    # - land label row (contains "Länder")
    # - date header row (contains YYYY-12-31 columns, e.g. '2015-12-31')

    land_header_row = None
    for i in df_raw.index:
        row0 = df_raw.iloc[i].tolist()
        joined = " ".join(["" if pd.isna(x) else str(x) for x in row0])
        if "Länder" in joined:
            land_header_row = int(i)
            break
    if land_header_row is None:
        land_header_row = find_header_row(df_raw, "Länder")

    # Best date header row: row with most date-like strings in columns 1..end
    best_date_row = None
    best_count = -1
    for i in df_raw.index:
        date_like = 0
        for v in df_raw.iloc[i, 1:].tolist():
            if pd.isna(v):
                continue
            s = str(v).strip()
            if len(s) >= 10 and s[0].isdigit() and s[4] == "-":
                date_like += 1
        if date_like > best_count:
            best_count = date_like
            best_date_row = int(i)

    if best_date_row is None or best_count == 0:
        raise ValueError("Could not detect date headers in Population sheet.")

    # Build table body
    # After dropping up to land_header_row, rows become the land blocks.
    df_body = df_raw.drop(index=df_raw.index[: land_header_row + 1]).reset_index(drop=True)

    # Use the date header row values as the real column names for value columns.
    raw_date_values = df_raw.iloc[best_date_row].tolist()

    # Column 0 is the land column label. Determine land column name and then keep only date columns.
    # In your workbook, date headers appear at positions where raw_date_values[j] like '2015-12-31'.
    date_cols_by_pos: list[tuple[int, str]] = []
    for j, v in enumerate(raw_date_values):
        if j == 0:
            continue
        if pd.isna(v):
            continue
        s = str(v).strip()
        if len(s) >= 10 and s[0].isdigit() and s[4] == "-":
            date_cols_by_pos.append((j, s))

    if not date_cols_by_pos:
        raise ValueError("Could not map any date columns for Population table.")

    # Extract and rename those columns by position
    df_selected = df_body.iloc[:, [0] + [j for j, _ in date_cols_by_pos]].copy()
    df_selected = df_selected.rename(columns={df_selected.columns[0]: "Land"})

    rename_map = {df_selected.columns[k + 1]: date_str for k, (_, date_str) in enumerate(date_cols_by_pos)}
    df_selected = df_selected.rename(columns=rename_map)

    # Convert Date columns to Year
    df_long = df_selected.melt(id_vars=["Land"], var_name="Date", value_name="Population")
    df_long["Year"] = pd.to_datetime(df_long["Date"], errors="coerce").dt.year
    df_long = df_long.dropna(subset=["Year", "Population"]).copy()
    df_long["Year"] = df_long["Year"].astype(int)
    df_long["Land"] = df_long["Land"].astype(str).str.strip()

    # Drop any accidental header row remnants
    df_long = df_long[df_long["Land"].ne("Länder")]

    return df_long[["Land", "Year", "Population"]]


def main() -> int:
    live_birth_xlsx = str(REPO_DIR / "live_birth.xlsx")
    marriages_xlsx = str(REPO_DIR / "Marriages.xlsx")
    population_xlsx = str(REPO_DIR / "Population.xlsx")

    live_sheet = "12612-0100"
    mar_sheet = "12611-0010"
    pop_sheet = "12411-0010"

    df_live_long = prepare_live_or_marriages(live_birth_xlsx, live_sheet, measure_name="LiveBirths")
    df_mar_long = prepare_live_or_marriages(marriages_xlsx, mar_sheet, measure_name="Marriages")
    df_pop_long = prepare_population(population_xlsx, pop_sheet)

    combined = (
        df_live_long.merge(df_mar_long, on=["Land", "Year"], how="left")
        .merge(df_pop_long, on=["Land", "Year"], how="left")
        .sort_values(["Land", "Year"])
        .reset_index(drop=True)
    )

    out_path = REPO_DIR / "combined_births_population_marriages.csv"
    combined.to_csv(out_path, index=False)

    print("LiveBirths rows:", len(df_live_long))
    print("Marriages rows:", len(df_mar_long))
    print("Population rows:", len(df_pop_long))
    print("Combined rows:", len(combined))
    print("Missing Marriages:", int(combined["Marriages"].isna().sum()))
    print("Missing Population:", int(combined["Population"].isna().sum()))
    print("Wrote:", str(out_path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

