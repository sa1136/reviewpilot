import { describe, expect, it } from "vitest";
import type { PendingItem } from "./api";
import { approveAll, choose, initialChoices, tally, toDecisions } from "./decisions";

const item = (id: string, kind: PendingItem["kind"], extra: Partial<PendingItem> = {}): PendingItem => ({
  id,
  kind,
  default: kind === "possible" ? "reject" : "approve",
  action: kind === "possible" ? "reject" : "approve",
  body: "original",
  ...extra,
});
const items = [item("i1", "inline"), item("i2", "inline"), item("p1", "possible"), item("n1", "nit"), item("t1", "tests", { body: null, text: "untested: f" })];

describe("decisions", () => {
  it("defaults: inline posts, possible issues are off, nits and tests go to the summary", () => {
    const c = initialChoices(items);
    expect(tally(items, c)).toEqual({ inline: 2, summary: 2, off: 1 });
    expect(toDecisions(items, c)).toEqual([]);
  });

  it("reject, edit and promote become API decisions", () => {
    let c = initialChoices(items);
    c = choose(c, "i1", { action: "reject" });
    c = choose(c, "i2", { body: "better text" });
    c = choose(c, "p1", { action: "promote" });
    expect(toDecisions(items, c)).toEqual([
      { id: "i1", action: "reject" },
      { id: "i2", action: "edit", body: "better text" },
      { id: "p1", action: "promote" },
    ]);
    expect(tally(items, c)).toEqual({ inline: 2, summary: 2, off: 1 });
  });

  it("approve all never includes possible issues", () => {
    const c = approveAll(items, choose(initialChoices(items), "i1", { action: "reject" }));
    expect(c.i1.action).toBe("approve");
    expect(c.p1).toMatchObject({ action: "reject", touched: false });
    expect(toDecisions(items, c).map((d) => d.id)).toEqual(["i1", "i2", "n1", "t1"]);
  });

  it("restores decisions saved earlier", () => {
    const saved = [item("i1", "inline", { action: "edit", body_edit: "kept" }), item("p1", "possible", { action: "promote" })];
    const c = initialChoices(saved);
    expect(c.i1).toEqual({ action: "approve", body: "kept", touched: true });
    expect(toDecisions(saved, c)).toEqual([
      { id: "i1", action: "edit", body: "kept" },
      { id: "p1", action: "promote" },
    ]);
  });
});
