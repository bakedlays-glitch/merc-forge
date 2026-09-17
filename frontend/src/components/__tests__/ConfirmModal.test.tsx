// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ConfirmModal from "../ConfirmModal";
import { DialogProvider, useDialog } from "../DialogProvider";

afterEach(cleanup);

function ModalHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Open dialog</button>
      <ConfirmModal
        open={open}
        title="Delete fixture?"
        body="This cannot be undone."
        destructive
        onConfirm={() => setOpen(false)}
        onCancel={() => setOpen(false)}
      />
    </>
  );
}

describe("ConfirmModal", () => {
  it("labels itself, traps tab focus, and restores focus after close", async () => {
    const user = userEvent.setup();
    render(<ModalHarness />);
    const opener = screen.getByRole("button", { name: "Open dialog" });

    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Delete fixture?" });
    const cancel = screen.getByRole("button", { name: "Cancel" });
    const confirm = screen.getByRole("button", { name: "Confirm" });
    await waitFor(() => expect(document.activeElement).toBe(cancel));

    await user.tab();
    expect(document.activeElement).toBe(confirm);
    await user.tab();
    expect(document.activeElement).toBe(cancel);
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(confirm);

    fireEvent.click(cancel);
    expect(document.body.contains(dialog)).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it("does not dismiss through Escape or the backdrop while busy", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmModal
        open
        title="Busy dialog"
        body="Please wait."
        busy
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );

    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("dialog"));
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("keeps focus inside the modal when every control is disabled", () => {
    render(
      <ConfirmModal
        open
        title="Busy dialog"
        body="Please wait."
        busy
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog");
    dialog.focus();
    expect(document.activeElement).toBe(dialog);

    expect(fireEvent.keyDown(dialog, { key: "Tab" })).toBe(false);
    expect(document.activeElement).toBe(dialog);
    expect(fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true })).toBe(false);
    expect(document.activeElement).toBe(dialog);
  });

  it("gives the type-to-confirm textbox an accessible instruction label", () => {
    render(
      <ConfirmModal
        open
        title="Restore fixture?"
        body="This overwrites files."
        typeToConfirm="restore"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByRole("textbox", { name: /type restore to confirm/i })).toBeTruthy();
  });
});

function PendingConfirmationHarness({ complete }: { complete: Promise<void> }) {
  const { confirm } = useDialog();
  const [result, setResult] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        onClick={() => void confirm({
          title: "Remove entry?",
          body: "The removal is pending.",
          destructive: true,
          onConfirm: () => complete,
        }).then((approved) => setResult(String(approved)))}
      >
        Begin remove
      </button>
      {result && <output>{result}</output>}
    </>
  );
}

describe("DialogProvider", () => {
  it("keeps an async confirmation mounted and settles it only once", async () => {
    const user = userEvent.setup();
    let resolveComplete: (() => void) | undefined;
    const complete = new Promise<void>((resolve) => { resolveComplete = resolve; });
    render(
      <DialogProvider>
        <PendingConfirmationHarness complete={complete} />
      </DialogProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Begin remove" }));
    const confirm = screen.getByRole("button", { name: "Confirm" });
    await user.click(confirm);
    expect(screen.getByRole("dialog", { name: "Remove entry?" })).toBeTruthy();
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    await user.click(confirm);

    resolveComplete?.();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("true")).toBeTruthy();
  });

  it("shows a safe retryable error, then settles false once on cancel", async () => {
    const user = userEvent.setup();
    const rejectedAction = vi.fn<() => Promise<void>>().mockRejectedValue(new Error("sensitive backend detail"));
    render(
      <DialogProvider>
        <PendingConfirmationHarness complete={Promise.resolve()} />
        <RejectingConfirmationHarness action={rejectedAction} />
      </DialogProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Begin rejected remove" }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect((await screen.findByRole("alert")).textContent).toContain("could not be completed");
    expect(screen.getByRole("dialog", { name: "Remove rejected entry?" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).not.toContain("sensitive backend detail");

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("false")).toBeTruthy();
    expect(rejectedAction).toHaveBeenCalledTimes(1);
  });

  it("clears an error before retrying an async destructive confirmation", async () => {
    const user = userEvent.setup();
    let resolveSecond: (() => void) | undefined;
    const action = vi.fn<() => Promise<void>>()
      .mockRejectedValueOnce(new Error("sensitive backend detail"))
      .mockImplementationOnce(() => new Promise<void>((resolve) => { resolveSecond = resolve; }));
    render(
      <DialogProvider>
        <PendingConfirmationHarness complete={Promise.resolve()} />
        <RejectingConfirmationHarness action={action} />
      </DialogProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Begin rejected remove" }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("alert")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(screen.queryByRole("alert")).toBeNull();
    resolveSecond?.();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("true")).toBeTruthy();
    expect(action).toHaveBeenCalledTimes(2);
  });
});

function RejectingConfirmationHarness({ action }: { action: () => Promise<void> }) {
  const { confirm } = useDialog();
  const [result, setResult] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        onClick={() => void confirm({
          title: "Remove rejected entry?",
          body: "The removal is pending.",
          destructive: true,
          onConfirm: action,
        }).then((approved) => setResult(String(approved)))}
      >
        Begin rejected remove
      </button>
      {result && <output>{result}</output>}
    </>
  );
}
