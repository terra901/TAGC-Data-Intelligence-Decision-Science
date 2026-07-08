# 1.1 SchemaCompletionKnowledgeBase：构建方法与流程说明

## 1. 目标与产物
本知识库用于**补全/增强赛题数据库的 Schema 元信息**，将“静态 schema.json（SME 描述较少）”增强为可用于 Agent 检索/推理的“富描述 schema”，核心增强包括：

- 字段级：基于**数据剖析统计 + 业务知识库 + LLM**生成 `llm_short_description/llm_long_description`。
- 表级：基于字段摘要汇总 + 时间字段辨析 + 表类型验证（`_di/_df` 覆盖率）生成表级描述。
- 连接信息：基于 MinHash/Jaccard + DB 实连验证，给字段注入 `potential_links`（可连接字段列表）。

**最终产物**（核心）：
- `data/schema_all.json`：将 LLM 的表/字段描述合并回 schema。
- `run/agent/schema.json`：同步给运行时 Agent 使用。

辅助产物：
- `profiling_output_per_table/profile_*.json`：表剖析结果（NULL/基数/TopK/范围/MinHash 等）。
- `join_candidates_verified.json`：验证通过的 join 候选。
- `profiling_output_merged/profile_*.json`：合并了 join 信息，并去掉 MinHash 签名后的剖析文件。

---

## 2. 输入依赖
### 2.1 数据文件
- `schema.json`
  - 输入 schema（表/字段/类型/SME 描述）。
- `final_dataset.json`
  - 赛题任务集（至少包含 `sql_id`, `table_list`, `knowledge`）。用于将“题目知识”聚合到**表维度**，给 LLM 提供任务相关业务信息。
- `common_knowledge.md` / `common_knowledge2.md`
  - 通用知识（字段命名习惯、后缀 `_di/_df/_nf`、常见指标口径等）。
- （可选）`knowledge_add_clean_list.json`
  - 增强知识（清洗后的强规则），用于在生成描述时强化“必须/严禁/固定过滤”等约束。

> 注意：`schema_llm_pipeline.py` 默认从 `BASE_DIR/data/` 下读取上述文件；而 `analyze_data.py/analyza_join.py/merge_joins_to_profiles.py` 默认从**当前目录**读取 `schema.json/final_dataset.json` 并输出到当前目录下的 profiling 文件夹。

### 2.2 外部依赖
- StarRocks/MySQL 兼容数据库（用于剖析与验证 join、获取 deep stats）。
- LLM（Gemini 兼容 OpenAI ChatCompletions 接口）：用于生成描述。

### 2.3 Python 依赖（最低集合）
- `mysql-connector-python`
- `pymysql`（部分脚本/环境可能使用）
- `datasketch`（MinHash）
- `tqdm`
- `openai`（用于走 Gemini/OpenAI 兼容接口）

---

## 3. 构建流程（推荐顺序）
下面按“可复现链路”给出从原始 schema 到 `schema_all.json` 的完整流程。

### Step 1：数据剖析 Profiling（字段统计 + MinHash）
**脚本**：`analyze_data.py`

**做什么**：
- 对 `schema.json` 中每张表：
  - 统计 `total_records`。
  - 对每列统计：
    - `null_values`、`cardinality`。
    - `shape`：长度分布、min/max（不 CAST）、字符集分析、Top prefixes。
    - `top_k_values`。
    - `minhash_signature`（对 DISTINCT 值采样后计算 MinHash）。

**输出**：
- `profiling_output_per_table/profile_{table}.json`

**关键参数**（脚本内常量）：
- `MINHASH_SAMPLE_SIZE=10000`
- `MINHASH_PERMUTATIONS=128`

**注意事项**：
- `DB_CONFIG` 在脚本内硬编码，需要按实际数据库修改。
- 复杂类型（如 `bitmap/hll/json`）会跳过部分统计，避免 SQL 失败或成本过高。

---

### Step 2：Join 候选挖掘（MinHash 相似 + DB 实连验证）
**脚本**：`analyza_join.py`

**做什么**：
1. 读取 `profiling_output_per_table/*.json`，把“基数足够且有 MinHash 的列”作为候选 join key。
2. 读取 `final_dataset.json` 的 `table_list`，只在同一题目涉及的表对之间做 join 候选比对（减少组合爆炸）。
3. 对列对计算 `jaccard = MinHash(A) ∩ MinHash(B)`；当 `jaccard >= JACCARD_THRESHOLD(0.8)` 时：
4. 再在数据库上真实执行：
   - `SELECT 1 FROM t1 JOIN t2 ON t1.c1=t2.c2 LIMIT 1`
   - 只有 SQL 能执行（类型兼容）才认为候选“可 join”。

**输出**：
- `join_candidates_verified.json`

