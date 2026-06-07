import { useCallback, useEffect, useRef, useState } from "react";

// Eye frames map to the engine's NewEye state machine (Faces.cpp): an idle
// merc only plays frames 1→2 (the blink). Frames 3 and 4 are the "angry" and
// "surprised" expression art — while a merc is talking the engine also shows
// one of the two at random (on an expression timer), so they're optional
// accents, not tied to the merc actually being angry or startled.
const EYE_SLOT_META = [
  { label: "Blink (half)", hint: "Eyelid partway down" },
  { label: "Blink (closed)", hint: "Eye fully shut" },
  { label: "Angry", hint: "Random expression accent shown while talking — optional" },
  { label: "Surprised", hint: "Random expression accent shown while talking — optional" },
] as const;

// Mouth frames map to NewMouth: while talking, the engine picks one of these
// three (or the closed base portrait) at random each tick, so order doesn't
// matter — they just need to look different from each other.
const MOUTH_SLOT_META = [
  { label: "Slightly open", hint: "Lips just parted" },
  { label: "Mid-open", hint: "Mouth partway open" },
  { label: "Wide / vowel", hint: "Wide open or a vowel shape — either works" },
] as const;

const ACCEPT = "image/png,image/jpeg,image/webp,image/bmp";

interface FrameTileProps {
  file: File | null;
  label: string;
  hint: string;
  onChange: (file: File | null) => void;
  /** Optional dimmed styling for the optional expression slots (eyes 3 & 4). */
  muted?: boolean;
}

function FrameTile({ file, label, hint, onChange, muted = false }: FrameTileProps) {
  const [hover, setHover] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const accept = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const f = files[0];
      if (!f || !f.type.startsWith("image/")) return;
      onChange(f);
    },
    [onChange],
  );

  return (
    <div className="flex flex-col">
      <div
        className={`relative aspect-square rounded border-2 border-dashed transition-colors cursor-pointer overflow-hidden ${
          hover
            ? "border-rust-500 bg-rust-500/10"
            : file
              ? "border-wasteland-600 bg-wasteland-800"
              : "border-wasteland-700 bg-wasteland-900/50 hover:border-wasteland-500"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setHover(true);
        }}
        onDragLeave={() => setHover(false)}
        onDrop={(e) => {
          e.preventDefault();
          setHover(false);
          accept(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        aria-label={`${label}: ${file ? file.name : "empty, click to upload"}`}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          tabIndex={-1}
          onChange={(e) => accept(e.target.files)}
        />
        {previewUrl ? (
          <img
            src={previewUrl}
            alt={`${label} preview`}
            className="absolute inset-0 w-full h-full object-contain"
            style={{ imageRendering: "pixelated" }}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-wasteland-500 text-xs">
            drop
          </div>
        )}
        {file && (
          <button
            type="button"
            className="absolute top-1 right-1 rounded bg-wasteland-900/80 px-1.5 text-rust-300 hover:text-rust-100 hover:bg-wasteland-900 text-xs leading-tight"
            onClick={(e) => {
              e.stopPropagation();
              onChange(null);
            }}
            title="Remove this frame"
            aria-label={`Remove ${label}`}
          >
            ×
          </button>
        )}
      </div>
      <div className="mt-1.5">
        <div className={`text-xs font-medium ${muted ? "text-wasteland-500" : "text-wasteland-200"}`}>
          {label}
        </div>
        <div className="text-[10px] text-wasteland-500 leading-tight">{hint}</div>
      </div>
    </div>
  );
}

interface Props {
  eyeFrames: (File | null)[];
  mouthFrames: (File | null)[];
  onEyeFramesChange: (frames: (File | null)[]) => void;
  onMouthFramesChange: (frames: (File | null)[]) => void;
}

export default function AnimationFrameStrip({
  eyeFrames,
  mouthFrames,
  onEyeFramesChange,
  onMouthFramesChange,
}: Props) {
  const setEye = (idx: number, f: File | null) => {
    const next = eyeFrames.slice();
    next[idx] = f;
    onEyeFramesChange(next);
  };
  const setMouth = (idx: number, f: File | null) => {
    const next = mouthFrames.slice();
    next[idx] = f;
    onMouthFramesChange(next);
  };
  const fillEmpty = (frames: (File | null)[]): (File | null)[] => {
    const first = frames[0];
    if (!first) return frames;
    return frames.map((f) => f ?? first);
  };
  const eyeFilled = eyeFrames.filter(Boolean).length;
  const mouthFilled = mouthFrames.filter(Boolean).length;

  return (
    <div className="space-y-5">
      <p className="text-sm font-semibold text-wasteland-100">
        Leave these empty for a still face
        <span className="ml-2 font-normal text-wasteland-400">
          — the merc just won't blink or talk. Fill them in only if you want animation.
        </span>
      </p>
      <section>
        <div className="flex items-baseline justify-between mb-2">
          <h4 className="text-sm font-semibold text-wasteland-100">
            Eye animation frames
            <span className="ml-2 text-xs text-wasteland-500">({eyeFilled} / 4 supplied)</span>
          </h4>
          <button
            type="button"
            className="text-xs text-rust-400 hover:text-rust-200 disabled:text-wasteland-600 disabled:hover:text-wasteland-600"
            disabled={!eyeFrames[0] || eyeFrames.slice(1).every(Boolean)}
            onClick={() => onEyeFramesChange(fillEmpty(eyeFrames))}
            title="Use slot 1 to fill any empty eye slots"
          >
            Fill empty from slot 1
          </button>
        </div>
        <div className="grid grid-cols-4 gap-3">
          {EYE_SLOT_META.map((meta, idx) => (
            <FrameTile
              key={idx}
              file={eyeFrames[idx] ?? null}
              label={`${idx + 1}. ${meta.label}`}
              hint={meta.hint}
              onChange={(f) => setEye(idx, f)}
              muted={idx === 2 || idx === 3}
            />
          ))}
        </div>
      </section>

      <section>
        <div className="flex items-baseline justify-between mb-2">
          <h4 className="text-sm font-semibold text-wasteland-100">
            Mouth animation frames
            <span className="ml-2 text-xs text-wasteland-500">({mouthFilled} / 3 supplied)</span>
          </h4>
          <button
            type="button"
            className="text-xs text-rust-400 hover:text-rust-200 disabled:text-wasteland-600 disabled:hover:text-wasteland-600"
            disabled={!mouthFrames[0] || mouthFrames.slice(1).every(Boolean)}
            onClick={() => onMouthFramesChange(fillEmpty(mouthFrames))}
            title="Use slot 1 to fill any empty mouth slots"
          >
            Fill empty from slot 1
          </button>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {MOUTH_SLOT_META.map((meta, idx) => (
            <FrameTile
              key={idx}
              file={mouthFrames[idx] ?? null}
              label={`${idx + 1}. ${meta.label}`}
              hint={meta.hint}
              onChange={(f) => setMouth(idx, f)}
            />
          ))}
        </div>
      </section>

      <p className="text-xs text-wasteland-400">
        Each frame can be a pre-cropped sub-frame (vanilla 17×6 eyes / 14×6 mouths; the larger
        "Big Frames" style uses 31×13 / 32×21), or a full 48×43 face the wizard crops at the
        eye/mouth coordinates. All four eye frames must share one size; all three mouth frames must
        share one size. While talking, the engine randomly alternates between the closed mouth (your
        base portrait) and the three mouth frames, so they don't need a set order — just make them
        visibly different.
      </p>
    </div>
  );
}
