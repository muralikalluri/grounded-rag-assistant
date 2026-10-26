import { expect, test, type Page } from "@playwright/test";

import { loginViaKeycloak } from "../e2e/helpers";

// One continuous browser session: Alice (HR) gets a cited answer and opens the source page;
// Bob (Engineering) asks the same question and gets "not found"; then a question the
// documents do not cover gets "not found" too. Playwright records the video; make demo-gif
// converts it to docs/img/demo.gif.
const SALARY_Q = "What is the salary band for L5 engineers?";
const OFF_CORPUS_Q = "What is the company policy on working remotely from abroad?";

// Contexts made with browser.newContext() ignore the config's `video` option, so each one asks
// for its recording directly (one subfolder per user; make demo-gif joins them oldest first).
const recordVideo = (who: string) => ({
  dir: `demo/.recording/${who}`,
  size: { width: 1280, height: 800 },
});

async function ask(page: Page, question: string) {
  const input = page.getByTestId("question-input");
  await expect(input).toHaveAttribute("data-ready", "true");
  await input.pressSequentially(question, { delay: 25 });
  const answered = page.waitForResponse((r) => r.url().endsWith("/api/chat") && r.request().method() === "POST");
  await page.getByTestId("ask-button").click();
  await answered;
}

async function pause(page: Page, ms: number) {
  await page.waitForTimeout(ms);
}

test("record demo", async ({ browser }) => {
  // Alice: cited answer, then click the citation to show the highlighted source page.
  const aliceCtx = await browser.newContext({ recordVideo: recordVideo("alice") });
  const alice = await aliceCtx.newPage();
  await loginViaKeycloak(alice, "alice");
  await pause(alice, 800);
  await ask(alice, SALARY_Q);
  await expect(alice.getByTestId("answer")).toBeVisible();
  await pause(alice, 2500);
  await alice.locator('[data-testid^="cite-"]').first().click();
  await expect(alice.getByTestId("citation-page")).toHaveAttribute("data-found", "true");
  await pause(alice, 3500);
  await aliceCtx.close();

  // Bob: same question, but HR documents are never retrieved for him.
  const bobCtx = await browser.newContext({ recordVideo: recordVideo("bob") });
  const bob = await bobCtx.newPage();
  await loginViaKeycloak(bob, "bob");
  await pause(bob, 800);
  await ask(bob, SALARY_Q);
  await expect(bob.getByTestId("not-found")).toBeVisible();
  await pause(bob, 3000);

  // A question the documents do not answer: "not found", not a guess.
  await bob.goto("/");
  await ask(bob, OFF_CORPUS_Q);
  await expect(bob.getByTestId("not-found")).toBeVisible();
  await pause(bob, 3000);
  await bobCtx.close();
});
