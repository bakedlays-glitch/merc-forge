# sidecar/data/placement

Sidecar-owned placement data that supplements sitekit's own `data/` tables
(`Headless_Compiler/sitekit/data/t{tileset}_categories.json` etc., read
directly from Headless_Compiler — never copied here). `routes/mapforge_placement.py`
looks for `t{tileset}_fences.json` here to fold a fence-role table into the
`GET /placement/tables` response; `t72_fences.json` arrives in Phase 4 of the
MapForge StarCraft-placement work. Until then `"fences"` in that response is
an empty object.
