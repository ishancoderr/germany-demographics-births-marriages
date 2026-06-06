# Germany Demographics: Live Births, Marriages & Population

This repository combines three official **Destatis Genesis-Online** datasets into a single, analysis-ready file covering all 16 German federal states (Bundesländer) from 1990 to 2025.

## Data Sources

### Statistical data (Destatis Genesis-Online)

| Dataset | Table ID | Genesis Link |
|---------|----------|--------------|
| Live births | 12612-0100 | https://genesis.destatis.de/datenbank/online/statistic/12612/table/12612-0100 |
| Marriages | 12611-0010 | https://genesis.destatis.de/datenbank/online/statistic/12611/table/12611-0010 |
| Population | 12411-0010 | https://genesis.destatis.de/datenbank/online/statistic/12411/table/12411-0010 |

### Cities dataset

| File | Source | Coordinate System |
|------|--------|-------------------|
| `cities_dataset.csv` | [SimpleMaps World Cities Database](https://simplemaps.com/data/world-cities) | WGS84 (SRID 4326) — decimal degree latitude/longitude |

The SimpleMaps dataset provides city name, `lat` (latitude), `lng` (longitude), and `admin_name` (state/province).
All coordinates are in **WGS84 (EPSG:4326)**, the standard GPS coordinate reference system used by GeoJSON and PostGIS geographic queries.
City point geometries are stored in PostGIS as `GEOMETRY(POINT, 4326)` using `ST_SetSRID(ST_MakePoint(lng, lat), 4326)`.

## Files

### Pre-made data inputs (in this repo)
- `live_birth.xlsx` (sheet `12612-0100`)
- `Marriages.xlsx` (sheet `12611-0010`)
- `Population.xlsx` (sheet `12411-0010`)
- `germany_states.geojson`
- `cities_dataset.csv`
- `agent1_db.csv`
- `agent2_db.csv`

### Outputs
- `combined_births_population_marriages.csv`
- `agent1_postgis_db.sql`
- `agent2_postgis_db.sql`
- `split_dataset_agents.py` outputs:
  - `agent1_db.csv`
  - `agent2_db.csv`
  - `split_metadata.json`

## Setup

### 1) Install dependencies

```bash
pip install -r requirements.txt
```

### Notes
- `agent*_postgis_db.sql` generation relies on PostGIS being available in your target PostgreSQL database.
- Some city names in `cities_dataset.csv` may contain mojibake (encoding issues). The SQL generator escapes single quotes, but does not “fix” encoding beyond best-effort Land normalization.

## Build the combined CSV

Run:

```bash
python build_combined.py
```

Produces:
- `combined_births_population_marriages.csv`

## Split into two agent datasets

Run:

```bash
python split_dataset_agents.py
```

Produces:
- `agent1_db.csv`
- `agent2_db.csv`
- `split_metadata.json`

## Generate PostGIS init SQL for Agent 1 and Agent 2

Run:

```bash
python build_agent1_and_agent2_sql.py
```

Produces:
- `agent1_postgis_db.sql`
- `agent2_postgis_db.sql`

These SQL files create (idempotently):
- `states` (includes geometry from `germany_states.geojson`)
- `state_demographics` (population, live births, and marriages per state per year)
- `cities` (point geometries from `cities_dataset.csv`)

## Load SQL into PostgreSQL (example)

After generating the SQL, you can load it into a PostgreSQL database with PostGIS enabled.

Example (adjust connection parameters):

```bash
psql -d your_db_name -U your_user -f agent1_postgis_db.sql
psql -d your_db_name -U your_user -f agent2_postgis_db.sql
```

## Coordinate System: How It Works End-to-End

This section explains where the coordinates come from, how they are read, and how they end up stored in the database.

---

### What coordinate system is used?

All geometry in this project uses **WGS84 (EPSG:4326)** — the standard GPS coordinate system.
Coordinates are expressed as **decimal degree longitude and latitude**, for example:
- Berlin: longitude `13.38`, latitude `52.52`
- Hamburg: longitude `10.00`, latitude `53.55`

No reprojection is needed anywhere in this pipeline because all source files already use WGS84.

---

### Step 1 — Getting coordinates from source files

**From `germany_states.geojson` (state boundary polygons)**

GeoJSON is an open standard (RFC 7946) that always uses WGS84 by default.
The file stores each German state as a `MultiPolygon` with coordinates in `[longitude, latitude]` order.
There is no `"crs"` field in the file, which by the GeoJSON spec confirms WGS84.

Example of what the raw coordinate data looks like inside the file:
```
[8.708, 47.715]  →  longitude 8.708°E, latitude 47.715°N  (Baden-Württemberg border)
```

**From `cities_dataset.csv` (city locations)**

Downloaded from [SimpleMaps World Cities Database](https://simplemaps.com/data/world-cities).
The file has two plain numeric columns: `lat` (latitude) and `lng` (longitude), both in WGS84 decimal degrees.

Example rows:
```
city       lat      lng
Berlin     52.5167  13.3833
Munich     48.1375  11.5750
Hamburg    53.5753  10.0153
```

Confirmed WGS84 because:
- Latitude range for Germany: 47.4° to 54.9° (correct for WGS84)
- Longitude range for Germany: 5.9° to 14.9° (correct for WGS84)
- Values match known GPS positions for each city

---

### Step 2 — How `build_agent1_and_agent2_sql.py` reads and converts them

**State polygons (GeoJSON → WKT → PostGIS)**

The script reads the GeoJSON geometry and converts it to WKT (Well-Known Text) format,
preserving the original `[longitude, latitude]` coordinate order:

```
GeoJSON:  [[8.708, 47.715], [8.709, 47.713], ...]
WKT:      MULTIPOLYGON(((8.708 47.715, 8.709 47.713, ...)))
```

This WKT string is then wrapped in PostGIS functions in the SQL:
```sql
ST_SetSRID(ST_GeomFromText('MULTIPOLYGON((...))'), 4326)
```
- `ST_GeomFromText(...)` — parses the WKT text into a PostGIS geometry object
- `ST_SetSRID(..., 4326)` — labels the geometry with SRID 4326 (WGS84)

**City points (CSV lat/lng → PostGIS point)**

The script reads the `lat` and `lng` columns directly and builds a point in the SQL:
```sql
ST_SetSRID(ST_MakePoint(lng, lat), 4326)
```
- `ST_MakePoint(lng, lat)` — builds a Point geometry. Note: **longitude first, latitude second**
  because PostGIS uses X/Y order where X = longitude, Y = latitude
- `ST_SetSRID(..., 4326)` — labels the point with SRID 4326 (WGS84)

---

### Step 3 — How the database stores them

PostgreSQL/PostGIS stores all geometry internally as **EWKB (Extended Well-Known Binary)** —
a compact binary format with the SRID embedded inside it.

The column types enforce the CRS at the database level:

| Table | Column | Type | Meaning |
|-------|--------|------|---------|
| `states` | `geo_shape` | `GEOMETRY(MULTIPOLYGON, 4326)` | State boundary polygon, WGS84 |
| `cities` | `centroid` | `GEOMETRY(POINT, 4326)` | City location point, WGS84 |

PostgreSQL will **reject any insert** that provides a geometry with a different SRID than 4326.
This prevents accidentally mixing coordinate systems in the database.

The `cities` table also keeps the raw `lat` and `lng` columns as plain `DOUBLE PRECISION` numbers
so you can read the coordinates without using any PostGIS functions.

---

### Full pipeline summary

```
germany_states.geojson          cities_dataset.csv
  [lng, lat] coordinates          lat / lng columns
         |                               |
   Read as WKT text              Read as float numbers
         |                               |
  ST_GeomFromText(wkt)          ST_MakePoint(lng, lat)
         |                               |
   ST_SetSRID(..., 4326)          ST_SetSRID(..., 4326)
         |                               |
GEOMETRY(MULTIPOLYGON, 4326)    GEOMETRY(POINT, 4326)
    states.geo_shape               cities.centroid
         |                               |
         +------------- Both in WGS84 ---+
              stored as EWKB binary in PostgreSQL
```

---

## Quick sanity checks

- Regenerate SQL any time you change input CSV/GeoJSON:
  ```bash
  python build_agent1_and_agent2_sql.py
  ```
- If your database complains about encoding, ensure your PostgreSQL client/server encoding is set appropriately (UTF-8 recommended).
- To verify geometries loaded correctly in PostgreSQL:
  ```sql
  SELECT state_name, ST_SRID(geo_shape), ST_GeometryType(geo_shape) FROM states LIMIT 5;
  SELECT city_name, ST_SRID(centroid), ST_AsText(centroid) FROM cities LIMIT 5;
  ```

