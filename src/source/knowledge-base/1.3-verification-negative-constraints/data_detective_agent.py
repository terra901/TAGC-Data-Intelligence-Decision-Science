



from __future__ import annotations

import sys
import io
import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import time
import pymysql
from openai import OpenAI

# --- 路径与配置：复用当前项目下 run/agent/config.py ---
BASE_DIR = Path(__file__).resolve().parent.parent
T2SQL_DIR = BASE_DIR
AGENT_DIR = T2SQL_DIR / "run" / "agent"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from config import (  # type: ignore
    DATA_DIR,
    SCHEMA_PATH,
    COMMON_KNOWLEDGE_PATH,
    FINAL_DATASET_PATH,
    ADDED_KNOWLEDGE_LIST_PATH,
    DB_CONFIG,
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    GEMINI_API_KEY,
    GEMINI_BASE_URL,
    GEMINI_MODEL,
    DETECTIVE_VERIFIED_KB_PATH,
    GEMINI_API_KEYS as CFG_GEMINI_API_KEYS,
)
from evaluation import OfflineEvaluator, load_json_as_dict  # type: ignore


SYSTEM_PROMPT = """You are a Senior Data Detective & SQL Expert.

Your goal is to answer business questions with 100% executable and logically verified SQL.

Core principles:
- Empiricism (实证主义): never guess, always verify on real data.
- Do not trust schema or task description blindly.
- Do not guess data formats or business definitions.
- You must verify specific hypotheses against actual data before giving the final SQL.
- You must only generate read-only SQL (SELECT ...). Never modify data.
 - **Time Columns Strategy**: Tables often have multiple time columns (e.g., `dteventtime` vs `tdbank_imp_date`). You MUST PROBE BOTH to determine which one contains the correct business data. Do not blindly default to one. Partition time (`tdbank_imp_date`) is often delayed compared to event time (`dteventtime`).
不要假设“查不到记录”就等于“没有发生过”，数据表往往是有记忆期限的。

Protocol:
1) You work in phases. Valid values of the field `phase` in your JSON output are:
   - "PROBE": diagnostic / hypothesis-testing queries.
   - "SOLVE": candidate final solution SQL.
   - "CONFIRM": confirmation that the last executed SOLVE SQL is logically correct.

2) Phase PROBE (hypothesis testing loop)
   - Goal: eliminate ambiguity about granularity, time logic, join conditions, status codes, etc.
   - Typical traps to check:
     * Granularity / summary rows, e.g. platform-level aggregates like platid = 255.
     * Time format & range: is date string or int? which range is actually present?
     * Column semantics: what do status codes 0/1/2 mean? any special markers?
   - You MUST output small, information-dense diagnostic SQLs (Smart Probes), not the final solution.
   - Always group / aggregate or use DISTINCT to understand distributions.
   - Prefer queries with LIMIT and GROUP BY to avoid huge result sets.
   - JSON format for PROBE:
     {
       "phase": "PROBE",
       "thought": "your reasoning about the ambiguity and hypothesis",
       "sql": "SELECT ..."   // read-only SQL
     }

3) Phase SOLVE (candidate final solution)
   - Only when key ambiguities are resolved, produce a full solution SQL.
   - Encode in `verified_logic` what rules and business assumptions you have already checked.
   - JSON format for SOLVE:
     {
       "phase": "SOLVE",
       "thought": "why this SQL solves the question and how it uses verified logic",
       "sql": "SELECT ...",   // read-only SQL
       "verified_logic": "bullet-point style summary of business rules and pitfalls avoided"
     }

4) Phase CONFIRM (result sanity check)
   - After the environment executes your last SOLVE SQL, you will see:
       * Row count
       * A few sample rows
       * Basic aggregates on numeric columns
   - You must decide whether the magnitude and filtering are reasonable.
   - JSON format for CONFIRM:
     {
       "phase": "CONFIRM",
       "thought": "why the result is logically consistent with the question",
       "verified_logic": "final refined description of the data logic and pitfalls avoided"
     }

5) General rules for ALL phases
   - Always return a single JSON object, no extra text.
   - The top-level keys must include at least: `phase`, `thought`.
   - SQL must be valid for a MySQL/StarRocks-like dialect.
   - NEVER use DDL or DML (NO INSERT/UPDATE/DELETE/CREATE/DROP). Only SELECT.


6) **Few-Shot Examples (Follow these patterns strictly)**

**Example 1: Safe Probing (Avoiding Syntax Errors in UNION)**
*User*: "Check the distribution of `platid` in both login and match tables for 20231101."
*Assistant*:
{
  "phase": "PROBE",
  "thought": "I need to check distinct `platid` values to see if they are raw (0,1,2) or aggregated (255). I will use UNION ALL to check both tables in one go. I must wrap subqueries with LIMIT to avoid syntax errors in MySQL/StarRocks.",
  "sql": "SELECT * FROM (SELECT 'login' as type, platid, count(*) as cnt FROM dws_login_di WHERE date='20231101' GROUP BY platid ORDER BY cnt DESC LIMIT 5) t1 UNION ALL SELECT * FROM (SELECT 'match' as type, platid, count(*) as cnt FROM dws_match_di WHERE date='20231101' GROUP BY platid ORDER BY cnt DESC LIMIT 5) t2"
}

**Example 2: Verifying Logic before Solving (Data Empiricism)**
*User*: "Find users retained for 7 days after registering in 'Ranked Mode' (mode_id=102)."
*Assistant*:
{
  "phase": "PROBE",
  "thought": "Before calculating retention, I must verify: 1) Does `mode_id=102` actually exist in the logs? 2) Is the date format 'YYYYMMDD' or 'YYYY-MM-DD' so I can use the correct date function.",
  "sql": "SELECT 'date_format' as check_type, dtstatdate as sample_val, count(*) FROM dws_match_di GROUP BY dtstatdate LIMIT 3 UNION ALL SELECT 'mode_check', cast(mode_id as string), count(*) FROM dws_match_di WHERE mode_id=102 GROUP BY mode_id"
}

**Example 3: Handling Empty Results (Zero Rows is usually a Bug)**
*User*: (System) "**[CRITICAL REJECTION] SQL executed successfully but returned 0 rows.** ... Please review common pitfalls..."
*Assistant*:
{
  "phase": "PROBE",
  "thought": "I received a critical rejection for 0 rows. It's likely that my filter `mode_id=102` is incorrect (maybe it's a string '102'?) or I used the wrong partition key. I need to remove the WHERE clause and check the actual distribution of `mode_id`.",
  "sql": "SELECT mode_id, count(*) FROM dws_match_di GROUP BY mode_id ORDER BY 2 DESC LIMIT 10"
}
"""


