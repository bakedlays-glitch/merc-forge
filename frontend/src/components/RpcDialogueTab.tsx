/**
 * RPC recruitment + dialogue editor.
 *
 * Authors the <profile>.NPC recruit records + the pre-recruit and post-recruit
 * .EDT dialogue. Full control: multiple recruit branches (leadership / give-item
 * / fact-gated), all standard quote slots + custom lines, and post-recruit barks.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  formatApiError,
  getRpcDialogue,
  getRpcSmallFace,
  listRpcFactSetters,
  putRpcDialogue,
  removeRpcFactSetter,
  removeRpcSmallFace,
  setRpcFactSetter,
  setRpcSmallFace,
  type RpcBranch,
} from "../lib/api";
import type { Merc } from "../lib/schema";
import {
  canRemoveRecruitBranch,
  makeDefaultRpcBranch,
  nonEmptyQuoteIndices,
  referencedBlankAcceptQuotes,
} from "../lib/rpcWorkflow";
import { useDialog } from "./DialogProvider";

const APPROACH_RECRUIT = 4;
const APPROACH_GIVE_ITEM = 6;
const QUOTE_MAX = 239; // 240 UTF-16 units per slot minus the NUL terminator

export default function RpcDialogueTab({ merc }: { merc: Merc }) {
  const qc = useQueryClient();
  const profile = merc.uiIndex;
  const voice = merc.usVoiceIndex;

  const { data, isLoading, error } = useQuery({
    queryKey: ["rpc-dialogue", profile, voice],
    queryFn: () => getRpcDialogue(profile, voice),
  });

  const [branches, setBranches] = useState<RpcBranch[]>([]);
  const [pre, setPre] = useState<string[]>([]);
  const [post, setPost] = useState<string[]>([]);
  const [expandedPost, setExpandedPost] = useState(false);
  const [keptPostIndices, setKeptPostIndices] = useState<number[]>([]);
  const [labels, setLabels] = useState<string[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [allowOverwrite, setAllowOverwrite] = useState(false);

  // Seed local editable state once the file loads. Pad pre-quotes so all 10
  // standard slots are always editable.
  useEffect(() => {
    if (!data) return;
    setBranches(data.branches.length ? data.branches : [makeDefaultRpcBranch()]);
    const padded = [...data.pre_quotes];
    while (padded.length < data.standard_quote_labels.length) padded.push("");
    setPre(padded);
    setPost(data.post_quotes);
    setKeptPostIndices(nonEmptyQuoteIndices(data.post_quotes));
    setExpandedPost(false);
    setLabels(data.standard_quote_labels);
  }, [data]);

  const save = useMutation({
    mutationFn: () =>
      putRpcDialogue(
        profile,
        { voice_index: voice, branches, pre_quotes: pre, post_quotes: post },
        undefined,
        allowOverwrite,
      ),
    onSuccess: () => {
      setMsg("Saved — recruit logic + dialogue written.");
      qc.invalidateQueries({ queryKey: ["rpc-dialogue", profile, voice] });
      qc.invalidateQueries({ queryKey: ["rpc-readiness", profile] });
    },
    onError: (e) => setMsg(formatApiError(e)),
  });

  const tooLong = [...pre, ...post].some((q) => q.length > QUOTE_MAX);
  const blankAcceptQuotes = referencedBlankAcceptQuotes(branches, pre);
  const populatedPostCount = nonEmptyQuoteIndices(post).length;
  const visiblePostIndices = useMemo(() => {
    if (expandedPost) return post.map((_, index) => index);
    return [...new Set([...nonEmptyQuoteIndices(post), ...keptPostIndices])]
      .sort((a, b) => a - b);
  }, [expandedPost, keptPostIndices, post]);
  const hiddenPostCount = Math.max(0, post.length - visiblePostIndices.length);

  function patchBranch(i: number, patch: Partial<RpcBranch>) {
    setBranches((bs) => bs.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  }
  function moveBranch(i: number, d: -1 | 1) {
    setBranches((bs) => {
      const j = i + d;
      if (j < 0 || j >= bs.length) return bs;
      const copy = [...bs];
      const a = copy[i];
      const b = copy[j];
      if (!a || !b) return bs;
      copy[i] = b;
      copy[j] = a;
      return copy;
    });
  }

  if (isLoading) {
    return (
      <section className="card space-y-2" aria-live="polite">
        <h2 className="text-sm font-semibold text-wasteland-100">Loading recruitment settings…</h2>
        <p className="text-xs text-wasteland-300">
          Reading recruit conditions and the dialogue lines already assigned to this RPC.
        </p>
      </section>
    );
  }
  if (error) {
    return (
      <section className="card border-rust-700/60 text-xs text-rust-200">
        Recruitment settings could not be loaded: {formatApiError(error)}
      </section>
    );
  }

  return (
    <div className="space-y-4">
      <section className="card space-y-2">
        <h2 className="text-sm font-semibold uppercase text-wasteland-400">Recruitment &amp; dialogue</h2>
        <p className="text-xs text-wasteland-400">
          Authors <code className="font-mono">NpcData/{String(profile).padStart(3, "0")}.NPC</code> +{" "}
          <code className="font-mono">{String(voice).padStart(3, "0")}.EDT</code>. Recruit branches are
          tried top-to-bottom (first match wins). Set the merc to <strong>Type RPC</strong> and place
          it on the map (other tabs) for the dialogue to trigger in game.
        </p>
        {merc.Type !== 3 && (
          <div className="rounded border border-rust-700/60 bg-rust-950/30 p-2 text-xs text-rust-200">
            This merc is not <strong>Type RPC (3)</strong> — dialogue files will be written, but the
            engine only runs NPC dialogue for RPC/NPC profiles. Set the Type in the Profile tab.
          </div>
        )}
        {data?.warnings.map((w, i) => (
          <div key={i} className="rounded border border-rust-700/60 bg-rust-950/30 p-2 text-xs text-rust-200">
            ⚠ {w}
          </div>
        ))}
        {data?.lossy && (
          <div className="rounded border-2 border-rust-500 bg-rust-950/50 p-2 text-xs text-rust-100">
            <p className="font-semibold">This .NPC contains dialogue logic the editor can't represent.</p>
            <p className="mt-1">
              It has records or fields beyond the recruit branches shown here (quote-only records,
              quest gates, custom actions, or a newer file format). <strong>Saving will discard them.</strong>
            </p>
            <label className="mt-2 flex items-center gap-2">
              <input type="checkbox" checked={allowOverwrite} onChange={(e) => setAllowOverwrite(e.target.checked)} />
              I understand — overwrite with only what's shown here.
            </label>
          </div>
        )}
      </section>

      {/* ── Recruit branches ─────────────────────────────────────────── */}
      <section className="card space-y-3">
        <h3 className="text-xs font-semibold uppercase text-wasteland-500">Recruit branches</h3>
        {branches.map((b, i) => (
          <div key={i} className="rounded border border-wasteland-700 p-2 space-y-2">
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-xs text-wasteland-400">
                Condition
                <select
                  className="input mt-1"
                  value={b.approach}
                  onChange={(e) => {
                    const approach = Number(e.target.value);
                    patchBranch(i, {
                      approach,
                      ...(approach === APPROACH_GIVE_ITEM && b.required_item <= 0
                        ? { required_item: 1 }
                        : {}),
                    });
                  }}
                >
                  <option value={APPROACH_RECRUIT}>Leadership (talk to recruit)</option>
                  <option value={APPROACH_GIVE_ITEM}>Give item</option>
                </select>
              </label>

              {b.approach === APPROACH_RECRUIT ? (
                <label className="text-xs text-wasteland-400">
                  Opinion / talk-desire required
                  <input
                    className="input mt-1 w-28"
                    type="number"
                    min={0}
                    max={255}
                    value={b.opinion_required}
                    onChange={(e) => patchBranch(i, { opinion_required: Number(e.target.value) })}
                  />
                </label>
              ) : (
                <label className="text-xs text-wasteland-400">
                  Required item #
                  <input
                    className="input mt-1 w-28"
                    type="number"
                    min={1}
                    value={b.required_item}
                    onChange={(e) => patchBranch(i, { required_item: Number(e.target.value) })}
                  />
                </label>
              )}

              <label className="text-xs text-wasteland-400">
                Accept quote #
                <input
                  className="input mt-1 w-24"
                  type="number"
                  min={0}
                  value={b.accept_quote}
                  onChange={(e) => patchBranch(i, { accept_quote: Number(e.target.value) })}
                />
              </label>

              <label className="text-xs text-wasteland-400">
                Also require fact # (optional)
                <input
                  className="input mt-1 w-32"
                  type="number"
                  min={0}
                  placeholder="none"
                  value={b.fact_must_be_true ?? ""}
                  onChange={(e) =>
                    patchBranch(i, {
                      fact_must_be_true: e.target.value === "" ? null : Number(e.target.value),
                    })
                  }
                />
              </label>
            </div>
            <div className="flex gap-2">
              <button className="btn-ghost px-2 py-1 text-xs" onClick={() => moveBranch(i, -1)} disabled={i === 0}>
                ↑
              </button>
              <button
                className="btn-ghost px-2 py-1 text-xs"
                onClick={() => moveBranch(i, 1)}
                disabled={i === branches.length - 1}
              >
                ↓
              </button>
              <button
                className="btn-ghost px-2 py-1 text-xs text-rust-300"
                onClick={() => setBranches((bs) => bs.filter((_, j) => j !== i))}
                disabled={!canRemoveRecruitBranch(branches.length)}
              >
                Remove
              </button>
            </div>
          </div>
        ))}
        <button className="btn-secondary text-xs" onClick={() => setBranches((bs) => [...bs, makeDefaultRpcBranch()])}>
          + Add recruit branch
        </button>
        <p className="text-[10px] text-wasteland-500">
          Fact-gated branches require a fact a Lua hook must set. Configure a carried-item trigger
          below to set that fact (the Dogmeat-jacket pattern).
        </p>
      </section>

      {branches.some((b) => b.fact_must_be_true != null) && (
        <FactTriggerSection
          profile={profile}
          gatedFacts={branches
            .map((b) => b.fact_must_be_true)
            .filter((f): f is number => f != null)}
        />
      )}

      {/* ── Pre-recruit quotes ───────────────────────────────────────── */}
      <section className="card space-y-3">
        <h3 className="text-xs font-semibold uppercase text-wasteland-500">Pre-recruit dialogue</h3>
        {pre.map((q, i) => {
          const label = labels[i] ?? `Custom quote ${i}`;
          return (
            <label key={i} className="block text-xs text-wasteland-400">
              <span className="font-mono text-wasteland-500">
                [{i}] {label}
              </span>
              <textarea
                className="input mt-1 h-16 w-full font-sans"
                maxLength={QUOTE_MAX}
                value={q}
                onChange={(e) => setPre((qs) => qs.map((x, j) => (j === i ? e.target.value : x)))}
              />
            </label>
          );
        })}
        <button className="btn-ghost text-xs" onClick={() => setPre((qs) => [...qs, ""])}>
          + Add custom quote (#{pre.length})
        </button>
      </section>

      {/* ── Post-recruit barks ───────────────────────────────────────── */}
      <section className="card space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-wasteland-100">After-recruitment lines</h3>
            <p className="mt-1 text-xs text-wasteland-300">
              {populatedPostCount} line{populatedPostCount === 1 ? "" : "s"} in use.
              {hiddenPostCount > 0 ? ` ${hiddenPostCount} empty slots are hidden.` : ""}
            </p>
          </div>
          {post.length > 0 && (
            <button
              type="button"
              className="btn-ghost px-2 py-1 text-xs"
              onClick={() => setExpandedPost((value) => !value)}
            >
              {expandedPost ? "Hide empty slots" : `Show all ${post.length} slots`}
            </button>
          )}
        </div>
        {visiblePostIndices.length === 0 && (
          <p className="rounded border border-dashed border-wasteland-700 p-3 text-xs text-wasteland-400">
            No after-recruitment lines yet. Add one when this RPC has a bark to say in the field.
          </p>
        )}
        {visiblePostIndices.map((i) => (
          <label key={i} className="block text-xs text-wasteland-400">
            <span className="font-mono text-wasteland-300">Line {i}</span>
            <textarea
              className="input mt-1 h-12 w-full font-sans"
              maxLength={QUOTE_MAX}
              value={post[i] ?? ""}
              onChange={(e) => setPost((qs) => qs.map((x, j) => (j === i ? e.target.value : x)))}
            />
          </label>
        ))}
        <button
          className="btn-secondary text-xs"
          onClick={() => {
            const nextIndex = post.length;
            setPost((quotes) => [...quotes, ""]);
            setKeptPostIndices((indices) => [...indices, nextIndex]);
          }}
        >
          + Add line {post.length}
        </button>
      </section>

      <div className="flex items-center gap-3">
        <button
          className="btn-primary text-sm"
          disabled={branches.length === 0 || save.isPending || tooLong || (!!data?.lossy && !allowOverwrite)}
          onClick={() => save.mutate()}
        >
          Save recruitment &amp; dialogue
        </button>
        {blankAcceptQuotes.length > 0 && (
          <span className="text-xs text-rust-300">
            Recruit accept quote{blankAcceptQuotes.length > 1 ? "s" : ""} {blankAcceptQuotes.join(", ")} {blankAcceptQuotes.length > 1 ? "are" : "is"} blank.
          </span>
        )}
        {tooLong && <span className="text-xs text-rust-300">A quote exceeds {QUOTE_MAX} characters.</span>}
        {msg && <span className="text-xs text-wasteland-200">{msg}</span>}
      </div>

      <SmallFaceOverrideSection merc={merc} />
    </div>
  );
}

