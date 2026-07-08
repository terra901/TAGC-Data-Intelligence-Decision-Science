from typing import List, Dict, Any, Tuple
import json

# --- SQL 生成三种思维模式 ---

STARROCKS_SYNTAX_RULES = """
### Strict StarRocks SQL Dialect Rules:
1. **Date/Time**:
   - Parsing: Use `str_to_date(col, '%Y%m%d')`. ❌ NO `to_date(col, fmt)`.
   - Calc: Use `date_add(col, INTERVAL n DAY)`. ❌ NO `date + n`.
2. **Casting & Quotes**:
   - Use `CAST(val AS TYPE)`. ❌ NO `val::type` (PG syntax).
   - Use single quotes `'val'`. ❌ NO double quotes `"val"`.
3. **Hive/Spark Forbidden**:
   - ❌ NO `LATERAL VIEW explode`, `posexplode`, `split(space())`.
   - ❌ NO `collect_set`/`collect_list` (Use `group_concat`/`array_agg`).
4. **Functions**:
   - Use `split_part(str, sep, index)` where index starts at 1.
5. **Logic**:
   - Avoid `WITH RECURSIVE`.
   - GROUP BY original expressions, not aliases.
"""

SQL_SELF_CHECKLIST = """
### Self-Correction Checklist (在输出最终 SQL 前逐项检查):
- [ ] 检查是否只使用了 Schema 中存在的表和字段。
- [ ] 检查所有聚合查询的 GROUP BY 是否包含所有非聚合 SELECT 列（或这些列都被合法的聚合函数包裹）。
- [ ] 检查是否违反了《Strict StarRocks SQL Dialect Rules》中的任何一条（尤其是日期函数、CAST、引号、禁止的 Hive/Spark 语法）。
- [ ] 检查日期区间是否与题目要求匹配（起止是否包含边界，单位是否一致）。
- [ ] 检查 WHERE / JOIN 条件是否与业务描述一致，没有遗漏关键过滤条件。
"""

# 1. 标准模式 System Prompt (快速、直接，作为基准)
SYSTEM_PROMPT_STANDARD = f"""
你是一个专家级的 Text-to-SQL 工程师。你的任务是基于用户的自然语言问题、知识库和数据库的详细元数据，生成一个 100% 准确且可执行的 StarRocks SQL 查询。

思考与输出模式：
1. 先用简短的自然语言分步分析（可以使用中文或英文），但不要输出 JSON。
2. 然后给出唯一的最终 SQL，并且必须放在一个 Markdown 代码块中，格式如下：

```sql
-- 最终的 StarRocks SQL
SELECT ...
```

3. 代码块内只包含 SQL 语句，不要再附带解释、JSON 或额外文本。

{STARROCKS_SYNTAX_RULES}

{SQL_SELF_CHECKLIST}
"""

# 2. 思维链模式 (Ali-CoT)，强调列/值对齐
SYSTEM_PROMPT_ALI_COT = f"""
你是一个专家级的 Text-to-SQL 工程师。
请采用 **Chain-of-Thought** 策略，先分析数据结构，再写 SQL。

输出格式建议：
1. **Reasoning**: 分析用户意图和各个子任务。
2. **Columns**: 列出需要用到的表和字段。
3. **Filters**: 列出 WHERE / HAVING 中关键的过滤条件和值。
4. **SQL**: 最后给出唯一的最终 SQL，放在一个 ```sql ... ``` 代码块中，代码块内只包含 SQL。

生成 SQL 时必须遵守下面的 StarRocks 语法规则和自检清单：

{STARROCKS_SYNTAX_RULES}

{SQL_SELF_CHECKLIST}
"""

# 向后兼容：cot 视为 ali_cot 的别名
SYSTEM_PROMPT_COT = SYSTEM_PROMPT_ALI_COT

