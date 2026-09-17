/**
 * IsoRendererGL — WebGL2 implementation of IsoRenderer.
 *
 * Replaces the Canvas2D painter's algorithm in IsoRenderer with a
 * GPU-side textured-quad renderer that uses WebGL2's hardware Z-buffer.
 * Solves the "non-wall struct overhang isn't clipped by wall" bug
 * tracked in `docs/HANDOFF_iso_renderer_z_buffer.md` — the engine
 * (`Blt8BPPDataTo16BPPBufferTransZClip` in renderworld.cpp:5221+) writes
 * + tests Z per pixel with `LEQUAL` ("BurnsThrough") semantics; the
 * Canvas2D path doesn't.
 *
 * Architecture:
 *   - Extends IsoRenderer so it inherits all non-render state: atlas
 *     image, manifest, parsed sector, undo stack, edit ops, inspector
 *     queries. Only the render() method is overridden.
 *   - Acquires its OWN webgl2 context off the canvas passed to render().
 *     If a 2d context was already acquired on that canvas, getContext
 *     will return null — the IsoRenderer base class can co-exist on
 *     a different canvas but NOT the same one.
 *   - Atlas image uploaded once as a single 2D texture (RGBA8). Sprite
 *     quads sample sub-rects via UV coordinates derived from each
 *     AtlasCell's (x, y, w, h).
 *   - Per render: builds a single interleaved vertex buffer with one
 *     quad per visible sprite (6 vertices × 5 floats = posX, posY, uvX,
 *     uvY, depth). Single drawArrays per pass. The GPU's Z-buffer + the
 *     LEQUAL depth test does the engine-faithful clipping.
 *
 * Ground layers retain separate depth tiers. STRUCTS, ROOFS, and ONROOFS
 * use the engine's world-Y and wall-height offsets via isoDepth.ts;
 * z-strip deltas remain one iso row each.
 *
 * BurnsThrough mapping: engine rule is `existing_Z <= sprite_Z → draw`.
 * WebGL `LEQUAL` is `sprite_depth <= existing_depth → draw` (smaller
 * depth wins). We flip the engine's Z by mapping engine_Z to (1 -
 * normalize(engine_Z)) so iso-front (= larger engine_Z) ends up with
 * smaller WebGL depth.
 */

import {
  IsoRenderer,
  effectiveShadowEntries,
  TILE_W,
  TILE_H,
  WALL_HEIGHT,
  spriteIntersectsCrop,
  type LayerName,
  type RenderMeta,
  type RenderOptions,
  type ProgressPhase,
} from "./IsoRenderer";
import type { AtlasCell, AtlasManifest, ParsedSector } from "./mapforge";
import { atlasTextureLayout } from "./atlasTextureLayout";
import { ISO_ROW_DEPTH_STEP, structuralDepth } from "./isoDepth";

const TILE_HW = TILE_W / 2;
const TILE_HH = TILE_H / 2;

// Layer-Y lift (mirror of LAYER_Y_LIFT in IsoRenderer.ts). Inlined
// here because the base class keeps its copy private.
const LAYER_Y_LIFT_GL: Record<LayerName, number> = {
  land: 0,
  objs: 0,
  shadows: 0,
  structs: 0,
  roofs: WALL_HEIGHT,
  onroofs: WALL_HEIGHT,
};

// Ground depth tiers stay behind structures. Structural layer depth is
// calculated from the engine Z formula in isoDepth.ts.
const GROUND_BASE_DEPTH = {
  land: 0.99,
  objs: 0.97,
  shadows: 0.95,
} as const;

// One Z-strip transition in the engine = `Z_STRIP_DELTA_Y` = 80 engine
// Z units = exactly one iso-row's worth of base-Z change (since engine
// sZLevel = sWorldY × 8 and adjacent iso rows differ by 10 in sWorldY,
// → 80 in engine Z). Map to WebGL depth at the same scale as the
// per-iso-row step so a "+1 strip" delta puts the strip at the same
// depth as the next iso row's base. Negative deltas push DEEPER (away
// from viewer = larger WebGL depth = drawn behind).
const STRIP_DEPTH_STEP = ISO_ROW_DEPTH_STEP;

