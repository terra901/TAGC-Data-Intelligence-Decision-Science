# TGAC 2025 Proof Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a static GitHub Pages proof site for the TGAC 2025 Second Place certificate.

**Architecture:** The site is a static `/docs` page with local certificate assets and a no-dependency Node verification script. GitHub Pages will serve `/docs/index.html`; assets stay under `/docs/assets` so links are stable and easy to audit.

**Tech Stack:** HTML, CSS, local PDF/PNG assets, Node.js built-in modules, GitHub Pages.

---

## File Structure

- Create `tests/verify-site.mjs`: checks that required content, links, image metadata, and SHA-256 are present.
- Create `docs/index.html`: proof page with bilingual certificate facts, direct PDF link, certificate preview, hash, and disclaimers.
- Copy `sealdone_3-2.pdf` to `docs/assets/sealdone_3-2.pdf`.
- Copy rendered preview `tmp/pdfs/sealdone_3-2-1.png` to `docs/assets/tgac-2025-second-place-certificate.png`.
- Create `README.md`: repository overview, proof URL, verification hash, and deployment/auth notes.
- Create `.gitignore`: excludes temporary render output and local OS/editor noise.

### Task 1: Verification Script

**Files:**
- Create: `tests/verify-site.mjs`

- [ ] **Step 1: Write the failing test**

```javascript
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname;
const indexPath = join(root, "docs", "index.html");
const pdfPath = join(root, "docs", "assets", "sealdone_3-2.pdf");
const imagePath = join(root, "docs", "assets", "tgac-2025-second-place-certificate.png");

assert.ok(existsSync(indexPath), "docs/index.html must exist");
assert.ok(existsSync(pdfPath), "certificate PDF must exist in docs/assets");
assert.ok(existsSync(imagePath), "certificate preview PNG must exist in docs/assets");

const html = readFileSync(indexPath, "utf8");
const requiredText = [
  "Tencent Games Algorithm Competition 2025",
  "Second Place",
  "Data-Intelligence Decision Science",
  "数智决策科学赛道",
  "Help Me! KFC Grandpa",
  "Haizhen Gao",
  "Gang Xu",
  "Jiyun Chen",
  "2026-01-06",
  "1FD24D09D2E1D5EBBC887B75B59DCE129F63BE14D276B428C01C011C1189128C",
  "https://tgac.tencent.com/",
  "https://github.com/terra901/TAGC-Data-Intelligence-Decision-Science",
  "https://terra901.github.io/TAGC-Data-Intelligence-Decision-Science/"
];

for (const text of requiredText) {
  assert.ok(html.includes(text), `docs/index.html must include: ${text}`);
}

assert.match(html, /<img[^>]+src="assets\/tgac-2025-second-place-certificate\.png"[^>]+alt="TGAC 2025 Second Place certificate for team Help Me! KFC Grandpa"/);
assert.match(html, /<a[^>]+href="assets\/sealdone_3-2\.pdf"/);
assert.match(html, /<meta name="description" content="[^"]*TGAC 2025[^"]*Second Place[^"]*"/);

const pdfHash = createHash("sha256").update(readFileSync(pdfPath)).digest("hex").toUpperCase();
assert.equal(pdfHash, "1FD24D09D2E1D5EBBC887B75B59DCE129F63BE14D276B428C01C011C1189128C");
assert.ok(statSync(imagePath).size > 100_000, "certificate preview should be a rendered image, not a placeholder");

console.log("Site verification passed.");
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/verify-site.mjs`

Expected: FAIL with `docs/index.html must exist`.

- [ ] **Step 3: Commit test**

```bash
git add tests/verify-site.mjs
git commit -m "test: add proof site verification"
```

### Task 2: Static Site and Assets

**Files:**
- Create: `docs/index.html`
- Create: `docs/assets/sealdone_3-2.pdf`
- Create: `docs/assets/tgac-2025-second-place-certificate.png`
- Create: `.gitignore`

- [ ] **Step 1: Copy certificate assets**

```powershell
New-Item -ItemType Directory -Force docs/assets | Out-Null
Copy-Item -LiteralPath sealdone_3-2.pdf -Destination docs/assets/sealdone_3-2.pdf
Copy-Item -LiteralPath tmp/pdfs/sealdone_3-2-1.png -Destination docs/assets/tgac-2025-second-place-certificate.png
```

- [ ] **Step 2: Create `docs/index.html`**

Create a single static page containing:

- Title: `TGAC 2025 Second Place Proof`
- Hero claim: `Tencent Games Algorithm Competition 2025 - Second Place`
- Track: `Data-Intelligence Decision Science / 数智决策科学赛道`
- Team: `Help Me! KFC Grandpa`
- Members: `Haizhen Gao`, `Gang Xu`, `Jiyun Chen`
- Date: `2026-01-06`
- Links to:
  - `assets/sealdone_3-2.pdf`
  - `https://tgac.tencent.com/`
  - `https://github.com/terra901/TAGC-Data-Intelligence-Decision-Science`
  - `https://terra901.github.io/TAGC-Data-Intelligence-Decision-Science/`
- Certificate preview image:
  - `src="assets/tgac-2025-second-place-certificate.png"`
  - `alt="TGAC 2025 Second Place certificate for team Help Me! KFC Grandpa"`
- SHA-256 hash exactly:
  - `1FD24D09D2E1D5EBBC887B75B59DCE129F63BE14D276B428C01C011C1189128C`

- [ ] **Step 3: Create `.gitignore`**

```gitignore
tmp/
.DS_Store
Thumbs.db
```

- [ ] **Step 4: Run verification**

Run: `node tests/verify-site.mjs`

Expected: PASS with `Site verification passed.`

- [ ] **Step 5: Commit static site**

```bash
git add .gitignore docs/index.html docs/assets/sealdone_3-2.pdf docs/assets/tgac-2025-second-place-certificate.png
git commit -m "feat: add tgac proof page"
```

### Task 3: Repository README and Deployment Notes

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create README**

The README must include:

- Public proof URL: `https://terra901.github.io/TAGC-Data-Intelligence-Decision-Science/`
- Repository URL: `https://github.com/terra901/TAGC-Data-Intelligence-Decision-Science`
- Certificate PDF path: `docs/assets/sealdone_3-2.pdf`
- SHA-256 hash: `1FD24D09D2E1D5EBBC887B75B59DCE129F63BE14D276B428C01C011C1189128C`
- GitHub Pages setting: deploy from `main` branch and `/docs` folder.
- Note that GitHub passwords are not used for deployment.

- [ ] **Step 2: Run verification**

Run: `node tests/verify-site.mjs`

Expected: PASS with `Site verification passed.`

- [ ] **Step 3: Commit README**

```bash
git add README.md
git commit -m "docs: add repository proof instructions"
```

### Task 4: GitHub Remote Preparation

**Files:**
- Modify local git branch and remote only.

- [ ] **Step 1: Rename branch**

Run: `git branch -M main`

- [ ] **Step 2: Add remote**

Run: `git remote add origin https://github.com/terra901/TAGC-Data-Intelligence-Decision-Science.git`

- [ ] **Step 3: Verify repository state**

Run: `git status --short --branch`

Expected: clean working tree on `main`.

- [ ] **Step 4: Push after safe GitHub authentication**

Use one of these safe methods:

```powershell
git push -u origin main
```

If Git asks for credentials, use username `terra901` and a GitHub personal access token, not the account password.

### Self-Review

- Spec coverage: all design requirements map to Tasks 1-4.
- Placeholder scan: no TBD/TODO/fill-in placeholders.
- Type and path consistency: asset names match between tests, HTML, and README.
