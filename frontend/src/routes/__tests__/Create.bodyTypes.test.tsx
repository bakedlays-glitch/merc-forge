// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import Create from "../Create";

const api = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getBodyTypes: vi.fn(),
  listGearPresets: vi.fn(),
  compilePortrait: vi.fn(),
  createMerc: vi.fn(),
  extendFaceGear: vi.fn(),
  getFaceGearCapacity: vi.fn(),
  getSlot: vi.fn(),
  saveRpcPortrait: vi.fn(),
  uploadVoiceClips: vi.fn(),
}));

vi.mock("../../lib/api", () => ({
  ...api,
  ApiError: class ApiError extends Error { status = 500; },
}));
vi.mock("../../lib/slotLocks", () => ({
  useSlotLockGuard: () => ({ guard: vi.fn(), pending: null, confirm: vi.fn(), cancel: vi.fn() }),
}));

describe("Create body type registry", () => {
  it("renders every engine-authorable target body type and never observed-only IDs", async () => {
    api.getHealth.mockResolvedValue({ active_install_id: "target" });
    api.getBodyTypes.mockResolvedValue({
      mod_id: "wasteland",
      source: "engine-known",
      options: [
        { id: 0, name: "REGMALE", authorable: true },
        { id: 41, name: "MARCUS", authorable: true },
        { id: 91, name: "Observed 91", authorable: false },
      ],
    });
    api.listGearPresets.mockResolvedValue([]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/create?slot=220"]}>
          <Create />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const select = await screen.findByRole("combobox", { name: "Body type" });
    await screen.findByRole("option", { name: /MARCUS/ });
    expect(select.querySelector('option[value="41"]')?.textContent).toContain("MARCUS");
    expect(select.querySelector('option[value="91"]')).toBeNull();
  });
});
