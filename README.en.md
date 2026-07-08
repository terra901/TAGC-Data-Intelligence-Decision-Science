# TGAC 2025 Solution Writeup and Proof

[中文](README.md) | English

![TGAC 2025 Data-Intelligence Decision Science Second Place](TAGC.png)

## Overview

This repository publishes a public proof page and a sanitized technical writeup for a Tencent Games Algorithm Competition 2025 Second Place award in the Data-Intelligence Decision Science track.

- Public page: https://terra901.github.io/TAGC-Data-Intelligence-Decision-Science/
- GitHub repository: https://github.com/terra901/TAGC-Data-Intelligence-Decision-Science
- Certificate PDF: `docs/assets/sealdone_3-2.pdf`
- Architecture PDF: `docs/assets/text-to-sql-architecture.pdf`
- Sanitized source snapshot: `docs/source`
- TGAC official website: https://tgac.tencent.com/

## Award Claim

- Event: Tencent Games Algorithm Competition 2025
- Award: Second Place
- Track: Data-Intelligence Decision Science / 数智决策科学赛道
- Team: 帮帮我！肯德基爷爷
- Members: 高海圳 / Haizhen Gao, 许刚 / Gang Xu, 陈继昀 / Jiyun Chen
- Certificate date: 2026-01-06

## Solution Material

The public page includes a Text-to-SQL solution writeup covering:

- Agentic Workflow
- closed-loop knowledge evolution
- Augmented Schema
- Positive Knowledge
- Verification Knowledge
- Few-shot CoT
- Execution & Fix
- History Guard
- Majority Vote and LLM Judge arbitration

The `docs/source` folder contains a sanitized source snapshot. It keeps the key module structure and selected build guides, but removes API keys, private database hosts, logs, model caches, large generated artifacts, and raw sensitive configuration.

## File Integrity

SHA-256 for `docs/assets/sealdone_3-2.pdf`:

```text
1FD24D09D2E1D5EBBC887B75B59DCE129F63BE14D276B428C01C011C1189128C
```

To verify locally on Windows PowerShell:

```powershell
Get-FileHash docs/assets/sealdone_3-2.pdf -Algorithm SHA256
```

## Local Verification

```bash
node tests/verify-site.mjs
```

Expected output:

```text
Site verification passed.
```

## GitHub Pages Setup

Use these GitHub repository settings:

- Repository: `TAGC-Data-Intelligence-Decision-Science`
- Visibility: Public
- Pages source: Deploy from a branch
- Branch: `main`
- Folder: `/docs`

## Boundary

This repository is an independently published proof and technical writeup. It is not an official Tencent page.
