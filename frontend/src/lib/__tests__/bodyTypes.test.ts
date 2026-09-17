import { describe, expect, it } from "vitest";

import { bodyTypeQueryKey, mergeBodyTypeOptions } from "../../components/forms/DemographicsForm";


describe("mergeBodyTypeOptions", () => {
  it("keeps the current observed custom option while target options load", () => {
    expect(mergeBodyTypeOptions([], 41)).toEqual([
      { id: 41, name: "Custom 41", authorable: false, category: "observed" },
    ]);
  });

  it("does not duplicate a body type already supplied by the target", () => {
    expect(mergeBodyTypeOptions([{ id: 41, name: "MARCUS" }], 41)).toEqual([
      { id: 41, name: "MARCUS" },
    ]);
  });

  it("uses an install-specific cache key and drops A options while B loads", () => {
    const optionsForA = [{ id: 41, name: "MARCUS" }];

    expect(bodyTypeQueryKey("install-a")).toEqual(["body-types", "install-a"]);
    expect(bodyTypeQueryKey("install-b")).toEqual(["body-types", "install-b"]);
    expect(bodyTypeQueryKey("install-a")).not.toEqual(bodyTypeQueryKey("install-b"));
    expect(mergeBodyTypeOptions([], 3)).toEqual([
      { id: 3, name: "Custom 3", authorable: false, category: "observed" },
    ]);
    expect(mergeBodyTypeOptions([], 3)).not.toEqual(optionsForA);
  });
});
