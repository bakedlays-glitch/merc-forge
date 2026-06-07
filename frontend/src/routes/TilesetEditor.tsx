/**
 * Tileset Editor — first screen.
 *
 * Lists every tileset block defined in the active install's
 * Ja2Set.dat.xml. Click a tileset to drop into the per-tileset editor
 * (`/tileset-editor/:tileset`), where the user can browse slots, view
 * the JSD, add STIs from the library, and inject sub-frames.
 *
 * See `docs/TILESET_EDITOR_SPLIT.md` for the design rationale.
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { formatApiError } from "../lib/api";
import {
  getMapForgeHealth,
  listInstallMaps,
  listTilesets,
  type TilesetInfo,
} from "../lib/mapforge";

export default function TilesetEditor() {
  const health = useQuery({
    queryKey: ["mapforge", "health"],
    queryFn: getMapForgeHealth,
  });
  // We piggyback on the existing install-maps endpoint to get the
  // active install's ja2set_xml path — no need for a duplicate active-
  // install lookup. The maps payload is cached on disk so this is
  // usually instant.
  const maps = useQuery({
    queryKey: ["mapforge", "installs", "maps"],
    queryFn: () => listInstallMaps(),
    enabled: health.data?.renderer_available === true
      && health.data.active_install_id !== null,
    staleTime: 5 * 60 * 1000,
  });
  const xmlPath = maps.data?.ja2set_xml ?? null;

  const tilesets = useQuery({
    queryKey: ["tileset-editor", "tilesets", xmlPath],
    queryFn: () => listTilesets(xmlPath!),
    enabled: !!xmlPath,
    staleTime: 60 * 1000,
  });

  const [filter, setFilter] = useState("");
  const filtered: TilesetInfo[] = useMemo(() => {
    if (!tilesets.data) return [];
    const needle = filter.trim().toLowerCase();
    if (!needle) return tilesets.data.tilesets;
    return tilesets.data.tilesets.filter((t) =>
      String(t.index).includes(needle)
      || (t.name ?? "").toLowerCase().includes(needle)
    );
  }, [tilesets.data, filter]);

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <Link to="/" className="text-sm text-blue-400 hover:underline">
            ← MercForge Hub
          </Link>
          <h1 className="mt-2 text-2xl font-semibold">Tileset Editor</h1>
          <p className="text-sm text-gray-400">
            Browse every tileset registered in the active install's
            <code className="mx-1">Ja2Set.dat.xml</code>. Pick one to add
            STIs from the library, inject sub-frames, or edit JSD
            companion files.
          </p>
        </div>
      </div>

      {/* Active-install / renderer gates */}
      {health.isLoading && (
        <p className="text-sm text-gray-400">Checking backend…</p>
      )}
      {health.data && !health.data.renderer_available && (
        <div className="rounded border border-amber-700 bg-amber-950 p-3 text-sm">
          <strong>MapForge backend not ready.</strong> The iso renderer
          isn't importable. The Tileset Editor uses the same backend as
          MapForge, so it can't run until the renderer's available.
        </div>
      )}
      {health.data?.renderer_available && health.data.active_install_id === null && (
        <div className="rounded border border-amber-700 bg-amber-950 p-3 text-sm">
          <strong>No active install.</strong> Activate one from the Hub
          (Settings → Switch install), then come back here.
        </div>
      )}
      {maps.isFetching && !maps.data && (
        <p className="text-sm text-gray-400">Locating Ja2Set.dat.xml…</p>
      )}
      {maps.data && !maps.data.ja2set_xml && (
        <div className="rounded border border-amber-700 bg-amber-950 p-3 text-sm">
          <strong>No Ja2Set.dat.xml in this install.</strong> The
          Tileset Editor reads tileset definitions from that file. The
          install at <code className="ml-1">{maps.data.install_path}</code>{" "}
          doesn't have one in any of its data layers ({maps.data.data_layers.join(", ")}).
        </div>
      )}
      {tilesets.error && (
        <div className="rounded border border-red-700 bg-red-950 p-3 text-sm">
          {formatApiError(tilesets.error)}
        </div>
      )}

      {tilesets.data && (
        <div>
          <div className="mb-3 flex items-center justify-between gap-3">
            <p className="text-xs text-gray-500">
              <span className="text-gray-300">
                {tilesets.data.tilesets.length} tilesets
              </span>
              {" "}in <code>{tilesets.data.xml_path}</code>
            </p>
          </div>
          <div className="mb-3">
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by index or name…"
              className="w-full max-w-md rounded border border-gray-700 bg-gray-900 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
          </div>

          {filtered.length === 0 && (
            <p className="text-sm text-gray-400">
              No tilesets match this filter.
            </p>
          )}

          <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
            {filtered.map((t) => (
              <Link
                key={t.index}
                to={`/tileset-editor/${t.index}`}
                className="rounded border border-gray-700 bg-gray-900 p-3 text-sm hover:border-blue-500 hover:bg-gray-800"
                title={`Open tileset ${t.index} for editing`}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-mono text-base text-blue-300">
                    Tileset {t.index}
                  </span>
                  <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                    {t.slot_count} slots
                  </span>
                </div>
                <div className="mt-0.5 truncate text-xs text-gray-400">
                  {t.name ?? <span className="italic text-gray-500">unnamed</span>}
                </div>
                {t.inherits_from_0 && (
                  <div className="mt-1 text-[10px] text-gray-500">
                    inherits from tileset 0
                  </div>
                )}
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
