import json
import os
import requests
import shutil
from pathlib import Path
from typing import Dict, Any, List
import sys
from openai import OpenAI

# 路径配置
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
T2SQL_DIR = BASE_DIR

FINAL_DATASET_PATH = DATA_DIR / "final_dataset.json"
SCHEMA_SHORT_PATH = DATA_DIR / "schema_all_gemini.json"
CORRECT_SQL_PATH = DATA_DIR / "correct_59.json"
BAD_SQL_JSONL_PATH = T2SQL_DIR / "run/datafile/output/results-finalize.jsonl"
ADDED_KNOWLEDGE_PATH = DATA_DIR / "knowledge_add_clean_list.json"
OUTPUT_PATH = DATA_DIR / "knowledge_ideal_generated.json"
# 与 agent 保持一致，使用 common_knowledge2.md 作为通用知识源
COMMON_KNOWLEDGE_PATH = DATA_DIR / "common_knowledge2.md"
EVAL_INCORRECT_PATH = T2SQL_DIR / "run/datafile/output/eval_incorrect_ids.json"
VERIFIED_KB_PATH = BASE_DIR / "data_detective_knowledge" / "correct_verified_knowledge.json"

# 使用的 Gemini 模型名称（可根据需要调整或改为从环境变量读取）
GEMINI_MODEL = "gemini-3-pro-preview"

# 优先从 run/agent/config.py 读取 GEMINI_API_KEY / GEMINI_MODEL，其次才回退到环境变量
_RUN_DIR = T2SQL_DIR / "run"
_AGENT_DIR = _RUN_DIR / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

try:
    from config import (
        GEMINI_API_KEY as CFG_GEMINI_API_KEY,
        GEMINI_MODEL as CFG_GEMINI_MODEL,
        GEMINI_API_KEY_LIST as CFG_GEMINI_API_KEY_LIST,
        GEMINI_BASE_URL as CFG_GEMINI_BASE_URL,
        RESULTS_PATH as CFG_RESULTS_PATH,
    )
except Exception:
    CFG_GEMINI_API_KEY = ""
    CFG_GEMINI_MODEL = None
    CFG_GEMINI_API_KEY_LIST = []
    CFG_GEMINI_BASE_URL = ""
    CFG_RESULTS_PATH = None

if CFG_GEMINI_MODEL:
    GEMINI_MODEL = CFG_GEMINI_MODEL

if CFG_RESULTS_PATH:
    BAD_SQL_JSONL_PATH = CFG_RESULTS_PATH

# 需要处理的 sql_id 列表
TARGET_SQL_IDS = [
    "sql_1", "sql_11", "sql_24", "sql_26",
     "sql_50" ,"sql_65", "sql_103",  "sql_113"
]


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_final_dataset() -> Dict[str, Dict[str, Any]]:
    data = load_json(FINAL_DATASET_PATH)
    return {item.get("sql_id"): item for item in data if item.get("sql_id")}


def load_schema_short() -> Dict[str, Dict[str, Any]]:
    arr = load_json(SCHEMA_SHORT_PATH)
    return {t.get("table_name"): t for t in arr if t.get("table_name")}


def load_correct_sql_map() -> Dict[str, str]:
    arr = load_json(CORRECT_SQL_PATH)
    m: Dict[str, str] = {}
    for item in arr:
        sid = item.get("sql_id")
        sql = item.get("sql")
        if sid and isinstance(sql, str) and sql.strip():
            m[sid] = sql.strip()
    return m


