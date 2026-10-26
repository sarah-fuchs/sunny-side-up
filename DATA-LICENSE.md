# Data licences

The files in `web/data/` are built from two open data sources:

- **Stadt Wien** — building heights (Baukörpermodell, `ogdwien:FMZKBKMOGD`) and approved Schanigärten (`ogdwien:SCHANIGARTENOGD`).
  Datenquelle: Stadt Wien – data.wien.gv.at, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  Modified: rows merged per address, footprints simplified, coordinates rounded, map tiles cut.
- **OpenStreetMap** — venue names, street names and lines, water and green areas.
  © OpenStreetMap contributors, available under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
  The parts of `web/data/` derived from OpenStreetMap are likewise available under the ODbL.

The code in this repository is separate from the data.
