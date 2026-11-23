// What the person chose for each pending item, and what that means for the API.
// Possible issues default to "reject" (not posted); everything else defaults to "approve".
import type { Decision, PendingItem } from "./api";

export type Action = "approve" | "reject" | "promote";
export type Choice = { action: Action; body: string | null; touched: boolean };
export type Choices = Record<string, Choice>;

export function initialChoices(items: PendingItem[]): Choices {
  const out: Choices = {};
  for (const item of items) {
    // Decisions saved earlier (post=false) come back as the item's action and body_edit.
    const saved = item.action;
    const action: Action = saved === "reject" ? "reject" : saved === "promote" ? "promote" : "approve";
    out[item.id] = { action, body: item.body_edit ?? null, touched: saved !== item.default };
  }
  return out;
}

export function choose(choices: Choices, id: string, patch: Partial<Omit<Choice, "touched">>): Choices {
  const prev = choices[id] ?? { action: "approve", body: null, touched: false };
  return { ...choices, [id]: { ...prev, ...patch, touched: true } };
}

/** Every item except possible issues set to approve. Possible issues are never included. */
export function approveAll(items: PendingItem[], choices: Choices): Choices {
  let next = choices;
  for (const item of items) {
    if (item.kind === "possible") continue;
    next = choose(next, item.id, { action: "approve" });
  }
  return next;
}

/** Decisions for the API: only items the person touched; an edited body becomes edit/promote. */
export function toDecisions(items: PendingItem[], choices: Choices): Decision[] {
  const out: Decision[] = [];
  for (const item of items) {
    const c = choices[item.id];
    if (!c || !c.touched) continue;
    const body = c.body?.trim() ? c.body : null;
    if (c.action === "reject") out.push({ id: item.id, action: "reject" });
    else if (c.action === "promote") out.push(body ? { id: item.id, action: "promote", body } : { id: item.id, action: "promote" });
    else if (body) out.push({ id: item.id, action: "edit", body });
    else out.push({ id: item.id, action: "approve" });
  }
  return out;
}

export type Outcome = "inline" | "summary" | "off";

/** Where an item ends up with the current choice. */
export function outcome(item: PendingItem, choice: Choice | undefined): Outcome {
  const action = choice?.action ?? (item.default === "reject" ? "reject" : "approve");
  if (action === "reject") return "off";
  if (item.kind === "inline") return "inline";
  if (item.kind === "possible") return action === "promote" ? "inline" : "summary";
  return "summary"; // more (over budget), nit, tests
}

export function tally(items: PendingItem[], choices: Choices) {
  let inline = 0;
  let summary = 0;
  let off = 0;
  for (const item of items) {
    const o = outcome(item, choices[item.id]);
    if (o === "inline") inline += 1;
    else if (o === "summary") summary += 1;
    else off += 1;
  }
  return { inline, summary, off };
}
