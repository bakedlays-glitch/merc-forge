/**
 * Standalone STI Viewer.
 *
 * Pick any `.sti` file from disk → render its metadata, per-frame
 * thumbnails, and read-only JSD companion (when one exists at
 * `<file>.jsd`). Independent of the active install or any tileset.
 *
 * Frame indexing in this viewer is 0-based (matches the underlying
 * STI binary's `frames[]` array). The `.dat` sector format uses
 * 1-based sub indices when referencing slot frames; that translation
 * happens elsewhere — the standalone viewer is a raw inspector.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import { pickFile, pickSaveFile } from "../lib/tauri";
import {
  decodeStiMeta,
  fetchStiFrameBlob,
  getStiJsd,
  saveStiFrameAsPng,
  type StiViewerMeta,
  type ToolsJsdParsed,
  type ToolsJsdProfileTile,
} from "../lib/tools";

// Mirrors _JSD_FLAG_LABELS in sidecar/routes/mapforge.py — read-only
// here so we can show the bits as chips. (The full editor in
// TilesetEditorJsdPanel uses the same list and writes through a
// separate endpoint.)
const JSD_FLAG_BITS: { bit: number; name: string }[] = [
  { bit: 0x0001, name: "TILE_ON_ROOF" },
  { bit: 0x0002, name: "HAS_SHADOW_BUDDY" },
  { bit: 0x0004, name: "DAMAGED" },
  { bit: 0x0008, name: "EXPLOSIVE" },
  { bit: 0x0010, name: "PARTIAL_WALL" },
  { bit: 0x0020, name: "FULL_WALL" },
  { bit: 0x0040, name: "WIREFRAME" },
  { bit: 0x0080, name: "PASSABLE" },
  { bit: 0x0100, name: "EXIT_GRID" },
  { bit: 0x0200, name: "BLOCKS_LOS" },
  { bit: 0x0400, name: "OBSTACLE" },
  { bit: 0x0800, name: "SLIDING_DOOR" },
  { bit: 0x1000, name: "DOOR" },
  { bit: 0x2000, name: "OPENABLE" },
  { bit: 0x4000, name: "SEETHROUGH" },
  { bit: 0x8000, name: "BURNABLE" },
];

export default function ToolsStiViewer() {
  const [path, setPath] = useState<string | null>(null);

  const meta = useQuery({
    queryKey: ["tools", "sti-meta", path],
    queryFn: () => decodeStiMeta(path!),
    enabled: !!path,
    retry: false,
    staleTime: 60 * 1000,
  });

  async function onPick() {
    const picked = await pickFile("Pick an .sti file", [
      { name: "STI sprite", extensions: ["sti", "STI"] },
    ]);
    if (picked) setPath(picked);
  }

  return (
    <div className="mx-auto max-w-6xl px-6 py-8 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">STI Viewer</h1>
          <p className="text-sm text-wasteland-300 mt-1">
            Open any .sti from disk — works on STIs outside the active install or
            tileset.
          </p>
        </div>
        <Link to="/tools" className="text-sm text-wasteland-400 hover:text-rust-400">
          ← Tools
        </Link>
      </div>

      <div className="flex items-center gap-3">
        <button onClick={onPick} className="btn-primary">
          {path ? "Pick another .sti..." : "Pick .sti file..."}
        </button>
        {path && (
          <span className="text-xs font-mono text-wasteland-400 truncate" title={path}>
            {path}
          </span>
        )}
      </div>

      {meta.isLoading && (
        <div className="card text-sm text-wasteland-300">Decoding STI...</div>
      )}
      {meta.isError && (
        <div className="card border-rust-500/40 bg-rust-500/10 text-sm text-rust-200">
          {String(meta.error instanceof Error ? meta.error.message : meta.error)}
        </div>
      )}

      {meta.data && (
        <>
          <MetadataPanel meta={meta.data} />
          <FrameGrid path={meta.data.path} frames={meta.data.frames} />
          {meta.data.has_jsd && <JsdViewerPanel stiPath={meta.data.path} />}
        </>
      )}

      {!path && !meta.isLoading && (
        <div className="card text-sm text-wasteland-300">
          Pick a .sti file to inspect. Frames are decoded against the STI's own
          palette and displayed at native size.
        </div>
      )}
    </div>
  );
}

// ─── Metadata panel ───────────────────────────────────────────────────

function MetadataPanel({ meta }: { meta: StiViewerMeta }) {
  return (
    <div className="card text-sm">
      <h2 className="text-base font-semibold mb-2">Metadata</h2>
      <dl className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-x-4 gap-y-1 text-xs">
        <Row label="Canvas">
          <span className="font-mono">
            {meta.width} × {meta.height}
          </span>
        </Row>
        <Row label="Frames">
          <span className="font-mono">{meta.frame_count}</span>
        </Row>
        <Row label="Encoding">
          <span className="font-mono">{meta.is_8bit ? "8-bit indexed" : "16-bit RGB"}</span>
        </Row>
        <Row label="Palette">
          <span className="font-mono">{meta.palette_present ? "yes" : "no"}</span>
        </Row>
        <Row label="File size">
          <span className="font-mono">{meta.size_bytes.toLocaleString()} bytes</span>
        </Row>
        <Row label="JSD companion">
          <span className="font-mono">{meta.has_jsd ? "yes" : "no"}</span>
        </Row>
      </dl>
      {!meta.is_8bit && (
        <p className="mt-2 text-[11px] text-amber-300">
          This is a 16-bit STI. Per-frame thumbnails aren't supported — the
          format is used for non-sprite images (UI panels, screen backgrounds).
        </p>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-wasteland-500">
        {label}
      </span>
      {children}
    </div>
  );
}

// ─── Frame grid ───────────────────────────────────────────────────────

type ZoomLevel = number | "fit";

const ZOOM_OPTIONS: ZoomLevel[] = ["fit", 1, 2, 4, 8, 16];

/** Pick a scale factor that makes a frame at least this wide in pixels
 * when in "fit" mode. STI sprites range from 15×14 (tiny portraits) to
 * 106×122 (BigFace) — 96 px is a comfortable readable minimum. */