// ─── Shaders ────────────────────────────────────────────────────────
const VERTEX_SHADER = `#version 300 es
in vec2 aPos;       // canvas-pixel coords (0..canvasW, 0..canvasH)
in vec2 aUv;        // atlas-pixel coords (0..atlasW, 0..atlasH)
in float aDepth;    // WebGL depth in [0, 1]

uniform vec2 uViewport;   // canvas width, height
uniform vec2 uAtlasSize;  // atlas width, height

out vec2 vUv;

void main() {
  // Canvas pixels → clip space: (0..W) → (-1..1), Y flipped (canvas Y
  // grows down, clip Y grows up).
  vec2 clip = vec2(
    (aPos.x / uViewport.x) * 2.0 - 1.0,
    1.0 - (aPos.y / uViewport.y) * 2.0
  );
  // Map depth [0, 1] to clip-space Z [-1, 1]. WebGL2 then maps clip Z
  // back to [0, 1] for the depth buffer via gl_DepthRange.
  gl_Position = vec4(clip, aDepth * 2.0 - 1.0, 1.0);

  // Atlas pixels → UV [0, 1].
  vUv = aUv / uAtlasSize;
}
`;

const FRAGMENT_SHADER = `#version 300 es
precision highp float;
in vec2 vUv;
uniform sampler2D uAtlas;
uniform float uShadowAlpha;   // 1.0 for normal, 0.5 for shadow pass
uniform float uAlphaCut;      // alpha-test threshold (0.5 at 1:1; lower when minified)
uniform bool uShadow;         // true: output black with src.alpha * uShadowAlpha
out vec4 outColor;

void main() {
  vec4 src = texture(uAtlas, vUv);
  // Engine: palette index 0 = transparent. The atlas PNG already ships
  // with alpha=0 for those pixels — discard via shader instead of
  // relying on blending (so the Z-buffer write is gated too).
  // When the atlas is MINIFIED (bigmap buffer < 1:1, mipmapped
  // sampling) a 1-px chainlink wire averages to alpha ~0.5, so a
  // fixed 0.5 cut would dash it. uAlphaCut drops to 0.2 there.
  if (src.a < uAlphaCut) {
    discard;
  }
  if (uShadow) {
    // Shadow pass — black with half alpha. Matches the Python
    // alpha_composite of half-alpha black silhouette.
    outColor = vec4(0.0, 0.0, 0.0, src.a * uShadowAlpha);
  } else {
    outColor = src;
  }
}
`;

const FLOATS_PER_VERTEX = 5;  // aPos.xy, aUv.xy, aDepth
const VERTICES_PER_QUAD = 6;  // two triangles

// ─── Class ──────────────────────────────────────────────────────────
export class IsoRendererGL extends IsoRenderer {
  // GL state — initialized lazily on first render() because we need
  // the canvas to acquire the webgl2 context. None of these are valid
  // before initGL() runs.
  private gl: WebGL2RenderingContext | null = null;
  private program: WebGLProgram | null = null;
  private atlasTexture: WebGLTexture | null = null;
  private vertexBuffer: WebGLBuffer | null = null;
  private vao: WebGLVertexArrayObject | null = null;

  // Uniform locations cached so we don't re-look-them-up per render.
  private uViewport: WebGLUniformLocation | null = null;
  private uAtlasSize: WebGLUniformLocation | null = null;
  private uAtlas: WebGLUniformLocation | null = null;
  private uShadow: WebGLUniformLocation | null = null;
  private uShadowAlpha: WebGLUniformLocation | null = null;
  private uAlphaCut: WebGLUniformLocation | null = null;
  // Backing-store scale of the last full-map render (<1 on bigmaps
  // under Chrome's drawing-buffer clamp). Drives the minification
  // filter + alpha cut below.
  private bufScale = 1;

  // Cached state for GL setup. The base class also keeps these in its
  // own private fields; we mirror in protected/public form would be
  // cleaner, but for now we re-read from the manifest passed to
  // create() / replaceAtlas() and stash here.
  private glCellMap: Map<number, AtlasCell> = new Map();
  private glAtlasImg: HTMLImageElement | HTMLCanvasElement;
  private glAtlasW: number;
  private glAtlasH: number;
  private glParsed: ParsedSector;
  private glMeta: RenderMeta = {
    ixMin: 0, iyMin: 0, canvasW: 0, canvasH: 0,
    tileW: TILE_W, tileH: TILE_H,
  };

  protected constructor(
    atlas: HTMLImageElement,
    darkenAtlas: HTMLCanvasElement,
    manifest: AtlasManifest,
    parsed: ParsedSector,
  ) {
    super(atlas, darkenAtlas, manifest, parsed);
    this.glAtlasImg = atlas;
    this.glAtlasW = atlas.naturalWidth;
    this.glAtlasH = atlas.naturalHeight;
    this.glParsed = parsed;
    for (const c of manifest.cells) {
      this.glCellMap.set((c.slot << 16) | (c.sub & 0xffff), c);
    }
  }

