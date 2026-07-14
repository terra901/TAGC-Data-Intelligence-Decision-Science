# TGAC 2025 Text-to-SQL Solution

English | [中文](README.zh.md)

![TGAC 2025 Data-Intelligence Decision Science Second Place](rank.png)

## Overview

This repository contains a sanitized implementation of the TGAC 2025 Data-Intelligence Decision Science Text-to-SQL solution. The system targets StarRocks-compatible business databases with sparse schema semantics, implicit business rules, and multi-table query ambiguity.

- Source code: `src/source`
- Architecture PDF: `src/assets/text-to-sql-architecture.pdf`
- Award certificate: `src/assets/sealdone_3-2.pdf`
- TGAC official website: https://tgac.tencent.com/

## Award

- Event: Tencent Games Algorithm Competition 2025
- Award: Second Place
- Track: Data-Intelligence Decision Science
- Team: Help Me! KFC Grandpa
- Members: [Haizhen Gao](https://github.com/gstranded), Gang Xu, Jiyun Chen
- Certificate date: 2026-01-06

## Pipeline

1. Build an Augmented Schema from profiling statistics, business metadata, value distributions, and LLM-generated descriptions.
2. Discover implicit join keys with MinHash/Jaccard candidates and database validation, then build a runtime Join Graph.
3. Mine Positive Knowledge, Verification Knowledge, and negative constraints from gold SQL and execution feedback.
4. Retrieve Few-shot CoT examples using question, knowledge, and table context.
5. Generate diverse SQL candidates with Standard, Schema-CoT, Divide-and-Conquer, and Query Plan strategies.
6. Execute and repair candidates against StarRocks-compatible SQL.
7. Select the final SQL through result consistency, Majority Vote, History Guard, and LLM Judge arbitration.

## Source Layout

- `src/source/pipeline/`: runtime generation, Join Graph retrieval, execution repair, voting, and arbitration.
- `src/source/knowledge-base/`: Augmented Schema, positive knowledge, verification constraints, and Few-shot CoT construction.
- `src/assets/`: architecture and award files.

Private task data, database credentials, execution logs, model caches, generated indexes, and raw submission outputs are intentionally excluded.
