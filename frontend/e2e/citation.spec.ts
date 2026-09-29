import { expect, test, type Browser, type Page } from "@playwright/test";

import { loginViaKeycloak } from "./helpers";

// Leave policy: "Carry forward" is the 4th section, each section on its own page (SPEC demo).
const LEAVE_Q = "How many days of annual leave can be carried forward?";
const EXPECTED_PAGE = 4;

type Cite = { n: number; chunk_id: string; document_id: string; page_start: number; quote: string };
type Answer = { status: string; generator: string; citations: Cite[] };

async function asUser(browser: Browser, user: "alice" | "bob") {
  const context = await browser.newContext({ storageState: `e2e/.auth/${user}.json` });
  return { context, page: await context.newPage() };
}

async function ask(page: Page, question: string): Promise<Answer> {
  await page.goto("/");
  const input = page.getByTestId("question-input");
  // data-ready flips to true only after hydration; filling earlier would race React.
  await expect(input).toHaveAttribute("data-ready", "true");
  await expect(page.getByTestId("ask-button")).toBeDisabled(); // disabled while the input is empty
  const answered = page.waitForResponse((r) => r.url().endsWith("/api/chat") && r.request().method() === "POST");
  await input.fill(question);
  await expect(page.getByTestId("ask-button")).toBeEnabled();
  await page.getByTestId("ask-button").click();
  const res = await answered;
  expect(res.status()).toBe(200);
  const closing = (await res.text())
    .split("\n\n")
    .filter((b) => b.includes("event: answer") || b.includes("event: error"));
  expect(closing).toHaveLength(1); // exactly one closing event
  expect(closing[0]!).toContain("event: answer");
  return JSON.parse(closing[0]!.split("data: ")[1]!) as Answer;
}