function FactTriggerSection({ profile, gatedFacts }: { profile: number; gatedFacts: number[] }) {
  const qc = useQueryClient();
  const { confirm } = useDialog();
  const { data } = useQuery({ queryKey: ["rpc-fact-setters"], queryFn: () => listRpcFactSetters() });
  const existing = data?.setters.find((s) => s.profile === profile);

  const [item, setItem] = useState("");
  const [sector, setSector] = useState("");
  const [fact, setFact] = useState<string>(gatedFacts[0] != null ? String(gatedFacts[0]) : "");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (existing) {
      setItem(String(existing.item));
      setSector(existing.sector);
      setFact(String(existing.fact));
    }
  }, [existing?.item, existing?.sector, existing?.fact]);

  const save = useMutation({
    mutationFn: () =>
      setRpcFactSetter({ profile, sector: sector.trim(), item: Number(item), fact: Number(fact) }),
    onSuccess: () => {
      setMsg("Trigger saved to strategicmap.lua.");
      qc.invalidateQueries({ queryKey: ["rpc-fact-setters"] });
    },
    onError: (e) => setMsg(formatApiError(e)),
  });
  const remove = useMutation({
    mutationFn: () => removeRpcFactSetter(profile),
    onSuccess: () => {
      setMsg("Trigger removed.");
      qc.invalidateQueries({ queryKey: ["rpc-fact-setters"] });
    },
    onError: (e) => setMsg(formatApiError(e)),
  });

  const sectorValid = /^[A-Pa-p]\s*\d{1,2}$/.test(sector.trim());
  const factNum = Number(fact);
  const factValid = fact !== "" && factNum >= 431 && factNum <= 499;
  const valid = sectorValid && item.trim() !== "" && factValid;

  async function confirmRemove() {
    await confirm({
      title: "Remove carried-item trigger?",
      body: "This removes the persisted strategicmap.lua fact setter for this RPC.",
      confirmLabel: "Remove trigger",
      destructive: true,
      onConfirm: () => remove.mutateAsync(),
    });
  }

  return (
    <section className="card space-y-3">
      <h3 className="text-xs font-semibold uppercase text-wasteland-500">Carried-item recruit trigger</h3>
      <p className="text-xs text-wasteland-400">
        Writes a hook into <code className="font-mono">strategicmap.lua</code>: when any squad member
        carries the item in the RPC's sector, it sets the fact. Point a fact-gated branch above at the
        same fact number. One trigger per RPC.
      </p>
      <div className="grid grid-cols-3 gap-3">
        <label className="text-xs text-wasteland-400">
          Item #
          <input className="input mt-1" type="number" min={0} value={item} onChange={(e) => setItem(e.target.value)} />
        </label>
        <label className="text-xs text-wasteland-400">
          Sector
          <input className="input mt-1" placeholder="A9" value={sector} onChange={(e) => setSector(e.target.value)} />
        </label>
        <label className="text-xs text-wasteland-400">
          Sets fact #
          <input
            className="input mt-1"
            type="number"
            min={431}
            max={499}
            value={fact}
            onChange={(e) => setFact(e.target.value)}
          />
        </label>
      </div>
      <div className="flex gap-2">
        <button className="btn-secondary text-xs" disabled={!valid || save.isPending} onClick={() => save.mutate()}>
          {existing ? "Update trigger" : "Save trigger"}
        </button>
        {existing && (
          <button className="btn-ghost text-xs" disabled={remove.isPending} onClick={() => void confirmRemove()}>
            Remove trigger
          </button>
        )}
      </div>
      {!factValid && fact !== "" && <p className="text-xs text-rust-300">Fact must be in the free range 431–499.</p>}
      {msg && <p className="text-xs text-wasteland-200">{msg}</p>}
    </section>
  );
}

