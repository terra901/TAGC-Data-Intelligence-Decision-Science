# TGAC 2025 Solution Writeup and Proof / TGAC 2025 方案复盘与获奖证明

![TAGC 2025 Data-Intelligence Decision Science Second Place](TAGC.png)

## 中文

本仓库发布 TGAC 2025 腾讯游戏算法竞赛「数智决策科学赛道」二等奖的公开证明页面，以及经过脱敏处理的 Text-to-SQL 技术方案复盘。

- 在线页面: https://terra901.github.io/TAGC-Data-Intelligence-Decision-Science/
- GitHub 仓库: https://github.com/terra901/TAGC-Data-Intelligence-Decision-Science
- 证书 PDF: `docs/assets/sealdone_3-2.pdf`
- 架构 PDF: `docs/assets/text-to-sql-architecture.pdf`
- 脱敏源码快照: `docs/source`
- TGAC 官网: https://tgac.tencent.com/

## 获奖信息

- 赛事: Tencent Games Algorithm Competition 2025
- 奖项: 二等奖 / Second Place
- 赛道: Data-Intelligence Decision Science / 数智决策科学赛道
- 队伍: 帮帮我！肯德基爷爷
- 成员: 高海圳 / Haizhen Gao, 许刚 / Gang Xu, 陈继昀 / Jiyun Chen
- 证书日期: 2026-01-06

## 方案材料

公开页面包含 Text-to-SQL 方案复盘，重点覆盖:

- Agentic Workflow
- 闭环知识进化
- Augmented Schema
- Positive Knowledge
- Verification Knowledge
- Few-shot CoT
- Execution & Fix
- History Guard
- Majority Vote 与 LLM Judge 仲裁

`docs/source` 目录保留了脱敏后的核心模块结构和构建说明，移除了 API key、私有数据库地址、日志、模型缓存、大型生成产物和原始敏感配置。

## 文件完整性

`docs/assets/sealdone_3-2.pdf` 的 SHA-256:

```text
1FD24D09D2E1D5EBBC887B75B59DCE129F63BE14D276B428C01C011C1189128C
```

Windows PowerShell 本地校验:

```powershell
Get-FileHash docs/assets/sealdone_3-2.pdf -Algorithm SHA256
```

## 本地验证

```bash
node tests/verify-site.mjs
```

期望输出:

```text
Site verification passed.
```

## GitHub Pages 设置

在仓库设置中启用 GitHub Pages:

- Repository: `TAGC-Data-Intelligence-Decision-Science`
- Visibility: Public
- Pages source: Deploy from a branch
- Branch: `main`
- Folder: `/docs`

## English

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

The public page includes a Text-to-SQL solution writeup covering Agentic Workflow, closed-loop knowledge evolution, Augmented Schema, Positive Knowledge, Verification Knowledge, Few-shot CoT, Execution & Fix, History Guard, Majority Vote, and LLM Judge arbitration.

The `docs/source` folder contains a sanitized source snapshot. It keeps the key module structure and selected build guides, but removes API keys, private database hosts, logs, model caches, large generated artifacts, and raw sensitive configuration.

## Boundary

This repository is an independently published proof and technical writeup. It is not an official Tencent page.
