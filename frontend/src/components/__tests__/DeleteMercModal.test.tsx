// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import DeleteMercModal from "../DeleteMercModal";

vi.mock("../../lib/api", () => ({
  deleteMerc: vi.fn(),
  formatApiError: (error: Error) => error.message,
}));

vi.mock("../../lib/slotLocks", async () => {
  const React = await import("react");
  return {
    useSlotLockGuard: () => {
      const [pending, setPending] = React.useState<{
        lock: { slot: number; tier: "locked"; name: string; role: string };
        callback: () => void;
      } | null>(null);
      return {
        pending,
        guard: (slot: number, callback: () => void) => setPending({
          lock: { slot, tier: "locked", name: "MIGUEL", role: "Story character" },
          callback,
        }),
        confirm: () => {
          pending?.callback();
          setPending(null);
        },
        cancel: () => setPending(null),
      };
    },
    suppressLockTier: vi.fn(),
    tierStyle: () => ({ label: "Locked", description: "Story slot" }),
  };
});

vi.mock("../SaveSnapshotBanner", () => ({ default: () => null }));

afterEach(cleanup);

function Harness() {
  const [open, setOpen] = useState(true);
  return open ? <DeleteMercModal slot={7} nickname="Miguel" name="Miguel" onClose={() => setOpen(false)} /> : null;
}

describe("DeleteMercModal", () => {
  it("hands focus and Escape to the lock warning without leaving two modal traps mounted", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><Harness /></QueryClientProvider>);

    await user.type(screen.getByRole("textbox"), "Miguel");
    await user.click(screen.getByRole("button", { name: "Delete" }));

    const warning = screen.getByRole("dialog", { name: /Slot 7 is locked/i });
    expect(document.querySelectorAll('[aria-modal="true"]')).toHaveLength(1);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" })));

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "Delete Miguel?" })).toBeTruthy();
    expect(document.querySelectorAll('[aria-modal="true"]')).toHaveLength(1);

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(warning.isConnected).toBe(false);
  });
});
