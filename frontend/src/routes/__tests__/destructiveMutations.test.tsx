// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import Backgrounds from "../Backgrounds";
import Backups from "../Backups";

const api = vi.hoisted(() => ({
  listBackgrounds: vi.fn(),
  createBackground: vi.fn(),
  updateBackground: vi.fn(),
  deleteBackground: vi.fn(),
  setBackgroundImpThreshold: vi.fn(),
  listBackups: vi.fn(),
  takeSnapshot: vi.fn(),
  restoreBackup: vi.fn(),
  deleteBackup: vi.fn(),
}));

vi.mock("../../lib/api", () => ({
  ...api,
  formatApiError: () => "Sensitive backend detail",
}));

function renderRoute(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("destructive mutation dialogs", () => {
  it("keeps a background deletion dialog busy, then shows a safe local retry error", async () => {
    const user = userEvent.setup();
    api.listBackgrounds.mockResolvedValue({
      backgrounds: [{
        id: 1, name: "Survivor", short_name: "Survivor", description: "", modifiers: [],
        imp_selectable: true, has_advanced_data: false,
      }],
      schema_fields: [], install_id: "test", file_present: true, writable: true, write_path: "C:/test",
      num_found_background: 1, max_index: 499, name_max: 60, short_name_max: 40, description_max: 250,
      duplicate_ids: [],
    });
    let rejectDelete: ((reason?: unknown) => void) | undefined;
    api.deleteBackground.mockImplementation(() => new Promise((_, reject) => { rejectDelete = reject; }));
    renderRoute(<Backgrounds />);

    await user.click(await screen.findByRole("button", { name: /Survivor/ }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog", { name: "Delete background?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect((within(dialog).getByRole("button", { name: "Delete" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(true);
    rejectDelete?.(new Error("Sensitive backend detail"));

    const error = await within(dialog).findByRole("alert");
    expect(error.textContent).toContain("could not be deleted");
    expect(error.textContent).not.toContain("Sensitive backend detail");
    expect((within(dialog).getByRole("button", { name: "Delete" }) as HTMLButtonElement).disabled).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps a backup deletion dialog busy, then shows a safe local retry error", async () => {
    const user = userEvent.setup();
    api.listBackups.mockResolvedValue([{
      id: "backup-1", timestamp: "2026-08-29T12:00:00Z", install_id: "test", reason: "before edit",
      root_dir: "C:/test", files: ["MercProfiles.xml"], total_size_bytes: 12,
    }]);
    let rejectDelete: ((reason?: unknown) => void) | undefined;
    api.deleteBackup.mockImplementation(() => new Promise((_, reject) => { rejectDelete = reject; }));
    renderRoute(<Backups />);

    await user.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog", { name: "Delete this backup snapshot?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete snapshot" }));

    expect((within(dialog).getByRole("button", { name: "Delete snapshot" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(true);
    rejectDelete?.(new Error("Sensitive backend detail"));

    const error = await within(dialog).findByRole("alert");
    expect(error.textContent).toContain("could not be deleted");
    expect(error.textContent).not.toContain("Sensitive backend detail");
    expect((within(dialog).getByRole("button", { name: "Delete snapshot" }) as HTMLButtonElement).disabled).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("clears a rejected restore error before the same backup is reopened", async () => {
    const user = userEvent.setup();
    api.listBackups.mockResolvedValue([{
      id: "backup-1", timestamp: "2026-08-29T12:00:00Z", install_id: "test", reason: "before edit",
      root_dir: "C:/test", files: ["MercProfiles.xml"], total_size_bytes: 12,
    }]);
    api.restoreBackup.mockRejectedValue(new Error("Sensitive backend detail"));
    renderRoute(<Backups />);

    await user.click(await screen.findByRole("button", { name: "Restore" }));
    let dialog = screen.getByRole("dialog", { name: "Restore this backup?" });
    await user.type(within(dialog).getByRole("textbox"), "restore");
    await user.click(within(dialog).getByRole("button", { name: "Restore" }));
    expect(await within(dialog).findByRole("alert")).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await user.click(screen.getByRole("button", { name: "Restore" }));
    dialog = screen.getByRole("dialog", { name: "Restore this backup?" });
    expect(within(dialog).queryByRole("alert")).toBeNull();
  });

  it("confirms bulk IMP visibility changes and clears a rejected error on reopen", async () => {
    const user = userEvent.setup();
    api.listBackgrounds.mockResolvedValue({
      backgrounds: [{
        id: 1, name: "Survivor", short_name: "Survivor", description: "", modifiers: [],
        imp_selectable: false, has_advanced_data: false,
      }],
      schema_fields: [], install_id: "test", file_present: true, writable: true, write_path: "C:/test",
      num_found_background: 0, max_index: 499, name_max: 60, short_name_max: 40, description_max: 250,
      duplicate_ids: [],
    });
    api.setBackgroundImpThreshold.mockRejectedValue(new Error("Sensitive backend detail"));
    renderRoute(<Backgrounds />);

    await user.click(await screen.findByRole("button", { name: "Make all IMP-selectable" }));
    let dialog = screen.getByRole("dialog", { name: "Make all backgrounds selectable in IMP?" });
    await user.click(within(dialog).getByRole("button", { name: "Make all selectable" }));
    expect(await within(dialog).findByRole("alert")).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await user.click(screen.getByRole("button", { name: "Make all IMP-selectable" }));
    dialog = screen.getByRole("dialog", { name: "Make all backgrounds selectable in IMP?" });
    expect(within(dialog).queryByRole("alert")).toBeNull();
  });

  it("does not submit bulk IMP changes on cancel and submits exactly once after confirmation", async () => {
    const user = userEvent.setup();
    api.listBackgrounds.mockResolvedValue({
      backgrounds: [{
        id: 1, name: "Survivor", short_name: "Survivor", description: "", modifiers: [],
        imp_selectable: false, has_advanced_data: false,
      }],
      schema_fields: [], install_id: "test", file_present: true, writable: true, write_path: "C:/test",
      num_found_background: 0, max_index: 499, name_max: 60, short_name_max: 40, description_max: 250,
      duplicate_ids: [],
    });
    let resolveMutation: (() => void) | undefined;
    api.setBackgroundImpThreshold.mockImplementation(
      () => new Promise<void>((resolve) => { resolveMutation = resolve; }),
    );
    renderRoute(<Backgrounds />);

    await user.click(await screen.findByRole("button", { name: "Make all IMP-selectable" }));
    let dialog = screen.getByRole("dialog", { name: "Make all backgrounds selectable in IMP?" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(api.setBackgroundImpThreshold).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Make all IMP-selectable" }));
    dialog = screen.getByRole("dialog", { name: "Make all backgrounds selectable in IMP?" });
    await user.click(within(dialog).getByRole("button", { name: "Make all selectable" }));
    await user.click(within(dialog).getByRole("button", { name: "Make all selectable" }));
    expect(api.setBackgroundImpThreshold).toHaveBeenCalledTimes(1);
    expect(api.setBackgroundImpThreshold).toHaveBeenCalledWith({ all: true });
    resolveMutation?.();
  });
});