const FIT_TARGET_PX = 96;

function pickScale(zoom: ZoomLevel, frameWidth: number): number {
  if (zoom !== "fit") return zoom;
  const w = Math.max(1, frameWidth);
  return Math.max(1, Math.min(16, Math.floor(FIT_TARGET_PX / w)));
}

function FrameGrid({
  path,
  frames,
}: {
  path: string;
  frames: StiViewerMeta["frames"];
}) {
  const [zoom, setZoom] = useState<ZoomLevel>("fit");

  if (frames.length === 0) {
    return (
      <div className="card text-sm text-wasteland-300">
        No 8-bit frames to display.
      </div>
    );
  }
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2 gap-3 flex-wrap">
        <h2 className="text-base font-semibold">Frames</h2>
        <ZoomControl zoom={zoom} onChange={setZoom} />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
        {frames.map((f) => (
          <FrameCard key={f.index} path={path} frame={f} zoom={zoom} />
        ))}
      </div>
    </div>
  );
}

function ZoomControl({
  zoom,
  onChange,
}: {
  zoom: ZoomLevel;
  onChange: (z: ZoomLevel) => void;
}) {
  return (
    <div className="flex items-center gap-1 text-xs">
      <span className="text-wasteland-500 mr-1 uppercase tracking-wider text-[10px]">
        Zoom
      </span>
      {ZOOM_OPTIONS.map((v) => {
        const active = zoom === v;
        return (
          <button
            key={String(v)}
            type="button"
            onClick={() => onChange(v)}
            className={`rounded border px-1.5 py-0.5 text-[10px] font-mono ${
              active
                ? "border-rust-500 bg-rust-500/20 text-rust-200"
                : "border-wasteland-700 text-wasteland-300 hover:border-wasteland-500"
            }`}
            title={v === "fit" ? "Auto: scale to ~96 px wide" : `${v}× pixel scale`}
          >
            {v === "fit" ? "Fit" : `${v}×`}
          </button>
        );
      })}
    </div>
  );
}