def load_bad_sql_map() -> Dict[str, List[str]]:
    """从 Agent / batch 结果中收集每个 sql_id 的所有错误 SQL 文本列表。

    - 对于 results-finalize.jsonl:
        * 收集 final_sql/sql
        * 收集每个 candidate.generated_sql
        * 收集每个 candidate.used_sql
      这样可以覆盖 4 个策略的原始 SQL + 修复后 SQL（如果有），统一视作 Bad Case。

    - 兼容旧的 batch 文件:
        * 每行一个 JSON, 带 custom_id 和嵌套 response.body.choices.message.content
    """
    m: Dict[str, List[str]] = {}
    if not BAD_SQL_JSONL_PATH.exists():
        return m

    # 使用额外的 seen 集合做去重，避免同一 sql_id 下出现重复 SQL
    seen: Dict[str, set] = {}

    def _add_sql(sid: Any, sql: Any):
        if not sid or not isinstance(sql, str):
            return
        s = sql.strip()
        if not s:
            return
        key = str(sid)
        if key not in seen:
            seen[key] = set()
        if s in seen[key]:
            return
        seen[key].add(s)
        m.setdefault(key, []).append(s)

    # 优先尝试整体解析为 JSON 数组 (finalize_results-3.py / agent.py 的输出)
    try:
        text = BAD_SQL_JSONL_PATH.read_text(encoding="utf-8", errors="ignore")
        stripped = text.lstrip()
        if stripped and stripped[0] in "[{":
            data = json.loads(text)
            if isinstance(data, list):
                for obj in data:
                    if not isinstance(obj, dict):
                        continue
                    sid = obj.get("sql_id") or obj.get("id")
                    if not sid:
                        continue

                    # 1) 最终 SQL
                    final_sql = obj.get("final_sql") or obj.get("sql")
                    _add_sql(sid, final_sql)

                    # 2) 所有 candidates 的 generated_sql / used_sql
                    cands = obj.get("candidates") or []
                    if isinstance(cands, list):
                        for cand in cands:
                            if not isinstance(cand, dict):
                                continue
                            _add_sql(sid, cand.get("generated_sql"))
                            _add_sql(sid, cand.get("used_sql"))

                if m:
                    return m
    except Exception:
        pass

    # 兜底: 按行解析 JSONL, 兼容简单 agent 结构与旧的 batch 结构
    with open(BAD_SQL_JSONL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            if not isinstance(obj, dict):
                continue

            # 1) 兼容 agent 输出: {sql_id, final_sql/sql, candidates:[...]}
            sid = obj.get("sql_id") or obj.get("id")
            if sid:
                final_sql = obj.get("final_sql") or obj.get("sql")
                _add_sql(sid, final_sql)

                cands = obj.get("candidates") or []
                if isinstance(cands, list):
                    for cand in cands:
                        if not isinstance(cand, dict):
                            continue
                        _add_sql(sid, cand.get("generated_sql"))
                        _add_sql(sid, cand.get("used_sql"))
                continue

            # 2) 兼容旧 batch 输出: {custom_id, response.body.choices[0].message.content}
            cid = obj.get("custom_id") or ""
            if "-gen-" not in cid:
                continue
            sql_id = cid.split("-gen-", 1)[0]
            text_val = None
            try:
                body = obj.get("response", {}).get("body", {})
                choices = body.get("choices", [])
                if choices:
                    text_val = choices[0].get("message", {}).get("content")
            except Exception:
                text_val = None
            _add_sql(sql_id, text_val)
    return m


def load_old_knowledge_map() -> Dict[str, str]:
    arr = load_json(ADDED_KNOWLEDGE_PATH)
    m: Dict[str, str] = {}
    for item in arr:
        sid = item.get("sql_id")
        kn = item.get("knowledge")
        if sid and isinstance(kn, str) and kn.strip():
            m[sid] = kn.strip()
    return m


def load_verified_kb_map() -> Dict[str, str]:
    """加载 data_detective_knowledge/verified_knowledge_base.json 中按 sql_id 聚合的已验证知识描述。"""
    if not VERIFIED_KB_PATH.exists():
        return {}
    try:
        data = load_json(VERIFIED_KB_PATH)
        m: Dict[str, str] = {}
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                sid = item.get("sql_id")
                vlogic = item.get("verified_logic")
                if sid and isinstance(vlogic, str) and vlogic.strip():
                    m[sid] = vlogic.strip()
        if m:
            print(f"[*] 已加载已验证知识 {len(m)} 条 (from {VERIFIED_KB_PATH})")
        return m
    except Exception as e:
        print(f"[!] 读取已验证知识失败: {e}")
        return {}


def load_common_knowledge() -> str:
    """加载通用知识，如果没有则返回空字符串。

    这里按纯文本读取 common_knowledge2.md，与 agent 使用方式保持一致，
    避免误把 Markdown 当作 JSON 解析失败。
    """
    if not COMMON_KNOWLEDGE_PATH.exists():
        return ""
    try:
        return COMMON_KNOWLEDGE_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


def load_incorrect_sql_ids() -> List[str]:
    if not EVAL_INCORRECT_PATH.exists():
        print(f"[!] 未找到错误题目列表文件: {EVAL_INCORRECT_PATH}")
        return []
    try:
        with open(EVAL_INCORRECT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[!] 读取错误题目列表失败: {e}")
        return []

    if isinstance(data, list):
        return [str(x) for x in data if x]
    if isinstance(data, dict):
        ids = data.get("incorrect_ids") or []
        return [str(x) for x in ids if x]
    return []


def _load_existing_knowledge_entries() -> List[Dict[str, Any]]:
    if not ADDED_KNOWLEDGE_PATH.exists():
        return []
    try:
        arr = load_json(ADDED_KNOWLEDGE_PATH)
        if isinstance(arr, list):
            return arr
    except Exception:
        pass
    return []


def update_knowledge_base(new_knowledge_map: Dict[str, str]):
    existing_list = _load_existing_knowledge_entries()
    merged: Dict[str, Dict[str, Any]] = {}

    for item in existing_list:
        if not isinstance(item, dict):
            continue
        sid = item.get("sql_id")
        if not sid:
            continue
        merged[sid] = item

    for sid, text in new_knowledge_map.items():
        if not sid or not isinstance(text, str):
            continue
        trimmed = text.strip()
        if not trimmed:
            continue
        merged[sid] = {"sql_id": sid, "knowledge": trimmed}

    merged_list = sorted(merged.values(), key=lambda x: x.get("sql_id", "")) if merged else []

    if ADDED_KNOWLEDGE_PATH.exists():
        try:
            backup_path = ADDED_KNOWLEDGE_PATH.with_suffix(ADDED_KNOWLEDGE_PATH.suffix + ".bak")
            shutil.copy2(ADDED_KNOWLEDGE_PATH, backup_path)
            print(f"[*] 已备份旧知识文件到: {backup_path}")
        except Exception as e:
            print(f"[!] 备份旧知识文件失败: {e}")

    with open(ADDED_KNOWLEDGE_PATH, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)
    print(f"[*] 已将新知识合并写回: {ADDED_KNOWLEDGE_PATH}")


def build_schema_snippet(table_list: List[str], schema_map: Dict[str, Dict[str, Any]]) -> str:
    """根据 table_list 从 schema_all_gemini.json 中提取精简版表结构描述。

    - 表级描述优先使用 table_description_llm_long，其次 table_description_llm_short，最后 table_description。
    - 列级描述优先使用 llm_long_description，其次 llm_short_description，最后原始 description。
    - 只输出 table_name, table_description, columns[col, type, description]，避免将无关字段塞给 LLM。
    """

    tables: List[Dict[str, Any]] = []
    for name in table_list or []:
        t = schema_map.get(name)
        if not isinstance(t, dict):
            continue

        # 表级描述
        table_desc = (
            t.get("table_description_llm_long")
            or t.get("table_description_llm_short")
            or t.get("table_description")
            or ""
        )

        # 列级描述
        cols: List[Dict[str, Any]] = []
        for col in t.get("columns", []):
            if not isinstance(col, dict):
                continue
            col_name = col.get("col") or col.get("name") or col.get("column_name")
            if not col_name:
                continue
            desc = (
                col.get("llm_long_description")
                or col.get("llm_short_description")
                or col.get("description")
                or ""
            )
            cols.append({
                "col": col_name,
                "type": col.get("type"),
                "description": desc,
            })

        tables.append({
            "table_name": t.get("table_name", name),
            "table_description": table_desc,
            "columns": cols,
        })

    if not tables:
        return ""
    return json.dumps(tables, ensure_ascii=False, indent=2)


SYSTEM_PROMPT = """### 角色
你是一名资深的 AI 知识库工程师（Knowledge Engineer）。

### 核心任务
你的任务是基于用户提供的“原材料”（SQL方言、正误SQL、参考知识、旧知识等），通过对比和归因分析，**修正并优化“旧知识”**。
最终，你将生成一段单一的、高度凝练的“黄金知识规则”（Ideal Knowledge Text），用于指导 AI 未来正确执行此类任务。

### 输入定义 (Input Specification)
在 User Prompt 中，你将收到以下信息：
1.  **SQL 方言**：StarRocks。
2.  **表结构 (Schema)**：帮助理解字段。
3.  **查询问题**：自然语言问题。
4.  **正确 SQL (Golden SQL)**：必须遵循的“黄金标准”。
5.  **错误 SQL (Bad Case)**：AI 曾犯过的错误。
6.  **参考知识 (Reference Knowledge)**：[只读] 包含题目自带提示(Evidence)、通用常识(Common)和已验证的业务逻辑(Verified)。**这些是已知事实，必须遵守，无需修正。**
7.  **旧知识 (Old Knowledge)**：[待修正] 导致 AI 犯错的原始知识，是你的**主要修正对象**。

### 目标输出 (Ideal Output)
1.  **唯一产物**：输出**必须**是那段最终的“黄金知识规则”文本。
2.  **内容要求**：该文本必须融合参考知识中的关键点，并修正旧知识中的错误。封装所有业务逻辑、字段映射、特定函数和语法规避。
3.  **格式**：一段凝练的、指令清晰的描述性文本。

### 严格约束
- **禁止**输出分析过程、SQL 模板或解释。
- **你只能返回那段最终优化后的“黄金知识文本”。**
"""


def build_user_prompt(
    schema_text: str,
    question: str,
    right_sql: str,
    bad_sql_list: List[str],
    old_knowledge: str,
    evidence: str,
    common_kn: str,
    verified_kn: str,
) -> str:

    schema_block = schema_text or "(无)"

    # 构建错误 SQL 块
    if bad_sql_list:
        labeled_bad_sql = []
        for idx, sql in enumerate(bad_sql_list, start=1):
            labeled_bad_sql.append(f"错误SQL{idx}:\n{sql}")
        bad_sql_block = "\n\n".join(labeled_bad_sql)
    else:
        bad_sql_block = "(无错误SQL，仅供补充)"

    # --- 构建 Reference Knowledge 块 (只读/事实) ---
    ref_parts: List[str] = []
    if common_kn:
        ref_parts.append(f"[Common Knowledge / 通用常识]\n{common_kn}")
    if evidence:
        ref_parts.append(f"[Dataset Evidence / 题目提示]\n{evidence}")
    if verified_kn:
        ref_parts.append(f"[Verified Logic / 已验证逻辑]\n{verified_kn}")

    reference_block = "\n\n".join(ref_parts) if ref_parts else "(无参考知识)"

    # --- 待修正旧知识 ---
    old_kn_block = old_knowledge or "(无旧知识，请根据上下文自行抽象规则)"

    return f"""### 1. 数据库方言 (SQL Dialect)
StarRocks

### 2. 相关表结构 (Schema)
{schema_block}

### 3. 查询问题
{question}

### 4. 正确 SQL (Golden SQL)
{right_sql}

### 5. 错误 SQL (Bad Case)
{bad_sql_block}

### 6. 参考知识 (Reference Knowledge)
**注意：本部分为已知事实，请作为推理依据，不可违背。**
{reference_block}

### 7. 待修正旧知识 (Old Knowledge)
**注意：本部分可能存在缺陷或不完整，请基于以上信息对其进行修正和补充。**
{old_kn_block}
"""


def get_client() -> List[str]:
    """获取 Gemini API Key 列表。

    优先从 config.GEMINI_API_KEY_LIST 读取，其次回退到单个 GEMINI_API_KEY / 环境变量
    GEMINI_API_KEY / GOOGLE_API_KEY。
    """
    keys: List[str] = []
    if CFG_GEMINI_API_KEY_LIST:
        keys = [k for k in CFG_GEMINI_API_KEY_LIST if k]
    if not keys and CFG_GEMINI_API_KEY:
        keys = [CFG_GEMINI_API_KEY]
    if not keys:
        env_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if env_key:
            keys = [env_key]
    if not keys:
        raise RuntimeError("GEMINI_API_KEY 未在 config.py 或环境变量中设置！")
    return keys


def call_llm(client_keys: List[str], user_prompt: str, model: str) -> str:
    """调用 Gemini 模型，返回纯文本结果。

    直接通过 HTTP 调用自定义的 Gemini 兼容接口：
    https://gemini-api.poenl.top/v1beta/models/{model}:generateContent
    """
    combined = f"{SYSTEM_PROMPT}\n\n{user_prompt}"
    print(combined)

    last_error = None
    base_url = CFG_GEMINI_BASE_URL or os.environ.get("GEMINI_BASE_URL") or "https://api.gemai.cc/v1"

    for idx, key in enumerate(client_keys):
        print(f"[*] 使用 Gemini Key #{idx + 1}/{len(client_keys)} 调用接口...")
        try:
            client = OpenAI(api_key=key, base_url=base_url)
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=4096,
                reasoning_effort="high",
            )
        except Exception as e:
            print(f"[!] 调用 Gemini 接口失败 (Key #{idx + 1}): {e}")
            last_error = e
            continue

        text = ""
        try:
            choice = resp.choices[0]
            msg = getattr(choice, "message", choice)
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                texts: List[str] = []
                for part in content:
                    t = getattr(part, "text", None)
                    if isinstance(t, str):
                        texts.append(t)
                text = "".join(texts)
        except Exception:
            text = ""
        return (text or "").strip()

    if last_error is not None:
        print(f"[!] 所有 Gemini Key 均调用失败，最后一次错误: {last_error}")
    else:
        print("[!] 所有 Gemini Key 均调用失败")
    return ""


def main():
    final_dataset = load_final_dataset()
    schema_map = load_schema_short()
    correct_sql_map = load_correct_sql_map()
    bad_sql_map = load_bad_sql_map()
    old_kn_map = load_old_knowledge_map()
    verified_kb_map = load_verified_kb_map()
    common_kn_text = load_common_knowledge()

    client_keys = get_client()

    results: Dict[str, str] = {}

    target_sql_ids = load_incorrect_sql_ids()
    if not target_sql_ids:
        print("[*] 未找到错误 sql_id，程序结束。")
        return

    for sid in target_sql_ids:
        task = final_dataset.get(sid)
        if not task:
            print(f"[!] 跳过 {sid}: 在 final_dataset.json 中未找到")
            continue

        question = task.get("question", "").strip()
        evidence = task.get("evidence", "").strip()
        table_list = task.get("table_list", [])
        schema_text = build_schema_snippet(table_list, schema_map)

        right_sql = correct_sql_map.get(sid, "").strip()
        if not right_sql:
            # 这里使用 CORRECT_SQL_PATH.name 动态展示当前使用的正确答案文件名，避免误导
            print(f"[!] 跳过 {sid}: 在 {CORRECT_SQL_PATH.name} 中未找到正确 SQL")
            continue

        bad_sql_list = bad_sql_map.get(sid, [])
        old_kn = (old_kn_map.get(sid, "") or "").strip()
        vlogic = (verified_kb_map.get(sid, "") or "").strip()
        user_prompt = build_user_prompt(
            schema_text=schema_text,
            question=question,
            right_sql=right_sql,
            bad_sql_list=bad_sql_list,
            old_knowledge=old_kn,
            evidence=evidence,
            common_kn=common_kn_text,
            verified_kn=vlogic,
        )

        print(f"[*] 调用 Gemini 生成 {sid} 的黄金知识文本...")
        ideal_text = call_llm(client_keys, user_prompt, GEMINI_MODEL)
        results[sid] = ideal_text

    # 写出结果
    out_obj = [
        {"sql_id": sid, "knowledge": txt}
        for sid, txt in results.items()
    ]
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out_obj, f, ensure_ascii=False, indent=2)

    print(f"[*] 已为 {len(results)} 个 sql_id 生成黄金知识规则，写入 {OUTPUT_PATH}")

    update_knowledge_base(results)
    print(f"[*] 已将新知识合并到 {ADDED_KNOWLEDGE_PATH}")


if __name__ == "__main__":
    main()
