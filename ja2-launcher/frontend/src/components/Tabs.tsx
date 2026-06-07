// Pure-React tab navigation. Portable to MercForge.

interface TabDef {
  id: string;
  label: string;
  badge?: string | number;        // optional dot/count next to label (e.g. error count)
  badgeKind?: "info" | "warn" | "danger";
}

interface Props {
  tabs: TabDef[];
  activeId: string;
  onChange: (id: string) => void;
}

export function Tabs({ tabs, activeId, onChange }: Props) {
  return (
    <nav className="flex border-b border-ja2-border" role="tablist">
      {tabs.map((t) => {
        const active = t.id === activeId;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.id)}
            className={[
              "px-5 py-2.5 text-sm transition-colors border-b-2 -mb-px",
              active
                ? "border-ja2-accent text-ja2-accent"
                : "border-transparent text-ja2-dim hover:text-ja2-text",
            ].join(" ")}
          >
            {t.label}
            {t.badge != null && t.badge !== 0 && t.badge !== "" && (
              <span
                className={[
                  "ml-2 px-1.5 py-0.5 rounded text-xs",
                  t.badgeKind === "danger"
                    ? "bg-ja2-danger text-ja2-bg"
                    : t.badgeKind === "warn"
                      ? "bg-ja2-accent text-ja2-bg"
                      : "bg-ja2-border text-ja2-text",
                ].join(" ")}
              >
                {t.badge}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
