/** Wiki-style markup for FieldHelp: [[term]], **bold**, *italic*, `code`, ## headings, - bullets. */

export type InlinePart =
  | { type: "text"; value: string }
  | { type: "term"; id: string; label: string }
  | { type: "bold"; value: string }
  | { type: "italic"; value: string }
  | { type: "code"; value: string };

export type BlockPart =
  | { type: "heading"; level: 2 | 3; children: InlinePart[] }
  | { type: "bullet"; children: InlinePart[] }
  | { type: "para"; children: InlinePart[] };

const INLINE_RE = /\[\[([^|\]]+)(?:\|([^\]]+))?\]\]|\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`/g;

export function parseInline(text: string): InlinePart[] {
  const parts: InlinePart[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) parts.push({ type: "text", value: text.slice(last, idx) });
    if (m[1] != null) {
      parts.push({ type: "term", id: m[1].trim(), label: (m[2] ?? m[1]).trim() });
    } else if (m[3] != null) {
      parts.push({ type: "bold", value: m[3] });
    } else if (m[4] != null) {
      parts.push({ type: "italic", value: m[4] });
    } else if (m[5] != null) {
      parts.push({ type: "code", value: m[5] });
    }
    last = idx + m[0].length;
  }
  if (last < text.length) parts.push({ type: "text", value: text.slice(last) });
  if (parts.length === 0) parts.push({ type: "text", value: text });
  return parts;
}

export function parseHelpMarkup(text: string): BlockPart[] {
  const blocks: BlockPart[] = [];
  for (const raw of text.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (line.trim() === "") continue;
    if (line.startsWith("### ")) {
      blocks.push({ type: "heading", level: 3, children: parseInline(line.slice(4)) });
    } else if (line.startsWith("## ")) {
      blocks.push({ type: "heading", level: 2, children: parseInline(line.slice(3)) });
    } else if (line.startsWith("- ") || line.startsWith("• ")) {
      blocks.push({ type: "bullet", children: parseInline(line.slice(2)) });
    } else {
      blocks.push({ type: "para", children: parseInline(line) });
    }
  }
  if (blocks.length === 0) blocks.push({ type: "para", children: parseInline(text) });
  return blocks;
}

function stripInline(parts: InlinePart[]): string {
  return parts.map((p) => (p.type === "term" ? p.label : p.value)).join("");
}

export function stripHelpMarkup(text: string): string {
  return parseHelpMarkup(text)
    .map((b) => {
      const inner = stripInline(b.children);
      if (b.type === "bullet") return `• ${inner}`;
      return inner;
    })
    .join("\n");
}
