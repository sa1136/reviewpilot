import { expect, test, type Page } from "@playwright/test";

const API = "http://localhost:8001";
// The demo API serves the fixture PR for any number, so each test uses its own PR (and review).
const prLink = (n: number) => `https://github.com/sa1136/reviewpilot-sandbox/pull/${n}`;
const agents = ["correctness", "security", "quality"];

async function startFromHome(page: Page, pr: number) {
  await page.goto("/");
  await expect(page.getByTestId("demo-badge")).toBeVisible();
  await page.getByLabel("Pull request link").fill(prLink(pr));
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await page.waitForURL(/\/reviews\/[A-Za-z0-9_-]+$/);
  return page.url().split("/reviews/")[1];
}

test("paste a link, watch the agents, reject one, edit one, post", async ({ page, request }) => {
  const id = await startFromHome(page, 101);
  await expect(page.getByTestId("pr-title")).toHaveText("Add coupon helpers for the checkout page");

  for (const name of agents) await expect(page.getByTestId(`agent-${name}`)).toHaveAttribute("data-status", "done");
  await expect(page.getByTestId("agent-correctness")).toContainText("2 findings");
  await expect(page.getByTestId("round-lines")).toContainText("kept 5, dropped 2");
  await expect(page.getByTestId("round-lines")).toContainText("confirmed 4 of 5");

  const panel = page.getByTestId("approval-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("post-tally")).toContainText("4 inline");

  // Reject the mutable default comment.
  const i2 = page.getByTestId("item-i2");
  await i2.getByTestId("reject").click();
  await expect(i2).toHaveAttribute("data-outcome", "off");

  // Edit the path traversal comment.
  const i3 = page.getByTestId("item-i3");
  await i3.getByTestId("edit").click();
  await i3.getByRole("textbox").fill("Validate the coupon name before joining it into a path.");
  await i3.getByRole("button", { name: "Save" }).click();
  await expect(i3).toContainText("Validate the coupon name before joining it into a path.");
  await expect(i3.getByTestId("outcome")).toHaveText("Inline comment · edited");

  await expect(page.getByTestId("post-tally")).toContainText("3 inline");
  await page.getByTestId("post-button").click();
  await expect(page.getByTestId("confirm-post")).toContainText("Post 3 inline comments");
  await page.getByTestId("confirm-post-button").click();

  await expect(page.getByTestId("posted-title")).toContainText("Posted 3 inline comments");
  await expect(page.getByTestId("review-link")).toHaveAttribute("href", /pull\/101#demo-review/);
  await expect(page.locator('[data-step="Posted"]')).toHaveAttribute("data-state", "done");

  const snapshot = await (await request.get(`${API}/reviews/${id}`)).json();
  expect(snapshot.status).toBe("posted");
  expect(snapshot.review_stats).toMatchObject({ reject: 1, edit: 1 });

  await page.getByRole("link", { name: "History" }).click();
  await expect(page.getByTestId("history-table")).toContainText("sa1136/reviewpilot-sandbox#101");
});

test("refresh mid run recovers from the snapshot and the stream", async ({ page }) => {
  await startFromHome(page, 102);
  await expect(page.locator('[data-testid^="agent-"][data-status="running"]').first()).toBeVisible();
  await page.reload();
  for (const name of agents) await expect(page.getByTestId(`agent-${name}`)).toHaveAttribute("data-status", "done");
  await expect(page.getByTestId("approval-panel")).toBeVisible();
  await expect(page.getByTestId("item-i1")).toContainText("Identity comparison");
  await expect(page.getByTestId("round-lines")).toContainText("confirmed 4 of 5");
});

test("approve all leaves possible issues alone and posting an already posted review is clear", async ({ page }) => {
  await startFromHome(page, 103);
  await expect(page.getByTestId("approval-panel")).toBeVisible();
  await page.getByTestId("item-i1").getByTestId("reject").click();
  await page.getByTestId("approve-all").click();
  await expect(page.getByTestId("item-i1")).toHaveAttribute("data-outcome", "inline");
  await page.getByTestId("post-button").click();
  await page.getByTestId("confirm-post-button").click();
  await expect(page.getByTestId("posted-title")).toContainText("Posted 4 inline comments");

  // Starting the same PR again opens the finished review instead of running it twice.
  await page.goto("/");
  await page.getByLabel("Pull request link").fill(prLink(103));
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await expect(page.getByTestId("posted-panel")).toBeVisible();
});

test("clear errors: bad link, unknown review", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Pull request link").fill("https://github.com/o/r/issues/4");
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await expect(page.getByText("That's an issue link")).toBeVisible();
  await expect(page).toHaveURL("/");

  await page.goto("/reviews/bm9wZQ");
  await expect(page.getByText("Review not found")).toBeVisible();
});
