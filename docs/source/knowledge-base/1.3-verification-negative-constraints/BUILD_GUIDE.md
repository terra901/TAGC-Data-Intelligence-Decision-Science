# 1.3 KnowledgeVerificationAndNegativeConstraint：构建方法与流程说明

## 1. 目标与产物
本知识库用于构建一套“**知识验证（Verified Knowledge）+ 负向约束（Negative Constraint）**”体系，核心目标是：

- 通过“数据侦探（Data Detective）”代理以**实证主义**方式对疑难题进行探针验证，沉淀为可复用的 **Verified Logic**。
- 用历史正确结果（Golden）与历史错误结果集（Error Book）构建 **HistoryGuard**，在生成 SQL 时：
  - 已经被正确解出的题：结果必须与历史正确结果一致（防回归）。
  - 未解出但有历史错误的题：结果不得重现任何历史错误结果集（防踩坑）。

该模块直接服务于两类资产：
- **已验证知识库**：`verified_knowledge_base_run.json`（按 `sql_id` 保存 `final_sql` 与 `verified_logic`）。
- **错题本（负向约束来源）**：`error_feedback.json`（按 `sql_id` 保存多个历史错误结果集批次）。

---

## 2. 输入依赖
### 2.1 数据文件
- `data/final_dataset.json`
  - 用到：`sql_id`, `question`, `table_list`, `knowledge`。
- `data/schema_all_gemini.json`（或等价 schema）
  - 用于给侦探代理提供相关表结构与字段描述。
- `data/common_knowledge2.md` / `data/common_knowledge.md`
  - 全局通用知识（字段习惯、后缀规则、常见口径等）。
- `data/starrock_knowledge.md`
  - StarRocks 方言/语法知识，降低探针 SQL 的语法错误率。
- `data/knowledge_add_clean_list.json`
  - 已沉淀的知识补充，侦探代理会将其作为“已验证知识”参考输入。
- `verified_knowledge_base_run.json`
  - 本模块生成/持续追加的 verified 产物。

### 2.2 评测与历史约束数据
- 正确结果（Golden）：脚本默认引用 `data/correct_58.json`（以项目配置为准）。
- 历史错误结果（Error Book）：`data/error_feedback.json`（由 `build_error_feedback.py` 生成/累积）。

### 2.3 外部依赖
- StarRocks/MySQL 兼容数据库：用于执行探针 SQL / 验证最终 SQL。
- LLM：用于生成 PROBE/SOLVE/CONFIRM 三阶段的 JSON 结构化输出。

---

## 3. 核心设计

### 3.1 Data Detective 三阶段协议
侦探代理输出必须是单个 JSON 对象，`phase` 取值：
- `PROBE`：生成小而密的诊断 SQL（确认时间列、粒度、枚举值、过滤条件、join 键等）。
- `SOLVE`：在关键歧义被 probe 消除后，输出候选最终 SQL，并附 `verified_logic` 说明已验证的规则。
- `CONFIRM`：在环境执行 SOLVE SQL 之后，根据返回的行数/样例/统计判断结果是否合理，最终确认并写入 verified KB。

该协议用于把“推理”变成“可验证的实验序列”，避免靠猜。

### 3.2 HistoryGuard（负向约束哨兵）
`HistoryGuard.check(sql_id, current_rows)` 两类约束：
- 若存在该题的黄金正确结果：必须与正确结果完全一致。
- 否则若存在历史错误批次：当前结果若与任意错误批次完全一致，则判定踩坑并强制打回（要求回到 PROBE）。

---

## 4. 构建流程（推荐顺序）

### Step 0（可选）：构建/更新错题本 error_feedback.json
**脚本**：`build_error_feedback.py`

**做什么**：
- 从某次评测的“执行结果文件”里抽取错题的结果集（Batch），并合并进 `data/error_feedback.json`。
- 通过对结果集“行序无关序列化指纹”去重，避免重复写入同一错误批次。

**输入**：
- `INPUT_PATH`：某次预测执行结果文件（JSON 或 JSONL），每条包含 `sql_id` 与 `result`。
- `WRONG_IDS`：你希望纳入错题本的 sql_id 列表。

**输出**：
- `data/error_feedback.json`

**注意事项**：
- 脚本中 `INPUT_PATH/WRONG_IDS` 默认是手工配置（硬编码），用于离线维护错题本。
- 若某题 result 为空（0 rows），默认跳过记录（空结果通常不足以形成有效负向约束）。

---

### Step 1：运行 Data Detective（探针 -> 求解 -> 确认）
**脚本**：`data_detective_agent.py`

**做什么**：
- 针对 `SQL_ID_LIST` 中的每个题：
  1. 组装初始上下文：Question、相关表 Schema、Common/StarRocks/Task Knowledge、Added Knowledge。
  2. 进入多轮循环（最多 `MAX_TURNS`）：
     - LLM 输出 PROBE/SOLVE/CONFIRM JSON。
     - 环境执行 SQL，并将结果反馈给代理。
     - SOLVE 阶段会受到 HistoryGuard 约束（防回归/防踩坑）。
  3. 当 CONFIRM 通过时：写入 verified 结果。

**输出**：
- `verified_knowledge_base_run.json`（追加 entry）：
  - `sql_id`
  - `final_sql`
  - `verified_logic`
  - `timestamp`

**落盘逻辑**：
- 通过 `append_verified_entry()` 读取现有 KB 列表并 append，再写回文件。

---

## 5. 质量控制与验收
- **可执行性**：所有探针与最终 SQL 必须是只读 `SELECT`。
- **证据链完整**：`verified_logic` 应清晰写出：
  - 时间列选择（业务时间 vs 分区时间）
  - 过滤条件与枚举取值
  - 粒度（行代表什么）与去重口径
  - 关键 join 关系与映射链路
  - 已知陷阱规避（例如 platid=255 汇总行、空分区、重复行、类型不一致）
- **防回归**：对已正确题，HistoryGuard 强制结果与 golden 完全一致。
- **防踩坑**：对有历史错误批次的题，禁止复现历史错误结果集。

---

## 6. 更新策略
- 当发现某题“错误模式”重复出现：将该次错误结果集通过 `build_error_feedback.py` 合并进 `error_feedback.json`，增强负向约束。
- 当确认某题稳定正确：通过 `data_detective_agent.py` 产出并写入 `verified_knowledge_base_run.json`，作为后续知识抽取/提示词注入的只读事实。

---

## 7. 复现运行建议（Windows）
- 优先保证项目配置文件中的路径一致（schema/common/starrock/knowledge_add/golden 等）。
- 运行 `data_detective_agent.py` 前：
  - 确保 DB 可连。
  - 确保 LLM key/base_url/model 已正确配置（以工程的 config 为准）。
  - 建议先准备/更新 `error_feedback.json`（负向约束），提升侦探代理的收敛速度。

> 注：本提交目录下的 `verified_knowledge_base_run.json` 是一次运行产物示例；实际工程运行时可能由配置指向其他路径（以 config 中 `DETECTIVE_VERIFIED_KB_PATH` 为准）。