  /** WebGL flavor of IsoRenderer.create. Same args + behavior; returns
   * an IsoRendererGL that will use a WebGL2 context on first render(). */
  static async createGL(
    atlasUrl: string,
    manifest: AtlasManifest,
    parsed: ParsedSector,
    onProgress?: (phase: ProgressPhase, pct: number) => void,
  ): Promise<IsoRendererGL> {
    // Reuse the base class's protected static loader so the loading
    // flow stays identical (same progress phases). The shadow atlas
    // isn't used by the WebGL render path — shadow comes from the
    // fragment shader — but the cost is paid once at session open
    // and the public state shape stays consistent with IsoRenderer.
    const { atlas, darkenAtlas } = await IsoRendererGL.loadAtlasState(
      atlasUrl, onProgress,
    );
    return new IsoRendererGL(atlas, darkenAtlas, manifest, parsed);
  }

  /** Initialize WebGL state on the given canvas. Idempotent — second
   * call with the same canvas is a no-op; with a different canvas
   * reinitializes from scratch. */
  private initGL(canvas: HTMLCanvasElement): void {
    if (this.gl && this.gl.canvas === canvas) return;
    const gl = canvas.getContext("webgl2", {
      alpha: true,
      depth: true,
      premultipliedAlpha: false,
      antialias: false,  // nearest-neighbor sprite sampling — no AA
      preserveDrawingBuffer: false,
    });
    if (!gl) {
      throw new Error(
        "IsoRendererGL.initGL: failed to acquire webgl2 context. " +
        "WebView2 should support it; check that the canvas hasn't " +
        "already had a 2d context acquired on it."
      );
    }
    this.gl = gl;

    // Compile + link program.
    const vs = this.compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
    const fs = this.compileShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
    const prog = gl.createProgram();
    if (!prog) throw new Error("IsoRendererGL: createProgram failed");
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.bindAttribLocation(prog, 0, "aPos");
    gl.bindAttribLocation(prog, 1, "aUv");
    gl.bindAttribLocation(prog, 2, "aDepth");
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      const log = gl.getProgramInfoLog(prog);
      throw new Error(`IsoRendererGL: program link failed: ${log}`);
    }
    this.program = prog;
    this.uViewport = gl.getUniformLocation(prog, "uViewport");
    this.uAtlasSize = gl.getUniformLocation(prog, "uAtlasSize");
    this.uAtlas = gl.getUniformLocation(prog, "uAtlas");
    this.uShadow = gl.getUniformLocation(prog, "uShadow");
    this.uShadowAlpha = gl.getUniformLocation(prog, "uShadowAlpha");
    this.uAlphaCut = gl.getUniformLocation(prog, "uAlphaCut");

    this.fitAtlasToDevice(gl);
    // Atlas texture upload.
    const tex = gl.createTexture();
    if (!tex) throw new Error("IsoRendererGL: createTexture failed");
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texImage2D(
      gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, this.glAtlasImg,
    );
    // Mipmaps for MINIFICATION: bigmaps rasterize the whole map at
    // ~0.55 scale (drawing-buffer clamp) and NEAREST sampling there
    // drops 1-px art (chainlink fence wires) entirely - the zoomed-out
    // 'broken fence' artifact. MIN filter is set per render (NEAREST
    // at 1:1, mipmapped when the buffer is downscaled); MAG stays
    // NEAREST so 1:1 / zoomed-in pixels remain crisp.
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this.atlasTexture = tex;

    // Vertex buffer + VAO. We rebuild the buffer contents per render;
    // the buffer object itself is reused with bufferData(DYNAMIC_DRAW).
    const vao = gl.createVertexArray();
    const vbo = gl.createBuffer();
    if (!vao || !vbo) throw new Error("IsoRendererGL: createBuffer/VAO failed");
    gl.bindVertexArray(vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    const stride = FLOATS_PER_VERTEX * 4;
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, stride, 0);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribPointer(1, 2, gl.FLOAT, false, stride, 8);
    gl.enableVertexAttribArray(2);
    gl.vertexAttribPointer(2, 1, gl.FLOAT, false, stride, 16);
    gl.bindVertexArray(null);
    this.vao = vao;
    this.vertexBuffer = vbo;

