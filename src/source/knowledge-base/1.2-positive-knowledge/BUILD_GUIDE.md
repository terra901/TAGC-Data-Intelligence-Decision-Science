# 1.2 PositiveKnowledgeSourcesConstruction：构建方法与流程说明

## 1. 目标与产物
本知识库用于从“错题样本”中自动抽取**可复用的正向知识规则（Positive Knowledge）**，并持续合并到全局知识文件中，帮助 Agent 在后续生成 SQL 时避免已知错误模式。

该模块的核心思想是：
- 用**离线评测**定位错题（`eval_incorrect_ids.json`）。
- 对每道错题：对比
  - 题目文本/提示（Evidence）
  - Schema（含 LLM 版列描述）
  - 正确 SQL（Golden SQL）
  - 错误 SQL（Bad Cases，来自 Agent 输出的多策略候选 SQL）
  - 已验证逻辑（Verified Logic，可选）
  - 旧知识（Old Knowledge，可选）
- 由 LLM 归因并产出一段**“黄金知识规则文本（Ideal Knowledge Text）”**，并写回到 `knowledge_add_clean_list.json`。

**最终产物**：
- `data/knowledge_add_clean_list.json`：按 `sql_id` 聚合的知识补充列表（会被 Agent 直接使用）。

辅助产物：
- `data/knowledge_ideal_generated.json`：本轮生成的“黄金知识规则”快照。
- `run/datafile/output/eval_incorrect_ids.json`：离线评测得到的错题列表。

---

## 2. 输入依赖
### 2.1 数据文件
- `data/final_dataset.json`
  - 用到：`sql_id`, `question`, `evidence`, `table_list`, `knowledge`。
- `data/schema_all_gemini.json`
  - LLM 增强后的 schema（表/字段描述更充分），用于向 LLM 提供上下文。
- `data/correct_59.json`
  - 正确 SQL（Golden SQL）。
- `run/datafile/output/results-finalize.jsonl`（或等价的 Agent 输出路径）
  - 错误 SQL 来源。
  - 该脚本会尽可能收集：
    - `final_sql/sql`
    - 每个 candidate 的 `generated_sql/used_sql`
  - 目的：覆盖“生成阶段 + 修复阶段”的错误样本。
- `data/knowledge_add_clean_list.json`
  - 旧知识（Old Knowledge）：LLM 会在此基础上修正/补全。
- `data_detective_knowledge/correct_verified_knowledge.json`（可选）
  - 已验证逻辑（Verified Logic）：作为只读事实约束注入提示词。
- `data/common_knowledge2.md`
  - 通用知识（与 Agent 保持一致）。

### 2.2 外部依赖
- 数据库（用于 sql_exe 执行预测 SQL 并产出结果集）。
- LLM（Gemini/OpenAI 兼容 ChatCompletions 接口）。

---

## 3. 构建流程（推荐顺序）

### Step 1：生成 SQL（Agent 输出）
该知识库的输入之一来自 Agent 在全量题目上的输出（通常是 JSONL 或 JSON list）。

- 常见输出位置：`run/datafile/output/results-*.jsonl`
- 每条记录至少应包含：
  - `sql_id`
  - `final_sql` 或 `sql`
  - （可选）`candidates` 列表（用于收集更多 bad cases）

> 本目录仅负责“用错题反推知识”，不限定你用哪种 agent 策略产出结果。

---

### Step 2：执行 SQL（sql_exe）并落盘结果
**脚本**：`sql_exe.py`

**输入**：一份形如 `[{"sql_id":..., "sql":...}]` 的 JSON（可由 pipeline 自动生成）。

**输出**：预测结果文件（含 result 集）：
- 典型位置：`run/datafile/output/agent_exe_results.json`

**说明**：
- 使用 PyMySQL 执行 SQL，返回结果以 list[dict] 存储。
- 对数值做标准化，便于后续评测对比。

---

### Step 3：离线评测（对比 golden result，产出错题列表）
**脚本**：`evaluation.py`

