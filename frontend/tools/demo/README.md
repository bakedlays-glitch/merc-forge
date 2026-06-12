# MapForge demo runner

Scripted demos for recording YouTube feature videos. A written **agenda**
is played against the real MapForge UI in its own Chromium window —
smooth human-paced mouse glides, eased camera pans, big on-screen
captions, deliberate pauses for narration — while you record the window
with OBS. UI changed? Edit the agenda, re-run, re-record.

## Run (one command)

```powershell
cd "C:\AI Projects\The Wasteland\MercWizard2\frontend\tools\demo"
node start_demo_rig.mjs agenda_building_library.mjs
```

That starts the sidecar (port 8773, no token) + Vite (port 1420), waits
for both to be healthy, opens a headed 1920×1080 Chromium and plays the
agenda. Anything already running on those ports is reused (and left
running afterwards); whatever the rig spawned is killed when it exits,
Ctrl+C included.

Runner-only (servers already up):

```powershell
node runner.mjs agenda_building_library.mjs
```

Extra flags (both commands accept them after the agenda):

| Flag | Effect |
| --- | --- |
| `--record out.webm` | Also save Playwright's own video of the run (self-check / bonus output — your real recording is OBS over the window) |
| `--headless` | No visible window (verification runs) |
| `--shots <dir>` | Where the `shot` verb saves screenshots (default `scratch/demo_frames/`) |
| `--base <url>` | Editor origin if not `http://localhost:1420` |

## Recording with OBS

1. Start the rig. The agenda begins with `countdown(3)` — a big 3‑2‑1
   caption — so you have a sync point.
2. In OBS add a **Window Capture** of the Chromium window (or a Display
   Capture region). The browser viewport is exactly the agenda's
   `viewport` (default 1920×1080); the OS window is slightly taller for
   the browser chrome — crop to the page in OBS.
3. Hit OBS record, then watch the countdown; trim to the end of the
   countdown in the edit.
4. The runner exits when the agenda finishes — stop OBS.

Re-takes are free: the agenda is deterministic, just run it again.

## Writing an agenda

An agenda is a `.mjs` module (or plain `.json`):

```js
export default {
  viewport: { width: 1920, height: 1080 },   // optional
  dat: "C:/path/to/SCRATCH_COPY.DAT",        // the map to open
  xml: "C:/path/to/Ja2Set.dat.xml",          // tileset xml (read-only)
  tileset: 0,                                // optional override
  steps: [
    ["caption", "Hello"],
    ["camera", 80, 80, 2000],
    // ...
  ],
};
```

Each step is `["verb", ...args]`, executed in order.

### Verb reference

| Verb | Args | Does |
| --- | --- | --- |
| `caption` | `text \| null` | Show / hide the big bottom-center caption bar |
| `wait` | `ms` | Pause (narration beat) |
| `camera` | `x, y, ms` | Eased pan centering tile (x, y) at current zoom |
| `zoom` | `z, ms` | Eased zoom about the viewport center (0.25–8) |
| `moveMouse` | `target, ms` | Human-paced eased mouse glide |
| `click` | `target` | Short glide + real mousedown/up |
| `drag` | `from, to, ms` | Press, eased move, release (region picks) |
| `press` | `key` | Keyboard, e.g. `"Escape"`, `"Control+z"` |
| `clickText` | `text` | Glide to + click the button containing `text` |
| `activateTab` | `title` | Activate a dockview tab (`"Generate"`, `"Inspector"`, …) |
| `waitFor` | `selector, timeoutMs?` | Wait until an element is visible |
| `countdown` | `n` | Big n…1 caption, 1 s per number (OBS sync) |
| `shot` | `name` | Save `<shots>/<name>.png` (verification checkpoints) |
| `done` | `text` | Closing caption, 3 s hold, fade |

A mouse **target** is one of:

- `"css selector"` — center of the first match. Library cards have stable
  hooks: `[data-demo-card]`, e.g. `:nth-match([data-demo-card], 3)`.
- `{ x, y }` — viewport CSS pixels.
- `{ tile: [x, y] }` — a map tile; resolved live through the demo hook,
  so it's correct mid-pan/zoom.

### The demo hook

The runner opens the editor with `&demo=1`, which makes MapForgeSector
expose `window.__mapforgeDemo`:

```ts
panTo(x, y, ms): Promise<void>   // eased pan centering tile (x, y)
zoomTo(z, ms): Promise<void>     // eased zoom, viewport center fixed
caption(text | null): void      // the caption bar
getState(): { ready, zoom, pan } // ready = session open + atlas painted
tileToScreen(x, y): { x, y }     // client px of a tile's center
```

Without `demo=1` none of this exists — zero effect on normal use.

## SAFETY RULE

**Always point `dat:` at a scratch copy** (e.g.
`scratch/clifftest/C6_test.DAT`), **never** at a live install's map, and
**never put a Save action in an agenda** (no clicking Save, no
`Control+s`). The bundled agenda stamps two buildings and undoes both;
the session is discarded when the browser closes. The install's
`Ja2Set.dat.xml` is read-only input.
