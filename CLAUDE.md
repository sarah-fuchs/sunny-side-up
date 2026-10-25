# sunny-side-up — notes for Claude

Personal web app for Sarah: **which Viennese Schanigärten (café/restaurant/bar terraces) are in direct sun at a given time**, using real building heights to cast shadows. Covers all 23 districts. Repo: `sarah-fuchs/sunny-side-up` (public). The app is called "Sunny Side Up" in the UI (renamed from "Sonnenseite").

## Working rules

- Use only Sarah's personal GitHub account `sarah-fuchs`. **Never access the Simacek GmbH organization.**
- No `Co-Authored-By` or other AI attribution lines in commits or PRs.

## What Sarah wants (decided)

- Personal tool first. Mobile is the main device.
- **Keep the custom canvas map rendering** (warm = sunlit ground, cool = shadow, buildings drawn from footprints). Don't replace it with a tile map.
- **Show venue names** on the map and in the list, with the address alongside. Terraces with no known name show just the address; never write "name unknown".
- **No weather / cloud forecast.**
- **One map for all of Vienna** (decided 2026-10-19, replaced the per-district view): pan/zoom freely; water in blue. The district picker is a "Jump to" shortcut that centres on the district's busiest café cluster. The list shows the terraces in view, sunniest first.

## Design (decided with Sarah)

