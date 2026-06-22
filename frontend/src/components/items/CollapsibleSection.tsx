// frontend/src/components/items/CollapsibleSection.tsx
import { useState, type ReactNode } from "react";

interface Props {
  title: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

/**
 * A labelled disclosure section. Header button toggles visibility; `aria-expanded`
 * reflects state. Used to group the item edit fields (Identity / Economy /
 * Graphic / Advanced) with the niche group collapsed by default.
 */
export default function CollapsibleSection({ title, defaultOpen = true, children }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <fieldset className="border border-wasteland-700 rounded">
      <legend className="px-1">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1 text-xs text-wasteland-300 hover:text-wasteland-100 px-1"
        >
          <span className="text-wasteland-500">{open ? "▼" : "▶"}</span>
          {title}
        </button>
      </legend>
      {open && <div className="p-2">{children}</div>}
    </fieldset>
  );
}
