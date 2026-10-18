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
- District picker; everything else in the UI stays as in the prototype.

## Design (decided with Sarah)

- Fried-egg theme ("sunny side up"): the sun is a fried egg on the header sun-path arc; map markers are plain circles: yolk orange when sunny, mint when shaded, mint with a yolk pie when partly sunny (egg-shaped markers were tried and dropped).
- Map: dark grey buildings, soft lilac shadows (#5B4FD6 at 34 %), cream sunlit ground (#FFF9E6) — chosen over mint/pink/green variants because buildings and shadows read clearly, like the first prototype.
- Colours: mint page; bright mint / pink / yellow / yolk orange (#FFA51F) only on markers, buttons and tags. Neutral charcoal outlines (#2B2B2B), never dark blue.
- Dark mode: neutral near-black (#18181A), not blue. Map: grey buildings, near-black shadows, warm olive sunlit ground.
- Fonts: Bagel Fat One (display), Figtree (everything else). No monospace, no all-caps labels.
- Sticker look: thick outlines with flat offset shadows on map, controls and selected terrace.

## Layout

- `pipeline/build.py` — stdlib-only Python. Fetches terraces, buildings, OSM names and streets; writes `web/data/districts/<NN>.json` and `web/data/index.json`. `--fixtures DIR` reads cached responses instead of the network.
- `pipeline/overrides.json` — hand-maintained address → name (wins over OSM).
- `web/index.html` — the app, plain HTML/JS, no build step. Loads `data/index.json`, then a district file. District in URL hash (`#7`) and localStorage.
- `.github/workflows/build.yml` — runs the pipeline (manual with optional district list, monthly cron), commits data, deploys `web/` to Pages. Pages on a private repo needs a paid plan; otherwise use Cloudflare Pages/Netlify with output dir `web`.
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