- **Simple and calm, inspired by the drip app** (decided 2026-10-25, replaced the louder "sticker" look): flat white surfaces on a soft mint-grey page, no thick outlines or offset shadows, one accent colour (yolk orange #FFA51F). Name stays "Sunny Side Up"; the fried egg stays as the logo/mascot (Sarah and others find it cute).
- Header: "Sunny Side Up" (Bagel Fat One, the only display-font use) on the left; on the right the fried egg travelling along a dotted sun arc (sunrise → sunset, follows the time slider) with "26° high in the south-east" under it. Sarah likes the egg showing the sun's height. The "Districts" jump menu sits on its own row under the header.
- Time card: Now / Today / Tomorrow / calendar icon (transparent native date input on top; shows the date when another day is picked); thin slider with orange thumb; sunrise · big time · sunset under it.
- List: one white rounded panel with hairline rows: coloured dot (orange sun, mint shade, half/half partly), name + address, status on the right ("Sun until 14:40" in orange when sunny). Tapping a row opens its sun-timeline bar and details and centres the map.
- Map: dark grey buildings, solid lilac shadows (#B9B0EE, opaque — semi-transparent shade over yellow turned muddy brown), strong golden sunlit ground (#FFD84D), blue water. Terrace dots with a thin white ring; plain white name pills, selected one dark.
- Dark mode: neutral near-black (#18181A), not blue. Map: grey buildings, neutral dark shade (#2F2D33), golden mustard sun (#C9A531).
- Fonts: Figtree everywhere except the wordmark. No monospace, no all-caps labels. Never dark blue.
- The Flutter app (`sarah-fuchs/sunny-side-up-app`, private) follows this same design: free, no account, no ads, no tracking, like drip.

## Layout

- `pipeline/build.py` — stdlib-only Python. Fetches terraces, buildings, OSM names and streets; writes `web/data/districts/<NN>.json` and `web/data/index.json`. `--fixtures DIR` reads cached responses instead of the network.
- `pipeline/overrides.json` — hand-maintained address → name (wins over OSM).
- `web/index.html` — the app, plain HTML/JS, no build step. Loads `data/index.json` + `data/city.json` (all terraces + water, ~0.3 MB + water). Zoomed out (< 0.32 px/m) it shows only water, district names and terrace dots (coloured from already-computed sun data, else grey). Zoomed in, it loads the district files of the terraces in view (a terrace's own district file holds every building that can shade it), dedupes overlapping buildings, and computes sun for those terraces nearest-first. One global projection centred on 16.3725, 48.2083. `#7` jumps to a district; last view is kept in localStorage.
- `web/data/city.json` — written by `write_city()` at the end of every pipeline run (and by `--city-only`): every terrace with its district, water areas (OSM natural=water / waterway=riverbank, multipolygons joined, simplified 1.5 m, < 300 m² dropped) and river/canal centre lines. Also adds `bbox`/`center` per district to `index.json`.
- `.github/workflows/build.yml` — runs the pipeline (manual with optional district list, `missing`, or `city` for just the overview + water; monthly cron), commits whatever was built even if the run fails, deploys `web/` to Pages. The pipeline saves `index.json` after every district, pauses between requests, and stops early if the city server stops answering for two districts in a row (it did once, mid-run, on 2026-10-18).
- `.github/workflows/probe.yml` — manual check whether data.wien.gv.at and Overpass answer from a GitHub runner; results appear as annotations. Pages on a private repo needs a paid plan; otherwise use Cloudflare Pages/Netlify with output dir `web`.
- `tests/` — fixture generator + offline smoke test.
- `prototype/` — notes on the original Spittelberg prototype (files kept in the Claude project).

The Claude cloud sandbox cannot reach data.wien.gv.at or Overpass; GitHub runners can. Test pipeline changes with fixtures, run real builds via the Action.

## District file format

```json
{"d":7,"name":"Neubau","plz":1070,"center":[lon,lat],
 "terraces":[{"id":1,"addr":"Schrankgasse 2","name":"Plutzer Bräu","note":"…","kind":"Gehsteig","m2":84,"lon":…,"lat":…}],
 "buildings":[{"o":roof_elev,"t":ground_elev,"r":[[lon,lat],…]}],
 "streets":[{"n":"Burggasse","l":[[lon,lat],…]}]}
```

## Data sources (all open data)

### Stadt Wien WFS — `https://data.wien.gv.at/daten/geo`

Common params: `service=WFS&request=GetFeature&srsName=EPSG:4326&outputFormat=json`

**Schanigärten** — `ogdwien:SCHANIGARTENOGD`. Point geometry. `NAME` is the *type* ("Schanigarten Gehsteig", "… Parkspur", "… Fußgänger/Begegnungszone", "… Privatgrund"), `PLZ`, `STRASSE`, `ONR`, `FLAECHE` (m²), `GENEHMIGUNGSSTATUS`. One venue can have several rows → merged by street + number, areas summed. District = PLZ (1070 → 7).

**Buildings (Baukörpermodell, FMZK)** — `ogdwien:FMZKBKMOGD`, ~678,700 polygons. Height = `O_KOTE − T_KOTE`; `O_KOTE` null for passages → skipped. Outer ring of each polygon, parts under 2.5 m dropped. Paging: `count` + `startIndex` + `sortBy=FMZK_ID`.

**BBOX axis order (tested):** WFS 1.1.0 `bbox=minLon,minLat,maxLon,maxLat,EPSG:4326`; WFS 2.0.0 `bbox=minLat,minLon,maxLat,maxLon,urn:ogc:def:crs:EPSG::4326` (used by the pipeline). Mixing them returns 0 features silently.

### OpenStreetMap (Overpass)
- Venues: `amenity` cafe|restaurant|bar|pub|biergarten|ice_cream|fast_food with `name`, inside `area[name=Wien][admin_level=4]`. Match on normalized street + number, then base number ("18A" ↔ "18"). Several at one address → prefer `outdoor_seating=yes`, else join with " / ". Fallback: the single named venue within 12 m.
- Streets: named `highway` ways in each district's terrace bbox + 150 m.

## Sun & shadow model (browser)

- Local metric projection around the district centre: `x = (lon−lon0)·cos(lat0)·111320`, `y = (lat−lat0)·110540`.
- SunCalc formulas inlined; azimuth from north, clockwise.
- Terrace point inside a footprint → moved to nearest edge + 1.8 m outward. Ground = `T_KOTE` of nearest building; eye height 1.2 m.
- Samples: centre + 4 points at radius `min(4, max(1.5, √area/2))` m, minus those inside buildings. Sunny if ≥ 50 % lit.
- Shadow test: uniform 25 m grid of buildings; walk cells along the ray toward the sun (max 600 m), test edges of each building once. Day timeline in 10-min steps, computed in time slices so big districts stay responsive.
- Sun windows < 20 min starting within 30 min of sunset are dropped as artefacts.
- Map draws only buildings/shadows/terraces in view; list shows 40 rows, then "Show all".

## Ideas not done yet

- Precompute daily sun windows in the pipeline for reference dates if browser compute gets slow on phones.
- Trees, awnings, terrace orientation/extent aren't modelled.
- "Near me" / geolocation, favourites.
- Unload district buildings that are far from the view (memory grows as you roam; ~10k buildings per district).