function FrameCard({
  path,
  frame,
  zoom,
}: {
  path: string;
  frame: StiViewerMeta["frames"][number];
  zoom: ZoomLevel;
}) {
  const [url, setUrl] = useState<string | null>(null);
  // Hold onto the Blob alongside the object URL so "Copy PNG to
  // clipboard" can write it without re-fetching. URL.createObjectURL is
  // one-way — there's no way to recover the Blob from a blob: URL
  // synchronously, so we keep both.
  const [blobData, setBlobData] = useState<Blob | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // "Copied!" flash after a successful clipboard write. Self-resets
  // after 1.5 s.
  const [copiedAt, setCopiedAt] = useState<number | null>(null);
  useEffect(() => {
    if (copiedAt === null) return;
    const t = window.setTimeout(() => setCopiedAt(null), 1500);
    return () => window.clearTimeout(t);
  }, [copiedAt]);

  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    // Drop the stale URL immediately so a `path` or `frame` change
    // doesn't briefly render a just-revoked blob.
    setUrl(null);
    setBlobData(null);
    setErr(null);
    // Fetch as Blob first, then create the object URL. Same network
    // hit as the previous `fetchStiFrameBlobUrl` helper, but gives us
    // the Blob to keep around for clipboard writes.
    fetchStiFrameBlob(path, frame.index)
      .then((b) => {
        if (cancelled) return;
        const u = URL.createObjectURL(b);
        createdUrl = u;
        setBlobData(b);
        setUrl(u);
      })
      .catch((e: unknown) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [path, frame.index]);

  const save = useMutation({
    mutationFn: async () => {
      const out = await pickSaveFile(
        `frame_${frame.index}.png`,
        [{ name: "PNG image", extensions: ["png"] }],
        "Save STI frame as PNG",
      );
      if (!out) return null;
      return saveStiFrameAsPng(path, frame.index, out);
    },
  });

  const copy = useMutation({
    mutationFn: async () => {
      if (!blobData) throw new Error("Frame not loaded yet");
      // Tauri 2's webview supports the Clipboard API for images.
      // ClipboardItem("image/png", blob) is the standard shape — same
      // as paste-into-Discord / Slack / Word.
      await navigator.clipboard.write([
        new ClipboardItem({ "image/png": blobData }),
      ]);
      setCopiedAt(Date.now());
    },
  });

  const scale = pickScale(zoom, frame.width);
  const renderedW = frame.width * scale;
  const renderedH = frame.height * scale;

  return (
    <div className="rounded border border-wasteland-700 bg-wasteland-900 p-2 flex flex-col gap-1.5">
      {/* Image well: fixed display area; large zooms scroll inside. */}
      <div className="flex items-center justify-center bg-[#1d1d1d] border border-wasteland-800 h-32 overflow-auto">
        {err && <span className="text-[10px] text-rust-300 p-2">{err}</span>}
        {url && (
          <img
            src={url}
            alt={`frame ${frame.index}`}
            // STI frames are pixel art — `pixelated` keeps the upscale
            // crisp rather than smoothing into mush.
            style={{
              imageRendering: "pixelated",
              width: renderedW,
              height: renderedH,
              // Allow the image to be larger than its container; the
              // scroll lives on the well above.
              maxWidth: "none",
              maxHeight: "none",
            }}
          />
        )}
      </div>
      <div className="text-[10px] font-mono text-wasteland-300 flex flex-col gap-0.5">
        <span>frame {frame.index}</span>
        <span>
          {frame.width}×{frame.height} px
          {scale > 1 && (
            <span className="text-wasteland-500"> · @ {scale}×</span>
          )}
        </span>
        <span>
          offset ({frame.offset_x}, {frame.offset_y})
        </span>
      </div>
      <div className="flex gap-1">
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="flex-1 text-[10px] rounded border border-wasteland-700 px-1.5 py-0.5 hover:border-rust-500 disabled:opacity-50"
          title="Pick a destination and save this frame as a .png file"
        >
          {save.isPending ? "Saving..." : "Save as PNG..."}
        </button>
        <button
          type="button"
          onClick={() => copy.mutate()}
          disabled={!blobData || copy.isPending}
          className="flex-1 text-[10px] rounded border border-wasteland-700 px-1.5 py-0.5 hover:border-rust-500 disabled:opacity-50"
          title="Copy this frame's PNG to the system clipboard"
        >
          {copiedAt
            ? "Copied!"
            : copy.isPending
              ? "Copying..."
              : "Copy PNG"}
        </button>
      </div>
      {save.isError && (
        <span className="text-[10px] text-rust-300">
          {String(save.error instanceof Error ? save.error.message : save.error)}
        </span>
      )}
      {save.data && !save.isError && (
        <span className="text-[10px] text-emerald-300 truncate" title={save.data.out_path}>
          Saved
        </span>
      )}
      {copy.isError && (
        <span className="text-[10px] text-rust-300" title={String(copy.error)}>
          Clipboard write failed
        </span>
      )}
    </div>
  );
}

// ─── JSD viewer (read-only) ───────────────────────────────────────────

