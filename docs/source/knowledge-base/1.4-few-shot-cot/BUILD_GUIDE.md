# 1.4 fscot_generation：构建方法与流程说明

## 1. 目标与产物
本模块用于构建 **Few-shot CoT（Few-shot Chain-of-Thought）/结构化推理样本库**，将赛题中的“正确 SQL（Gold SQL）”与多源知识（Common/Task/Verified/Added）组合起来，交由 LLM **反向工程（reverse-engineer）**出可复用的推理过程，并以统一字段写入训练/检索用的数据文件。

核心动机：
- 将“正确 SQL 的业务逻辑”显式化为：
  - `plan`：Query Plan（步骤化执行计划）
  - `divide`：Divide-and-Conquer（主问题/子问题拆解）
  - `cot`：包含固定结构：`#answer/#reason/#columns/#values/#SELECT/#SQL-like`
- 使 Agent 在检索 few-shot 时，不只是拿到 SQL，还能拿到“为什么这么写”。

**最终产物**：
- `generated_goldsql_entries.json`（默认输出到 `run/datafile/output/`）
  - 每条 entry 包含：`sql_id/question/sql/table_list/knowledge/plan/divide/cot` 等。

可选更新产物：
- `data/goldsql.json`：若开启 `--update-goldsql`，会把新生成的 `plan/divide/cot` 回填到 goldsql 资产。

---

## 2. 输入依赖
### 2.1 数据文件
- `data/final_dataset.json`
  - 用到：`sql_id`, `question`, `table_list`, `knowledge`。
- `data/correct_59.json`
  - 默认正确 SQL 来源（可通过 `--correct-sql-file` 指定）。
- `data_detective_knowledge/correct_verified_knowledge.json`
  - 若某题存在 `final_sql`，会优先覆盖 `correct_59.json`（确保使用已验证的最终 SQL）。
- `data/common_knowledge2.md`
  - 通用知识（第一段）。
- `data/knowledge_add_clean_list.json`
  - 正向知识补充（最后一段）。

### 2.2 代码依赖
- `run/agent/agent.py`（以模块方式导入）
  - 用于读取 tasks、schema_map，格式化 schema block（`t2sql_prompts.format_schema_block`）。

---

## 3. 知识拼接策略（输入给 LLM 的 define）
脚本将多源知识按固定顺序合并为 `knowledge_text`：
1. `[Common Knowledge v2]`：`common_knowledge2.md`
2. `[Task Knowledge]`：`final_dataset.json` 中该题的 `knowledge`
3. `[Correct Verified Knowledge]`：`correct_verified_knowledge.json` 的 `verified_logic`（若存在）
4. `[Knowledge Add Clean]`：`knowledge_add_clean_list.json` 对应 `sql_id` 的补充规则（若存在）

目的：
- 让 LLM 在“严格不改变 SQL”的约束下，生成与 SQL 一致的推理过程。

---

## 4. 构建流程

### Step 1：确定要生成的 sql_id 集合
脚本支持两种方式输入：
- `--sql-id`：可多次传入
- `--ids-file`：读一个 JSON/JSONL 文件，兼容字段：`incorrect_ids/correct_ids/sql_ids/ids` 或顶层 list

内部会对 id 去重并保持顺序。

---

### Step 2：加载 correct SQL（优先 verified 覆盖）
对每个 `sql_id`：
- 若 `correct_verified_knowledge.json` 中存在该题 `final_sql`：优先使用（更可信）。
- 否则使用 `correct_59.json` 中的 `sql`。

---

### Step 3：构建 schema block
- 从 `final_dataset` 的 `table_list` 提取相关表。
- 使用 agent 工具函数格式化为 prompt 需要的 schema 文本（包含表、字段、描述等）。

---

### Step 4：调用 LLM 生成结构化推理（plan/divide/cot）
**脚本**：`generate_fewshot_cot.py`

**Prompt 关键约束**：
- 必须输出严格 JSON，且仅包含键：`plan`, `divide`, `cot`。
- `plan` 必须以 `Query Plan:` 开头。
- `divide` 必须以 `1. Divide and Conquer:` 开头。
- `cot` 必须以 `#answer:` 开头，且必须包含：
  - `#reason:`
  - `#columns:`（列出 SQL 中所有列，格式 `table.column`）
  - `#values:`（把 define 中每个枚举/时间窗/条件映射成具体过滤条件，要求“完全枚举”）
  - `#SELECT:`
  - `#SQL-like:`

**稳健性处理**：
- 若模型输出不是合法 JSON：
  - 会尝试从文本中提取第一个 JSON 对象。
  - 若仍失败，会调用一次“repair formatter”把输出修成合法 JSON。

---

### Step 5：写出 few-shot 数据文件
默认写入：
- `run/datafile/output/generated_goldsql_entries.json`

每条 entry（示例字段）：
- `sql_id`
- `question`
- `sql`（最终使用的 correct sql）
- `table_list`
- `knowledge`（task knowledge 原文）
- `plan/divide/cot`

---

### Step 6（可选）：回填 goldsql.json
若指定 `--update-goldsql`：
- 会把本次生成的 `plan/divide/cot` 回写到 `data/goldsql.json` 的对应 `sql_id`。

---

## 5. 质量控制与验收
- **一致性**：推理过程必须与给定 SQL 严格一致（脚本明确要求“Do NOT change the given SQL”）。
- **完整性**：`#values` 必须完整枚举 define 中的所有条件/枚举值，禁止“etc./...”省略。
- **可用性**：生成文件可被后续检索/训练直接消费（统一结构）。

---

## 6. 更新策略
- 当 `knowledge_add_clean_list.json` 或 `correct_verified_knowledge.json` 更新后，建议对对应 `sql_id` 重新生成 few-shot CoT，以保持 few-shot 与最新 verified 逻辑一致。

---

## 7. 复现运行建议（Windows）
- 若仅预览输入拼接与正确 SQL 来源，可使用：
  - `--preview` / `--preview-only`
- 若要检查 prompt 内容，可使用：
  - `--dump-messages`

> 实际运行时以工程的 `run/agent/config.py`（或提交代码中的 `2.pipeline/config.py`）配置的 LLM key/base_url/model 为准。
