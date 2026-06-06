from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


REPO_DIR = Path(__file__).resolve().parent

GEOJSON_PATH = REPO_DIR / "germany_states.geojson"
AGENT1_CSV_PATH = REPO_DIR / "agent1_db.csv"
AGENT2_CSV_PATH = REPO_DIR / "agent2_db.csv"
CITIES_CSV_PATH = REPO_DIR / "cities_dataset.csv"

OUT_SQL_PATH_1 = REPO_DIR / "agent1_postgis_db.sql"
OUT_SQL_PATH_2 = REPO_DIR / "agent2_postgis_db.sql"


def _fix_mojibake(s: str) -> str:
    s = str(s)
    if "Ã" not in s and "Â" not in s:
        return s
    try:
        return s.encode("latin-1", errors="strict").decode("utf-8", errors="strict")
    except Exception:
        return s


def _normalize_state_name(s: str) -> str:
    s = _fix_mojibake(str(s)).strip()

    s = s.replace("Lower Saxony", "Niedersachsen")
    s = s.replace("Bavaria", "Bayern")
    s = s.replace("Hesse", "Hessen")
    s = s.replace("Hessenn", "Hessen")
    s = s.replace("Baden-WÃ¼rttemberg", "Baden-Württemberg")
    s = s.replace("Baden-Wuerttemberg", "Baden-Württemberg")
    s = (
        s.replace("Ã¼", "ü")
         .replace("Ãœ", "Ü")
         .replace("Ã¶", "ö")
         .replace("Ã–", "Ö")
         .replace("Ã¤", "ä")
         .replace("Ã„", "Ä")
         .replace("ÃŸ", "ß")
    )
    s = s.replace("Saxony-Anhalt", "Sachsen-Anhalt")
    s = s.replace("Saxony", "Sachsen")
    s = s.replace("Mecklenburg-Western Pomerania", "Mecklenburg-Vorpommern")
    s = s.replace("Rhineland-Palatinate", "Rheinland-Pfalz")
    s = s.replace("North Rhine-Westphalia", "Nordrhein-Westfalen")
    s = s.replace("Thuringia", "Thüringen")
    return s


def _read_geojson_states(geojson_path: Path) -> dict[str, dict]:
    gj = json.loads(geojson_path.read_text(encoding="utf-8"))
    mapping: dict[str, dict] = {}
    for feat in gj.get("features", []):
        props = feat.get("properties", {}) or {}
        name = props.get("name") or props.get("Land") or props.get("land_name")
        if not name:
            continue
        name = _normalize_state_name(name)
        mapping[name] = {
            "feature": feat,
            "geometry": feat.get("geometry"),
        }
    return mapping


def _state_name_to_id() -> dict[str, int]:
    return {
        "Baden-Württemberg": 1,
        "Bayern": 2,
        "Berlin": 3,
        "Brandenburg": 4,
        "Bremen": 5,
        "Hamburg": 6,
        "Hessen": 7,
        "Mecklenburg-Vorpommern": 8,
        "Niedersachsen": 9,
        "Nordrhein-Westfalen": 10,
        "Rheinland-Pfalz": 11,
        "Saarland": 12,
        "Sachsen": 13,
        "Sachsen-Anhalt": 14,
        "Schleswig-Holstein": 15,
        "Thüringen": 16,
    }


def _ring_to_wkt_coords(coords) -> str:
    out_parts: list[str] = []
    for pt in coords:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            x, y = pt[0], pt[1]
        else:
            raise ValueError(f"Invalid coordinate point: {pt!r}")
        out_parts.append(f"{float(x)} {float(y)}")
    return ",".join(out_parts)


def _geom_to_multipolygon_wkt(geometry: dict) -> str:
    """Convert GeoJSON geometry (MultiPolygon or Polygon) to WKT. Source CRS is WGS84 (SRID 4326)."""
    if geometry is None:
        return "NULL"
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return "NULL"

    if gtype == "MultiPolygon":
        polys = []
        for poly_rings in coords:
            rings_wkt = [f"({_ring_to_wkt_coords(ring)})" for ring in poly_rings]
            polys.append(f"({','.join(rings_wkt)})")
        return f"MULTIPOLYGON({','.join(polys)})"

    if gtype == "Polygon":
        rings_wkt = [f"({_ring_to_wkt_coords(ring)})" for ring in coords]
        return f"MULTIPOLYGON(({','.join(rings_wkt)}))"

    return "NULL"


def _escape_sql_string(s: str) -> str:
    return str(s).replace("'", "''")


