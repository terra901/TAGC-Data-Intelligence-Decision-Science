# Source Code

This directory contains the sanitized TGAC 2025 Text-to-SQL implementation and its build guides.

## Modules

- `pipeline/agent.py`: multi-strategy SQL generation, execution feedback, repair, result voting, and LLM arbitration.
- `pipeline/prompts.py`: prompts for Standard, Schema-CoT, Divide-and-Conquer, and Query Plan generation.
- `pipeline/join_graph.py`: Join Graph loading, bounded path selection, bridge-table expansion, and prompt evidence rendering.
- `pipeline/utils.py`: schema filtering, retrieval helpers, SQL execution, embedding, and FAISS utilities.
- `pipeline/build_vector_db.py`: Few-shot vector index builder.
- `pipeline/config.example.py`: environment-variable based configuration template.
- `knowledge-base/`: Augmented Schema, Join Graph, Positive Knowledge, Verification Knowledge, negative constraints, and Few-shot CoT construction.

## Execution Flow

1. Build an Augmented Schema from profiling, join discovery, business metadata, and LLM descriptions.
2. Generate and deploy the verified table-level Join Graph.
3. Build positive knowledge, verification knowledge, and negative constraints.
4. Retrieve Few-shot CoT examples for each task.
5. Select a bounded Join Graph subgraph and add any required bridge table to the prompt schema.
6. Generate candidates through four complementary reasoning strategies.
7. Execute, repair, and filter candidates against StarRocks-compatible SQL.
8. Select the final answer through result consistency, Majority Vote, History Guard, and LLM Judge arbitration.

## Configuration

Copy `pipeline/config.example.py` to `pipeline/config.py`, configure the required environment variables, and provide the competition data files locally.

The repository excludes API keys, private database access, execution logs, large result files, FAISS indexes, model caches, and confidential infrastructure configuration.