    // Persistent GL state.
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(60 / 255, 50 / 255, 40 / 255, 1.0);  // Python (60,50,40)
    gl.clearDepth(1.0);
  }

  private compileShader(
    gl: WebGL2RenderingContext, type: number, src: string,
  ): WebGLShader {
    const sh = gl.createShader(type);
    if (!sh) throw new Error("IsoRendererGL: createShader failed");
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(sh);
      throw new Error(`IsoRendererGL: shader compile failed: ${log}`);
    }
    return sh;
  }

  override render(
    canvas: HTMLCanvasElement, opts: RenderOptions,
  ): RenderMeta {
    this.initGL(canvas);
    const gl = this.gl!;

    const meta = this.computeMeta(opts);
    // Chrome silently clamps a WebGL drawing buffer to ~2^25 total
    // pixels (aspect preserved). A 360×360 bigmap's canvas
    // (14520×7440 ≈ 108M px) came back as an 8046×4123 buffer, so a
    // full-size gl.viewport rasterized only the bottom-left wedge and
    // CSS stretched it across the canvas — cropped map + chunky
    // NEAREST-upscaled sprites. Size the backing store to fit under
    // the clamp instead: the vertex shader maps logical px → NDC via
    // uViewport (resolution-independent), MapForgeSector pins the
    // canvas CSS size to meta.canvasW/H, and hit-testing scales by
    // clientWidth, so all logical coordinates are unaffected —
    // bigmaps just rasterize at reduced resolution.
    // ponytail: whole-map buffer at ~0.55 scale on 360×360 maps (soft
    // at high zoom); upgrade path = viewport-region rendering.
    const MAX_BUFFER_PX = 32 * 1024 * 1024;
    const bufScale = Math.min(
      1, Math.sqrt(MAX_BUFFER_PX / (meta.canvasW * meta.canvasH)),
    );
    const bufW = Math.max(1, Math.round(meta.canvasW * bufScale));
    const bufH = Math.max(1, Math.round(meta.canvasH * bufScale));
    this.bufScale = bufScale;
    if (canvas.width !== bufW || canvas.height !== bufH) {
      canvas.width = bufW;
      canvas.height = bufH;
    }
    // Viewport from what Chrome ACTUALLY allocated, not what we asked
    // for — the clamp threshold is device-dependent (observed ~33.17M
    // px here, under 2^25), and a viewport larger than the real buffer
    // is exactly the crop bug this block exists to fix.
    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
    this.glMeta = meta;

    const t0 = performance.now();
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    // One-time atlas-manifest stat: log how many cells of the loaded
    // atlas carry zstrip. Critical sanity check — if 0, the sidecar
    // didn't bake with per-strip Z (probably running pre-v6 cached
    // code) and wall clipping won't work. See
    // docs/HANDOFF_iso_renderer_z_buffer.md.
    // Dev-only: this is a developer sanity check whose message tells the
    // reader to restart for stale sidecar code — not something to say to
    // someone running a release build.
    if (import.meta.env.DEV && !this.atlasStatsLogged) {
      let n_zstrip = 0;
      let n_burns = 0;
      for (const cell of this.glCellMap.values()) {
        if (cell.zstrip) {
          n_zstrip++;
          if (cell.zstrip.burns_through) n_burns++;
        }
      }
      // eslint-disable-next-line no-console
      console.log(
        `[IsoRendererGL] atlas: ${this.glCellMap.size} cells, ` +
        `${n_zstrip} with zstrip (${n_burns} burns_through, ` +
        `${n_zstrip - n_burns} strict). If 0, sidecar's mapforge.py ` +
        `is on old code — restart MercForge fully.`,
      );
      this.atlasStatsLogged = true;
    }

    const { rx0, ry0, rx1, ry1 } = this.glResolveRegion(opts);
    const spill = this.collectGlCropSpill(rx0, ry0, rx1, ry1);
    const skip = opts.skipLayers ?? new Set<LayerName>();

    // Iso-row groupings (mirror of base class).
    const rowsByXy = new Map<number, [number, number][]>();
    for (let ty = ry0; ty <= ry1; ty++) {
      for (let tx = rx0; tx <= rx1; tx++) {
        const k = tx + ty;
        let row = rowsByXy.get(k);
        if (!row) {
          row = [];
          rowsByXy.set(k, row);
        }
        row.push([tx, ty]);
      }
    }
    for (const gn of spill.tiles) {
      const tx = gn % this.glParsed.cols;
      const ty = Math.floor(gn / this.glParsed.cols);
      const k = tx + ty;
      const row = rowsByXy.get(k);
      if (row) row.push([tx, ty]);
      else rowsByXy.set(k, [[tx, ty]]);
    }
    // Preserve this renderer's existing row order (ty ascending) for
    // equal-Z overlap; only the new spill anchors need merging into it.
    for (const row of rowsByXy.values()) {
      row.sort((a, b) => a[1] - b[1]);
    }
    const orderedXy = [...rowsByXy.keys()].sort((a, b) => a - b);

    // Build vertex data for four batches with distinct depth-test
    // rules (mirrors engine's blitter dispatch at renderworld.cpp:2436+):
    //   opaqueVerts  — standard sprites (LAND/OBJS/non-multi-tile
    //                  STRUCT/ROOF/ONROOF). depthFunc = LEQUAL.
    //                  Equal-Z draws (matches standard blitter `JA`).
    //   strictVerts  — multi-Z non-wall sprites (lawless4, trees, etc.).
    //                  depthFunc = LESS (STRICT). Equal-Z SKIPS — this
    //                  is what hides the lawless4 overhang behind a
    //                  neighboring wall whose flat Z matches a strip Z.
    //                  Mirrors blitter `JAE` at renderworld.cpp:5061.
    //   burnsVerts   — multi-Z WALL sprites. depthFunc = LEQUAL
    //                  (BurnsThrough). Empty in the reference-install tilesets;
    //                  populated when an install ships structurally
    //                  multi-tile walls.
    //   shadowVerts  — shadow pass, separate fragment shader path.
    const opaqueVerts: number[] = [];
    const strictVerts: number[] = [];
    const burnsVerts: number[] = [];
    const shadowVerts: number[] = [];

    // LAND/OBJS use standard semantics (no zstrip on these layers).
    if (!skip.has("land")) {
      for (const xy of orderedXy) {
        for (const [tx, ty] of rowsByXy.get(xy)!) {
          const inRegion = tx >= rx0 && tx <= rx1 && ty >= ry0 && ty <= ry1;
          this.emitTileLayerVerts(opaqueVerts, strictVerts, burnsVerts,
            tx, ty, "land", inRegion ? undefined : (spill.entries.get("land")?.get(ty * this.glParsed.cols + tx) ?? []));
        }
      }
    }
    if (!skip.has("objs")) {
      for (const xy of orderedXy) {
        for (const [tx, ty] of rowsByXy.get(xy)!) {
          const inRegion = tx >= rx0 && tx <= rx1 && ty >= ry0 && ty <= ry1;
          this.emitTileLayerVerts(opaqueVerts, strictVerts, burnsVerts,
            tx, ty, "objs", inRegion ? undefined : (spill.entries.get("objs")?.get(ty * this.glParsed.cols + tx) ?? []));
        }
      }
    }
    // Shadows: separate batch (different fragment behavior via uShadow).
    if (!skip.has("shadows")) {
      for (const xy of orderedXy) {
        for (const [tx, ty] of rowsByXy.get(xy)!) {
          const inRegion = tx >= rx0 && tx <= rx1 && ty >= ry0 && ty <= ry1;
          this.emitTileLayerVerts(shadowVerts, shadowVerts, shadowVerts,
            tx, ty, "shadows", inRegion ? undefined : (spill.entries.get("shadows")?.get(ty * this.glParsed.cols + tx) ?? []));
        }
      }
    }
    // STRUCT + ROOF + ONROOF — level-major within each iso row. This is
    // the pass where zstrip dispatch matters; emitTileLayerVerts routes
    // per cell.zstrip into opaque/strict/burns as appropriate.
    const layers4: LayerName[] = (["structs", "roofs", "onroofs"] as const)
      .filter((l) => !skip.has(l));
    if (layers4.length > 0) {
      for (const xy of orderedXy) {
        for (const layer of layers4) {
          for (const [tx, ty] of rowsByXy.get(xy)!) {
            const inRegion = tx >= rx0 && tx <= rx1 && ty >= ry0 && ty <= ry1;
            this.emitTileLayerVerts(opaqueVerts, strictVerts, burnsVerts,
              tx, ty, layer, inRegion ? undefined : (spill.entries.get(layer)?.get(ty * this.glParsed.cols + tx) ?? []));
          }
        }
      }
    }

    // Draw batches with the right depthFunc per engine semantics.
    // Order doesn't materially affect correctness because the Z-buffer
    // arbitrates pixel-by-pixel, but we draw opaque first so the
    // strict-LESS batch tests against the most-populated Z-buffer
    // (mirrors the engine's per-iso-row STRUCT pass that interleaves
    // standard + multi-Z blitters by entry order — close enough).
    if (opaqueVerts.length > 0) {
      gl.depthFunc(gl.LEQUAL);
      this.drawBatch(gl, opaqueVerts, false);
    }
    if (strictVerts.length > 0) {
      gl.depthFunc(gl.LESS);
      this.drawBatch(gl, strictVerts, false);
    }
    if (burnsVerts.length > 0) {
      gl.depthFunc(gl.LEQUAL);
      this.drawBatch(gl, burnsVerts, false);
    }
    if (shadowVerts.length > 0) {
      gl.depthFunc(gl.LEQUAL);
      this.drawBatch(gl, shadowVerts, true);
    }
    // Per-render timing for perf budget enforcement.
    // Cold C6 sector should
    // come in under 30 ms on typical modern hardware.
    // Logged every 60th render to avoid console spam during pan/zoom.
    const elapsed = performance.now() - t0;
    this.lastRenderMs = elapsed;
    this.renderCount += 1;
    // Dev-only — a per-render timing trace has no audience in a release
    // build, where it is just console noise for the lifetime of the session.
    if (import.meta.env.DEV && this.renderCount % 60 === 1) {
      // eslint-disable-next-line no-console
      console.log(
        `[IsoRendererGL] render #${this.renderCount}: ${elapsed.toFixed(1)} ms ` +
        `(opaque=${opaqueVerts.length / (FLOATS_PER_VERTEX * VERTICES_PER_QUAD)} ` +
        `strict=${strictVerts.length / (FLOATS_PER_VERTEX * VERTICES_PER_QUAD)} ` +
        `burns=${burnsVerts.length / (FLOATS_PER_VERTEX * VERTICES_PER_QUAD)} ` +
        `shadow=${shadowVerts.length / (FLOATS_PER_VERTEX * VERTICES_PER_QUAD)} quads)`,
      );
    }
    return meta;
  }

  // One-shot atlas-stats log gate — flips true after the first render
  // logs the manifest's zstrip counts. Resets when replaceAtlas swaps.
  private atlasStatsLogged = false;

  /** The server's narrow atlas can exceed MAX_TEXTURE_SIZE in height.
   * Repack only the GPU copy; keep source pixels and editor metadata intact. */
  private fitAtlasToDevice(gl: WebGL2RenderingContext): void {
    const limit = Math.min(16384, gl.getParameter(gl.MAX_TEXTURE_SIZE) as number);
    if (this.glAtlasW <= limit && this.glAtlasH <= limit) return;
    const packed = atlasTextureLayout([...this.glCellMap.values()], limit);
    const canvas = document.createElement('canvas');
    canvas.width = packed.width;
    canvas.height = packed.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Unable to allocate GPU atlas repacking canvas');
    ctx.imageSmoothingEnabled = false;
    const cells = new Map<number, AtlasCell>();
    for (const { source, target } of packed.placements) {
      ctx.drawImage(this.glAtlasImg, source.x, source.y, source.w, source.h,
        target.x, target.y, target.w, target.h);
      cells.set((target.slot << 16) | (target.sub & 0xffff), target);
    }
    console.info(`[IsoRendererGL] repacked atlas ${this.glAtlasW}x${this.glAtlasH} -> ${canvas.width}x${canvas.height} (GPU limit ${limit})`);
    this.glAtlasImg = canvas;
    this.glAtlasW = canvas.width;
    this.glAtlasH = canvas.height;
    this.glCellMap = cells;
  }
  // Perf instrumentation. lastRenderMs is the wall-clock time of the
  // most recent render() in milliseconds — readable for budget checks.
  private lastRenderMs = 0;
  private renderCount = 0;

  /** Wall-clock time (ms) of the last render() call. Used by perf
   * scripts and by anyone who wants to verify the demo-readiness
   * budget (cold C6 sector ≤ 30 ms on typical modern hardware). */
  getLastRenderMs(): number {
    return this.lastRenderMs;
  }

  private drawBatch(
    gl: WebGL2RenderingContext, verts: number[], shadow: boolean,
  ): void {
    gl.useProgram(this.program);
    gl.bindVertexArray(this.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.vertexBuffer);
    // Float32Array conversion is the per-frame cost. For ~4000 sprites
    // × 6 verts × 5 floats = 120k floats = 480 KB allocation.
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(verts), gl.DYNAMIC_DRAW);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.atlasTexture);
    gl.uniform1i(this.uAtlas, 0);
    gl.uniform2f(this.uViewport, this.glMeta.canvasW, this.glMeta.canvasH);
    gl.uniform2f(this.uAtlasSize, this.glAtlasW, this.glAtlasH);
    gl.uniform1i(this.uShadow, shadow ? 1 : 0);
    gl.uniform1f(this.uShadowAlpha, 0.5);
    const minified = this.bufScale < 1;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER,
      minified ? gl.LINEAR_MIPMAP_LINEAR : gl.NEAREST);
    gl.uniform1f(this.uAlphaCut, minified ? 0.2 : 0.5);

    const vertCount = verts.length / FLOATS_PER_VERTEX;
    gl.drawArrays(gl.TRIANGLES, 0, vertCount);

    gl.bindVertexArray(null);
    gl.bindBuffer(gl.ARRAY_BUFFER, null);
  }

  /** Emit quad(s) per entry at this tile/layer. Routes into one of
   * three batches based on the entry's AtlasCell.zstrip:
   *   - null  → opaqueOut (one quad, flat depth)
   *   - present, burns_through=false → strictOut (N quads per strip,
   *     per-strip depth)
   *   - present, burns_through=true → burnsOut (N quads per strip)
   *
   * Strip emission walks the sprite left-to-right, advancing through
   * the zstrip's first_strip_width then 20-pixel strips. Each strip
   * becomes one quad with width = strip_pixel_count, depth offset by
   * (running_z_delta × STRIP_DEPTH_STEP) from the sprite's base. The
   * last strip is truncated to whatever sprite pixels remain. */
  private emitTileLayerVerts(
    opaqueOut: number[],
    strictOut: number[],
    burnsOut: number[],
    tx: number, ty: number, layer: LayerName,
    entriesOverride?: number[][],
  ): void {
    const parsed = this.glParsed;
    const gn = ty * parsed.cols + tx;
    // Shadows: overlay engine buddy shadows (ephemeral) — see IsoRenderer.
    const entries = entriesOverride ?? (layer === "shadows"
      ? effectiveShadowEntries(parsed, gn, this.glCellMap)
      : parsed[layer][gn]);
    if (!entries || entries.length === 0) return;
    const yLift = LAYER_Y_LIFT_GL[layer];
    const isStructTier =
      layer === "structs" || layer === "roofs" || layer === "onroofs";
    const tileBaseDepth = layer === "structs" || layer === "roofs" || layer === "onroofs"
      ? structuralDepth(layer, tx + ty)
      : GROUND_BASE_DEPTH[layer];
    const rawX = (tx - ty) * TILE_HW;
    const rawY = (tx + ty) * TILE_HH;
    const px = rawX - this.glMeta.ixMin;
    const py = rawY - this.glMeta.iyMin - yLift;
    for (const entry of entries) {
      if (entry.length < 2) continue;
      const slot = entry[0] as number;
      const sub = entry[1] as number;
      const cell = this.glCellMap.get((slot << 16) | (sub & 0xffff));
      if (!cell) continue;
      const spriteX = px + cell.ox;
      const spriteY = py + cell.oy;
      const yTop = spriteY;
      const yBot = spriteY + cell.h;
      const vTop = cell.y;
      const vBot = cell.y + cell.h;
      const zs = cell.zstrip;
      if (!zs || !isStructTier) {
        // Standard path: one flat-depth quad. Non-STRUCT layers can't
        // meaningfully use per-strip Z anyway — LAND/OBJS/SHADOWS use
        // their own depth tier.
        const x1 = spriteX + cell.w;
        const u0 = cell.x;
        const u1 = cell.x + cell.w;
        opaqueOut.push(spriteX, yTop, u0, vTop, tileBaseDepth);
        opaqueOut.push(x1,       yTop, u1, vTop, tileBaseDepth);
        opaqueOut.push(x1,       yBot, u1, vBot, tileBaseDepth);
        opaqueOut.push(spriteX, yTop, u0, vTop, tileBaseDepth);
        opaqueOut.push(x1,       yBot, u1, vBot, tileBaseDepth);
        opaqueOut.push(spriteX, yBot, u0, vBot, tileBaseDepth);
        continue;
      }
      // Multi-Z path: emit one quad per strip with its strip depth.
      const out = zs.burns_through ? burnsOut : strictOut;
      let stripStartPx = 0;                    // pixel offset within sprite
      let runningDelta = zs.initial_z_change;  // engine running Z delta in strips
      const stripCount = 1 + zs.z_changes.length;
      for (let i = 0; i < stripCount; i++) {
        const stripWidth = i === 0
          ? zs.first_strip_width
          : 20;  // WORLD_TILE_X / 2 — engine strip width
        const stripEndPx = Math.min(stripStartPx + stripWidth, cell.w);
        if (stripEndPx <= stripStartPx) {
          // Sprite consumed entirely — remaining strips have no
          // pixels to draw. Skip.
          break;
        }
        const stripDepth = tileBaseDepth - runningDelta * STRIP_DEPTH_STEP;
        const qx0 = spriteX + stripStartPx;
        const qx1 = spriteX + stripEndPx;
        const qu0 = cell.x + stripStartPx;
        const qu1 = cell.x + stripEndPx;
        out.push(qx0, yTop, qu0, vTop, stripDepth);
        out.push(qx1, yTop, qu1, vTop, stripDepth);
        out.push(qx1, yBot, qu1, vBot, stripDepth);
        out.push(qx0, yTop, qu0, vTop, stripDepth);
        out.push(qx1, yBot, qu1, vBot, stripDepth);
        out.push(qx0, yBot, qu0, vBot, stripDepth);
        stripStartPx = stripEndPx;
        // Apply the next strip's Z delta (index i-1 of z_changes
        // applies BETWEEN strip i-1 and strip i for i >= 1; we just
        // emitted strip i, so apply z_changes[i] to set up strip i+1).
        if (i < zs.z_changes.length) {
          runningDelta += zs.z_changes[i] ?? 0;
        }
      }
    }
  }

  /** WebGL twin of IsoRenderer's crop-spill collector. It retains the
   * tile-derived camera and admits every projected entry rectangle,
   * including independent land, so GPU and Canvas2D crops stay coherent. */
  private collectGlCropSpill(rx0: number, ry0: number, rx1: number, ry1: number): {
    tiles: Set<number>;
    entries: Map<LayerName, Map<number, number[][]>>;
  } {
    const entries = new Map<LayerName, Map<number, number[][]>>();
    const tiles = new Set<number>();
    const layers: LayerName[] = ["land", "objs", "shadows", "structs", "roofs", "onroofs"];
    for (const layer of layers) entries.set(layer, new Map());
    const parsed = this.glParsed;
    for (let gn = 0; gn < parsed.rows * parsed.cols; gn++) {
      const tx = gn % parsed.cols;
      const ty = Math.floor(gn / parsed.cols);
      if (tx >= rx0 && tx <= rx1 && ty >= ry0 && ty <= ry1) continue;
      const rawX = (tx - ty) * TILE_HW;
      const rawY = (tx + ty) * TILE_HH;
      for (const layer of layers) {
        const source = layer === "shadows"
          ? effectiveShadowEntries(parsed, gn, this.glCellMap)
          : parsed[layer][gn];
        if (!source || source.length === 0) continue;
        const selected: number[][] = [];
        for (const entry of source) {
          if (entry.length < 2) continue;
          const cell = this.glCellMap.get(((entry[0] as number) << 16)
            | ((entry[1] as number) & 0xffff));
          if (cell && spriteIntersectsCrop(rawX, rawY, cell, LAYER_Y_LIFT_GL[layer], this.glMeta)) {
            selected.push(entry);
          }
        }
        if (selected.length > 0) {
          entries.get(layer)!.set(gn, selected);
          tiles.add(gn);
        }
      }
    }
    return { tiles, entries };
  }

  /** Local resolveRegion mirror — the base class's is private. */
  private glResolveRegion(opts: RenderOptions): {
    rx0: number; ry0: number; rx1: number; ry1: number;
  } {
    const parsed = this.glParsed;
    const { rows, cols } = parsed;
    if (opts.roomId !== undefined && opts.roomId !== null) {
      const ring = opts.ring ?? 5;
      const xs: number[] = [];
      const ys: number[] = [];
      for (let g = 0; g < parsed.rooms.length; g++) {
        if (parsed.rooms[g] === opts.roomId) {
          xs.push(g % cols);
          ys.push(Math.floor(g / cols));
        }
      }
      if (xs.length > 0) {
        return {
          rx0: Math.max(0, Math.min(...xs) - ring),
          ry0: Math.max(0, Math.min(...ys) - ring),
          rx1: Math.min(cols - 1, Math.max(...xs) + ring),
          ry1: Math.min(rows - 1, Math.max(...ys) + ring),
        };
      }
    }
    if (opts.bbox) {
      const [x0, y0, x1, y1] = opts.bbox;
      return { rx0: x0, ry0: y0, rx1: x1, ry1: y1 };
    }
    return { rx0: 0, ry0: 0, rx1: cols - 1, ry1: rows - 1 };
  }

  /** Override replaceAtlas to also re-upload the GL texture + rebuild
   * the per-cell lookup. Called when the user adds an STI to the
   * tileset and the atlas needs to be re-baked. */
  override async replaceAtlas(
    atlasUrl: string,
    manifest: AtlasManifest,
    onProgress?: (phase: ProgressPhase, pct: number) => void,
  ): Promise<void> {
    await super.replaceAtlas(atlasUrl, manifest, onProgress);
    // Read the new atlas via the protected field on the base class.
    const atlas = this.atlas;
    this.glAtlasImg = atlas;
    // New atlas → re-log its zstrip stats on the next render so the
    // user can see whether the swap brought zstrip data.
    this.atlasStatsLogged = false;
    this.glAtlasW = atlas.naturalWidth;
    this.glAtlasH = atlas.naturalHeight;
    this.glCellMap = new Map();
    for (const c of manifest.cells) {
      this.glCellMap.set((c.slot << 16) | (c.sub & 0xffff), c);
    }
    // Re-upload texture if GL is already initialized.
    if (this.gl && this.atlasTexture) {
      this.fitAtlasToDevice(this.gl);
      this.gl.bindTexture(this.gl.TEXTURE_2D, this.atlasTexture);
      this.gl.texImage2D(
        this.gl.TEXTURE_2D, 0, this.gl.RGBA, this.gl.RGBA,
        this.gl.UNSIGNED_BYTE, this.glAtlasImg,
      );
      this.gl.generateMipmap(this.gl.TEXTURE_2D);
    }
  }

  /** Override setParsed to also update our local mirror used in render. */
  override setParsed(parsed: ParsedSector): void {
    super.setParsed(parsed);
    this.glParsed = parsed;
  }
}