# 3. 分治法 (Divide & Conquer)，专攻复杂嵌套逻辑
SYSTEM_PROMPT_DIVIDE = f"""
你是一个专家级的 Text-to-SQL 工程师。
对于复杂问题，请采用 **Divide and Conquer (分治法)** 策略。
请将大问题拆解为子问题 (Sub-questions)，先写出子查询 (Sub-SQL)，最后组装成最终 SQL。

**思考过程示例：**
1. **Main Question**: 分析主问题。
2. **Sub-question 1**: 定义第一个子步骤（例如：先找出昨天的活跃用户）。
   - **Pseudo SQL**: 写出对应的子查询逻辑。
3. **Sub-question 2**: 定义第二个子步骤（例如：再找出今天的活跃用户）。
4. **Assembly**: 将子查询通过 JOIN 或 IN 组合。

**最后输出要求：**
- 你可以展示分步推理过程；
- 最后必须给出唯一的最终 SQL，放在一个 ```sql ... ``` 代码块中，代码块内只包含 SQL。

生成 SQL 时必须遵守下面的 StarRocks 语法规则和自检清单：

{STARROCKS_SYNTAX_RULES}

{SQL_SELF_CHECKLIST}
"""

# 4. 查询计划 (Query Plan)，专攻多表 Join 路径
SYSTEM_PROMPT_PLAN = f"""
你是一个专家级的 Text-to-SQL 工程师。
为了保证 SQL 执行路径的正确性，请模仿数据库优化器，先制定 **Query Plan (查询计划)**。

**思考步骤：**
1. **Preparation**: 确定涉及哪些表 (Location, Info...)。
2. **Matching**: 描述如何过滤行 (Scan Table -> Filter Condition)。
3. **Linking**: 描述表之间如何连接 (Join Path, Foreign Keys)，确保没有笛卡尔积。
4. **Delivering**: 确定最终输出列和聚合函数。

**最后输出要求：**
- 你可以先给出结构化的 Query Plan 描述；
- 最后必须给出唯一的最终 SQL，放在一个 ```sql ... ``` 代码块中，代码块内只包含 SQL。

生成 SQL 时必须遵守下面的 StarRocks 语法规则和自检清单：

{STARROCKS_SYNTAX_RULES}

{SQL_SELF_CHECKLIST}
"""

# 兼容旧代码的占位常量（当前未直接使用）
SQL_GEN_SYSTEM = SYSTEM_PROMPT_STANDARD

SQL_FIX_SYSTEM = f"""
你是一个 SQL 调试专家。你的任务是修复一个 StarRocks SQL 查询。
用户提供了一个 错误的 SQL 和 一个它在 StarRocks 数据库上执行时返回的 错误信息。
你的工作是分析错误，返回一个已修正且可以正确运行的 SQL。

思考与输出模式：
1. 可以先用自然语言简要说明错误原因和修复思路（不要使用 JSON）。
2. 最后必须给出唯一的修正后 SQL，并放在一个 ```sql ... ``` 代码块中，代码块内只包含 SQL，不要再附带解释或 JSON。

在修复 SQL 时必须遵守下面的 StarRocks 语法规则和自检清单：

{STARROCKS_SYNTAX_RULES}

{SQL_SELF_CHECKLIST}
"""


def format_schema_block(schema_tables: List[Dict[str, Any]]) -> str:
    # 为了减少无关字段，仅保留核心说明与列描述，但若存在 potential_links 也保留
    compact_tables = []
    allowed_tables = {t.get("table_name") for t in schema_tables if t.get("table_name")}
    def _filter_links_by_allowed(links):
        try:
            if isinstance(links, dict):
                return {k: v for k, v in links.items() if k in allowed_tables}
            if isinstance(links, list):
                filtered_list = []
                for item in links:
                    if isinstance(item, dict):
                        target = None
                        for k in ("table", "table_name", "target_table", "to_table", "ref_table", "target", "to", "ref", "foreign_table", "fk_table", "join_table", "dst_table"):
                            if k in item and isinstance(item[k], str):
                                target = item[k].split(".")[0]
                                break
                        if target is None:
                            filtered_list.append(item)
                        elif target in allowed_tables:
                            filtered_list.append(item)
                    elif isinstance(item, str):
                        target = item.split(".")[0]
                        if target in allowed_tables:
                            filtered_list.append(item)
                    else:
                        filtered_list.append(item)
                return [x for x in filtered_list if x]
            return links
        except Exception:
            return links
    for t in schema_tables:
        obj = {
            "table_name": t.get("table_name"),
            "llm_table_long_description": t.get("llm_table_long_description"),
        }
        cols = []
        for c in t.get("columns", []):
            cc = {
                "column_name": c.get("column_name"),
                "column_type": c.get("column_type"),
                "llm_long_description": c.get("llm_long_description"),
            }
            if "potential_links" in c:
                _pl = _filter_links_by_allowed(c["potential_links"])
                if _pl:
                    cc["potential_links"] = _pl
            cols.append(cc)
        obj["columns"] = cols
        if "potential_links" in t:
            _tpl = _filter_links_by_allowed(t["potential_links"])
            if _tpl:
                obj["potential_links"] = _tpl
        compact_tables.append(obj)
    return json.dumps(compact_tables, ensure_ascii=False, indent=2)


