import { describe, expect, it } from "vitest";
import { parsePrUrl, prUrlProblem } from "./pr-url";

describe("pr url", () => {
  it("accepts PR links with extra path or query", () => {
    expect(parsePrUrl("https://github.com/sa1136/reviewpilot-sandbox/pull/5")).toEqual({ repo: "sa1136/reviewpilot-sandbox", pr: 5 });
    expect(parsePrUrl(" https://github.com/o/r.js/pull/12/files?w=1 ")).toEqual({ repo: "o/r.js", pr: 12 });
  });

  it("explains what is wrong", () => {
    expect(prUrlProblem("")).toBeNull();
    expect(prUrlProblem("github.com/o/r/pull/1")).toMatch(/full link/);
    expect(prUrlProblem("https://gitlab.com/o/r/pull/1")).toMatch(/github.com/);
    expect(prUrlProblem("https://github.com/o/r/issues/1")).toMatch(/issue/);
    expect(prUrlProblem("https://github.com/o/r")).toMatch(/should look like/);
  });
});
