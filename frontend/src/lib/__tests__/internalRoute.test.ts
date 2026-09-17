import { describe, expect, it } from "vitest";

import { normalizeInternalRoute } from "../internalRoute";

describe("normalizeInternalRoute", () => {
  it("preserves a safe app-relative path, query, and hash", () => {
    expect(normalizeInternalRoute("/mapforge/sector?dat=A1.dat#inspect")).toBe(
      "/mapforge/sector?dat=A1.dat#inspect",
    );
  });

  it("preserves Windows backslashes encoded inside the query", () => {
    expect(normalizeInternalRoute("/mapforge/sector?dat=C%3A%5CMaps%5CA1.dat")).toBe(
      "/mapforge/sector?dat=C%3A%5CMaps%5CA1.dat",
    );
  });

  it("keeps encoded separators and delimiters opaque after the pathname", () => {
    expect(
      normalizeInternalRoute("/mapforge/sector?next=%2Fhub%3Ftab%3D1#return=%23top"),
    ).toBe("/mapforge/sector?next=%2Fhub%3Ftab%3D1#return=%23top");
  });

  it.each([
    "//outside.example/path",
    "/mapforge\\sector",
    "/%2foutside",
    "/%5coutside",
    "/%252foutside",
    "/%255coutside",
    "/%25252Foutside",
    "/%25%32%66outside",
    "/%25%35%43outside",
    "/mapforge%3F//outside",
    "/mapforge%23//outside",
    "/mapforge%2fsector",
    "/mapforge%2Fsector",
    "/mapforge%5csector",
    "/mapforge%5Csector",
    "/mapforge/%2fsector",
    "mapforge/sector",
    "/bad%",
  ])("rejects unsafe pathname %s", (route) => {
    expect(normalizeInternalRoute(route)).toBeNull();
  });
});
