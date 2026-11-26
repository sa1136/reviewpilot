// The public demo: the first visit, two separate browser sessions, and the event stream.
import { expect, test, type Page } from "@playwright/test";

const reviewId = (page: Page) => page.url().split("/reviews/")[1];

async function tryDemo(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("demo-notice")).toContainText("This is a demo");
  await page.getByRole("button", { name: "Try the demo" }).click();
  await page.waitForURL(/\/reviews\/[A-Za-z0-9_-]+$/);
  return reviewId(page);
}

async function post(page: Page) {
  await page.getByTestId("post-button").click();
  await page.getByTestId("confirm-post-button").click();
  await expect(page.getByTestId("posted-panel")).toBeVisible();
}

test("two browser sessions never see or change each other's demo", async ({ browser }) => {
  const alice = await (await browser.newContext()).newPage();
  const bob = await (await browser.newContext()).newPage();

  const aliceId = await tryDemo(alice);
  await expect(alice.getByTestId("approval-panel")).toBeVisible();
  const i3 = alice.getByTestId("item-i3");
  await i3.getByTestId("edit").click();
  await i3.getByRole("textbox").fill("Only Alice should ever read this.");
  await i3.getByRole("button", { name: "Save" }).click();

  const [cookie] = await alice.context().cookies();
  expect(cookie).toMatchObject({ name: "rp_visitor", httpOnly: true, sameSite: "Lax" });
  expect(await alice.evaluate(() => document.cookie)).not.toContain("rp_visitor");

  // Bob: the same PR gives a fresh demo of his own.
  const bobId = await tryDemo(bob);
  expect(bobId).not.toBe(aliceId);
  await expect(bob.getByTestId("approval-panel")).toBeVisible();
  await expect(bob.getByTestId("approval-panel")).not.toContainText("Only Alice");
  await bob.getByRole("link", { name: "History" }).click();
  await expect(bob.getByTestId("history-table").locator("tbody tr")).toHaveCount(1);

  // Bob: Alice's direct link, and her review through the web proxy, even claiming to be her.
  await bob.goto(`/reviews/${aliceId}`);
  await expect(bob.getByText("Review not found")).toBeVisible();
  const forged = { "x-reviewpilot-visitor": cookie.value, authorization: "Bearer guess" };
  for (const path of [`/api/reviews/${aliceId}`, `/api/reviews/${aliceId}/events`]) {
    expect((await bob.request.get(path, { headers: forged })).status()).toBe(404);
  }
  const attack = await bob.request.post(`/api/reviews/${aliceId}/decisions`, {
    headers: forged,
    data: { decisions: [{ id: "i1", action: "reject" }], post: true },
  });
  expect(attack.status()).toBe(404);
  const bobList = await (await bob.request.get("/api/reviews", { headers: forged })).json();
  expect(bobList.map((r: { review_id: string }) => r.review_id)).toEqual([bobId]);

  // Alice is untouched, posts her own, and gets it back after a refresh.
  await post(alice);
  await alice.reload();
  await expect(alice.getByTestId("posted-panel")).toBeVisible();
  const mine = await (await alice.request.get(`/api/reviews/${aliceId}`)).json();
  expect(mine.review_stats).toMatchObject({ edit: 1, reject: 0 });

  // Bob's is still waiting for him.
  await bob.goto(`/reviews/${bobId}`);
  await expect(bob.getByTestId("approval-panel")).toBeVisible();
});

test("a stream that drops reconnects with Last-Event-ID, then rests while waiting for approval", async ({ page }) => {
  const streams: (string | undefined)[] = [];
  await page.route("**/api/reviews/*/events", async (route) => {
    streams.push(route.request().headers()["last-event-id"]);
    // The first connection ends at once, as when a function hits its time limit.
    if (streams.length === 1) {
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: ": cut\n\n" });
    } else {
      await route.continue();
    }
  });
  await tryDemo(page);
  await expect(page.getByTestId("approval-panel")).toBeVisible();
  for (const name of ["correctness", "security", "quality"]) {
    await expect(page.getByTestId(`agent-${name}`)).toHaveAttribute("data-status", "done");
  }
  expect(streams.length).toBeGreaterThanOrEqual(2);
  expect(streams.every((id) => id !== undefined)).toBe(true);

  const opened = streams.length;
  await page.waitForTimeout(10_000); // longer than the longest reconnect backoff
  expect(streams.length).toBe(opened);
  await expect(page.getByTestId("reconnecting")).toHaveCount(0);

  await post(page); // the stream reopens for the posting events
  await expect(page.locator('[data-step="Posted"]')).toHaveAttribute("data-state", "done");
});

test("a second tab catches up when you come back to it", async ({ context }) => {
  const first = await context.newPage();
  const id = await tryDemo(first);
  await expect(first.getByTestId("approval-panel")).toBeVisible();
  const second = await context.newPage();
  await second.goto(`/reviews/${id}`);
  await expect(second.getByTestId("approval-panel")).toBeVisible();

  await post(first);
  await second.bringToFront();
  await second.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(second.getByTestId("posted-panel")).toBeVisible();
});

test("approving a demo that has expired explains what happened", async ({ page }) => {
  await tryDemo(page);
  await expect(page.getByTestId("approval-panel")).toBeVisible();
  // As after the free server restarts: the API no longer has the review.
  await page.route("**/api/reviews/*/decisions", (route) =>
    route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"unknown review"}' }),
  );
  await page.getByTestId("post-button").click();
  await page.getByTestId("confirm-post-button").click();
  await expect(page.getByText("This review is no longer available")).toBeVisible();
  await expect(page.getByRole("link", { name: "Start a new review" })).toBeVisible();
});
