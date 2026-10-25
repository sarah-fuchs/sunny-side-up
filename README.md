# sunny-side-up

Which Viennese Schanigärten are in direct sun right now? A personal web app that casts shadows from real building heights (Stadt Wien Baukörpermodell) onto every approved café, restaurant and bar terrace, district by district.

## How it works

```
pipeline/build.py  ──►  web/data/index.json
  (Stadt Wien WFS,       web/data/districts/01.json … 23.json
   OpenStreetMap)        web/data/city.json, tiles/<x>_<y>.json
                                 │
                                 ▼
                       web/index.html  (plain HTML/JS, no build step)
```

- **Terraces**: approved Schanigärten from `ogdwien:SCHANIGARTENOGD`, merged per address (Gehsteig + Parkspur rows become one terrace).
- **Buildings**: `ogdwien:FMZKBKMOGD`, every building in Vienna in 1 km map tiles (`web/data/tiles/`), height = `O_KOTE − T_KOTE`, simplified to 0.3 m, stored in decimetres.
- **Names**: `pipeline/overrides.json` first, then OpenStreetMap venues matched by street + house number, then a single named venue within 12 m. Terraces with no name show their address.
- **Street labels and water**: named OSM street centrelines; water areas and river lines from OSM.
- **One map**: `web/data/city.json` holds every terrace plus water for the zoomed-out view; the map tiles around the view load as you zoom in.
- **Sun**: computed in the browser (SunCalc formulas), 10-minute steps, ≥ 50 % of five sample points lit counts as sunny.

## Running it

**Data** is built by the GitHub Action *Build data and deploy* (Actions tab → Run workflow; optionally list district numbers like `7 6 1`, type `missing` to build only districts that have no data yet, `city` to refresh just the overview and water, or `tiles` to fetch the map tiles with every building). It also runs monthly and commits updated files in `web/data/`.

Locally:

```bash
python pipeline/build.py 7        # one district (needs network access to data.wien.gv.at and overpass-api.de)
python -m http.server -d web 8000 # then open http://localhost:8000/#7
```

Offline test with fixture data:

```bash
python tests/make_fixtures.py && python pipeline/build.py --fixtures tests/fixtures 7
```

## Hosting

The workflow deploys `web/` to GitHub Pages. GitHub Pages for a **private** repository needs a paid GitHub plan; on a free account, connect the repo to Cloudflare Pages or Netlify instead (build command: none, output directory: `web`).

## Adding or fixing a venue name

Edit `pipeline/overrides.json` (`"Straße Nr": "Name"` or `{"name": …, "note": …}`) and re-run the workflow for that district.

## Limits

Trees, awnings and umbrellas aren't modelled. The city gives one point and an area per terrace, not its shape or orientation. Very short sun windows right before sunset are dropped as likely artefacts.
