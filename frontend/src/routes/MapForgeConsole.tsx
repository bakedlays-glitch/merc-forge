/**
 * MapForge command console.
 *
 * Vim-style command bar toggled by `:` (configurable later via the
 * keybinding system). Provides keyboard-driven access to MapForge
 * operations + generator invocation.
 *
 * Architecture:
 *   - Commands registered via `registerCommand({name, args, handler})`
 *     — a plain object table, no class hierarchy.
 *   - Tab-completion: when the user has typed `:gen ` and presses Tab,
 *     the completion engine lists matching command names. After the
 *     command name, completion delegates to the command's `complete`
 *     callback (for generator name autocomplete, etc.).
 *   - History: ↑/↓ recalls prior commands within the session.
 *     Persisted to localStorage on submit so reloads keep the buffer.
 *
 * The console is a peer to the existing log panel — they share screen
 * real estate at the bottom of the editor. Console takes focus on
 * open; the existing keybindings (Ctrl+Z etc.) keep working when the
 * console isn't open.
 *
 * The command implementations are deliberately thin wrappers around
 * existing API calls + state mutations — the console isn't a
 * scripting language, it's a keyboard shortcut for things the GUI
 * already does. The one exception is `:gen <name> k=v ...` which
 * triggers a streaming generator run.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { useMapForgeLog } from "./MapForgeLog";

/** One parsed token from the command line. Quoted strings + key=value
 * pairs both surface as `{kind: "arg", value: ...}`; positional and
 * keyword args mix freely. */
export type ParsedArg =
  | { kind: "positional"; value: string }
  | { kind: "keyword"; key: string; value: string };

export interface ParsedCommand {
  name: string;
  args: ParsedArg[];
  /** Raw input minus the leading `:`. Surfaces to commands that want
   *  to do their own parsing (e.g. `:py <expr>` taking everything as
   *  a single Python expression). */
  raw: string;
}

export interface CommandContext {
  log: ReturnType<typeof useMapForgeLog>;
  /** Add a one-shot string to the console's output stream. Convenience
   *  wrapper around log.append — most commands prefer this since the
   *  log panel and console output share the same render. */
  print(text: string, severity?: "info" | "success" | "warn" | "error"): void;
}

export interface CommandSpec {
  /** The token after the leading `:`. Case-insensitive on lookup. */
  name: string;
  /** Short one-line description shown in the autocomplete suggestion. */
  summary: string;
  /** Optional longer help text shown via `:help <name>`. */
  help?: string;
  /** Async handler. Errors are caught and surfaced via `ctx.print`. */
  handler(parsed: ParsedCommand, ctx: CommandContext): Promise<void> | void;
  /** Optional completion callback for tab-complete. Receives the
   *  partial text AFTER the command name + space. Returns a list of
   *  candidate completions. */
  complete?(partial: string): string[];
}

const HISTORY_KEY = "mapforge.console.history";
const HISTORY_MAX = 200;

/** Persist + load console history across sessions. */
function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(-HISTORY_MAX) : [];
  } catch {
    return [];
  }
}
function saveHistory(history: string[]) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-HISTORY_MAX)));
  } catch {
    // localStorage quota / privacy mode — silently no-op
  }
}

/**
 * Parse a console input line into a structured command.
 *
 * Grammar (intentionally minimal):
 *   :name [pos ...] [k=v ...]
 *
 *   - `name` is the first token after `:`.
 *   - Subsequent tokens with no `=` are positional args.
 *   - Tokens with `=` are keyword args. RHS may be quoted with `"..."`.
 *   - Quoted values support `\"` escape; everything else is literal.
 *
 * Returns null if the input is empty or doesn't start with `:`.
 */
