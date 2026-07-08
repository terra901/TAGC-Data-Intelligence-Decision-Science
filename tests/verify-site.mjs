import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const docsDir = join(root, "docs");
const indexPath = join(docsDir, "index.html");
const pdfPath = join(docsDir, "assets", "sealdone_3-2.pdf");
const imagePath = join(docsDir, "assets", "tgac-2025-second-place-certificate.png");
const solutionPdfPath = join(docsDir, "assets", "text-to-sql-architecture.pdf");
const architectureImagePath = join(docsDir, "assets", "text-to-sql-architecture-page-01.png");
const sourceDir = join(docsDir, "source");
const sourceReadmePath = join(sourceDir, "README.md");
const gitignorePath = join(root, ".gitignore");
const readmePath = join(root, "README.md");
const chineseReadmePath = join(root, "README.zh.md");

const expectedPdfHash = "1FD24D09D2E1D5EBBC887B75B59DCE129F63BE14D276B428C01C011C1189128C";

function readText(path) {
  return readFileSync(path, "utf8");
}

function assertIncludes(haystack, needles, context) {
  for (const needle of needles) {
    assert.ok(haystack.includes(needle), `${context} must include: ${needle}`);
  }
}

function walkFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...walkFiles(path));
    } else {
      files.push(path);
    }
  }
  return files;
}

assert.ok(existsSync(indexPath), "docs/index.html must exist");
assert.ok(existsSync(pdfPath), "certificate PDF must exist in docs/assets");
assert.ok(existsSync(imagePath), "certificate preview PNG must exist in docs/assets");
assert.ok(existsSync(solutionPdfPath), "solution PDF must be published in docs/assets");
assert.ok(existsSync(architectureImagePath), "architecture preview image must be published in docs/assets");
assert.ok(existsSync(sourceReadmePath), "sanitized source README must exist");

const html = readText(indexPath);
const readme = readText(readmePath);
const chineseReadme = readText(chineseReadmePath);
const sourceReadme = readText(sourceReadmePath);
const joinAnalyzer = readText(join(sourceDir, "knowledge-base", "1.1-schema-completion", "analyza_join.py"));
const configExample = readText(join(sourceDir, "pipeline", "config.example.py"));
const gitignore = readText(gitignorePath);

assertIncludes(
  html,
  [
    "TGAC 2025 二等奖证明与 Text-to-SQL 方案复盘",
    "Tencent Games Algorithm Competition 2025",
    "Second Place",
    "Data-Intelligence Decision Science",
    "数智决策科学赛道",
    "Help Me! KFC Grandpa",
    "Haizhen Gao",
    "Gang Xu",
    "Jiyun Chen",
    "https://github.com/gstranded",
    "2026-01-06",
    expectedPdfHash,
    "方案总览",
    "Agentic Workflow",
    "闭环知识进化",
    "Augmented Schema",
    "Positive Knowledge",
    "Verification Knowledge",
    "Few-shot CoT",
    "Execution & Fix",
    "History Guard",
    "Majority Vote",
    "代码与复现",
    "source/README.md",
    "source/pipeline/agent.py",
    "source/pipeline/config.example.py",
    "assets/text-to-sql-architecture.pdf",
    "https://github.com/terra901/TAGC-Data-Intelligence-Decision-Science",
    "https://terra901.github.io/TAGC-Data-Intelligence-Decision-Science/"
  ],
  "docs/index.html"
);

assert.match(
  html,
  /<img[^>]+src="assets\/tgac-2025-second-place-certificate\.png"[^>]+alt="TGAC 2025 Second Place certificate for team Help Me! KFC Grandpa"/
);
assert.match(
  html,
  /<img[^>]+src="assets\/text-to-sql-architecture-page-01\.png"[^>]+alt="Text-to-SQL architecture overview"/
);
assert.match(
  html,
  /<img[^>]+src="assets\/text-to-sql-architecture-page-01\.png"[^>]+loading="eager"/
);
assert.match(html, /<a[^>]+href="assets\/sealdone_3-2\.pdf"/);
assert.match(html, /<a[^>]+href="assets\/text-to-sql-architecture\.pdf"/);
assert.match(html, /<meta name="description" content="[^"]*TGAC 2025[^"]*Text-to-SQL[^"]*"/);

