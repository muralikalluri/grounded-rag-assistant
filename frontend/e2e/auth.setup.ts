import { test as setup } from "@playwright/test";

import { loginViaKeycloak } from "./helpers";

// Log in once per demo user and keep the resulting browser state for the other tests.
for (const user of ["alice", "bob"]) {
  setup(`sign in as ${user}`, async ({ page }) => {
    await loginViaKeycloak(page, user);
    await page.context().storageState({ path: `e2e/.auth/${user}.json` });
  });
}