export function parseCommandLine(input: string): ParsedCommand | null {
  let line = input.trim();
  if (line.startsWith(":")) line = line.slice(1);
  if (!line) return null;

  const tokens: string[] = [];
  let i = 0;
  while (i < line.length) {
    // Skip whitespace between tokens
    while (i < line.length && /\s/.test(line[i]!)) i++;
    if (i >= line.length) break;
    let token = "";
    // Walk a single token, accounting for quoted RHS after a `=`.
    while (i < line.length && !/\s/.test(line[i]!)) {
      const ch = line[i]!;
      if (ch === '"' && (token.length === 0 || token.includes("="))) {
        // Open quote — consume until matching close quote
        i++;
        while (i < line.length && line[i] !== '"') {
          if (line[i] === "\\" && line[i + 1] === '"') {
            token += '"';
            i += 2;
          } else {
            token += line[i];
            i++;
          }
        }
        i++; // skip closing quote (or EOF)
        continue;
      }
      token += ch;
      i++;
    }
    if (token) tokens.push(token);
  }

  if (tokens.length === 0) return null;
  const name = tokens[0]!;
  const args: ParsedArg[] = [];
  for (let idx = 1; idx < tokens.length; idx++) {
    const t = tokens[idx]!;
    const eq = t.indexOf("=");
    if (eq > 0) {
      args.push({
        kind: "keyword",
        key: t.slice(0, eq),
        value: t.slice(eq + 1),
      });
    } else {
      args.push({ kind: "positional", value: t });
    }
  }
  return { name, args, raw: line };
}

/**
 * Pluck a typed keyword arg from a parsed command.
 * Returns undefined when the arg is missing OR can't be coerced.
 */
export function pickKw<T extends "int" | "float" | "str" | "bool">(
  parsed: ParsedCommand,
  key: string,
  kind: T,
): T extends "int" | "float" ? number | undefined
  : T extends "bool" ? boolean | undefined
  : string | undefined {
  const arg = parsed.args.find(
    (a) => a.kind === "keyword" && a.key === key,
  ) as Extract<ParsedArg, { kind: "keyword" }> | undefined;
  if (!arg) return undefined as never;
  const raw = arg.value;
  if (kind === "int") {
    const n = parseInt(raw, 10);
    return (Number.isFinite(n) ? n : undefined) as never;
  }
  if (kind === "float") {
    const n = parseFloat(raw);
    return (Number.isFinite(n) ? n : undefined) as never;
  }
  if (kind === "bool") {
    if (raw === "true" || raw === "1" || raw === "yes") return true as never;
    if (raw === "false" || raw === "0" || raw === "no") return false as never;
    return undefined as never;
  }
  return raw as never;
}

interface ConsoleProps {
  open: boolean;
  onClose(): void;
  commands: CommandSpec[];
}

