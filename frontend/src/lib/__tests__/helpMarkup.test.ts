import { describe, expect, it } from "vitest";

import { GLOSSARY } from "../glossary";
import { parseHelpMarkup, parseInline, stripHelpMarkup } from "../helpMarkup";

describe("parseHelpMarkup", () => {
  it("wraps plain text as a paragraph", () => {
    expect(parseHelpMarkup("no links here")).toEqual([
      { type: "para", children: [{ type: "text", value: "no links here" }] },
    ]);
  });

  it("parses [[id]] and [[id|label]]", () => {
    expect(parseHelpMarkup("Uses [[AP]] and [[CtH|chance-to-hit]].")).toEqual([
      {
        type: "para",
        children: [
          { type: "text", value: "Uses " },
          { type: "term", id: "AP", label: "AP" },
          { type: "text", value: " and " },
          { type: "term", id: "CtH", label: "chance-to-hit" },
          { type: "text", value: "." },
        ],
      },
    ]);
  });

  it("parses headings, bullets, bold, italic, and code", () => {
    const blocks = parseHelpMarkup("## Combat\n- **Throws** use `avg(DEX, Marks)`\n*not* foot speed");
    expect(blocks[0]).toEqual({
      type: "heading",
      level: 2,
      children: [{ type: "text", value: "Combat" }],
    });
    expect(blocks[1]!.type).toBe("bullet");
    expect(parseInline("**Throws** use `avg(DEX, Marks)`")).toEqual([
      { type: "bold", value: "Throws" },
      { type: "text", value: " use " },
      { type: "code", value: "avg(DEX, Marks)" },
    ]);
    expect(blocks[2]).toEqual({
      type: "para",
      children: [
        { type: "italic", value: "not" },
        { type: "text", value: " foot speed" },
      ],
    });
  });
});

describe("stripHelpMarkup", () => {
  it("keeps labels for copy/paste", () => {
    expect(stripHelpMarkup("[[percent|Percent]] of [[AP|APs]].")).toBe("Percent of APs.");
  });

  it("strips structure to readable text", () => {
    expect(stripHelpMarkup("## Combat\n- **Throws**")).toBe("Combat\n• Throws");
  });
});

describe("GLOSSARY", () => {
  it("has an entry for every term the schema is allowed to link", () => {
    const ids = [
      "AP",
      "CtH",
      "IMP",
      "percent",
      "flat",
      "clamp",
      "Strength",
      "Agility",
      "Dexterity",
      "Wisdom",
      "Leadership",
      "Marksmanship",
      "Mechanical",
      "Medical",
      "Explosives",
      "Breath",
      "Stealth",
      "Camo",
      "Survivalist",
      "SAM",
      "NPC",
      "Experience",
    ];
    expect(Object.keys(GLOSSARY).sort()).toEqual([...ids].sort());
    for (const id of ids) {
      expect(GLOSSARY[id], id).toBeTruthy();
      expect(GLOSSARY[id]!.body.length).toBeGreaterThan(20);
    }
  });
});
