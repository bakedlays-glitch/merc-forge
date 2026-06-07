import { useCallback, useRef, useState } from "react";

interface Props {
  onFileSelected: (file: File) => void;
  accept?: string;
  className?: string;
  /** Optional preview URL to show after selection */
  previewUrl?: string | null;
  /** Called when the user clears the selection via the ✕ button.
   * Parent should `URL.revokeObjectURL(previewUrl)` if it owns the
   * blob, then null its file state. When omitted, the ✕ button is
   * hidden. */
  onClear?: () => void;
}

export default function PortraitDropzone({
  onFileSelected,
  accept = "image/png,image/jpeg,image/webp,image/bmp",
  className = "",
  previewUrl,
  onClear,
}: Props) {
  const [hover, setHover] = useState(false);
  const [imageHover, setImageHover] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const f = files[0];
      if (!f) return;
      if (!f.type.startsWith("image/")) return;
      onFileSelected(f);
    },
    [onFileSelected]
  );

  return (
    <div
      className={`relative rounded-lg border-2 border-dashed transition-colors ${
        hover ? "border-rust-500 bg-rust-500/5" : "border-wasteland-700"
      } ${className}`}
      onDragOver={(e) => {
        e.preventDefault();
        setHover(true);
      }}
      onDragLeave={() => setHover(false)}
      onDrop={(e) => {
        e.preventDefault();
        setHover(false);
        handleFiles(e.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
      }}
      onMouseEnter={() => setImageHover(true)}
      onMouseLeave={() => setImageHover(false)}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        // The wrapping <div> is the tab target (tabIndex=0). The hidden
        // <input> needs tabIndex=-1 so keyboard nav doesn't land on it
        // first and bypass the visible dropzone.
        tabIndex={-1}
        onChange={(e) => handleFiles(e.target.files)}
        aria-label="Choose portrait image"
      />
      {previewUrl ? (
        <>
          <img
            src={previewUrl}
            alt="Portrait preview"
            className="w-full h-full object-contain rounded"
          />
          {/* Hover mask + replace-prompt. Without this the user sees a
              filled dropzone and loses the affordance that they can
              click/drop again to replace. The blur+darken on hover
              brings the interaction back. */}
          <div
            className={`absolute inset-0 flex items-center justify-center rounded transition-opacity pointer-events-none ${
              imageHover ? "opacity-100" : "opacity-0"
            }`}
            style={{
              backgroundColor: "rgba(0, 0, 0, 0.55)",
              backdropFilter: "blur(2px)",
            }}
          >
            <div className="text-center">
              <div className="text-2xl mb-1">📷</div>
              <div className="text-sm font-medium text-wasteland-100">
                Click or drop to replace
              </div>
              <div className="text-[10px] text-wasteland-300 mt-0.5">
                PNG / JPG / WebP / BMP
              </div>
            </div>
          </div>
          {/* Absolute-positioned ✕ to clear the current selection.
              Stop propagation so the click doesn't also re-open the
              file picker via the wrapping div's onClick. */}
          {onClear && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onClear();
              }}
              title="Clear portrait selection"
              aria-label="Clear portrait selection"
              className="absolute right-1.5 top-1.5 z-10 rounded-full border border-wasteland-600 bg-black/70 px-1.5 py-0 text-xs text-wasteland-200 hover:border-rust-500 hover:text-rust-300 hover:bg-black/90"
            >
              ✕
            </button>
          )}
        </>
      ) : (
        <div className="flex flex-col items-center justify-center py-16 text-center cursor-pointer">
          <div className="text-4xl mb-2">📷</div>
          <div className="text-wasteland-200 font-medium">
            Drop a portrait image here
          </div>
          <div className="text-xs text-wasteland-400 mt-1">
            or click to browse · PNG / JPG / WebP / BMP
          </div>
          <div className="text-xs text-wasteland-500 mt-3">
            Recommended: 1024×1024 with a face filling most of the frame
          </div>
        </div>
      )}
    </div>
  );
}