export default function MapForgeConsole({ open, onClose, commands }: ConsoleProps) {
  const log = useMapForgeLog();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [value, setValue] = useState("");
  const [history, setHistory] = useState<string[]>(() => loadHistory());
  // Position into history during ↑/↓ navigation. -1 = "not browsing,
  // showing live input". 0+ = recalled entry from `history`.
  const [historyPos, setHistoryPos] = useState<number>(-1);
  // Tab-completion cycle index — bumps each Tab, resets on any other key.
  const [completionCycle, setCompletionCycle] = useState<{
    matches: string[]; index: number; prefix: string;
  } | null>(null);

  const print = useCallback((text: string, severity: "info" | "success" | "warn" | "error" = "info") => {
    log?.append({ severity, message: text });
  }, [log]);

  // Build the command lookup once per render; small dict, no perf concern.
  const cmdByName = useCallback(() => {
    const m = new Map<string, CommandSpec>();
    for (const c of commands) m.set(c.name.toLowerCase(), c);
    return m;
  }, [commands]);

  // Focus the input when the console opens.
  useEffect(() => {
    if (open) {
      // Settle into the next frame so the autofocus + selection-set
      // don't fight the previous focus handoff (the `:` keydown that
      // opened us).
      const id = window.requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
      return () => window.cancelAnimationFrame(id);
    } else {
      setValue("");
      setHistoryPos(-1);
      setCompletionCycle(null);
    }
    return undefined;
  }, [open]);

  const submit = useCallback(async () => {
    const text = value.trim();
    if (!text) {
      onClose();
      return;
    }
    // Push to history (dedupe consecutive duplicates).
    const newHistory = [...history];
    if (newHistory[newHistory.length - 1] !== text) {
      newHistory.push(text);
      if (newHistory.length > HISTORY_MAX) newHistory.shift();
    }
    setHistory(newHistory);
    saveHistory(newHistory);

    const parsed = parseCommandLine(text);
    if (!parsed) {
      print("(empty command)", "warn");
      onClose();
      return;
    }
    const cmd = cmdByName().get(parsed.name.toLowerCase());
    if (!cmd) {
      print(`Unknown command: ${parsed.name}. Type :help for a list.`, "error");
      onClose();
      return;
    }
    print(`> :${text}`, "info");
    try {
      await cmd.handler(parsed, { log, print });
    } catch (err) {
      print(
        `:${parsed.name} failed: ${err instanceof Error ? err.message : String(err)}`,
        "error",
      );
    }
    onClose();
  }, [value, history, cmdByName, print, log, onClose]);

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      void submit();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (history.length === 0) return;
      const next = historyPos < 0
        ? history.length - 1
        : Math.max(0, historyPos - 1);
      setHistoryPos(next);
      setValue(history[next] ?? "");
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (historyPos < 0) return;
      const next = historyPos + 1;
      if (next >= history.length) {
        setHistoryPos(-1);
        setValue("");
      } else {
        setHistoryPos(next);
        setValue(history[next] ?? "");
      }
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      // Two-stage completion: command name if we haven't hit a space
      // yet; delegated to the command's `complete` callback otherwise.
      const text = value;
      const firstSpace = text.indexOf(" ");
      if (firstSpace === -1) {
        // Complete command name
        const prefix = text.toLowerCase();
        const matches = commands
          .map((c) => c.name)
          .filter((n) => n.toLowerCase().startsWith(prefix));
        if (matches.length === 0) return;
        const cycle = completionCycle && completionCycle.prefix === prefix
          ? { ...completionCycle, index: (completionCycle.index + 1) % completionCycle.matches.length }
          : { matches, index: 0, prefix };
        setCompletionCycle(cycle);
        setValue(cycle.matches[cycle.index]!);
        return;
      }
      // Delegated completion for the args portion.
      const namePart = text.slice(0, firstSpace);
      const argsPart = text.slice(firstSpace + 1);
      const cmd = cmdByName().get(namePart.toLowerCase());
      if (!cmd || !cmd.complete) return;
      const matches = cmd.complete(argsPart);
      if (matches.length === 0) return;
      const key = `${namePart} ${argsPart}`;
      const cycle = completionCycle && completionCycle.prefix === key
        ? { ...completionCycle, index: (completionCycle.index + 1) % completionCycle.matches.length }
        : { matches, index: 0, prefix: key };
      setCompletionCycle(cycle);
      // Replace the last whitespace-separated token in argsPart with
      // the cycled match. Keeps `:gen wipe se` intact when cycling
      // through `seed=`/`seed_value` etc.
      const lastSpace = argsPart.lastIndexOf(" ");
      const head = lastSpace === -1 ? "" : argsPart.slice(0, lastSpace + 1);
      setValue(`${namePart} ${head}${cycle.matches[cycle.index]}`);
      return;
    }
    // Any other key invalidates the completion cycle.
    setCompletionCycle(null);
    if (historyPos !== -1 && (e.key.length === 1 || e.key === "Backspace")) {
      // User started editing — exit history-recall mode but keep the value.
      setHistoryPos(-1);
    }
  }, [value, history, historyPos, completionCycle, commands, cmdByName, submit, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-label="MapForge command console"
      className="fixed bottom-0 left-0 right-0 z-50 border-t border-rust-500/60 bg-wasteland-950/95 backdrop-blur shadow-2xl"
    >
      <div className="mx-auto max-w-4xl px-3 py-2 flex items-center gap-2">
        <span className="text-rust-400 font-mono select-none">:</span>
        <input
          ref={inputRef}
          type="text"
          spellCheck={false}
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          className="flex-1 bg-transparent border-none outline-none font-mono text-sm text-wasteland-100 placeholder:text-wasteland-600"
          placeholder="type a command, e.g. gen wipe — Esc closes, Tab autocompletes, ↑↓ history"
        />
        <span className="text-[10px] text-wasteland-600 font-mono select-none">
          {commands.length} cmds
        </span>
      </div>
    </div>
  );
}
