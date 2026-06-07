import { useEffect, useMemo, useState } from "react";
import { loadSchema } from "../api/launcher";
import type { SchemaDoc, SchemaSection, SchemaProperty } from "../types/modpack";
import { PropertyCard } from "./PropertyCard";

interface Props {
  folder: string;
  /// "Ja2_Options.ini" — drives which schema to load
  iniFile: string;
  onError: (msg: string) => void;
}

export function SettingsBrowser({ folder, iniFile, onError }: Props) {
  const [schema, setSchema] = useState<SchemaDoc | null>(null);
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const s = await loadSchema(folder, iniFile);
        setSchema(s);
        if (s.sections.length > 0) setActiveSection(s.sections[0].name);
      } catch (e) {
        onError(String(e));
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder, iniFile]);

  // Global search across all sections + properties (matches name or description)
  const searchResults = useMemo(() => {
    if (!schema || !search.trim()) return null;
    const q = search.toLowerCase();
    const results: Array<{ section: SchemaSection; prop: SchemaProperty }> = [];
    for (const sect of schema.sections) {
      for (const prop of sect.properties) {
        if (
          prop.name.toLowerCase().includes(q) ||
          prop.description.toLowerCase().includes(q) ||
          sect.name.toLowerCase().includes(q)
        ) {
          results.push({ section: sect, prop });
        }
      }
    }
    return results;
  }, [schema, search]);

  if (loading) {
    return <p className="text-ja2-dim text-sm p-4">Loading schema…</p>;
  }
  if (!schema) {
    return <p className="text-ja2-danger text-sm p-4">No schema loaded.</p>;
  }

  const totalProps = schema.sections.reduce(
    (n, s) => n + s.properties.length,
    0
  );
  const currentSection = schema.sections.find((s) => s.name === activeSection);

  return (
    <div className="flex flex-col h-full min-h-[400px] border border-ja2-border rounded overflow-hidden">
      {/* Top bar: search + totals + source */}
      <div className="px-3 py-2 border-b border-ja2-border flex items-center gap-3 bg-ja2-bg">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={`Search ${totalProps} settings across ${schema.sections.length} sections…`}
          className="flex-1 bg-ja2-panel border border-ja2-border rounded px-2 py-1 text-sm
                     focus:outline-none focus:border-ja2-accent"
        />
        <span className="text-xs text-ja2-dim whitespace-nowrap">
          {schema.ini_file} ·{" "}
          <span className={schema.source === "modpack" ? "text-ja2-accent" : ""}>
            {schema.source}
          </span>
        </span>
      </div>

      {/* Body: search results OR sidebar + section view */}
      <div className="flex-1 flex overflow-hidden">
        {searchResults != null ? (
          <SearchResultList
            results={searchResults}
            folder={folder}
            iniFile={iniFile}
            onError={onError}
          />
        ) : (
          <>
            <SectionSidebar
              sections={schema.sections}
              activeName={activeSection}
              onSelect={setActiveSection}
            />
            <div className="flex-1 overflow-y-auto p-4">
              {currentSection ? (
                <SectionPanel
                  section={currentSection}
                  folder={folder}
                  iniFile={iniFile}
                  onError={onError}
                />
              ) : (
                <p className="text-ja2-dim text-sm">Pick a section.</p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function SectionSidebar({
  sections,
  activeName,
  onSelect,
}: {
  sections: SchemaSection[];
  activeName: string | null;
  onSelect: (name: string) => void;
}) {
  return (
    <nav className="w-64 border-r border-ja2-border overflow-y-auto bg-ja2-bg">
      {sections.map((s) => {
        const active = s.name === activeName;
        return (
          <button
            key={s.name}
            onClick={() => onSelect(s.name)}
            className={[
              "w-full text-left px-3 py-1.5 text-sm border-l-2 transition-colors",
              active
                ? "border-ja2-accent bg-ja2-panel text-ja2-accent"
                : "border-transparent text-ja2-text hover:bg-ja2-panel",
            ].join(" ")}
          >
            <div className="truncate">{s.name}</div>
            <div className="text-xs text-ja2-dim">{s.properties.length} keys</div>
          </button>
        );
      })}
    </nav>
  );
}

function SectionPanel({
  section,
  folder,
  iniFile,
  onError,
}: {
  section: SchemaSection;
  folder: string;
  iniFile: string;
  onError: (msg: string) => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <header>
        <h3 className="text-base font-semibold text-ja2-accent">{section.name}</h3>
        {section.description && (
          <p className="text-xs text-ja2-dim mt-1 whitespace-pre-line">
            {section.description}
          </p>
        )}
        <p className="text-xs text-ja2-dim mt-1">
          {section.properties.length} settings
        </p>
      </header>
      <div className="flex flex-col gap-3">
        {section.properties.map((p) => (
          <PropertyCard
            key={p.name}
            section={section.name}
            prop={p}
            folder={folder}
            iniFile={iniFile}
            onError={onError}
          />
        ))}
      </div>
    </div>
  );
}

function SearchResultList({
  results,
  folder,
  iniFile,
  onError,
}: {
  results: Array<{ section: SchemaSection; prop: SchemaProperty }>;
  folder: string;
  iniFile: string;
  onError: (msg: string) => void;
}) {
  return (
    <div className="flex-1 overflow-y-auto p-4">
      <p className="text-xs text-ja2-dim mb-3">{results.length} matches</p>
      <div className="flex flex-col gap-3">
        {results.slice(0, 200).map(({ section, prop }) => (
          <PropertyCard
            key={`${section.name}/${prop.name}`}
            section={section.name}
            prop={prop}
            folder={folder}
            iniFile={iniFile}
            onError={onError}
            showSectionContext
          />
        ))}
        {results.length > 200 && (
          <p className="text-xs text-ja2-dim italic">
            Showing first 200 of {results.length}. Narrow the search to see more.
          </p>
        )}
      </div>
    </div>
  );
}

// PropertyCard + PropertyEditor live in ./PropertyCard.tsx (shared with WizardTab).