assertIncludes(
  readme,
  [
    "TGAC 2025 Solution Writeup and Proof",
    "English | [中文](README.zh.md)",
    "rank.png",
    "docs/source",
    "sanitized",
    "Help Me! KFC Grandpa",
    "[Haizhen Gao](https://github.com/gstranded)"
  ],
  "README.md"
);

assertIncludes(
  chineseReadme,
  [
    "TGAC-腾讯游戏算法-数智决策科学-第二名方案",
    "[English](README.md) | 中文",
    "rank.png",
    "docs/source",
    "脱敏",
    "Help Me! KFC Grandpa",
    "[Haizhen Gao](https://github.com/gstranded)"
  ],
  "README.zh.md"
);

assertIncludes(
  sourceReadme,
  [
    "Sanitized Source Snapshot",
    "No API keys",
    "Agentic Workflow",
    "Join Graph construction",
    "closed-loop knowledge evolution",
    "config.example.py"
  ],
  "docs/source/README.md"
);

assertIncludes(
  joinAnalyzer,
  [
    "def build_join_graph(",
    "def save_join_graph(",
    "join_graph.json",
    "adjacency",
    "verified_join_count"
  ],
  "docs/source/knowledge-base/1.1-schema-completion/analyza_join.py"
);

assertIncludes(
  configExample,
  [
    'OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")',
    'OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")',
    'OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")',
    'LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")'
  ],
  "docs/source/pipeline/config.example.py"
);

const requiredSourceFiles = [
  "pipeline/agent.py",
  "pipeline/prompts.py",
  "pipeline/utils.py",
  "pipeline/build_vector_db.py",
  "pipeline/config.example.py",
  "knowledge-base/1.1-schema-completion/BUILD_GUIDE.md",
  "knowledge-base/1.2-positive-knowledge/BUILD_GUIDE.md",
  "knowledge-base/1.3-verification-negative-constraints/BUILD_GUIDE.md",
  "knowledge-base/1.4-few-shot-cot/BUILD_GUIDE.md",
  "knowledge-base/full-knowledge-file/knowledge-files.md"
];

for (const file of requiredSourceFiles) {
  assert.ok(existsSync(join(sourceDir, file)), `sanitized source must include ${file}`);
}

assertIncludes(gitignore, ["/code/", "/final/"], ".gitignore");

const pdfHash = createHash("sha256").update(readFileSync(pdfPath)).digest("hex").toUpperCase();
assert.equal(pdfHash, expectedPdfHash);
assert.ok(statSync(imagePath).size > 100_000, "certificate preview should be a rendered image, not a placeholder");
assert.ok(statSync(architectureImagePath).size > 100_000, "architecture preview should be a rendered image, not a placeholder");

const secretPattern = /(sk-[A-Za-z0-9][A-Za-z0-9._-]{10,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16}|172\.22\.194\.247|ghz\d{4}\.{2}|PASSWORD\s*=\s*["'][^"']+["'])/;
const removedPublicConfigPattern = /(https:\/\/api\.wapq\.cn\/v1|gpt-5\.5|LLM_PROVIDER\s*=\s*os\.getenv\("LLM_PROVIDER",\s*"openai"\))/;
const textExtensions = new Set([".html", ".md", ".mjs", ".py", ".json", ".txt", ".css"]);
const publicFiles = [
  readmePath,
  chineseReadmePath,
  join(root, ".gitignore"),
  ...walkFiles(docsDir)
].filter((path) => textExtensions.has(extname(path)));

for (const file of publicFiles) {
  const text = readText(file);
  assert.ok(!secretPattern.test(text), `public file must not contain secrets: ${relative(root, file)}`);
  assert.ok(!removedPublicConfigPattern.test(text), `public file must not contain removed public LLM defaults: ${relative(root, file)}`);
  assert.ok(!/[鏁甯楂璁闄][^\n]{0,12}[�€]/.test(text), `public file appears mojibaked: ${relative(root, file)}`);
}

console.log("Site verification passed.");
