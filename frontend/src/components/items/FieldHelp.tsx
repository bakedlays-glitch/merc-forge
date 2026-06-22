// frontend/src/components/items/FieldHelp.tsx
import { useId, useState } from "react";

interface Props {
  help: string;
}

/**
 * An accessible "?" help affordance. The icon is a focusable button; the
 * definition shows on hover OR keyboard focus (not a bare title=, which never
 * appears on focus). Linked to the tooltip via aria-describedby so screen
 * readers announce it.
 */
export default function FieldHelp({ help }: Props) {
  const [show, setShow] = useState(false);
  const id = useId();
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-label="Field help"
        aria-describedby={show ? id : undefined}
        className="text-[10px] leading-none w-3.5 h-3.5 rounded-full border border-wasteland-600 text-wasteland-400 hover:text-rust-300 hover:border-rust-400 focus:text-rust-300 focus:border-rust-400 outline-none"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        onFocus={() => setShow(true)}
        onBlur={() => setShow(false)}
      >
        ?
      </button>
      {show && (
        <span
          id={id}
          role="tooltip"
          className="absolute left-4 top-0 z-20 w-52 rounded border border-wasteland-600 bg-wasteland-900 px-2 py-1 text-[11px] text-wasteland-200 shadow-lg"
        >
          {help}
        </span>
      )}
    </span>
  );
}
