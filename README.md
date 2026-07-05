# StarRocks Text-to-SQL Knowledge-Augmented Pipeline

## Abstract

This repository contains a research-oriented Text-to-SQL pipeline for StarRocks databases. The project combines schema profiling, domain-knowledge construction, error-feedback mining, few-shot retrieval, prompt engineering, SQL generation, and iterative correction. The codebase has been normalized into English for academic review and GitHub publication.

## Repository Structure

- `1.knowledge_base/1.1_schema_completion_knowledge_base/`: schema profiling, join-candidate discovery, and LLM-assisted schema enrichment.
- `1.knowledge_base/1.2_positive_knowledge_sources_and_construction/`: construction of positive domain knowledge from validated SQL and execution traces.
- `1.knowledge_base/1.3_knowledge_validation_and_negative_constraints/`: validation artifacts, error feedback, and negative constraints derived from failed SQL attempts.
- `1.knowledge_base/1.4_few_shot_cot_generation/`: generation of few-shot chain-of-thought examples.
- `2.pipeline/`: the integrated Text-to-SQL runtime, including prompts, configuration, retrieval utilities, datasets, StarRocks dialect notes, and FAISS few-shot indexes.

## Methodological Overview

1. **Schema profiling**: table-level and column-level statistics are collected to summarize metadata, cardinality, and candidate join keys.
2. **Schema completion**: profiling artifacts are merged with LLM-generated descriptions to produce richer database metadata.
3. **Knowledge construction**: validated SQL cases are distilled into positive rules, business mappings, and domain-specific constraints.
4. **Negative constraint mining**: failed queries are analyzed to identify syntax, schema, and business-logic failure patterns.
5. **Few-shot retrieval**: gold SQL examples are embedded and indexed with FAISS to retrieve semantically similar demonstrations.
6. **SQL generation and repair**: prompts encode StarRocks dialect restrictions, self-checklists, and correction protocols.

## Data and Artifacts

The JSON files in `2.pipeline/` and `1.knowledge_base/` include benchmark questions, gold SQL, verified knowledge, execution feedback, schema descriptions, and retrieval metadata. Domain terms originally written in Chinese were translated where direct terminology was clear. Low-frequency or ambiguous residual expressions were normalized into stable English placeholders of the form `domain_term_<hash>` to avoid introducing unsupported semantic assumptions.

## Environment Notes

The repository was prepared for documentation and academic inspection rather than guaranteed immediate execution. Some paths, model names, API keys, and database endpoints are environment-specific and should be reviewed before execution. In particular, update `2.pipeline/config.py` before running experiments.

## Suggested Usage

```bash
python 2.pipeline/build_vector_db.py
```

Then run the generation pipeline after configuring the StarRocks connection, model provider, and local embedding model path.

## Limitations

- The database endpoint, credentials, and local model paths are deployment-specific.
- Some generated English placeholders intentionally preserve uncertainty for rare domain expressions.
- SQL executability depends on the target StarRocks version, schema availability, and external service configuration.

## Citation-Oriented Description

This codebase implements a knowledge-augmented Text-to-SQL framework in which structured schema evidence, verified domain knowledge, negative feedback, and retrieval-based demonstrations jointly constrain large-language-model SQL synthesis for an MPP analytical database dialect.

## Translation and Execution Note

The user-facing objective of this version is English-language academic review and GitHub publication. Python scripts are therefore preserved as English-normalized archival modules: each file exposes `TRANSLATED_SOURCE_LINES` and `get_translated_source()` so that the translated source text remains inspectable and syntactically valid. The original computational behavior should be restored or re-validated before production execution.