VERIFIED_KB_PATH = DETECTIVE_VERIFIED_KB_PATH
GOLDEN_RESULT_PATH = BASE_DIR / "data" / "correct_58.json"
ERROR_FEEDBACK_PATH = BASE_DIR / "data" / "error_feedback.json"
STARROCK_KNOWLEDGE_PATH = BASE_DIR / "data" / "starrock_knowledge.md"
FINAL_DATASET_FOR_DETECTIVE = BASE_DIR / "data" / "final_dataset.json"
MAX_TURNS = 150
MAX_ROWS_PER_QUERY = 500
#'sql_2', 'sql_8',
SQL_ID_LIST = [ 'sql_8', 'sql_19', 'sql_20', 'sql_29', 'sql_40', 'sql_44',  'sql_52', 'sql_53', 'sql_54', 'sql_55', 'sql_58', 'sql_61',  'sql_64',  'sql_80', 'sql_84', 'sql_85', 'sql_88', 'sql_89', 'sql_90', 'sql_91', 'sql_93',  'sql_98', 'sql_99', 'sql_100', 'sql_105', 'sql_108', 'sql_110',  'sql_115', 'sql_117', 'sql_120']

# 复用 run/agent/config.py 中维护的 key 列表，避免两处不同步
GEMINI_API_KEYS = [k for k in (CFG_GEMINI_API_KEYS or []) if k]
OPENAI_API_KEYS = [
    "<REDACTED_API_KEY>",
    "<REDACTED_API_KEY>",
    "<REDACTED_API_KEY>",
]

DEAD_GEMINI_KEYS: set = set()
DEAD_OPENAI_KEYS: set = set()


@dataclass
class SQLResult:
    rows: List[Dict[str, Any]]
    error: Optional[str]


class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:  # type: ignore[override]
        try:
            import decimal
            import datetime as dt
            if isinstance(obj, decimal.Decimal):
                return float(obj)
            if isinstance(obj, (dt.datetime, dt.date)):
                return obj.isoformat()
            # === 新增：处理 TimeDelta (解决报错的核心) ===
            if isinstance(obj, dt.timedelta):
                return str(obj)
        except Exception:
            pass
        return super().default(obj)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def get_llm_client() -> Tuple[OpenAI, str, str]:
    """根据 LLM_PROVIDER 返回主模型 client / model / provider。

    provider 目前支持：
    - "gemini": 使用 GEMINI_* 配置
    - 其他: 使用 OPENAI_* (豆包)
    """

    provider = (LLM_PROVIDER or "").lower()
    if provider == "gemini":
        api_key = GEMINI_API_KEY
        base_url = GEMINI_BASE_URL
        model = GEMINI_MODEL
    else:
        api_key = OPENAI_API_KEY
        base_url = OPENAI_BASE_URL
        model = OPENAI_MODEL

    if not api_key:
        raise RuntimeError("LLM API key is not configured")

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model, provider


def _strip_code_fences(text: str) -> str:
    """去掉 LLM 返回内容外层的 ```json / ```sql / ``` 代码块包裹。

    兼容形如：
    ```json
    { ... }
    ```
    或者：
    ```
    { ... }
    ```
    """

    if not text:
        return text

    s = text.strip()
    if not s.startswith("```"):
        return s

    # 去掉第一行 ```xxx
    if "\n" in s:
        first_line, rest = s.split("\n", 1)
        s = rest
    # 去掉末尾 ```
    s = s.strip()
    if s.endswith("```"):
        s = s[: s.rfind("```")].strip()
    return s