**输入**：
- `--predict-file`：sql_exe 输出（默认使用 `run/datafile/output/agent_exe_results.json`）。
- Golden：`data/correct_59.json`（脚本内固定 base_path + 相对路径）。
- 全量题目列表：`data/final_dataset.json`。

**输出**：写入到 output_dir（默认 `run/datafile/output/`）：
- `eval_correct_ids.json`
- `eval_incorrect_ids.json`

**评测规则要点**：
- 行数必须一致。
- 行内容按“行扁平化 + 排序”后对比，忽略行顺序。
- 特判：`sql_111` 要求坐标保留小数，若结果中为 int/纯整数字符串会判错。

---

### Step 4：构建“黄金知识规则”（Ideal Knowledge Text）并合并写回
**脚本**：`build_knowledge_prompts.py`

**做什么**：
- 读取错题列表：`run/datafile/output/eval_incorrect_ids.json`。
- 对每个错题：构造 LLM Prompt，输入包含：
  - SQL 方言（固定 StarRocks）
  - 相关 schema（从 `schema_all_gemini.json` 按 `table_list` 取子集）
  - Question
  - Golden SQL（`correct_59.json`）
  - Bad SQL list（从 results JSON/JSONL 收集）
  - Reference Knowledge（只读事实）：
    - Common Knowledge (`common_knowledge2.md`)
    - Dataset Evidence（题目 `evidence`）
    - Verified Logic（可选，来自 `correct_verified_knowledge.json`）
  - Old Knowledge（来自 `knowledge_add_clean_list.json`，待修正）
- LLM 输出要求：**只允许输出最终的知识规则文本**（禁止输出分析过程）。
- 产出写入：
  - `data/knowledge_ideal_generated.json`（本次生成快照）
  - 并调用 `update_knowledge_base()` 合并写回 `data/knowledge_add_clean_list.json`（会自动备份 `.bak`）。

---

## 4. 自动化流水线（可选）
本模块在项目中通常通过一个完整流水线串起来（Agent -> Execute -> Evaluate -> Build Knowledge）。

- 在仓库原工程中，对应脚本位于：`run/update_knowledge_pipeline/`
  - `pipeline_runner.py`：单轮执行
  - `pipeline_loop_runner.py`：多轮迭代，仅对“仍然错误的题”反复更新知识

本提交目录下的 `pipeline_loop_runner.py` 是对上述逻辑的抽取版，核心行为一致。

---

## 5. 质量控制与验收
- **知识有效性**：更新 `knowledge_add_clean_list.json` 后重新跑 Agent，错题数应下降或至少不回归。
- **知识可迁移**：理想知识应表达为“规则/约束/映射/用法”，避免写成只适用于单次 SQL 的 hardcode。
- **冲突处理**：同一 `sql_id` 的知识采用覆盖写回（以最新生成文本为准）。
- **可追溯性**：每轮生成保留 `knowledge_ideal_generated.json` 以便回滚/比对。

---

## 6. 更新策略
- 每次模型/策略更新后：建议重跑 `evaluation.py` 生成新错题列表，再跑 `build_knowledge_prompts.py` 更新知识。
- 当引入 `data_detective_knowledge/correct_verified_knowledge.json` 新增 verified 逻辑后：建议重跑错题的知识生成，使知识与 verified 事实对齐。

---

## 7. 复现运行建议（Windows）
由于 `evaluation.py` 使用了固定的 `base_path`（脚本内硬编码为 `D:/Desktop/研究生/比赛/腾讯算法`），请确保你的项目路径一致，或按需修改该变量。

常见复现方式：
- 先确保已有 Agent 输出与 sql_exe 输出。
- 再依次运行：
  - `evaluation.py`（生成 eval_incorrect_ids.json）
  - `build_knowledge_prompts.py`（生成并合并知识）

> 若使用完整自动化链路，请以仓库中的 `run/update_knowledge_pipeline/pipeline_runner.py` 或 `pipeline_loop_runner.py` 为准。
