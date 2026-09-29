import { expect, type Page } from "@playwright/test";

// Log in through Keycloak's real login form (authorization code + PKCE). The direct-access
// (password) grant is disabled on the client, so this is the only way in.
export async function loginViaKeycloak(page: Page, user: string) {
  await page.goto("/");
  await expect(page).toHaveURL(/:8080\/realms\/northwind\//); // redirected to the identity provider
  await page.locator("#username").fill(user);
  await page.locator("#password").fill(`${user}-demo-only`);
  await page.locator("#kc-login").click();
  await expect(page.getByTestId("signed-in-as")).toContainText(user);
}
