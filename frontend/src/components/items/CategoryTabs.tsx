// frontend/src/components/items/CategoryTabs.tsx
import { useRef } from "react";

export interface CategoryEntry {
  key: string;
  label: string;
  count: number;
}

interface Props {
  categories: CategoryEntry[];
  active: string;
  onSelect: (key: string) => void;
}

/**
 * Horizontal tab strip for item categories.
 * - Renders an "All" pseudo-tab that sums all counts.
 * - Arrow-key navigation moves focus between tabs.
 * - active tab gets `aria-selected="true"` and a rust accent.
 */
export default function CategoryTabs({ categories, active, onSelect }: Props) {
  const listRef = useRef<HTMLDivElement>(null);

  const totalCount = categories.reduce((s, c) => s + c.count, 0);

  const allTabs = [
    { key: "all", label: "All", count: totalCount },
    ...categories,
  ];

  function handleKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, idx: number) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>("button[role='tab']");
    if (!buttons || buttons.length === 0) return;
    const next = e.key === "ArrowRight"
      ? (idx + 1) % buttons.length
      : (idx - 1 + buttons.length) % buttons.length;
    buttons[next]?.focus();
    const nextTab = allTabs[next];
    if (nextTab) onSelect(nextTab.key);
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label="Item categories"
      className="flex flex-wrap gap-1 mb-2"
    >
      {allTabs.map((tab, idx) => {
        const isActive = tab.key === active;
        return (
          <button
            key={tab.key}
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onSelect(tab.key)}
            onKeyDown={(e) => handleKeyDown(e, idx)}
            className={`px-2 py-0.5 text-xs rounded-md border transition-colors ${
              isActive
                ? "border-rust-500 bg-rust-500/20 text-rust-300 font-medium"
                : "border-wasteland-700 bg-wasteland-900 text-wasteland-400 hover:bg-wasteland-800 hover:text-wasteland-200"
            }`}
          >
            {tab.label} ({tab.count})
          </button>
        );
      })}
    </div>
  );
}