test.describe("signed in as alice (HR)", () => {
  test.use({ storageState: "e2e/.auth/alice.json" });

  test("ask -> click the citation -> the correct page is shown with the quote highlighted", async ({ page }) => {
    const answer = await ask(page, LEAVE_Q);

    // A real answer, not a vacuous pass on the "not found" path.
    expect(answer.status).toBe("answered");
    expect(answer.generator).toBe("extractive");
    await expect(page.getByTestId("answer")).toBeVisible();
    await expect(page.getByTestId("test-mode-badge")).toBeVisible(); // test mode is never disguised
    const cite = answer.citations.find((c) => c.quote.includes("carry forward a maximum of 10 days"));
    expect(cite, "expected a citation for the carry-forward rule").toBeTruthy();
    expect(cite!.page_start).toBe(EXPECTED_PAGE);

    // The "verify in seconds" path: click -> highlighted page visible.
    const previewLoaded = page.waitForResponse((r) => /\/chunks\/[^/]+\/page\?page=/.test(r.url()));
    const started = Date.now();
    await page.getByTestId(`cite-${cite!.n}`).click();
    const preview = await previewLoaded;
    expect(preview.status()).toBe(200);
    expect(preview.headers()["content-type"]).toBe("image/png");
    expect(preview.headers()["cache-control"]).toBe("private, no-store");

    const panelImg = page.getByTestId("citation-page");
    await expect(panelImg).toHaveAttribute("data-page", String(EXPECTED_PAGE));
    await expect(panelImg).toHaveAttribute("data-found", "true");
    await expect(page.getByTestId("citation-title")).toContainText("Leave Policy");
    await expect(page.getByTestId("citation-quote")).toContainText("carry forward a maximum of 10 days");
    await expect(page.getByTestId("highlight-rect").first()).toBeVisible();
    expect(Date.now() - started).toBeLessThan(10_000); // SPEC §8: verify against the page in < 10 s

    // The image decoded, and the highlight box really sits on page text (not on white space).
    await expect
      .poll(() => panelImg.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0))
      .toBe(true);
    const inkUnderHighlight = await page.evaluate(() => {
      const img = document.querySelector<HTMLImageElement>('[data-testid="citation-page"]')!;
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d")!;
      ctx.drawImage(img, 0, 0);
      let dark = 0;
      for (const el of document.querySelectorAll<HTMLElement>('[data-testid="highlight-rect"]')) {
        const x = (parseFloat(el.style.left) / 100) * canvas.width;
        const y = (parseFloat(el.style.top) / 100) * canvas.height;
        const w = (parseFloat(el.style.width) / 100) * canvas.width;
        const h = (parseFloat(el.style.height) / 100) * canvas.height;
        const data = ctx.getImageData(x, y, Math.max(1, w), Math.max(1, h)).data;
        for (let i = 0; i < data.length; i += 4) {
          if ((data[i] ?? 255) + (data[i + 1] ?? 255) + (data[i + 2] ?? 255) < 300) dark++;
        }
      }
      return dark;
    });
    expect(inkUnderHighlight).toBeGreaterThan(50);
  });

  test("switching between citations shows the selected source, never a stale one", async ({ page }) => {
    const answer = await ask(page, "What is the daily meal allowance during business travel?");
    expect(answer.status).toBe("answered");
    expect(answer.citations.length).toBeGreaterThanOrEqual(2);
    const [c1, c2] = answer.citations as [Cite, Cite];
    expect(c1.chunk_id).not.toBe(c2.chunk_id);

    // Make citation 1's source request slow, so it would resolve AFTER citation 2's if unguarded.
    let slowDone: () => void = () => {};
    const slowFinished = new Promise<void>((r) => (slowDone = r));
    await page.route(new RegExp(`/chunks/${c1.chunk_id}$`), async (route) => {
      await new Promise((r) => setTimeout(r, 1500));
      await route.continue().catch(() => {}); // the client aborts this request: that is the point
      slowDone();
    });
    await page.getByTestId(`cite-${c1.n}`).click();
    await page.getByTestId(`cite-${c2.n}`).click();
    const panel = page.getByTestId("citation-panel");
    await expect(panel).toHaveAttribute("data-citation", String(c2.n));
    await expect(page.getByTestId("citation-title")).toContainText(/Version 1|Version 2/);
    const titleAfterSecond = await page.getByTestId("citation-title").innerText();

    await slowFinished; // the delayed request has been forwarded ...
    await page.waitForLoadState("networkidle"); // ... now let its response arrive and be processed
    await expect(panel).toHaveAttribute("data-citation", String(c2.n));
    await expect(page.getByTestId("citation-title")).toHaveText(titleAfterSecond);
    await expect(page.getByTestId("citation-page")).toHaveAttribute("data-page", String(c2.page_start));
    // data-chunk-id comes from the LOADED state (not props): a stale response would show c1's chunk.
    await expect(page.getByTestId("citation-page")).toHaveAttribute("data-chunk-id", c2.chunk_id);
  });

  test("no-answer questions abstain instead of guessing", async ({ page }) => {
    const a = await ask(page, "What is the airspeed velocity of an unladen swallow?");
    expect(a.status).toBe("not_found");
    await expect(page.getByTestId("not-found")).toBeVisible();
  });

  test("CSRF: a state-changing request without our origin and header is refused", async ({ page }) => {
    await page.goto("/");
    const r1 = await page.request.post("/api/chat", { data: { question: "hi" } }); // no Origin / X-Requested-With
    expect(r1.status()).toBe(403);
    const r2 = await page.request.post("/api/chat", {
      data: { question: "hi" },
      headers: { origin: "https://evil.example", "x-requested-with": "fetch" },
    });
    expect(r2.status()).toBe(403);
    const logout = await page.request.post("/auth/logout", { headers: { origin: "https://evil.example" }, maxRedirects: 0 });
    expect(logout.status()).toBe(403);
    await page.goto("/");
    await expect(page.getByTestId("signed-in-as")).toContainText("alice"); // still signed in
  });
});