function JsdViewerPanel({ stiPath }: { stiPath: string }) {
  const jsd = useQuery({
    queryKey: ["tools", "sti-jsd", stiPath],
    queryFn: () => getStiJsd(stiPath),
    retry: false,
    staleTime: 30 * 1000,
  });

  if (jsd.isLoading) {
    return <div className="card text-sm text-wasteland-300">Loading JSD…</div>;
  }
  if (jsd.isError) {
    return (
      <div className="card text-sm text-amber-300">
        Couldn't parse JSD:{" "}
        {String(jsd.error instanceof Error ? jsd.error.message : jsd.error)}
      </div>
    );
  }
  if (!jsd.data) return null;
  const parsed = jsd.data;
  return (
    <div className="card">
      <h2 className="text-base font-semibold mb-1">JSD companion (read-only)</h2>
      <div className="mb-2 truncate font-mono text-[10px] text-wasteland-500" title={parsed.jsd_path}>
        {parsed.jsd_path}
      </div>
      <JsdHeader parsed={parsed} />
      <details open className="mt-3">
        <summary className="cursor-pointer text-sm text-wasteland-200">
          Footprint tiles ({parsed.tiles.length})
        </summary>
        <div className="mt-2 space-y-1.5">
          {parsed.tiles.map((tile, i) => (
            <JsdTileRow key={i} tile={tile} index={i} />
          ))}
        </div>
      </details>
    </div>
  );
}

function JsdHeader({ parsed }: { parsed: ToolsJsdParsed }) {
  return (
    <div className="space-y-2 text-xs">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-wasteland-500">
          Flags{" "}
          <span className="font-mono normal-case text-wasteland-600">
            0x{parsed.flags_int.toString(16).padStart(4, "0").toUpperCase()}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap gap-1">
          {JSD_FLAG_BITS.map(({ bit, name }) => {
            const set = (parsed.flags_int & bit) !== 0;
            return (
              <span
                key={bit}
                title={`bit 0x${bit.toString(16).toUpperCase()} (${bit})`}
                className={`rounded border px-1.5 py-0.5 text-[10px] ${
                  set
                    ? "border-emerald-600 bg-emerald-900/50 text-emerald-200"
                    : "border-wasteland-700 bg-wasteland-900 text-wasteland-500"
                }`}
              >
                {name}
              </span>
            );
          })}
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <ReadOnlyStat label="ubArmour" value={parsed.ubArmour} />
        <ReadOnlyStat label="ubHP" value={parsed.ubHP} />
        <ReadOnlyStat label="ubDensity" value={parsed.ubDensity} />
        <ReadOnlyStat label="ubNumberOfTiles" value={parsed.ubNumberOfTiles} />
        <ReadOnlyStat label="bZTileOffsetX" value={parsed.bZTileOffsetX} />
        <ReadOnlyStat label="bZTileOffsetY" value={parsed.bZTileOffsetY} />
        <ReadOnlyStat label="struct_data_size" value={parsed.struct_data_size} />
        <ReadOnlyStat label="n_struct" value={parsed.n_struct} />
      </div>
    </div>
  );
}

function ReadOnlyStat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-wasteland-500">
        {label}
      </div>
      <div className="font-mono text-sm">{value}</div>
    </div>
  );
}

function JsdTileRow({ tile, index }: { tile: ToolsJsdProfileTile; index: number }) {
  return (
    <div className="rounded border border-wasteland-800 bg-wasteland-950/60 p-2">
      <div className="flex items-baseline gap-3 text-[11px] font-mono text-wasteland-300">
        <span>tile {index}</span>
        <span>bX={tile.bXPos}</span>
        <span>bY={tile.bYPos}</span>
        <span>sPos={tile.sPosRelToBase}</span>
      </div>
      <div className="mt-1">
        <ProfileGridReadonly grid={tile.profile} />
      </div>
    </div>
  );
}

function ProfileGridReadonly({ grid }: { grid: number[][] }) {
  return (
    <div className="inline-grid grid-cols-5 gap-px rounded border border-wasteland-700 bg-wasteland-900 p-px">
      {grid.flatMap((row, r) =>
        row.map((v, c) => {
          const intensity = Math.min(255, v) / 255;
          const bg = `rgba(110, 231, 183, ${0.05 + intensity * 0.6})`;
          return (
            <div
              key={`${r}-${c}`}
              title={`profile[${r}][${c}] = ${v}`}
              style={{ backgroundColor: bg }}
              className="flex h-5 w-5 items-center justify-center font-mono text-[8px] text-wasteland-200"
            >
              {v}
            </div>
          );
        }),
      )}
    </div>
  );
}