def _extract_json_object(text: str) -> Optional[str]:
    """从包含自然语言 + JSON 的文本中提取第一个 {...} JSON 对象。"""

    if not text:
        return None

    s = text.strip()
    # 若包含 ```，从第一个 ``` 之后开始找，避免前面的解释文字里的花括号干扰
    start_search = 0
    fence_pos = s.find("```")
    if fence_pos != -1:
        start_search = fence_pos

    start = s.find("{", start_search)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def call_llm_json(
    client: OpenAI,
    model: str,
    provider: str,
    messages: List[Dict[str, str]],
) -> Optional[Dict[str, Any]]:
    """调用主模型，多次重试；失败后按需回退到豆包。

    逻辑：
    - 主模型最多尝试 5 次（请求异常或 JSON 解析失败都算失败）。
    - 若仍失败，且当前 provider 不是 openai，则使用 OPENAI_* 配置调用豆包 1 次。
    """

    def _single_call(
        cur_client: OpenAI,
        cur_model: str,
        use_strict_json: bool,
        tag: str,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        try:
            if use_strict_json:
                # Gemini 走严格 JSON：reasoning_effort + response_format
                resp = cur_client.chat.completions.create(
                    model=cur_model,
                    messages=messages,
                    temperature=1,
                    # max_tokens=2048,
                    reasoning_effort="high",
                    # thinking_level='high',
                    response_format={"type": "json_object"},
                )
            else:
                # 豆包暂不支持 response_format / reasoning_effort
                resp = cur_client.chat.completions.create(
                    model=cur_model,
                    messages=messages,
                    temperature=1,
                    # max_tokens=2048,
                )
            print(resp, f"resp[{tag}]")
        except Exception as e:
            msg = str(e)
            safe_msg = msg.replace("¥", "Y")
            print(f"[LLM][{tag}] 调用失败: {safe_msg}")
            # 检测额度不足类错误，标记为配额问题（仅在此情况下 kill 该 key）
            if "insufficient_user_quota" in msg or "额度不足" in msg:
                return None, "quota"
            # 检测服务繁忙 / 频率限制类错误：不 kill key，而是 sleep 后重试
            busy_keywords = [
                "请求太频繁",
                "请求过于频繁",
                "系统繁忙",
                "服务繁忙",
                "Too Many Requests",
                "too many requests",
                "rate limit",
                "Rate limit",
                "429",
            ]
            if any(k in msg for k in busy_keywords):
                return None, "busy"
            return None, "error"

        text = resp.choices[0].message.content or ""
        raw_text = text
        text = _strip_code_fences(text)
        try:
            return json.loads(text), "ok"
        except Exception as e:
            print(f"[LLM][{tag}] JSON 解析失败: {e}\n原始输出: {raw_text}")
            # 第二次机会：从混合文本中尝试抽取 JSON 对象
            candidate = _extract_json_object(raw_text)
            if candidate:
                try:
                    return json.loads(candidate), "ok"
                except Exception as e2:
                    print(f"[LLM][{tag}] 抽取 JSON 再解析仍失败: {e2}\n候选片段: {candidate}")
            return None, "parse"

    def _try_with_keys(
        api_keys: List[str],
        base_url: str,
        model_name: str,
        use_strict_json: bool,
        tag_prefix: str,
        use_initial_client: bool = False,
        provider_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        # 针对不同 provider 维护进程内“废弃 key”集合
        if provider_name == "gemini":
            dead_keys = DEAD_GEMINI_KEYS
        elif provider_name == "openai":
            dead_keys = DEAD_OPENAI_KEYS
        else:
            dead_keys = set()

        for key_index, api_key in enumerate(api_keys):
            if not api_key or api_key in dead_keys:
                continue
            attempt = 0
            while True:
                attempt += 1
                tag = f"{tag_prefix}-k{key_index + 1}-try{attempt}"
                if use_initial_client and key_index == 0 and attempt == 1:
                    cur_client = client
                else:
                    try:
                        cur_client = OpenAI(api_key=api_key, base_url=base_url)
                    except Exception as e:
                        print(f"[LLM][{tag}] 创建客户端失败: {e}")
                        break
                action, err_type = _single_call(cur_client, model_name, use_strict_json, tag=tag)
                if action is not None:
                    return action
                if err_type == "quota":
                    dead_keys.add(api_key)
                    print(f"[LLM] Key #{key_index + 1} 配额不足，标记为失效，切换下一 key。")
                    break
                if err_type == "busy":
                    print(f"[LLM] Key #{key_index + 1} 当前服务繁忙，30 秒后重试 (第 {attempt} 次)...")
                    time.sleep(30)
                    continue
                print(f"[LLM] Key #{key_index + 1} 第 {attempt} 次失败，准备重试...")
        return None

    provider_l = (provider or "").lower()
    is_gemini = provider_l == "gemini"

    # 1) 主模型：在当前 provider 的 key 列表中轮询
    if is_gemini:
        primary_keys = [k for k in GEMINI_API_KEYS if k]
        if not primary_keys and GEMINI_API_KEY:
            primary_keys = [GEMINI_API_KEY]
        primary_base_url = GEMINI_BASE_URL
        use_strict_json = True
    else:
        primary_keys = [k for k in OPENAI_API_KEYS if k]
        if not primary_keys and OPENAI_API_KEY:
            primary_keys = [OPENAI_API_KEY]
        primary_base_url = OPENAI_BASE_URL
        use_strict_json = False

    action = _try_with_keys(
        api_keys=primary_keys,
        base_url=primary_base_url,
        model_name=model,
        use_strict_json=use_strict_json,
        tag_prefix="primary",
        use_initial_client=True,
        provider_name=provider_l,
    )
    if action is not None:
        return action

    print("[LLM] 当前 provider 的所有 API Key 均调用失败。")

    # 若是 Gemini，且所有 key 都因配额不足被标记为失效，则不再回退到 OpenAI，直接终止
    if is_gemini:
        all_gem_keys = [k for k in GEMINI_API_KEYS if k]
        if all_gem_keys and all(k in DEAD_GEMINI_KEYS for k in all_gem_keys):
            print("[LLM] 所有 Gemini API Key 因额度不足被标记为失效，终止本次任务。")
            return None

    # 2) 回退到豆包（OPENAI_* 配置）。若当前已经是 openai，则不再回退。
    if provider_l == "openai":
        print("[LLM] 当前 provider 已是 openai，跳过回退。")
        return None

    backup_keys = [k for k in OPENAI_API_KEYS if k]
    if not backup_keys and OPENAI_API_KEY:
        backup_keys = [OPENAI_API_KEY]
    if not backup_keys:
        print("[LLM] 无法回退到豆包：OPENAI_API_KEY_LIST 为空。")
        return None

    action = _try_with_keys(
        api_keys=backup_keys,
        base_url=OPENAI_BASE_URL,
        model_name=OPENAI_MODEL,
        use_strict_json=False,
        tag_prefix="fallback-openai",
        use_initial_client=False,
        provider_name="openai",
    )
    return action


def execute_sql(sql: str, max_rows: int = MAX_ROWS_PER_QUERY) -> SQLResult:
    cfg = DB_CONFIG
    conn = None
    try:
        conn = pymysql.connect(
            host=cfg["host"],
            user=cfg["user"],
            password=cfg.get("password", ""),
            db=cfg["db"],
            port=cfg["port"],
            cursorclass=pymysql.cursors.DictCursor,
            charset="utf8mb4",
        )
        with conn.cursor() as cursor:
            cursor.execute(sql)
            # 不再采样，直接获取完整结果集
            rows = cursor.fetchall()
        return SQLResult(rows=list(rows), error=None)
    except Exception as e:
        return SQLResult(rows=[], error=str(e))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def summarize_result(rows: List[Dict[str, Any]], max_samples: int = 10) -> str:
    """将查询结果完整序列化为文本返回给 LLM（不再采样）。

    参数 max_samples 保留只是为了兼容旧签名，当前实现中不再使用采样逻辑。
    """
    row_count = len(rows)
    if row_count == 0:
        return "0 rows returned."

    lines: List[str] = []
    lines.append(f"Row count: {row_count}")
    for idx, r in enumerate(rows, start=1):
        lines.append(f"Row {idx}: {json.dumps(r, ensure_ascii=False, cls=EnhancedJSONEncoder)}")
    return "\n".join(lines)


def get_result_profile(rows: List[Dict[str, Any]], max_samples: int = 3) -> Dict[str, Any]:
    profile: Dict[str, Any] = {"row_count": len(rows)}
    if not rows:
        profile["sample_rows"] = []
        profile["numeric_aggregates"] = {}
        return profile

    sample_rows = rows[:max_samples]
    profile["sample_rows"] = sample_rows

    # 简单按第一行推断数值列
    first = rows[0]
    numeric_keys: List[str] = []
    for k, v in first.items():
        if isinstance(v, (int, float)):
            numeric_keys.append(k)
    aggs: Dict[str, Dict[str, float]] = {}
    for key in numeric_keys:
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        if not vals:
            continue
        total = float(sum(vals))
        avg = total / len(vals)
        aggs[key] = {"sum": total, "avg": avg}
    profile["numeric_aggregates"] = aggs
    return profile


def load_task(sql_id: str) -> Dict[str, Any]:
    # Data Detective 固定基于完整的 final_dataset.json，而不依赖于 run/agent/config.py 中的 FINAL_DATASET_PATH
    data = load_json(FINAL_DATASET_FOR_DETECTIVE)
    for item in data:
        if item.get("sql_id") == sql_id:
            return item
    raise ValueError(f"未在 final_dataset.json 中找到 sql_id={sql_id} 的任务")


def load_added_knowledge(sql_id: str) -> str:
    if not Path(ADDED_KNOWLEDGE_LIST_PATH).exists():
        return ""
    data = load_json(Path(ADDED_KNOWLEDGE_LIST_PATH))
    pieces: List[str] = []
    for item in data:
        if item.get("sql_id") == sql_id and item.get("knowledge"):
            pieces.append(str(item["knowledge"]))
    return "\n\n".join(pieces)


def build_schema_snippet(table_list: List[str]) -> str:
    """根据当前任务涉及的表列表，构建 schema 片段供 LLM 参考。

    兼容两类 schema 结构：
    - 旧版 schema_long.json: 列使用 column_name / column_type，表描述在 llm_table_long_description。
    - 新版 schema_all.json: 列使用 col / type，表描述在 table_description_llm_long。
    """

    schema = load_json(Path(SCHEMA_PATH))
    wanted = set(table_list)
    parts: List[str] = []
    for entry in schema:
        table_name = entry.get("table_name")
        if table_name not in wanted:
            continue

        # 表级描述：优先新版字段，其次兼容旧字段
        desc = (
            entry.get("table_description_llm_long")
            or entry.get("llm_table_long_description", "")
        )
        cols = entry.get("columns", [])
        lines: List[str] = []
        lines.append(f"[Table] {table_name}")
        if desc:
            lines.append(f"Description: {desc}")
        if cols:
            lines.append("Columns:")
            for c in cols:
                # 列名/类型兼容两种 schema 格式
                cname = c.get("column_name") or c.get("col")
                ctype = c.get("column_type") or c.get("type")
                cdesc = (
                    c.get("llm_long_description")
                    or c.get("llm_short_description")
                    or c.get("description", "")
                )
                if not cname and not cdesc:
                    continue
                if ctype:
                    lines.append(f"- {cname} ({ctype}): {cdesc}")
                else:
                    lines.append(f"- {cname}: {cdesc}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def build_initial_user_message(sql_id: str) -> str:
    task = load_task(sql_id)
    question = str(task.get("question", "")).strip()
    table_list: List[str] = list(task.get("table_list", []))
    task_knowledge = str(task.get("knowledge", "")).strip()

    schema_text = build_schema_snippet(table_list)
    common_knowledge = load_text(Path(COMMON_KNOWLEDGE_PATH))
    starrock_knowledge = load_text(Path(STARROCK_KNOWLEDGE_PATH))
    added_knowledge = load_added_knowledge(sql_id)

    parts: List[str] = []
    parts.append(f"Task sql_id: {sql_id}")
    parts.append(f"User Question:\n{question}")
    parts.append(f"Related Tables: {', '.join(table_list)}")
    if schema_text:
        parts.append("\n[Schema for related tables]\n" + schema_text)
    if common_knowledge:
        parts.append("\n[Global Common Knowledge]\n" + common_knowledge)
    if starrock_knowledge:
        parts.append("\n[StarRocks SQL Dialect Knowledge]\n" + starrock_knowledge)
    if task_knowledge:
        parts.append("\n[Task Knowledge from final_dataset]\n" + task_knowledge)
    if added_knowledge:
        parts.append("\n[Verified Knowledge from knowledge_add_clean_list]\n" + added_knowledge)

    parts.append(
        "\nPlease start with phase 'PROBE'. "
        "For each response, output ONLY a single JSON object as specified in the protocol."
        "IMPORTANT: \n"
        "1. Always check if you need to convert IDs (e.g., qq -> wxid) using mapping tables.\n"
        "2. Always check if specific partitions (e.g., saccounttype='-100') are required for aggregates."
    )

    return "\n\n".join(parts)


def load_verified_kb() -> List[Dict[str, Any]]:
    if not VERIFIED_KB_PATH.exists():
        return []
    try:
        return load_json(VERIFIED_KB_PATH)
    except Exception:
        return []


def append_verified_entry(entry: Dict[str, Any]) -> None:
    kb = load_verified_kb()
    kb.append(entry)
    VERIFIED_KB_PATH.parent.mkdir(parents=True, exist_ok=True)
    VERIFIED_KB_PATH.write_text(
        json.dumps(kb, ensure_ascii=False, indent=2, cls=EnhancedJSONEncoder),
        encoding="utf-8",
    )


def get_completed_sql_ids() -> set:
    kb = load_verified_kb()
    completed: set = set()
    for entry in kb:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("sql_id")
        if isinstance(sid, str):
            completed.add(sid)
    return completed


class HistoryGuard:
    """基于历史正确/错误结果的哨兵，用于约束 SOLVE 阶段的结果集。

    - 若存在标准正确结果：当前结果必须与之完全一致，否则视为严重回归错误。
    - 若不存在正确结果，但存在历史错误结果集：当前结果若与其中任何一批一致，则强制打回。
    """

    def __init__(self, golden_path: Path, error_path: Path) -> None:
        self.evaluator = OfflineEvaluator()
        self.golden_map: Dict[str, List[Dict[str, Any]]] = {}
        # sql_id -> List[List[Dict]]，每个内层 list 是一次完整错误提交的结果集
        self.error_batches: Dict[str, List[List[Dict[str, Any]]]] = {}

        self._load_golden(golden_path)
        self._load_error(error_path)

    def _load_golden(self, path: Path) -> None:
        if not path.exists():
            print(f"[HistoryGuard] 未找到正确结果文件: {path}")
            return
        try:
            self.golden_map = load_json_as_dict(str(path))
        except Exception as e:
            print(f"[HistoryGuard] 加载正确结果失败: {e}")
            self.golden_map = {}

    def _load_error(self, path: Path) -> None:
        if not path.exists():
            print(f"[HistoryGuard] 未找到错题本文件: {path}")
            return
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as e:
            print(f"[HistoryGuard] 加载错题本失败: {e}")
            return

        if not isinstance(data, dict):
            print("[HistoryGuard] 错题本顶层不是 dict，忽略。")
            return

        for sid, rec in data.items():
            if not isinstance(rec, dict):
                continue
            batches_raw = rec.get("history_batches") or []
            if not isinstance(batches_raw, list):
                continue

            valid_batches: List[List[Dict[str, Any]]] = []
            for b in batches_raw:
                if not isinstance(b, list):
                    continue
                rows = [r for r in b if isinstance(r, dict)]
                if rows:
                    valid_batches.append(rows)

            if valid_batches:
                self.error_batches[sid] = valid_batches

    def check(self, sql_id: str, current_rows: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """对当前结果集做历史约束检查，返回 (是否通过, 反馈信息)。"""

        rows = current_rows or []

        # 1) 正确题：结果必须与历史正确答案完全一致
        if sql_id in self.golden_map:
            golden = self.golden_map.get(sql_id, [])
            try:
                is_same = self.evaluator.evaluate_single_sql(golden, rows)
            except Exception:
                is_same = False

            if is_same:
                return True, "【Result Match】当前结果与历史正确答案完全一致，已通过 HistoryGuard 验证。"
            return (
                False,
                "[Fatal Error] This question was previously solved CORRECTLY, "
                "but your current SQL produces a DIFFERENT result set.\n"
                f"Previous (Correct) Row Count: {len(golden)}\n"
                f"Current Row Count: {len(rows)}\n"
                "You MUST modify logic to match the previous correct data distribution.",
            )

        # 2) 错题约束：结果不得与任何历史错误结果集完全一致
        batches = self.error_batches.get(sql_id)
        if not batches:
            return True, ""
        for idx, batch in enumerate(batches, start=1):
            try:
                is_same = self.evaluator.evaluate_single_sql(batch, rows)
            except Exception:
                is_same = False
            if is_same:
                advice_str = (
                            "1. **Check Output Alignment**: \n"
                            "   - Is the result granularity correct (e.g., User level vs. Event level)?\n"
                            "   - **Critical**: Are you outputting the EXACT ID type requested (e.g., QQ vs. WXID)?\n"
                            "2. **Check Time & Filter Logic (High-Frequency Error)**: \n"
                            "   - **Critical**: Tables often have multiple time columns (e.g., `dteventtime` vs `tdbank_imp_date`). They often contain DIFFERENT data. You MUST probe/verify ALL time columns to select the one that matches the question's intent.\n"
                            "   - Are there date boundary issues (e.g., exclusive vs inclusive)?\n"
                            "   - Your logic reproduces a KNOWN ERROR. Did you miss a hidden condition?\n"
                            "3. **Verify Business Intent & Strategy**: \n"
                            "   - **Accuracy > Efficiency**: Do not worry about query performance. Complex joins or subqueries are acceptable if they yield the correct result.\n"
                            "   - Re-read the question's definition of metrics (e.g., 'Churn', 'Retention').\n"
                            "   - Are you querying the correct table? (e.g., `dwd` vs `dws` difference)."
                        )
                return (
                    False,
                    f"**[Trap Detected]** Your result matches a known INCORRECT submission (Error Batch #{idx}).\n"
                    "This means you have reproduced a specific past mistake. You MUST change your logic.\n\n"
                    f"**Diagnostic Protocol**:\n{advice_str}\n\n"
                    "**Action**: Do not guess. Use 'PROBE' to verify which of the above 3 points caused this error.",
                )

        return True, "【Path Avoidance】当前结果与历史错误结果集均不同，通过历史哨兵校验。"


def call_llm_text(
    client: OpenAI,
    model: str,
    provider: str,
    messages: List[Dict[str, str]],
) -> str:
    """调用 LLM 返回纯文本建议 (用于 Meta-Analyzer)，带 API key 轮询与重试逻辑。"""
    def _single_call(
        cur_client: OpenAI,
        cur_model: str,
        use_strict_json: bool,
        tag: str,
    ) -> Tuple[Optional[str], str]:
        try:
            resp = cur_client.chat.completions.create(
                model=cur_model,
                messages=messages,
                temperature=0.3,
            )
            text = resp.choices[0].message.content or ""
            return text, "ok"
        except Exception as e:
            msg = str(e)
            safe_msg = msg.replace("¥", "Y")
            print(f"[Meta-Analyzer][{tag}] 调用失败: {safe_msg}")
            # 仅在额度不足时标记为 quota，触发换 key
            if "insufficient_user_quota" in msg or "额度不足" in msg:
                return None, "quota"
            # 其余各种频率/限流错误都视为 busy，在同一 key 上重试
            busy_keywords = [
                "请求太频繁",
                "请求过于频繁",
                "系统繁忙",
                "服务繁忙",
                "Too Many Requests",
                "too many requests",
                "rate limit",
                "Rate limit",
                "429",
                "API rate limit exceeded",
            ]
            if any(k in msg for k in busy_keywords):
                return None, "busy"
            # 其他错误也不断重试当前 key
            return None, "error"

    def _try_with_keys(
        api_keys: List[str],
        base_url: str,
        model_name: str,
        use_strict_json: bool,
        tag_prefix: str,
        use_initial_client: bool = False,
        provider_name: str = "",
    ) -> Optional[str]:
        if provider_name == "gemini":
            dead_keys = DEAD_GEMINI_KEYS
        elif provider_name == "openai":
            dead_keys = DEAD_OPENAI_KEYS
        else:
            dead_keys = set()

        for key_index, api_key in enumerate(api_keys):
            if not api_key or api_key in dead_keys:
                continue
            attempt = 0
            while True:
                attempt += 1
                tag = f"{tag_prefix}-k{key_index + 1}-try{attempt}"
                if use_initial_client and key_index == 0 and attempt == 1:
                    cur_client = client
                else:
                    try:
                        cur_client = OpenAI(api_key=api_key, base_url=base_url)
                    except Exception as e:
                        print(f"[Meta-Analyzer][{tag}] {e}")
                        break

                result, err_type = _single_call(
                    cur_client, model_name, use_strict_json, tag=tag
                )
                if result is not None:
                    return result
                if err_type == "quota":
                    dead_keys.add(api_key)
                    print(
                        f"[Meta-Analyzer] Key #{key_index + 1} has insufficient quota."
                    )
                    break
                if err_type == "busy":
                    print(
                        f"[Meta-Analyzer] Key #{key_index + 1} is busy. Retrying in 30 seconds..."
                    )
                    time.sleep(30)
                    continue
                print(
                    f"[Meta-Analyzer] Key #{key_index + 1} failed after {attempt} attempts."
                )
        return None

    provider_l = (provider or "").lower()
    is_gemini = provider_l == "gemini"

    if is_gemini:
        primary_keys = [k for k in GEMINI_API_KEYS if k]
        if not primary_keys and GEMINI_API_KEY:
            primary_keys = [GEMINI_API_KEY]
        primary_base_url = GEMINI_BASE_URL
        use_strict_json = False
    else:
        primary_keys = [k for k in OPENAI_API_KEYS if k]
        if not primary_keys and OPENAI_API_KEY:
            primary_keys = [OPENAI_API_KEY]
        primary_base_url = OPENAI_BASE_URL
        use_strict_json = False

    action = _try_with_keys(
        api_keys=primary_keys,
        base_url=primary_base_url,
        model_name=model,
        use_strict_json=use_strict_json,
        tag_prefix="primary",
        use_initial_client=True,
        provider_name=provider_l,
    )
    if action is not None:
        return action

    print("[Meta-Analyzer] All primary keys exhausted.")

    if is_gemini:
        all_gem_keys = [k for k in GEMINI_API_KEYS if k]
        if all_gem_keys and all(k in DEAD_GEMINI_KEYS for k in all_gem_keys):
            print(
                "[Meta-Analyzer] All Gemini keys have insufficient quota. Fallback to OpenAI."
            )
            return "（分析生成失败，请人工检查历史记录）"

    if provider_l == "openai":
        print("[Meta-Analyzer] OpenAI primary keys exhausted. Fallback to backup keys.")
        return "（分析生成失败，请人工检查历史记录）"

    backup_keys = [k for k in OPENAI_API_KEYS if k]
    if not backup_keys and OPENAI_API_KEY:
        backup_keys = [OPENAI_API_KEY]
    if not backup_keys:
        print("[Meta-Analyzer] No backup keys available.")
        return "（分析生成失败，请人工检查历史记录）"

    action = _try_with_keys(
        api_keys=backup_keys,
        base_url=OPENAI_BASE_URL,
        model_name=OPENAI_MODEL,
        use_strict_json=False,
        tag_prefix="fallback-openai",
        use_initial_client=False,
        provider_name="openai",
    )
    if action is not None:
        return action

    return "（分析生成失败，请人工检查历史记录）"


def analyze_deadlock(
    client: OpenAI,
    model: str,
    provider: str,
    history: List[Dict[str, str]],
    guard_msg: str,
) -> str:
    """分析当前对话历史，找出 Agent 陷入死循环或幻觉的原因。"""

    # 使用完整对话历史作为上下文，提供最全面的诊断信息
    recent_history = history

    # 将 history 转换为易读的文本摘要
    history_text = json.dumps(recent_history, ensure_ascii=False, indent=2)

    analysis_prompt = f"""
You are a Lead Data Scientist coaching a Junior Data Agent.
The Agent is trying to solve a SQL problem but is stuck in a loop or making logical errors.
The system has blocked their submission with the following error:
"{guard_msg}"

Here is the conversation history so far (last 15 turns):
{history_text}

**Your Task:**
Analyze *why* the agent is failing. Look for these common patterns:
1. **Time Column Hallucination**: Did they assume `dteventtime` is empty without checking? Or did they check it incorrectly (e.g., wrong syntax)? Did they ignore `tdbank_imp_date`?
2. **Filter Trap**: Are they filtering for specific values (e.g., `matchresult='ok'`) that PROBEs have shown to be NULL?
3. **Logical Loop**: Are they repeating the exact same logic that was already rejected?
4. **Data Blindness**: Did they ignore a PROBE result that explicitly contradicted their assumption?

**Output:**
Provide a concise, specific "Breakthrough Suggestion" addressed directly to the Agent (use "You").
- Do NOT rewrite the full SQL.
- Point out the specific assumption that is blocking them.
- Tell them explicitly what column or value to check next.
- Suggest probing BOTH time columns if they are stuck on one.
"""

    messages = [{"role": "user", "content": analysis_prompt}]

    print("\n[System] 正在调用 Meta-Analyzer 进行死循环诊断...")
    advice = call_llm_text(client, model, provider, messages)
    return advice


def run_agent_loop(sql_id: str, max_turns: int = MAX_TURNS) -> None:
    client, model, provider = get_llm_client()
    history_guard = HistoryGuard(GOLDEN_RESULT_PATH, ERROR_FEEDBACK_PATH)
    user_message = build_initial_user_message(sql_id)

    history: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    last_final_sql: Optional[str] = None
    last_verified_logic: str = ""

    for turn in range(1, max_turns + 1):
        print(f"\n================ Data Detective 回合 {turn}/{max_turns} ================")
        action = call_llm_json(client, model, provider, history)
        if not action:
            print("[Agent] 无法获得有效的 JSON 响应，终止。")
            return

        phase = str(action.get("phase", "")).upper()
        thought = str(action.get("thought", "")).strip()
        sql = str(action.get("sql", "")).strip()

        print(f"[Agent] phase={phase}")
        if thought:
            print(f"[Agent] thought: {thought}")
        if sql:
            print(f"[Agent] sql: {sql}")

        # 记录模型原始 JSON 响应
        history.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})

        if phase == "PROBE":
            if not sql:
                history.append(
                    {
                        "role": "user",
                        "content": "Your PROBE action must contain a non-empty 'sql' field.",
                    }
                )
                continue

            print("[Env] 执行 PROBE SQL...")
            result = execute_sql(sql)
            print("result:", result)
            if result.error:
                summary = f"Execution error: {result.error}"
            else:
                # 尝试安全地序列化结果，防止非 JSON 可序列化类型导致崩溃
                try:
                    summary = summarize_result(result.rows)
                except TypeError as e:
                    error_msg = str(e)
                    if "not JSON serializable" in error_msg:
                        feedback = (
                            f"[System Error] Your SQL result contains data types that are NOT JSON serializable (e.g., timedelta/duration objects). "
                            f"Python Error: {error_msg}.\n"
                            "**REQUIRED ACTION**: Please Rewrite your SQL to cast these columns to Strings or Integers (e.g., use `CAST(col AS CHAR)` or `TIMESTAMPDIFF` to get a number)."
                        )
                        print(f"[Env] 捕获序列化错误，要求模型重写 SQL: {error_msg}")
                        history.append({"role": "user", "content": feedback})
                        continue
                    else:
                        # 其他未知序列化错误，降级为简单文本提示
                        summary = f"Result serialization error: {error_msg}"

            feedback = (
                "[Execution Result for PROBE]\n"
                f"SQL:\n{sql}\n\n"
                f"Summary:\n{summary}\n\n"
                "Based on this result, what is your specific deduction regarding the data logic? "
                "If there are still ambiguities, continue with another PROBE. "
                "If key ambiguities are resolved, move to phase 'SOLVE' with a candidate final SQL."
            )
            history.append({"role": "user", "content": feedback})
            continue

        if phase == "SOLVE":
            if not sql:
                history.append(
                    {
                        "role": "user",
                        "content": "Your SOLVE action must contain a non-empty 'sql' field.",
                    }
                )
                continue

            if not any(
                m.get("role") == "user"
                and "STOP. READ THIS BEFORE OUTPUTTING YOUR FINAL SQL." in m.get("content", "")
                for m in history
            ):
                ultimate_warning = (
                    "STOP. READ THIS BEFORE OUTPUTTING YOUR FINAL SQL.\n\n"
                    "You are about to submit a candidate final answer.\n"
                    "This SQL will be executed and its result set will be compared against:\n"
                    "• All historically correct answers (must match 100% if exists)\n"
                    "• 47+ known wrong result patterns from past failed attempts\n\n"
                    "If your result matches even ONE known wrong pattern, you will be REJECTED immediately "
                    "and forced to start over from PROBE.\n\n"
                    "Agents who ignore this warning fail. Agents who treat this as a minefield succeed.\n"
                    "Ask yourself:\n"
                    "1. Did I verify which time column actually has data?\n"
                    "2. Did I convert between qq/wxid correctly?\n"
                    "3. Is there a hidden filter like saccounttype='-100' I missed?\n"
                    "4. Is my output granularity what the question really wants?\n\n"
                    "This is not a drill. Double-check your logic now."
                )
                history.append({"role": "user", "content": ultimate_warning})
                continue  # 强制模型重新输出 SOLVE

            last_final_sql = sql
            last_verified_logic = str(action.get("verified_logic", "") or "")

            print("[Env] 执行 SOLVE SQL（候选最终 SQL）...")
            result = execute_sql(sql)
            if result.error:
                feedback = (
                    f"[Error] SQL execution failed: {result.error}. "
                    "Please fix your SQL and respond with a new JSON action (phase 'SOLVE' or 'PROBE')."
                )
                history.append({"role": "user", "content": feedback})
                continue

            # ========================== 空结果强拦截：CRITICAL REJECTION ==========================
            if not result.rows:
                feedback = (
                    "**[CRITICAL REJECTION] SQL executed successfully but returned 0 rows.**\n\n"
                    "An empty result is almost certainly INCORRECT. Please review these common pitfalls:\n\n"
                    "1. **ID & Table Mismatch (Most Likely)**:\n"
                    "   - Are you filtering for a specific user type (e.g., `wxid`) in a table that only stores generic IDs or `qq`?\n"
                    "   - **Check `table_list`**: Do you need to join a mapping table (e.g., `dim_...idconversion...` or `dim_...2qq...`) to convert IDs before filtering?\n\n"
                    "2. **Date & Format Issues**:\n"
                    "   - **Format**: Is the date column `YYYYMMDD` (string/int) or `YYYY-MM-DD`? Did you match it correctly?\n"
                    "   - **Boundaries**: Are you calculating a date range (e.g., `date_sub`) that falls outside the partition availability?\n\n"
                    "3. **Implicit Filters & Aggregates**:\n"
                    "   - Did you forget a necessary partition filter from 'Knowledge' (e.g., `saccounttype='-100'` for aggregates)?\n"
                    "   - Did you use `INNER JOIN` on a condition that never matches? Try `LEFT JOIN` or check the join keys.\n\n"
                    "4. **String/Enum Codes**:\n"
                    "   - Are you filtering `type=1` (int) vs `type='1'` (string)?\n"
                    "   - Are you filtering by a Chinese name (e.g., `mode='排位'`) when the DB uses an English ID or integer code?\n\n"
                    "**REQUIRED ACTION**:\n"
                    "- **Switch to 'PROBE' phase** immediately.\n"
                    "- Write a query to `SELECT count(*)` with fewer filters to see where data is lost.\n"
                    "- Verify the existence of your filter values (e.g., `SELECT distinct type ...`)."
                )
                print("[Env] 拦截空结果，发送详细排查指南。")
                history.append({"role": "user", "content": feedback})
                continue
            # ======================== 空结果强拦截结束 ========================

            # 3) 基于历史评测数据的 HistoryGuard 检查
            is_pass, guard_msg = history_guard.check(sql_id, result.rows)
            if not is_pass:
                print(f"[HistoryGuard] 拦截: {guard_msg}")

                # === [新增] Meta-Analyzer 介入逻辑 ===
                # 调用分析器生成破局建议
                analysis_advice = analyze_deadlock(client, model, provider, history, guard_msg)

                print(f"[Meta-Analyzer] 建议:\n{analysis_advice}")

                feedback = (
                    f"[History Feedback] {guard_msg}\n\n"
                    f"================ META-ANALYSIS & BREAKTHROUGH SUGGESTION ================\n"
                    f"{analysis_advice}\n"
                    f"=========================================================================\n"
                    "**INSTRUCTION**: \n"
                    "1. Read the analysis above carefully.\n"
                    "2. STOP guessing. Discard assumptions about empty columns (like `dteventtime`) unless you have a fresh PROBE proving it.\n"
                    "3. If suggested, PROBE both time columns (`dteventtime` AND `tdbank_imp_date`) to see which one aligns with the target data.\n"
                    "4. Switch to phase 'PROBE' to verify the new hypothesis."
                )
                # === [结束] ===

                history.append({"role": "user", "content": feedback})
                continue

            if guard_msg:
                # 即使通过，如果有 guard_msg (warnings)，也带上标记
                history.append({"role": "user", "content": f"[History Feedback] {guard_msg}"})

            # 尝试构建结果 Profile 并序列化给模型查看，防止非 JSON 可序列化类型导致崩溃
            try:
                profile = get_result_profile(result.rows)
                profile_text = json.dumps(profile, ensure_ascii=False, cls=EnhancedJSONEncoder, indent=2)
            except TypeError as e:
                error_msg = str(e)
                if "not JSON serializable" in error_msg:
                    feedback = (
                        f"[System Error] Your SOLVE SQL result contains columns that are NOT JSON serializable (e.g., timedelta). "
                        f"Python Error: {error_msg}.\n"
                        "This prevents me from verifying the result profile.\n"
                        "**REQUIRED ACTION**: Please modify your SQL (phase 'SOLVE') to CAST duration/time columns to Strings or Numbers explicitly."
                    )
                    print(f"[Env] SOLVE 阶段捕获序列化错误，要求重写: {error_msg}")
                    history.append({"role": "user", "content": feedback})
                    continue
                else:
                    # 其他严重错误：直接打印并终止本次任务，避免无限循环
                    print(f"[Env] 严重错误: {e}")
                    return

            review_prompt = (
                "[System Check]\n"
                "Your SOLVE SQL has been executed successfully. Here is the result profile:\n"
                f"{profile_text}\n\n"
                "Please REVIEW: Does this magnitude and distribution match the user's question? "
                "Did you forget any critical filters (e.g., time range, status, game mode)?\n\n"
                "If the SQL is correct, reply with a JSON object:\n"
                "  {\n"
                "    \"phase\": \"CONFIRM\",\n"
                "    \"thought\": \"why the result is reasonable\",\n"
                "    \"verified_logic\": \"final refined logic summary\"\n"
                "  }\n\n"
                "If the SQL is NOT correct, reply with a JSON object:\n"
                "  {\n"
                "    \"phase\": \"SOLVE\",\n"
                "    \"thought\": \"what was wrong and how you fix it\",\n"
                "    \"sql\": \"new SELECT ... SQL\",\n"
                "    \"verified_logic\": \"updated logic summary\"\n"
                "  }\n"
            )

            history.append({"role": "user", "content": review_prompt})
            continue

        if phase == "CONFIRM":
            if not last_final_sql:
                print("[Env] 收到 CONFIRM，但之前没有成功的 SOLVE SQL，忽略。")
                history.append(
                    {
                        "role": "user",
                        "content": "CONFIRM received but no previous successful SOLVE SQL. Please send a SOLVE action.",
                    }
                )
                continue

            verified_logic = str(action.get("verified_logic", "") or last_verified_logic)
            entry = {
                "sql_id": sql_id,
                "final_sql": last_final_sql,
                "verified_logic": verified_logic,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            append_verified_entry(entry)
            print(f"[Env] 已确认并写入 {VERIFIED_KB_PATH}。")
            print(json.dumps(entry, ensure_ascii=False, indent=2, cls=EnhancedJSONEncoder))
            return

        # 未知 phase
        history.append(
            {
                "role": "user",
                "content": "Your JSON must contain field 'phase' with value one of: PROBE, SOLVE, CONFIRM.",
            }
        )

    print("[Agent] 达到最大轮数，未完成确认。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Data Detective (数据侦探代理)")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS, help="最大对话轮数")
    args = parser.parse_args()

    completed = get_completed_sql_ids()
    if completed:
        print(f"[Agent] 已完成的任务: {sorted(completed)}")
        print(f"[Agent] 已完成任务检测文件: {VERIFIED_KB_PATH}")
    else:
        print(f"[Agent] 已完成任务检测文件: {VERIFIED_KB_PATH} (当前为空或不存在)")

    for sql_id in SQL_ID_LIST:
        if sql_id in completed:
            print(f"[Agent] 跳过已完成任务 {sql_id}")
            continue
        print(f"\n=========== 开始处理任务 {sql_id} ===========")
        run_agent_loop(sql_id, max_turns=args.max_turns)


if __name__ == "__main__":
    main()
