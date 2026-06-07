import { Link } from "react-router-dom";

/**
 * Tools index — surface for install-independent modder utilities.
 *
 * Each tool is a self-contained route that picks its own input file.
 * Room to grow: more tools (texture browser, MERC bio inspector, etc.)
 * can land here without touching the Hub.
 */
const tools = [
  {
    id: "sti-viewer",
    label: "STI Viewer",
    href: "/tools/sti-viewer",
    description:
      "Open any .sti file and inspect its frames, palette, offsets, and JSD companion. " +
      "Works on STIs that aren't registered in any tileset — drop in a file from anywhere.",
  },
  {
    id: "slf-extractor",
    label: "SLF Extractor",
    href: "/tools/slf-extractor",
    description:
      "Crack open a .slf archive and pull out files. Pick which entries to extract, " +
      "or take the whole archive. Useful for harvesting assets from third-party mods.",
  },
];

export default function Tools() {
  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Tools</h1>
          <p className="text-sm text-wasteland-300 mt-1">
            Standalone utilities — open a file from anywhere and inspect it.
          </p>
        </div>
        <Link to="/" className="text-sm text-wasteland-400 hover:text-rust-400">
          ← Hub
        </Link>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {tools.map((t) => (
          <Link
            key={t.id}
            to={t.href}
            className="card flex flex-col gap-3 hover:border-rust-500 transition-colors group min-h-[10rem]"
          >
            <div className="flex items-center justify-between">
              <span className="text-xl font-semibold group-hover:text-rust-400 transition-colors">
                {t.label}
              </span>
              <div className="text-rust-400 opacity-0 group-hover:opacity-100 transition-opacity">
                →
              </div>
            </div>
            <div className="text-sm text-wasteland-300 flex-1">{t.description}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
