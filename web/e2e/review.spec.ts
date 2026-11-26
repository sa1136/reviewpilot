import { expect, test, type Page } from "@playwright/test";

// The public demo has no link input: every test starts from Try the demo (a fresh run each time).
const DEMO_PR = "https://github.com/sa1136/reviewpilot-sandbox/pull/4";
const agents = ["correctness", "security", "quality"];

async function startFromHome(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("demo-badge")).toBeVisible();
  await page.getByRole("button", { name: "Try the demo" }).click();
  await page.waitForURL(/\/reviews\/[A-Za-z0-9_-]+$/);
  return page.url().split("/reviews/")[1];
}

test("try the demo, watch the agents, reject one, edit one, post", async ({ page }) => {
  const id = await startFromHome(page);
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
  // The demo says nothing was sent, and never links to a made up GitHub result.
  await expect(page.getByTestId("demo-posted-note")).toContainText("nothing was sent to GitHub");
  await expect(page.getByTestId("review-link")).toHaveCount(0);
  await expect(page.getByTestId("pr-title")).toBeVisible();
  await expect(page.locator('[data-step="Posted"]')).toHaveAttribute("data-state", "done");

  const snapshot = await (await page.request.get(`/api/reviews/${id}`)).json();
  expect(snapshot.status).toBe("posted");
  expect(snapshot.review_stats).toMatchObject({ reject: 1, edit: 1 });

  await page.getByRole("link", { name: "History" }).click();
  await expect(page.getByTestId("history-table")).toContainText("sa1136/reviewpilot-sandbox#4");
});

test("refresh mid run recovers from the snapshot and the stream", async ({ page }) => {
  await startFromHome(page);
  await expect(page.locator('[data-testid^="agent-"][data-status="running"]').first()).toBeVisible();
  await page.reload();
  for (const name of agents) await expect(page.getByTestId(`agent-${name}`)).toHaveAttribute("data-status", "done");
  await expect(page.getByTestId("approval-panel")).toBeVisible();
  await expect(page.getByTestId("item-i1")).toContainText("Identity comparison");
  await expect(page.getByTestId("round-lines")).toContainText("confirmed 4 of 5");
});

test("approve all leaves possible issues alone and posting an already posted review is clear", async ({ page }) => {
  await startFromHome(page);
  await expect(page.getByTestId("approval-panel")).toBeVisible();
  await page.getByTestId("item-i1").getByTestId("reject").click();
  await page.getByTestId("approve-all").click();
  await expect(page.getByTestId("item-i1")).toHaveAttribute("data-outcome", "inline");
  await page.getByTestId("post-button").click();
  await page.getByTestId("confirm-post-button").click();
  await expect(page.getByTestId("posted-title")).toContainText("Posted 4 inline comments");

  // Starting the same PR again (not fresh) opens the finished review instead of running it twice.
  const again = await (await page.request.post("/api/reviews", { data: { pr_url: DEMO_PR } })).json();
  expect(again.status).toBe("posted");
  await page.goto(`/reviews/${again.review_id}`);
  await expect(page.getByTestId("posted-panel")).toBeVisible();
});

test("the public demo has no link input; unknown review is clear", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("demo-notice")).toBeVisible();
  await expect(page.getByRole("button", { name: "Try the demo" })).toBeVisible();
  await expect(page.getByLabel("Pull request link")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(/paste/i);
  await expect(page.getByText("No demo reviews yet. Press Try the demo above.")).toBeVisible();
  await page.getByRole("link", { name: "History", exact: true }).click();
  await expect(page.getByTestId("history-table")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/paste/i);

  await page.goto("/reviews/bm9wZQ");
  await expect(page.getByText("Review not found")).toBeVisible();
});

test("the approval screen pings health every 5 minutes, for 30 minutes", async ({ page }) => {
  await page.clock.install();
  await startFromHome(page);
  await expect(page.getByTestId("approval-panel")).toBeVisible();
  let pings = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/api/health")) pings += 1;
  });
  await page.clock.runFor("04:50");
  expect(pings).toBe(0);
  await page.clock.runFor("00:20");
  await expect.poll(() => pings).toBe(1);
  await page.clock.runFor("40:00");
  await expect.poll(() => pings).toBe(6);
});