def _to_int_or_null_sql(x) -> str:
    """Return SQL literal: integer string or NULL."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "NULL"
    s = str(x).strip().replace(",", "")
    if s == "" or s.lower() == "nan":
        return "NULL"
    try:
        return str(int(float(s)))
    except ValueError:
        return "NULL"


def build_sql_for_agent(
    *,
    agent_csv_path: Path,
    out_sql_path: Path,
    states: dict[str, dict],
    state_name_to_id: dict[str, int],
    cities_df_all: pd.DataFrame,
) -> None:
    if not agent_csv_path.exists():
        raise FileNotFoundError(f"Missing {agent_csv_path}")

    df = pd.read_csv(agent_csv_path)

    required_cols = {"Land", "Year", "LiveBirths", "Marriages", "Population"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(
            f"{agent_csv_path.name} is missing columns {sorted(missing)}. "
            f"Found: {sorted(df.columns)}"
        )

    df["Land"] = df["Land"].astype(str).map(_normalize_state_name)
    df["state_id"] = df["Land"].map(state_name_to_id)
    if df["state_id"].isna().any():
        bad = df.loc[df["state_id"].isna(), "Land"].unique().tolist()
        raise ValueError(f"{agent_csv_path.name} has unknown Land values: {bad}")

    df["stat_year"] = df["Year"].astype(int)

    lines: list[str] = []
    lines.append("-- Auto-generated PostGIS init script")
    lines.append(f"-- Source: {agent_csv_path.name}")
    lines.append("-- Coordinate system: WGS84 (SRID 4326) for all geometries")
    lines.append("")

    # ------------------------------------------------------------------ #
    # Step 1: Enable PostGIS
    # ------------------------------------------------------------------ #
    lines.append("-- Step 1: Enable PostGIS extension")
    lines.append("CREATE EXTENSION IF NOT EXISTS postgis;")
    lines.append("")

    # ------------------------------------------------------------------ #
    # Step 2: states table — ALL 16 German states always inserted.
    # Spatial queries (ST_Touches, ST_DWithin, ST_Azimuth) require every
    # state's geometry to be present even if state_demographics has no rows for it.
    # ------------------------------------------------------------------ #
    lines.append("-- Step 2: All 16 German federal states with WGS84 polygon geometry")
    lines.append("-- NOTE: all states are always present for spatial relationship queries.")
    lines.append("CREATE TABLE IF NOT EXISTS states (")
    lines.append("  state_id    INT PRIMARY KEY,")
    lines.append("  state_name  VARCHAR(100) NOT NULL,")
    lines.append("  geo_shape   GEOMETRY(MULTIPOLYGON, 4326) DEFAULT NULL")
    lines.append(");")
    lines.append("")

    state_insert_rows: list[str] = []
    for sname, sid in sorted(state_name_to_id.items(), key=lambda x: x[1]):
        geom = states.get(sname, {}).get("geometry")
        wkt = _geom_to_multipolygon_wkt(geom)
        if wkt == "NULL":
            state_insert_rows.append(
                f"  ({sid}, '{_escape_sql_string(sname)}', NULL)"
            )
        else:
            wkt_escaped = wkt.replace("'", "''")
            state_insert_rows.append(
                f"  ({sid}, '{_escape_sql_string(sname)}', "
                f"ST_SetSRID(ST_GeomFromText('{wkt_escaped}'), 4326))"
            )

    if state_insert_rows:
        lines.append("INSERT INTO states (state_id, state_name, geo_shape) VALUES")
        lines.append(",\n".join(state_insert_rows))
        lines.append("ON CONFLICT (state_id) DO NOTHING;")
    lines.append("")

    # state_demographics rows only for states/years present in this agent's CSV
    state_ids_present = sorted(df["state_id"].dropna().unique().tolist())
    present_state_names = [k for k, v in state_name_to_id.items() if v in set(state_ids_present)]

    # ------------------------------------------------------------------ #
    # Step 3: state_demographics table
    # Columns: state_id, stat_year, population, live_births, marriages
    # ------------------------------------------------------------------ #
    lines.append("-- Step 3: State demographics (population, live births, marriages per state per year)")
    lines.append("CREATE TABLE IF NOT EXISTS state_demographics (")
    lines.append("  stat_id     SERIAL PRIMARY KEY,")
    lines.append("  state_id    INT NOT NULL,")
    lines.append("  stat_year   INT NOT NULL,")
    lines.append("  population  BIGINT DEFAULT NULL,")
    lines.append("  live_births BIGINT DEFAULT NULL,")
    lines.append("  marriages   BIGINT DEFAULT NULL,")
    lines.append("  CONSTRAINT fk_state_demographics_state")
    lines.append("    FOREIGN KEY (state_id) REFERENCES states (state_id) ON DELETE CASCADE")
    lines.append(");")
    lines.append("")
    lines.append(
        "CREATE UNIQUE INDEX IF NOT EXISTS uix_state_demographics_state_year "
        "ON state_demographics (state_id, stat_year);"
    )
    lines.append("")

    insert_rows: list[str] = []
    df_sorted = (
        df[["state_id", "stat_year", "Population", "LiveBirths", "Marriages"]]
        .copy()
        .sort_values(["state_id", "stat_year"])
        .reset_index(drop=True)
    )

    for _, r in df_sorted.iterrows():
        sid = int(r["state_id"])
        year_val = int(r["stat_year"])
        pop = _to_int_or_null_sql(r["Population"])
        births = _to_int_or_null_sql(r["LiveBirths"])
        marriages = _to_int_or_null_sql(r["Marriages"])
        insert_rows.append(
            f"  ({sid}, {year_val}, {pop}, {births}, {marriages})"
        )

    chunk_size = 500
    for i in range(0, len(insert_rows), chunk_size):
        chunk = insert_rows[i : i + chunk_size]
        lines.append(
            "INSERT INTO state_demographics (state_id, stat_year, population, live_births, marriages) VALUES"
        )
        lines.append(",\n".join(chunk))
        lines.append("ON CONFLICT (state_id, stat_year) DO NOTHING;")
        lines.append("")

    # ------------------------------------------------------------------ #
    # Step 4: cities table
    # Geometry column typed GEOMETRY(POINT, 4326) to enforce CRS.
    # ------------------------------------------------------------------ #
    lines.append("-- Step 4: Cities (point geometry in WGS84 SRID 4326)")
    lines.append("CREATE TABLE IF NOT EXISTS cities (")
    lines.append("  city_id    INT PRIMARY KEY,")
    lines.append("  city_name  VARCHAR(150) NOT NULL,")
    lines.append("  state_id   INT NOT NULL REFERENCES states (state_id) ON DELETE CASCADE,")
    lines.append("  lat        DOUBLE PRECISION NOT NULL,")
    lines.append("  lng        DOUBLE PRECISION NOT NULL,")
    lines.append("  centroid   GEOMETRY(POINT, 4326) DEFAULT NULL")
    lines.append(");")
    lines.append("")
    lines.append(
        "CREATE INDEX IF NOT EXISTS idx_cities_centroid ON cities USING GIST (centroid);"
    )
    lines.append("")

    cities_df = cities_df_all.copy()
    cities_df["admin_norm"] = cities_df["admin_name"].astype(str).map(_normalize_state_name)

    # Validate all admin_name values map to known states
    unknown = sorted(
        cities_df.loc[~cities_df["admin_norm"].isin(state_name_to_id.keys()), "admin_name"]
        .unique()
        .tolist()
    )
    if unknown:
        raise ValueError(
            f"cities_dataset.csv has admin_name values that cannot be normalized: {unknown}"
        )

    # Keep cities from ALL 16 states regardless of this agent's state_demographics partition.
    # Spatial queries (e.g. ST_DWithin from Munich) require city points for every
    # state to be present in both agents' databases.
    cities_df = cities_df[cities_df["admin_norm"].isin(state_name_to_id.keys())].copy()
    cities_df = cities_df.reset_index(drop=True)
    cities_df["city_id"] = (cities_df.index + 1).astype(int)

    cities_df["lat"] = pd.to_numeric(cities_df["lat"], errors="coerce")
    cities_df["lng"] = pd.to_numeric(cities_df["lng"], errors="coerce")
    bad_coords = cities_df["lat"].isna() | cities_df["lng"].isna()
    if bad_coords.any():
        raise ValueError(
            f"Invalid lat/lng in cities_dataset.csv:\n"
            f"{cities_df.loc[bad_coords, ['city', 'lat', 'lng']].head(20)}"
        )

    city_insert_rows: list[str] = []
    for _, r in cities_df.iterrows():
        city_id = int(r["city_id"])
        city_name = str(r["city"]).strip().replace("﻿", "")
        sid = int(state_name_to_id[r["admin_norm"]])
        lat = float(r["lat"])
        lng = float(r["lng"])
        city_insert_rows.append(
            f"  ({city_id}, '{_escape_sql_string(city_name)}', {sid}, "
            f"{lat}, {lng}, ST_SetSRID(ST_MakePoint({lng}, {lat}), 4326))"
        )

    for i in range(0, len(city_insert_rows), chunk_size):
        chunk = city_insert_rows[i : i + chunk_size]
        lines.append(
            "INSERT INTO cities (city_id, city_name, state_id, lat, lng, centroid) VALUES"
        )
        lines.append(",\n".join(chunk))
        lines.append("ON CONFLICT (city_id) DO NOTHING;")
        lines.append("")

    out_sql_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote: {out_sql_path}  ({len(lines)} lines)")


def main() -> int:
    for p in [GEOJSON_PATH, CITIES_CSV_PATH, AGENT1_CSV_PATH, AGENT2_CSV_PATH]:
        if not p.exists():
            raise FileNotFoundError(f"Missing required file: {p}")

    state_name_to_id = _state_name_to_id()
    states = _read_geojson_states(GEOJSON_PATH)

    cities_df = pd.read_csv(CITIES_CSV_PATH)
    expected_city_cols = {"city", "lat", "lng", "admin_name"}
    missing_city_cols = expected_city_cols - set(cities_df.columns)
    if missing_city_cols:
        raise ValueError(
            f"cities_dataset.csv is missing columns {sorted(missing_city_cols)}. "
            f"Found: {sorted(cities_df.columns)}"
        )

    for agent_csv, out_sql in [(AGENT1_CSV_PATH, OUT_SQL_PATH_1), (AGENT2_CSV_PATH, OUT_SQL_PATH_2)]:
        build_sql_for_agent(
            agent_csv_path=agent_csv,
            out_sql_path=out_sql,
            states=states,
            state_name_to_id=state_name_to_id,
            cities_df_all=cities_df,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