function SmallFaceOverrideSection({ merc }: { merc: Merc }) {
  const qc = useQueryClient();
  const { confirm } = useDialog();
  const profile = merc.uiIndex;
  const { data } = useQuery({
    queryKey: ["rpc-small-face", profile],
    queryFn: () => getRpcSmallFace(profile),
  });
  const ov = data?.override ?? null;

  const [ex, setEx] = useState("");
  const [ey, setEy] = useState("");
  const [mx, setMx] = useState("");
  const [my, setMy] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  // Seed from the existing override, else from the merc's profile coords.
  useEffect(() => {
    const src = ov ?? {
      eyesX: merc.usEyesX,
      eyesY: merc.usEyesY,
      mouthX: merc.usMouthX,
      mouthY: merc.usMouthY,
    };
    setEx(String(src.eyesX));
    setEy(String(src.eyesY));
    setMx(String(src.mouthX));
    setMy(String(src.mouthY));
  }, [ov?.eyesX, ov?.eyesY, ov?.mouthX, ov?.mouthY, merc.usEyesX, merc.usEyesY, merc.usMouthX, merc.usMouthY]);

  const save = useMutation({
    mutationFn: () =>
      setRpcSmallFace(profile, {
        name: merc.zNickname || merc.zName || `RPC${profile}`,
        eyesX: Number(ex),
        eyesY: Number(ey),
        mouthX: Number(mx),
        mouthY: Number(my),
      }),
    onSuccess: () => {
      setMsg("Small-face override saved.");
      qc.invalidateQueries({ queryKey: ["rpc-small-face", profile] });
      qc.invalidateQueries({ queryKey: ["rpc-readiness", profile] });
    },
    onError: (e) => setMsg(formatApiError(e)),
  });
  const clear = useMutation({
    mutationFn: () => removeRpcSmallFace(profile),
    onSuccess: () => {
      setMsg("Override cleared — the small face uses the profile's coords.");
      qc.invalidateQueries({ queryKey: ["rpc-small-face", profile] });
      qc.invalidateQueries({ queryKey: ["rpc-readiness", profile] });
    },
    onError: (e) => setMsg(formatApiError(e)),
  });

  const ok = (s: string) => s.trim() !== "" && Number.isInteger(Number(s)) && Number(s) >= 0;
  const valid = [ex, ey, mx, my].every(ok);

  async function confirmClear() {
    await confirm({
      title: "Clear small-face override?",
      body: "This removes the persisted RPCFacesSmall.xml override and restores profile coordinates.",
      confirmLabel: "Clear override",
      destructive: true,
      onConfirm: () => clear.mutateAsync(),
    });
  }

  return (
    <section className="card space-y-3">
      <h3 className="text-xs font-semibold uppercase text-wasteland-500">Small talking-face override (optional)</h3>
      <p className="text-xs text-wasteland-400">
        Overrides this RPC's small (talking) face eye/mouth coords via{" "}
        <code className="font-mono">RPCFacesSmall.xml</code>. Only needed when the small face's mouth or
        eyes sit differently than the profile's coords (the Dogmeat mouth-tweak case). Defaults shown are
        the profile's current values.
      </p>
      <div className="grid grid-cols-4 gap-3">
        <label className="text-xs text-wasteland-400">
          Eyes X
          <input className="input mt-1" type="number" min={0} value={ex} onChange={(e) => setEx(e.target.value)} />
        </label>
        <label className="text-xs text-wasteland-400">
          Eyes Y
          <input className="input mt-1" type="number" min={0} value={ey} onChange={(e) => setEy(e.target.value)} />
        </label>
        <label className="text-xs text-wasteland-400">
          Mouth X
          <input className="input mt-1" type="number" min={0} value={mx} onChange={(e) => setMx(e.target.value)} />
        </label>
        <label className="text-xs text-wasteland-400">
          Mouth Y
          <input className="input mt-1" type="number" min={0} value={my} onChange={(e) => setMy(e.target.value)} />
        </label>
      </div>
      <div className="flex gap-2">
        <button className="btn-secondary text-xs" disabled={!valid || save.isPending} onClick={() => save.mutate()}>
          {ov ? "Update override" : "Save override"}
        </button>
        {ov && (
          <button className="btn-ghost text-xs" disabled={clear.isPending} onClick={() => void confirmClear()}>
            Clear override
          </button>
        )}
      </div>
      {msg && <p className="text-xs text-wasteland-200">{msg}</p>}
    </section>
  );
}
