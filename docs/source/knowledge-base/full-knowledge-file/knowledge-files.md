# Knowledge Files

This public note describes the role of the private knowledge files used by the solution. Large JSON artifacts are not published in this proof repository because they may contain competition data, generated outputs, or internal execution traces.

## Main Files

- `final_dataset.json`: task set. Each item normally includes fields such as `sql_id`, question text, related tables, evidence, and metadata.
- `schema_all_gemini.json`: augmented schema with richer table and column descriptions for table selection, column selection, and reasoning.
- `goldsql.json`: few-shot example pool used for retrieval and prompt construction.
- `common_knowledge2.md`: shared business and SQL rules used across questions.
- `starrock_knowledge.md`: StarRocks dialect and syntax notes appended to common knowledge.
- `knowledge_add_clean_list.json`: cleaned supplemental knowledge, usually mined from iterative error analysis and successful rules.
- `correct_verified_knowledge.json`: verified logic knowledge used to steer generation toward known-correct reasoning patterns.
- `error_feedback.json`: negative constraints and wrong-result signatures used by the History Guard.
- `correct_60.json`: gold or aligned answer results used for local evaluation and consistency checks.

## Public Boundary

The repository publishes build guides and selected non-secret knowledge notes. It does not publish full task data, private database access, raw answer files, or any credential-bearing configuration.
