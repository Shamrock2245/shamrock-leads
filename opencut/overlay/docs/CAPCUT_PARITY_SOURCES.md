# Open-source methods to compete with CapCut (license-clean)

Use this as the standing research prompt for any agent expanding the Shamrock OpenCut library.

```
You are researching license-clean media and effect sources so Shamrock’s
self-hosted OpenCut (opencut-classic at edit.shamrockbailbonds.biz) can
compete with out-of-the-box CapCut.

Constraints:
- Do NOT scrape, reverse, or copy CapCut, TikTok, or any proprietary pack.
- Prefer CC0 / public domain. CC-BY only if attribution can be stored and shown.
- Exclude CC-BY-NC from any pack used in Shamrock ads.
- frei0r is GPL: document algorithms only; do not vendor GPL binaries into MIT OpenCut.
- gl-transitions (MIT) is the preferred transition library.
- Freesound is already integrated via /api/sounds/search — extend, don’t replace.
- Effects must fit OpenCut’s EffectDefinition + WGSL/WebGL pass model
  (see apps/web/src/effects/definitions/blur.ts and
   apps/web/src/services/renderer/webgl-effects.ts).
- Output must be production-usable for a Florida bail-bond brand
  (whooshes, hits, captions, jail/court SFX we generate ourselves).

Deliver a table with columns:
Source | URL | License | Asset type (sfx/music/lut/shader/lottie/sticker) |
Approx count | Commercial OK? | Redistribute-in-editor OK? |
Ingest method (API / git submodule / one-time download) |
Fit to OpenCut (bundled file vs live search vs WGSL/WebGL port) |
Risk notes

Then produce:
1. A recommended “Shamrock Starter Pack” of ~400 SFX, ~30 music beds,
   ~15 shaders, ~70 gl-transitions, ~20 LUTs, ~20 text presets.
2. Exact GitHub paths / API endpoints to pull from.
3. A folder layout and ATTRIBUTION.json schema.
4. A “do not use” list (CapCut, Epidemic, Artlist, unpaid Epidemic-style
   libraries, NC licenses, GPL-linked plugins).
5. Implementation order that ships audible whooshes first, then
   transitions, then filters.

Search: Freesound, Mixkit, Pixabay Content License, Kenney, Sonniss CC0,
OpenGameArt, ccMixter, FMA, Incompetech, gl-transitions, frei0r (as spec),
FFmpeg filters, AMPAS ACES LUTs, Lottie CC0 sets, OpenClipart.
```

## Already wired in this tree

| Surface | Path |
|---|---|
| Bundled SFX catalog | `apps/web/src/media/bundled-catalog.ts` + `/api/media/catalog` |
| Freesound search | `/api/sounds/search` |
| WebGL extra effects | `apps/web/src/services/renderer/webgl-effects.ts` |
| Effect definitions | `apps/web/src/effects/definitions/` |
| Attribution ledger | `assets/ATTRIBUTION.json` |
