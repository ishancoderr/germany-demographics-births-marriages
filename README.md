# Germany Demographics: Live Births, Marriages & Population

This repository combines three official **Destatis Genesis-Online** datasets into a single, analysis‑ready file covering all 16 German federal states (Bundesländer) from 1990 to 2025.

## Data Sources

| Dataset | Table ID | Genesis Link |
|---------|----------|---------------|
| Live births | 12612-0100 | [View Source](https://genesis.destatis.de/datenbank/online/statistic/12612/table/12612-0100) |
| Marriages | 12611-0010 | [View Source](https://genesis.destatis.de/datenbank/online/statistic/12611/table/12611-0010) |
| Population | 12411-0010 | [View Source](https://genesis.destatis.de/datenbank/online/statistic/12411/table/12411-0010) |



## Building the datasets

### 1) Create the combined CSV (all data)

The script `build_combined.py` reads the Excel exports in this repo:
- `live_birth.xlsx` (sheet `12612-0100`)
- `Marriages.xlsx` (sheet `12611-0010`)
- `Population.xlsx` (sheet `12411-0010`)

and writes:
- `combined_births_population_marriages.csv`

Run:

```bash
python build_combined.py
```

### 2) Split into two agent databases

The script `split_dataset_agents.py` reads `combined_births_population_marriages.csv` and produces:
- `agent1_db.csv`
- `agent2_db.csv`
- `split_metadata.json`

It uses a hard-coded Länder split:
- **Agent 1**: Brandenburg, Bremen, Hamburg, Hessen, Mecklenburg-Vorpommern, Niedersachsen, Nordrhein-Westfalen, Rheinland-Pfalz, Saarland, Sachsen, Sachsen-Anhalt, Schleswig-Holstein, Thüringen
- **Agent 2**: Baden-Württemberg, Bayern, Berlin, Brandenburg, Bremen, Hamburg, Hessen, Mecklenburg-Vorpommern, Niedersachsen, Nordrhein-Westfalen, Rheinland-Pfalz

It also applies deterministic attribute-level missingness patterns (with a fixed seed) and stores the Länder sets + NA counts in `split_metadata.json`.

Run:

```bash
python split_dataset_agents.py
```






