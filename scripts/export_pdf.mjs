// Exports Markdown deliverables to styled PDFs: pandoc converts Markdown to HTML, then headless Chromium
// (the Playwright copy already installed for the e2e tests) prints the page.
// Usage (from the repo root): node scripts/export_pdf.mjs sample-deliverable/POC_HANDOVER.md
// Needs: pandoc, and `pnpm install` + `pnpm exec playwright install chromium` done once in frontend/.
import { execFileSync } from "node:child_process";
import { appendFileSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "frontend", "package.json"));
const { chromium } = require("@playwright/test");

const inputs = process.argv.slice(2);
if (inputs.length === 0) {
  console.error("usage: node scripts/export_pdf.mjs <input.md> [...]");
  process.exit(2);
}
const browser = await chromium.launch();
try {
  for (const input of inputs) {
    const html = join(tmpdir(), `export-${basename(input)}.html`);
    const title = readFileSync(input, "utf8").split("\n")[0].replace(/^#\s*/, "");
    execFileSync("pandoc", [input, "-f", "gfm", "-t", "html5", "--standalone", "--embed-resources",
      "--css", join(root, "scripts", "pdf-style.css"), "--metadata", `title=${title}`,
      "--metadata", `pagetitle=${title}`, "-V", "document-css=false", "-o", html]);
    // The deliverable already starts with an H1, so hide pandoc's duplicate title block.
    appendFileSync(html, "<style>header#title-block-header{display:none}</style>");
    const page = await browser.newPage();
    await page.goto(pathToFileURL(html).href, { waitUntil: "load" });
    const out = input.replace(/\.md$/, ".pdf");
    const safe = title.replace(/[<&]/g, "");
    await page.pdf({
      path: out, format: "A4", printBackground: true, displayHeaderFooter: true,
      headerTemplate: "<span></span>",
      footerTemplate: `<div style="font-size:8px;width:100%;text-align:center;color:#5b6675">${safe} - page <span class="pageNumber"></span> of <span class="totalPages"></span></div>`,
      margin: { top: "18mm", bottom: "20mm", left: "15mm", right: "15mm" },
    });
    await page.close();
    console.log(`wrote ${out}`);
  }
} finally {
  await browser.close();
}