**关键参数**：
- `JACCARD_THRESHOLD=0.8`
- `MIN_CARDINALITY_FOR_JOIN=1`（基数>1）

---

### Step 3：将 join 信息合并回 Profiling 文件（生成 merged 版本）
**脚本**：`merge_joins_to_profiles.py`

**做什么**：
- 读取 `join_candidates_verified.json`，构建 `join_map`：
  - 对每个 `table.col` 记录其可连接字段列表 `potential_links`，并按 jaccard 降序排序。
- 遍历 `profiling_output_per_table/*.json`：
  - 对列写入 `potential_links`。
  - 删除 `minhash_signature`，减小文件体积。

**输出**：
- `profiling_output_merged/profile_*.json`

---

### Step 4：DB 深度统计增强 + LLM 字段/表摘要
**脚本**：`schema_llm_pipeline.py`

**做什么**（按脚本 `main()` 顺序）：
1. `enrich_profiles_with_db()`：
   - 连接数据库，为每列补充 `deep_stats`：
     - 时间列：MIN/MAX、非空数、distinct 时间点数。
     - 小基数字段：枚举全集（最多 50 值）。
   - 对每表额外生成 `verification_stats`：抽样 ID 在最近 N 天分区的覆盖率，用于推断表更像 `_df` 快照还是 `_di` 增量。
2. `summarize_fields_with_llm()`：
   - 为每列生成 `llm_short_description/llm_long_description`。
   - Prompt 组合信息：
     - SME 字段描述（schema.json）
     - Profiling 统计（TopK/范围/字符集等）
     - Join 连接信息（`potential_links`）
     - 通用知识（common）
     - 任务相关知识（从 final_dataset + knowledge_add_clean_list 聚合到表维度）
     - deep_stats（枚举/时间增强）
3. `summarize_tables_with_llm()`：
   - 汇总字段短描述，结合表级时间字段增强与覆盖率验证，生成表级短/长描述。

**输出（就地回写）**：
- `data/profiling_output_merged/*.json`（每个 profile 文件新增 LLM 描述、deep_stats、verification_stats）

**LLM 配置**：
- 通过 `config` 模块提供：`GEMINI_API_KEY_LIST/GEMINI_API_KEY/GEMINI_BASE_URL/GEMINI_MODEL`。

---

### Step 5：合并 LLM 描述回 schema
**脚本**：`merge_llm_to_schema.py`

**做什么**：
- 读取：
  - 原始 `data/schema.json`
  - LLM 处理后的 `data/profiling_output_merged/profile_*.json`
- 将表级与字段级描述写回 schema：
  - 表：`table_description_llm_short/long`，并在原 `table_description` 为空时回填短描述。
  - 字段：`llm_short_description/llm_long_description`，并在原 `description` 为空时回填短描述。
- （可选）如果存在 `run/agent/column_miss_empty_report.json`，会将判定为“全空字段”的列从 schema 中剔除。

**输出**：
- `data/schema_all.json`
- 同步写入：`run/agent/schema.json`

---

## 4. 质量控制与验收
- **Profiling 完整性**：`profiling_output_per_table` 中每表都有 `total_records`，列统计非空。
- **Join 可信度**：
  - 同时满足 MinHash 高相似 + DB join 可执行。
  - `potential_links` 按 jaccard 排序，优先用于 join 推断。
- **时间字段辨析**：
  - deep_stats 的 `distinct_count` 与 `total_present` 可帮助区分“分区/统计时间” vs “属性时间”。
- **表类型验证**：
  - `verification_stats.avg_coverage` 高 -> 快照表倾向；低 -> 增量表倾向。
  - 若与后缀 `_di/_df` 冲突，表级描述会输出醒目警告。

---

## 5. 更新策略
- 数据更新/补表后：建议重跑 Step1-3（profiling + join），再跑 Step4（deep_stats + LLM）。
- 如果仅新增/修改 `knowledge_add_clean_list.json`：可直接重跑 Step4 的 LLM 摘要（脚本会跳过已成功字段，必要时可手动清空 `llm_*` 字段触发重算）。

---

## 6. 复现运行建议（Windows）
由于不同脚本对工作目录与路径约定不同，推荐两种方式：

- **方式 A（按脚本默认相对路径运行）**：
  - 将 `schema.json`、`final_dataset.json` 放在本目录，直接在本目录依次运行 Step1-3。

- **方式 B（按项目统一 data/ 目录运行）**：
  - 保证 `BASE_DIR/data/` 下存在：`schema.json`、`final_dataset.json`、`common_knowledge.md`、（可选）`knowledge_add_clean_list.json`。
  - 再运行 `schema_llm_pipeline.py` 与 `merge_llm_to_schema.py`。
