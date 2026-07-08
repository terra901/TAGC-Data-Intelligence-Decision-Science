# Sanitized Source Snapshot

This folder is a public, sanitized snapshot of the TGAC 2025 Text-to-SQL solution code and build notes. It is meant for review, proof, and experience sharing, not as a turnkey private competition runtime.

## What Is Included

- `pipeline/agent.py`: runtime agent for multi-strategy SQL generation, execution feedback, repair, result voting, and LLM arbitration.
- `pipeline/prompts.py`: prompt construction for Standard, Schema-COT, Divide-and-Conquer, and Plan-style SQL generation.
- `pipeline/utils.py`: schema filtering, retrieval helpers, SQL execution helpers, embedding, and FAISS index utilities.
- `pipeline/build_vector_db.py`: few-shot vector index builder.
- `pipeline/config.example.py`: environment-variable based configuration template.
- `knowledge-base/`: build guides and scripts for Augmented Schema, Positive Knowledge, Verification Knowledge, negative constraints, and Few-shot CoT generation.
- `knowledge-base/full-knowledge-file/`: public notes describing the role of the knowledge files and selected non-secret knowledge docs.

## What Was Removed

- No API keys.
- No private database host, password, or internal network address.
- No execution logs, large result JSON files, FAISS binary index, model cache, or full raw submission outputs.
- No confidential infrastructure setup.

## Architecture Summary

The core idea is an Agentic Workflow with closed-loop knowledge evolution:

1. Build an Augmented Schema from profiling statistics, join discovery, business metadata, and LLM descriptions.
2. Mine Positive Knowledge from gold SQL, correct answers, and evaluation feedback.
3. Add Verification Knowledge and negative constraints through a Data Detective Agent and History Guard.
4. Retrieve Few-shot CoT examples for each question.
5. Generate candidates using Standard, Schema-COT, Divide-and-Conquer, and Plan routes.
6. Run Execution & Fix loops against StarRocks-compatible SQL.
7. Select final answers with Majority Vote and LLM Judge arbitration.

## Reproduction Notes

Copy `pipeline/config.example.py` to `pipeline/config.py`, set the required environment variables locally, and prepare the original competition data files locally. The public snapshot intentionally omits private data, credentials, model endpoint defaults, and large generated artifacts.
