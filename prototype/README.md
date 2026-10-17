# Prototype (Spittelberg only)

The first working version, built in a Claude chat with hand-collected data for one small area of 1070 Neubau.

- Live: https://claude.ai/artifact/NkmShVF4i41SUqtm5H1sYe
- Source files (`sonnenseite.html`, `template.html`, `buildings.json`, `raw.txt`, `build_data.py`) are kept in the Claude project "sunny-side-up". Only `build_data.py` is copied here.

`web/index.html` is this prototype refactored to load district data from `web/data/`, so the prototype is reference only. Its hand-transcribed building data is replaced entirely by `pipeline/build.py`.
