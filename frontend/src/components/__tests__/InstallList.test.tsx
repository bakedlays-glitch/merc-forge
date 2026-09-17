// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import InstallList from "../settings/InstallList";

const api = vi.hoisted(() => ({
  addInstall: vi.fn(),
  getHealth: vi.fn(),
  listInstalls: vi.fn(),
  refreshInstalls: vi.fn(),
  removeInstall: vi.fn(),
  setActiveInstall: vi.fn(),
}));

vi.mock("../../lib/api", () => ({
  ...api,
  formatApiError: () => "Backend said no",
}));

vi.mock("../../lib/tauri", () => ({
  isRunningInTauri: () => true,
  pickDirectory: vi.fn(),
}));

const INSTALLS = [
  { id: "a", path: "C:\\Games\\JA2_113", mod_id: "wasteland", mod_display: "The Wasteland" },
  { id: "b", path: "C:\\Program Files (x86)\\Steam\\JA2", mod_id: "stock", mod_display: "Stock 1.13" },
];

function renderList() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><InstallList /></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("InstallList", () => {
  it("removes an install only after the confirm, and says the folder is untouched", async () => {
    const user = userEvent.setup();
    api.listInstalls.mockResolvedValue(INSTALLS);
    api.getHealth.mockResolvedValue({ active_install_id: "a", version: "1.0.0-beta.4" });
    api.removeInstall.mockResolvedValue({ removed: true });

    renderList();
    const row = await screen.findByText("Stock 1.13");
    await user.click(within(row.closest("li")!).getByRole("button", { name: /remove/i }));

    // The confirm has to promise the game folder is left alone, or the
    // user cannot tell this apart from deleting their install.
    expect(await screen.findByText(/nothing inside the game folder is touched/i)).toBeTruthy();
    expect(api.removeInstall).not.toHaveBeenCalled();

    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(api.removeInstall).toHaveBeenCalledWith("b"));
  });

  it("surfaces a failed install switch instead of silently reverting the button", async () => {
    const user = userEvent.setup();
    api.listInstalls.mockResolvedValue(INSTALLS);
    api.getHealth.mockResolvedValue({ active_install_id: "a", version: "1.0.0-beta.4" });
    api.setActiveInstall.mockRejectedValue(new Error("gone"));

    renderList();
    const row = await screen.findByText("Stock 1.13");
    await user.click(within(row.closest("li")!).getByRole("button", { name: /set active/i }));

    expect(await screen.findByText("Backend said no")).toBeTruthy();
  });

  it("flags a Program Files install but still lists it as usable", async () => {
    api.listInstalls.mockResolvedValue(INSTALLS);
    api.getHealth.mockResolvedValue({ active_install_id: "a", version: "1.0.0-beta.4" });

    renderList();
    const steam = (await screen.findByText("Stock 1.13")).closest("li")!;
    expect(within(steam).getByText(/UAC-protected/)).toBeTruthy();
    // The warning must not cost the user the ability to select it.
    expect(within(steam).getByRole("button", { name: /set active/i })).toBeTruthy();
  });
});
