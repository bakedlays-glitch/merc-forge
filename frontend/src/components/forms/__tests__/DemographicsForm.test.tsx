// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import DemographicsForm from "../DemographicsForm";
import type { Merc } from "../../../lib/schema";

const api = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getBodyTypes: vi.fn(),
}));

vi.mock("../../../lib/api", () => api);

describe("DemographicsForm", () => {
  it("keeps the current observed body type visible but disables other preserve-only IDs", async () => {
    api.getHealth.mockResolvedValue({ active_install_id: "target" });
    api.getBodyTypes.mockResolvedValue({
      mod_id: "custom",
      source: "observed-extension",
      options: [
        { id: 0, name: "REGMALE", authorable: true },
        { id: 91, name: "Observed 91", category: "observed", authorable: false },
        { id: 92, name: "Observed 92", category: "observed", authorable: false },
      ],
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const merc = { bRace: 0, bNationality: 0, bSex: 0, ubBodyType: 91 } as Merc;

    render(
      <QueryClientProvider client={queryClient}>
        <DemographicsForm merc={merc} onChange={vi.fn()} />
      </QueryClientProvider>,
    );

    const current = await screen.findByRole("option", { name: /Observed 91.*preserve only/i });
    const other = screen.getByRole("option", { name: /Observed 92.*preserve only/i });
    expect((current as HTMLOptionElement).disabled).toBe(false);
    expect((other as HTMLOptionElement).disabled).toBe(true);
  });
});