def format_few_shots(pairs: List[Tuple[str, str]]) -> str:
    blocks = []
    for q, s in pairs:
        blocks.append(f"[Q]: {q}\n[SQL]:\n{s}")
    return "\n\n" + ("\n\n".join(blocks)) if blocks else ""


def build_sql_generation_messages(
    schema_tables: List[Dict[str, Any]],
    knowledge_text: str,
    few_shots: List[Tuple[str, str]],
    question: str,
    strategy: str = "standard",
):
    schema_block = format_schema_block(schema_tables)
    fs_block = format_few_shots(few_shots)

    # 根据策略选择 System Prompt
    if strategy == "divide":
        sys_content = SYSTEM_PROMPT_DIVIDE
        suffix = "请开始 **Divide and Conquer** 分析："
    elif strategy == "plan":
        sys_content = SYSTEM_PROMPT_PLAN
        suffix = "请开始制定 **Query Plan**："
    elif strategy in ("ali_cot", "cot"):
        sys_content = SYSTEM_PROMPT_ALI_COT
        suffix = "请开始分析 Schema 和 Values："
    else:
        sys_content = SYSTEM_PROMPT_STANDARD
        suffix = "[SQL]:\n"

    user_content = (
        "[数据库元数据 (Schema)]\n---\n"
        f"{schema_block}\n\n"
        "[知识库 (Knowledge)]\n---\n"
        f"{knowledge_text}\n\n"
        "[少样本示例 (Few-shot Examples)]\n---\n"
        f"{fs_block}\n\n"
        "[新问题]\n"
        f"[Q]: {question}\n"
        f"{suffix}"
    )
    return [
        {"role": "system", "content": sys_content},
        {"role": "user", "content": user_content},
    ]


def build_sql_fix_messages(sql_candidate: str, error_message: str, knowledge_text: str = ""):
    kb = ("\n\n[知识库 (Knowledge)]\n---\n" + knowledge_text + "\n") if knowledge_text else "\n"
    user_content = (
        "[错误的 SQL]\n"
        f"{sql_candidate}\n\n"
        "[StarRocks 错误信息]\n"
        f"{error_message}" + kb +
        "[修正后的 SQL]\n"
    )
    return [
        {"role": "system", "content": SQL_FIX_SYSTEM},
        {"role": "user", "content": user_content},
    ]


