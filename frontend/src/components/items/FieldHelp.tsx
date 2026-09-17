import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { GLOSSARY, type GlossaryEntry } from "../../lib/glossary";
import { parseHelpMarkup, stripHelpMarkup, type InlinePart } from "../../lib/helpMarkup";

interface Props {
  help: string;
}

interface Card {
  title: string;
  body: string;
}

/**
 * "?" help that can freeze (click / Shift+click / F) so the text is
 * selectable, plus [[term]] cross-links that open nested glossary cards.
 */
export default function FieldHelp({ help }: Props) {
  const uid = useId();
  const wrapRef = useRef<HTMLSpanElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [stack, setStack] = useState<Card[]>([]);
  const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(null);
  const [copied, setCopied] = useState(false);

  const showing = hover || pinned;
  const cards: Card[] = stack.length > 0
    ? stack
    : showing
      ? [{ title: "Help", body: help }]
      : [];

  function syncAnchor() {
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const width = 384;
    const left = r.right + 8 + width > window.innerWidth
      ? Math.max(8, r.left - width - 8)
      : r.right + 8;
    const top = Math.min(Math.max(8, r.top), window.innerHeight - 120);
    setAnchor({ top, left });
  }

  function pin(extra?: Card) {
    syncAnchor();
    setPinned(true);
    setStack((prev) => {
      const base = prev.length > 0 ? prev : [{ title: "Help", body: help }];
      if (!extra) return base;
      if (base.some((c) => c.title === extra.title && c.body === extra.body)) return base;
      return [...base, extra];
    });
  }

  function openTerm(id: string) {
    const g: GlossaryEntry | undefined = GLOSSARY[id];
    if (!g) return;
    pin({ title: g.title, body: g.body });
  }

  function unpinAll() {
    setPinned(false);
    setStack([]);
    setHover(false);
    setCopied(false);
  }

  function pop() {
    setStack((prev) => {
      if (prev.length <= 1) {
        setPinned(false);
        setHover(false);
        return [];
      }
      return prev.slice(0, -1);
    });
  }

  useLayoutEffect(() => {
    if (!pinned) return;
    syncAnchor();
    const sync = () => syncAnchor();
    window.addEventListener("scroll", sync, true);
    window.addEventListener("resize", sync);
    return () => {
      window.removeEventListener("scroll", sync, true);
      window.removeEventListener("resize", sync);
    };
  }, [pinned, stack.length]);

  useEffect(() => {
    if (!showing) return;
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
        || t.tagName === "SELECT" || t.isContentEditable);
      if (e.key === "Escape") {
        e.stopPropagation();
        pop();
        return;
      }
      if (typing) return;
      if (e.key === "f" || e.key === "F") {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        e.preventDefault();
        pin();
      }
    }
    function onPointerDown(e: PointerEvent) {
      const n = e.target as Node | null;
      if (wrapRef.current?.contains(n)) return;
      if (panelRef.current?.contains(n)) return;
      unpinAll();
    }
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, [showing, help, pinned]);

  async function copyCard(body: string) {
    try {
      await navigator.clipboard.writeText(stripHelpMarkup(body));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch { /* clipboard may be denied */ }
  }

  const preview = !pinned && hover;

  return (
    <span
      ref={wrapRef}
      className="relative inline-flex"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => { if (!pinned) setHover(false); }}
    >
      <button
        ref={btnRef}
        type="button"
        aria-label="Field help"
        aria-expanded={showing}
        aria-describedby={preview ? uid : undefined}
        className={`text-[10px] leading-none w-3.5 h-3.5 rounded-full border outline-none ${
          pinned
            ? "border-rust-400 text-rust-300"
            : "border-wasteland-600 text-wasteland-400 hover:text-rust-300 hover:border-rust-400 focus:text-rust-300 focus:border-rust-400"
        }`}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          if (pinned && stack.length <= 1) unpinAll();
          else pin();
        }}
      >
        ?
      </button>
      {preview && (
        <span
          id={uid}
          role="tooltip"
          className="absolute left-4 top-0 z-30 w-72 font-help whitespace-normal rounded border border-wasteland-600 bg-wasteland-900 px-2 py-1.5 text-left text-[11px] leading-snug text-wasteland-200 shadow-lg"
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); pin(); }}
        >
          <HelpBody text={help} onTerm={openTerm} />
          <span className="mt-1 block text-[10px] text-wasteland-500">
            Click or F to freeze · click a term for more
          </span>
        </span>
      )}
      {pinned && anchor && createPortal(
        <div
          ref={panelRef}
          className="fixed z-[80] flex flex-col gap-2"
          style={{ top: anchor.top, left: anchor.left }}
          onPointerDown={(e) => e.stopPropagation()}
        >
          {cards.map((c, i) => (
            <div
              key={`${c.title}-${i}`}
              className="w-96 max-h-[min(70vh,36rem)] overflow-y-auto rounded-md border border-rust-500/45 bg-wasteland-900/95 px-3 py-2 text-left font-help text-[12px] leading-relaxed text-wasteland-200 shadow-xl"
              style={{ marginLeft: i * 14 }}
            >
              <div className="mb-1.5 flex items-baseline justify-between gap-2 border-b border-wasteland-700 pb-1">
                <span className="font-serif text-[13px] font-semibold tracking-wide text-rust-400 truncate">{c.title}</span>
                <span className="flex items-center gap-2 shrink-0 font-help text-[10px] uppercase tracking-wider text-wasteland-500">
                  <button
                    type="button"
                    className="hover:text-rust-300"
                    onClick={() => void copyCard(c.body)}
                  >
                    {copied ? "Copied" : "Copy"}
                  </button>
                  <button type="button" className="hover:text-rust-300" onClick={pop}>
                    {i === 0 && cards.length === 1 ? "Close" : "Back"}
                  </button>
                </span>
              </div>
              <div className="select-text cursor-text">
                <HelpBody text={c.body} onTerm={openTerm} />
              </div>
              {i === cards.length - 1 && (
                <div className="mt-1 text-[10px] text-wasteland-500">
                  Shift+click a term to stack · Esc closes
                </div>
              )}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </span>
  );
}

function HelpBody({
  text,
  onTerm,
}: {
  text: string;
  onTerm: (id: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      {parseHelpMarkup(text).map((b, i) => {
        if (b.type === "heading") {
          return (
            <div
              key={i}
              className={
                b.level === 2
                  ? "mt-2 first:mt-0 font-serif text-[10px] font-semibold uppercase tracking-[0.16em] text-rust-400"
                  : "mt-1.5 font-serif text-[11px] italic text-wasteland-300"
              }
            >
              <Inline parts={b.children} onTerm={onTerm} />
            </div>
          );
        }
        if (b.type === "bullet") {
          return (
            <div key={i} className="flex gap-2">
              <span className="mt-[0.45rem] h-1.5 w-1.5 shrink-0 rounded-full bg-rust-400" />
              <span className="min-w-0">
                <Inline parts={b.children} onTerm={onTerm} />
              </span>
            </div>
          );
        }
        return (
          <p key={i} className="text-wasteland-200">
            <Inline parts={b.children} onTerm={onTerm} />
          </p>
        );
      })}
    </div>
  );
}

function Inline({
  parts,
  onTerm,
}: {
  parts: InlinePart[];
  onTerm: (id: string) => void;
}) {
  return (
    <>
      {parts.map((p, i) => {
        if (p.type === "text") return <span key={i}>{p.value}</span>;
        if (p.type === "bold") {
          return (
            <strong key={i} className="font-semibold text-wasteland-50">
              {p.value}
            </strong>
          );
        }
        if (p.type === "italic") {
          return (
            <em key={i} className="italic text-wasteland-300">
              {p.value}
            </em>
          );
        }
        if (p.type === "code") {
          return (
            <code
              key={i}
              className="rounded bg-wasteland-800 px-1 py-px font-mono text-[10px] text-amber-200/90"
            >
              {p.value}
            </code>
          );
        }
        const known = Boolean(GLOSSARY[p.id]);
        if (!known) return <span key={i}>{p.label}</span>;
        return (
          <button
            key={i}
            type="button"
            className="font-semibold text-rust-300 underline decoration-dotted underline-offset-2 hover:text-rust-200"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onTerm(p.id);
            }}
          >
            {p.label}
          </button>
        );
      })}
    </>
  );
}
