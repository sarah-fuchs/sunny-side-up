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
- Header (Sarah wants the playful one from the first design, restored 2026-10-25): big "Sunny Side Up" wordmark with a pink offset shadow, subtitle "Schanigärten in the sun, <PLZ> <district>", the fried egg travelling along a dotted sun arc with sunrise/sunset times and "29° high in the south", and a yellow outlined "District" pill (shows the district you're looking at; picking one jumps there). Only the header keeps the bold sticker style; the rest stays calm.
- Time card: Now / Today / Tomorrow / calendar icon (transparent native date input on top; shows the date when another day is picked); thin slider with orange thumb; sunrise · big time · sunset under it.
- List: one white rounded panel with hairline rows: coloured dot (orange sun, mint shade, half/half partly), name + address, status on the right ("Sun until 14:40" in orange when sunny). Tapping a row opens its sun-timeline bar and details and centres the map.
- Map: dark grey buildings, solid lilac shadows (#B9B0EE, opaque — semi-transparent shade over yellow turned muddy brown), strong golden sunlit ground (#FFD84D), blue water. Terrace dots with a thin white ring; plain white name pills, selected one dark.
- Dark mode: neutral near-black (#18181A), not blue. Map: grey buildings, neutral dark shade (#2F2D33), golden mustard sun (#C9A531).
- Fonts: Figtree everywhere except the wordmark. No monospace, no all-caps labels. Never dark blue.
- The Flutter app (`sarah-fuchs/sunny-side-up-app`, private) follows this same design: free, no account, no ads, no tracking, like drip.

## Layout

- `pipeline/build.py` — stdlib-only Python. Fetches terraces, buildings, OSM names and streets; writes `web/data/districts/<NN>.json` and `web/data/index.json`. `--tiles` writes the map tiles (below). `--fixtures DIR` reads cached responses instead of the network.
- `pipeline/overrides.json` — hand-maintained address → name (wins over OSM).
- `web/index.html` — the app, plain HTML/JS, no build step. Loads `data/index.json` + `data/city.json` (all terraces + water, ~0.3 MB + water). Zoomed out (< 0.32 px/m) it shows only water, district names and terrace dots (coloured from already-computed sun data, else grey). Zoomed in, it loads the map tiles within 400 m of the view (keeps at most 48, dropping the furthest), and computes sun nearest-first for terraces whose tiles within 400 m are all loaded. It no longer reads the district files (the Flutter app doesn't either). One global projection centred on 16.3725, 48.2083. `#7` jumps to a district; last view is kept in localStorage.
- `web/data/tiles/<ix>_<iy>.json` + `web/data/tiles.json` — **every building in Vienna** (decided 2026-10-25: buildings only near terraces left big empty "sunny" gaps), in 1 km squares of the global projection, written by `--tiles` (carries on where a stopped run left off; `--tiles-all` redoes all). A building part belongs to the tile holding the centre of its bounding box. Format: `{"x":ix,"y":iy,"b":[[roof_dm,ground_dm,x0,y0,dx1,dy1,…]],"s":[{"i":osm_id,"n":name,"l":[x0,y0,dx1,dy1,…]}]}`: decimetres from the tile's south-west corner, later points as steps, rings counter-clockwise. `tiles.json`: `{"size":1000,"tiles":{"ix_iy":buildings},"empty":[…]}`. Streets come from one Overpass query for the whole city. Not part of the monthly cron (buildings rarely change); run the workflow with `tiles-all` now and then.
- **Trees** (decided with Sarah 2026-10-25; next in line: green areas, then hills, one at a time): `--trees` puts the Stadt Wien tree register (`ogdwien:BAUMKATOGD`, ~233k trees, CC BY 4.0) into the tiles as `"t":[x_dm,y_dm,code,…]`, code = height class·10 + crown class (+100 evergreen by genus). Fetched tile by tile with a bbox query (paging one big sorted list failed twice: the city server times out on deep pages); progress in `tiles.json` (`treesDone`), so `--trees` carries on where it stopped, `--trees-all` redoes all. Run after `--tiles`; `--tiles` keeps a tile's trees when it rewrites it. Classes → metres: height 4/8/13/18/23/28/33 m, crown 2.5/5/8/11/14/17/20/23 m.
- `web/data/city.json` — written by `write_city()` at the end of every pipeline run (and by `--city-only`): every terrace with its district, water areas (OSM natural=water / waterway=riverbank, multipolygons joined, simplified 1.5 m, < 300 m² dropped) and river/canal centre lines. Also adds `bbox`/`center` per district to `index.json`.
- `.github/workflows/build.yml` — runs the pipeline (manual with optional district list, `missing`, `city` for just the overview + water, `tiles` / `tiles-all` for the map tiles, `trees` for the tree register; monthly cron), commits whatever was built even if the run fails, deploys `web/` to Pages. The pipeline saves `index.json` after every district, pauses between requests, and stops early if the city server stops answering for two districts in a row (it did once, mid-run, on 2026-10-18).
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
- Streets: named `highway` ways in each district's terrace bbox + 150 m (district files); for tiles, all named streets in Vienna in one query.

## Sun & shadow model (browser)

- Local metric projection around the district centre: `x = (lon−lon0)·cos(lat0)·111320`, `y = (lat−lat0)·110540`.
- SunCalc formulas inlined; azimuth from north, clockwise.
- Terrace point inside a footprint → moved to nearest edge + 1.8 m outward. Ground = `T_KOTE` of nearest building; eye height 1.2 m.
- Samples: centre + 4 points at radius `min(4, max(1.5, √area/2))` m, minus those inside buildings. Sunny if ≥ 50 % lit.
- Shadow test: uniform 25 m grid of buildings; walk cells along the ray toward the sun (max 400 m, same as the tile radius; drawn shadows are capped at 400 m too), test edges of each building once. Day timeline in 10-min steps, computed in time slices so big districts stay responsive.
- Trees: crown radius r from the crown class; lower crown edge at max(2 m, 35 % of height) (evergreens max(1 m, 15 %)); shape = cylinder up to the crown's middle plus a half-ellipsoid dome (flat underneath, so standing under a tree with high sun is shade). Ground = nearest building's `T_KOTE`. Deciduous trees only block from 10 April to 15 November; in winter they're drawn as rings. A round tree button under the compass (zoomed in only) hides trees and also takes them out of the sun calculation (so the list never shows shade without a visible cause); the choice is kept in localStorage (`trees`). Map: sage crowns (#7FAE8A / dark #3F6B4D); shadow = ellipse for the dome + a round-capped band for the flat underside, same lilac as building shade.
- Sun windows < 20 min starting within 30 min of sunset are dropped as artefacts.
- Map draws only buildings/shadows/terraces in view; list shows 40 rows, then "Show all".

## Ideas not done yet

- Precompute daily sun windows in the pipeline for reference dates if browser compute gets slow on phones.
- Awnings, terrace orientation/extent aren't modelled. Green areas (parks) and terrain horizon (Wienerwald) are planned next.
- The Flutter app doesn't know trees yet (ignores the tiles' `t`).
- "Near me" / geolocation, favourites.
- District files still carry their buildings (within 150 m of terraces); nothing reads them any more, so they could be slimmed down to terraces + streets.