# 针对不同错误类型的修正示范 (Few-shot)
CORRECTION_FEW_SHOTS: Dict[str, str] = {
    # 1. 语法/列名错误 (最常见)
    "SyntaxError": """
/* Example of Fixing Syntax or Column Error */
#Question: 统计2024年活跃用户
#Error SQL: SELECT count(*) FROM dws_login WHERE active_date = '2024'
#Error Message: Unknown column 'active_date' in 'where clause'
#Reasoning: The error indicates 'active_date' does not exist. I checked the [Database Schema] and found the correct column is 'dtstatdate'. Also, the format '2024' implies a yearly filter, but the column is yyyymmdd.
#Fixed SQL: SELECT count(*) FROM dws_login WHERE substr(dtstatdate, 1, 4) = '2024'
""",

    # 2. 结果为空 (逻辑过严或 ID 写错)
    "EmptyResult": """
/* Example of Fixing Empty Result */
#Question: 统计“消灭战”模式的参与人数
#Error SQL: SELECT count(*) FROM dws_round WHERE modename = '消灭战'
#Error Message: Execution succeeded but returned 0 rows.
#Reasoning: The query returned no data. I checked the [Knowledge] again. It says "消灭战" refers to `modename='组队竞技' AND submodename LIKE '%消灭战模式%'`. The previous SQL used the natural language name directly, which was wrong.
#Fixed SQL: SELECT count(*) FROM dws_round WHERE modename='组队竞技' AND submodename LIKE '%消灭战模式%'
""",

    # 3. 逻辑陷阱 (比如关联了不该关联的表)
    "LogicTrap": """
/* Example of Fixing Logic Trap */
#Question: 提取721号码包用户
#Error SQL: SELECT * FROM dws_login a JOIN dim_package b ON a.id = b.id WHERE b.pkg = '721'
#Error Message: Execution succeeded but returned 0 rows (or result is wrong).
#Reasoning: The [Knowledge] explicitly says "Do NOT join dim_package". I should use a direct filter or NOT IN logic instead of an INNER JOIN which might filter out valid data.
#Fixed SQL: SELECT * FROM dws_login WHERE id IN ('pkg7', 'pkg2', 'pkg1')
""",
}


def build_sql_fix_messages_super(
    schema_tables: List[Dict[str, Any]],
    knowledge_text: str,
    question: str,
    wrong_sql: str,
    error_msg: str,
):
    """
    Super Correction Prompt: 针对 Knowledge 和 Schema 的深度修正
    """
    error_type = "General"
    lower_msg = str(error_msg).lower()

    if ("unknown column" in lower_msg) or ("doesn't exist" in lower_msg) or ("syntax" in lower_msg):
        error_type = "SyntaxError"
    elif ("returned 0 rows" in lower_msg) or ("empty" in lower_msg):
        error_type = "EmptyResult"
    elif ("join" in wrong_sql.lower()) and ("dim_" in wrong_sql.lower()):
        error_type = "LogicTrap"

    correction_example = CORRECTION_FEW_SHOTS.get(error_type, CORRECTION_FEW_SHOTS["EmptyResult"])

    system_content = (
        "You are a **SQL Debugging Expert** for StarRocks.\n"
        "Your task is to fix the Error SQL based on the Error Message, Schema, and Domain Knowledge.\n\n"
        "**DEBUGGING STRATEGY:**\n"
        "1. **Check Schema**: If the error is \"Unknown column\", find the similar valid column in Schema.\n"
        "2. **Check Knowledge**: If the error is \"0 rows returned\", check if you missed a filter condition defined in [Knowledge] or used a wrong string literal.\n"
        "3. **Check Logic**: Avoid INNER JOIN that filters out rows; consider LEFT JOIN/EXISTS/IN when appropriate.\n\n"
        "**OUTPUT FORMAT (VERY IMPORTANT):**\n"
        "- First, you MAY provide a short natural-language explanation of the root cause (do NOT output JSON).\n"
        "- Then, you MUST output the corrected SQL wrapped in a single ```sql ... ``` code block. The code block MUST contain only valid StarRocks SQL, no comments, no JSON, no extra text.\n\n"
        f"{STARROCKS_SYNTAX_RULES}\n\n"
        f"{SQL_SELF_CHECKLIST}\n"
    )

    user_content = (
        "[Database Schema] (Reference for Column Names)\n"
        f"{json.dumps(schema_tables, indent=2, ensure_ascii=False)}\n\n"
        "[Domain Knowledge] (CRITICAL: Check mappings here)\n"
        f"{knowledge_text}\n\n"
        "[Correction Example] (How to fix similar errors)\n"
        f"{correction_example}\n\n"
        "-----------------------------------------\n"
        "[Current Task]\n"
        "#Question: \n"
        f"{question}\n\n"
        "#Error SQL: \n"
        f"{wrong_sql}\n\n"
        "#Error Message: \n"
        f"{error_msg}\n\n"
        "Please provide the fixed SQL.\n"
    )
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