test.describe("access control end to end", () => {
  test("bob cannot open HR content, and his HR question is a calm 'not found'", async ({ browser }) => {
    const alice = await asUser(browser, "alice");
    const a = await ask(alice.page, "What is the salary band for L5 engineers?");
    expect(a.status).toBe("answered"); // alice (HR) can read it
    const hr = a.citations[0]!;
    const url = `/api/documents/${hr.document_id}/chunks/${hr.chunk_id}/page?page=${hr.page_start}`;
    expect((await alice.page.request.get(url)).status()).toBe(200); // so bob's 404 below is meaningful

    const bob = await asUser(browser, "bob");
    await bob.page.goto("/");
    const denied = await bob.page.request.get(url);
    const missing = await bob.page.request.get(
      `/api/documents/${hr.document_id}/chunks/00000000-0000-4000-8000-000000000000/page?page=${hr.page_start}`,
    );
    expect(denied.status()).toBe(404);
    expect(await denied.text()).toBe(await missing.text()); // indistinguishable from "does not exist"

    const b = await ask(bob.page, "What is the salary band for L5 engineers?");
    expect(b.status).toBe("not_found");
    await expect(bob.page.getByTestId("not-found")).toBeVisible();
    await expect(bob.page.getByTestId("error-card")).toHaveCount(0);
    await expect(bob.page.getByTestId("not-found")).not.toContainText("80,000");
    await alice.context.close();
    await bob.context.close();
  });

  test("each user sees exactly their own groups' documents", async ({ browser }) => {
    const bob = await asUser(browser, "bob");
    const a = await ask(bob.page, "On which day of the week are production deployments frozen?");
    expect(a.status).toBe("answered"); // engineering runbook: bob may read it
    await bob.context.close();
    const alice = await asUser(browser, "alice");
    const denied = await ask(alice.page, "On which day of the week are production deployments frozen?");
    expect(denied.status).toBe("not_found"); // alice is HR: no engineering access
    await alice.context.close();
  });
});

test.describe("authentication", () => {
  test("an unauthenticated visit is sent to Keycloak, and the API is closed to anonymous callers", async ({ browser }) => {
    const context = await browser.newContext(); // no stored state
    const page = await context.newPage();
    await page.goto("/");
    await expect(page).toHaveURL(/:8080\/realms\/northwind\/protocol\/openid-connect\/auth/);
    expect(new URL(page.url()).searchParams.get("code_challenge_method")).toBe("S256");
    expect((await context.request.get("/api/documents/00000000-0000-4000-8000-000000000000/chunks/00000000-0000-4000-8000-000000000000")).status()).toBe(401);

    // The backend itself rejects everything but a real access token (no dev header, no forgery).
    const api = "http://localhost:8000/api/chat";
    const body = { question: "hi" };
    expect((await context.request.post(api, { data: body })).status()).toBe(401);
    expect((await context.request.post(api, { data: body, headers: { "x-dev-user": "alice" } })).status()).toBe(401);
    expect((await context.request.post(api, { data: body, headers: { authorization: "Bearer not.a.token" } })).status()).toBe(401);
    await context.close();
  });

  test("wrong password stays on the login page", async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto("/");
    await page.locator("#username").fill("alice");
    await page.locator("#password").fill("not-her-password");
    await page.locator("#kc-login").click();
    await expect(page).toHaveURL(/:8080\/realms\/northwind\//);
    await expect(page.locator("#input-error, .pf-m-error, [class*=error]").first()).toBeVisible();
    await context.close();
  });

  test("sign out ends the session everywhere", async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await loginViaKeycloak(page, "alice"); // a fresh login: logging out must not spoil the shared state
    await page.getByTestId("sign-out").click();
    await expect(page).toHaveURL(/localhost:3000\/?$|:8080\/realms\/northwind\//);
    expect((await context.request.get("/api/documents/00000000-0000-4000-8000-000000000000/chunks/00000000-0000-4000-8000-000000000000")).status()).toBe(401);
    await page.goto("/");
    await expect(page).toHaveURL(/:8080\/realms\/northwind\//); // must log in again
    await context.close();
  });
});
