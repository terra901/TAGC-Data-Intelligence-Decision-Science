import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _strip_code_fence(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if t.startswith("```"):
        t = t.strip("`")
        t = t.replace("json", "", 1).strip()
    return t.strip()


def _extract_first_json_obj(s: str) -> str:
    t = _strip_code_fence(s)
    if not t:
        return ""
    l = t.find("{")
    r = t.rfind("}")
    if l >= 0 and r > l:
        return t[l : r + 1]
    return ""


def _safe_json_loads(s: str) -> Optional[Dict[str, Any]]:
    try:
        t = _extract_first_json_obj(s)
        if t:
            return json.loads(t)
        return json.loads(_strip_code_fence(s))
    except Exception:
        return None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="ignore"))


def _load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_sql_ids(ids_arg: List[str], ids_file: Optional[str]) -> List[str]:
    ids: List[str] = []
    for x in ids_arg or []:
        if x:
            ids.append(str(x).strip())

    if ids_file:
        p = Path(ids_file)
        obj = _load_json(p)
        if isinstance(obj, dict):
            # 兼容 eval_incorrect_ids.json / eval_correct_ids.json
            for k in ("incorrect_ids", "correct_ids", "sql_ids", "ids"):
                v = obj.get(k)
                if isinstance(v, list):
                    ids.extend([str(i).strip() for i in v if i])
                    break
        elif isinstance(obj, list):
            ids.extend([str(i).strip() for i in obj if i])

    # 去重并保持顺序
    seen = set()
    out: List[str] = []
    for sid in ids:
        if sid and sid not in seen:
            out.append(sid)
            seen.add(sid)
    return out


def _load_correct_sql_map(correct_sql_path: str) -> Dict[str, str]:
    p = Path(correct_sql_path)
    obj = _load_json(p)
    m: Dict[str, str] = {}
    if isinstance(obj, list):
        for item in obj:
            if not isinstance(item, dict):
                continue
            sid = item.get("sql_id")
            sql = item.get("sql")
            if sid and isinstance(sql, str) and sql.strip():
                m[str(sid)] = sql.strip()
    return m


def _load_verified_map(correct_verified_path: str) -> Dict[str, Dict[str, str]]:
    p = Path(correct_verified_path)
    obj = _load_json(p)
    m: Dict[str, Dict[str, str]] = {}
    if isinstance(obj, list):
        for item in obj:
            if not isinstance(item, dict):
                continue
            sid = item.get("sql_id")
            if not sid:
                continue
            final_sql = item.get("final_sql")
            verified_logic = item.get("verified_logic")
            rec: Dict[str, str] = {}
            if isinstance(final_sql, str) and final_sql.strip():
                rec["final_sql"] = final_sql.strip()
            if isinstance(verified_logic, str) and verified_logic.strip():
                rec["verified_logic"] = verified_logic.strip()
            if rec:
                m[str(sid)] = rec
    return m


def _load_knowledge_add_map(knowledge_add_path: str) -> Dict[str, str]:
    p = Path(knowledge_add_path)
    obj = _load_json(p)
    m: Dict[str, str] = {}
    if isinstance(obj, list):
        for item in obj:
            if not isinstance(item, dict):
                continue
            sid = item.get("sql_id")
            kn = item.get("knowledge")
            if sid and isinstance(kn, str) and kn.strip():
                m[str(sid)] = kn.strip()
    return m


def _compose_knowledge_text(
    common_knowledge2: str,
    task_knowledge: str,
    verified_logic: str,
    knowledge_add: str,
) -> str:
    parts: List[str] = []
    # 按用户要求顺序：common_knowledge2 -> final_dataset knowledge -> correct_verified -> knowledge_add
    if common_knowledge2:
        parts.append(f"[Common Knowledge v2]\n{common_knowledge2}".strip())
    if task_knowledge:
        parts.append(f"[Task Knowledge]\n{task_knowledge}".strip())
    if verified_logic:
        parts.append(f"[Correct Verified Knowledge]\n{verified_logic}".strip())
    if knowledge_add:
        parts.append(f"[Knowledge Add Clean]\n{knowledge_add}".strip())
    return "\n\n".join([p for p in parts if p]).strip()


def _build_fewshot_examples() -> str:
    return (
        "/* Example 1 */\n"
        "#question: Please give the name of the course in which most numbers of the students got an A.\n"
        "define: most number of students got an A refers MAX(COUNT(student_id WHERE grade = 'A'));\n"
        "SQL: SELECT T3.name FROM registration AS T1 INNER JOIN course AS T3 ON T1.course_id = T3.course_id WHERE T1.grade = 'A' GROUP BY T3.name ORDER BY COUNT(T1.student_id) DESC LIMIT 1\n"
        "#answer:\n"
        "#reason: The question asks for the \"course name\". The condition is \"most students got an A\". We need to filter by grade='A', group by course, and sort by count in descending order.\n"
        "#columns: course.name, registration.grade, registration.student_id\n"
        "#values: got an A refers to registration.grade = 'A'\n"
        "#SELECT: course.name\n"
        "#SQL-like: Show course.name WHERE registration.grade = 'A' GROUP BY course.name ORDER BY count(student_id) DESC LIMIT 1\n\n"
        "/* Example 2 */\n"
        "#question: How much more votes for episode 1 than for episode 5?\n"
        "define: more votes refers to SUBTRACT(SUM(votes when episode = 1), SUM(votes when episode = 5))\n"
        "SQL: SELECT SUM(CASE WHEN T1.episode = 1 THEN T2.votes ELSE 0 END) - SUM(CASE WHEN T1.episode = 5 THEN T2.votes ELSE 0 END) FROM Episode AS T1 INNER JOIN Vote AS T2 ON T2.episode_id = T1.episode_id;\n"
        "#answer:\n"
        "#reason: The question asks for a calculation (difference). The logic is defined in \"define\": sum votes for ep 1 minus sum votes for ep 5.\n"
        "#columns: Episode.episode, Vote.votes\n"
        "#values: episode 1 refers to Episode.episode = 1, episode 5 refers to Episode.episode = 5\n"
        "#SELECT: SUBTRACT(SUM(votes when episode = 1), SUM(votes when episode = 5))\n"
        "#SQL-like: SELECT SUM(votes) WHERE episode=1 MINUS SELECT SUM(votes) WHERE episode=5\n\n"
        "/* Example JSON output (format only) */\n"
        "{\n"
        "  \"plan\": \"Query Plan: ...\",\n"
        "  \"divide\": \"1. Divide and Conquer: ...\",\n"
        "  \"cot\": \"#answer:\\n#reason: ...\\n#columns: ...\\n#values: ...\\n#SELECT: ...\\n#SQL-like: ...\"\n"
        "}\n"

        "\n/* Example JSON output (toy, with real structure) */\n"
        "{\n"
        "  \"plan\": \"Query Plan: (1) Identify the target metric from the question and map define terms to SQL filters. (2) Select the correct base table(s) from schema. (3) Apply filters exactly as defined (time window, types, platform). (4) Aggregate/group to the requested granularity. (5) Produce final columns and ordering per the SQL.\",\n"
        "  \"divide\": \"1. Divide and Conquer: Main Question: Convert the define terms into concrete SQL conditions and explain how the SQL computes the requested output. Analysis: (A) Inputs and time window. (B) Filter mapping from define -> WHERE. (C) Aggregation logic (GROUP BY / COUNT / SUM). (D) Output columns and ordering. Sub-questions: What is the cohort? What are the exact filters? What is the grouping key? What is the final metric?\",\n"
        "  \"cot\": \"#answer:\\n#reason: Use define to translate business terms into SQL filters, then explain how the SELECT/WHERE/GROUP BY/ORDER BY produce the requested result.\\n#columns: table_a.col1, table_a.col2, table_b.col3\\n#values: term1 refers to table_a.colX = 'Y', term2 refers to dt BETWEEN 'YYYYMMDD' AND 'YYYYMMDD'\\n#SELECT: COUNT(DISTINCT ...), SUM(...), grouped columns\\n#SQL-like: SELECT metrics WHERE filters GROUP BY keys ORDER BY metrics DESC\"\n"
        "}\n"
    )


def _build_reasoning_messages(
    schema_block: str,
    knowledge_text: str,
    question: str,
    correct_sql: str,
) -> List[Dict[str, str]]:
    system = (
        "You are a Data Analyst Expert and StarRocks SQL expert. "
        "You will receive Schema, explicit Domain Knowledge (define), a Question, and the CORRECT final SQL (Gold SQL). "
        "Reverse-engineer the reasoning process from Question/Knowledge to the given SQL. "
        "You MUST output STRICT JSON with exactly keys: plan, divide, cot (all strings). "
        "No markdown fences, no extra keys, no extra text."
    )

    examples = _build_fewshot_examples()

    user = (
        "You are a Data Analyst Expert. I will provide you with a Question, explicit Domain Knowledge (define), and the Correct SQL (Gold SQL).\n\n"
        "**Your Task:**\n"
        "Reverse-engineer the reasoning process that leads from the Question/Knowledge to the SQL. You must explain the logic step-by-step.\n\n"
        "**CRITICAL RULES (MUST FOLLOW):**\n"
        "1. **NO LAZINESS:** You must be EXHAUSTIVE. Do NOT use phrases like 'similarly for others', 'repeat for the rest', 'etc.', or '...'.\n"
        "2. **FULL ENUMERATION:** If the define/knowledge lists multiple items (modes/IDs/date ranges/enums), you MUST list the logic for EVERY SINGLE ONE of them in the #values section.\n"
        "3. **EXPLICIT MAPPING:** Do not just say 'filter by X'. You must write the specific condition, e.g., \"Mode A refers to submodename = 'X'\".\n"
        "4. Do NOT change the given SQL. The reasoning must be consistent with the SQL.\n"
        "5. Output MUST be STRICT JSON with exactly keys: plan, divide, cot. Values must be strings.\n\n"
        "**Input Data:**\n"
        "/* Database Schema */\n"
        f"{schema_block}\n\n"
        "/* Few-shot Examples of the Output Format */\n"
        f"{examples}\n\n"
        "**Now, process this specific task:**\n\n"
        f"#question: {question}\n"
        f"define: {knowledge_text}\n"
        f"SQL: {correct_sql}\n\n"
        "**Instruction:**\n"
        "Based on the define (Knowledge) and the SQL provided above, generate the reasoning steps in the requested fields.\n"
        "Please respond STRICTLY as JSON and do not include any other text.\n\n"
        "[Output requirements]\n"
        "plan: must start with 'Query Plan:' and be step-by-step; emphasize filters, group-by granularity, join logic, time window; do NOT output full SQL.\n"
        "divide: must start with '1. Divide and Conquer:' and include Main Question / Analysis / Sub-questions structure; do NOT output full SQL.\n"
        "cot: must start with '#answer:' and MUST include '#reason:' '#columns:' '#values:' '#SELECT:' '#SQL-like:' sections.\n"
        "#columns: list all columns used in the SQL (format: table.column).\n"
        "#values: list filtering/mapping conditions derived from define/Knowledge; must be fully enumerated; no laziness.\n"
        "Return JSON only, e.g. {\"plan\":\"Query Plan: ...\",\"divide\":\"1. Divide and Conquer: ...\",\"cot\":\"#answer:...\"}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _validate_reasoning_obj(obj: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    if not obj or not isinstance(obj, dict):
        return False, "not a dict"
    for k in ("plan", "divide", "cot"):
        v = obj.get(k)
        if not isinstance(v, str) or not v.strip():
            return False, f"missing_or_empty:{k}"
    if not str(obj.get("plan") or "").lstrip().startswith("Query Plan:"):
        return False, "plan_prefix"
    if not str(obj.get("divide") or "").lstrip().startswith("1. Divide and Conquer:"):
        return False, "divide_prefix"
    if not str(obj.get("cot") or "").lstrip().startswith("#answer:"):
        return False, "cot_prefix"
    return True, "ok"


def _repair_to_json(t2_agent: Any, bad_text: str) -> Optional[Dict[str, Any]]:
    system = (
        "You are a strict formatter. Convert the input into STRICT JSON with exactly keys: plan, divide, cot. "
        "No code fences, no extra text. plan must start with 'Query Plan:'. divide must start with '1. Divide and Conquer:'. cot must start with '#answer:'."
    )
    user = (
        "Fix the following output into valid JSON with keys plan/divide/cot. "
        "If some part is missing, rewrite it based on the content so it satisfies the format rules.\n\n"
        "[BAD OUTPUT]\n---\n"
        f"{bad_text}\n"
    )
    resp = t2_agent.chat_complete([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])
    return _safe_json_loads(resp)


def _preview_text_head_tail(text: str, n: int) -> str:
    t = (text or "").strip()
    n = max(0, int(n or 0))
    if n <= 0:
        return ""
    if len(t) <= n * 2:
        return t
    head = t[:n]
    tail = t[-n:]
    return head + "\n\n...[SNIP]...\n\n" + tail


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql-id", dest="sql_ids", action="append", default=[])
    ap.add_argument("--ids-file", dest="ids_file", default=None)
    ap.add_argument(
        "--correct-sql-file",
        dest="correct_sql_file",
        default=str(Path(__file__).resolve().parents[2] / "data" / "correct_59.json"),
    )
    ap.add_argument(
        "--out",
        dest="out_path",
        default=str(Path(__file__).resolve().parents[1] / "datafile" / "output" / "generated_goldsql_entries.json"),
    )
    ap.add_argument(
        "--update-goldsql",
        dest="update_goldsql",
        action="store_true",
        default=False,
    )
    ap.add_argument(
        "--goldsql-path",
        dest="goldsql_path",
        default=str(Path(__file__).resolve().parents[2] / "data" / "goldsql.json"),
    )
    ap.add_argument("--max-tries", dest="max_tries", type=int, default=2)

    ap.add_argument(
        "--common-knowledge2-path",
        dest="common_knowledge2_path",
        default=str(Path(__file__).resolve().parents[2] / "data" / "common_knowledge2.md"),
    )
    ap.add_argument(
        "--correct-verified-path",
        dest="correct_verified_path",
        default=str(Path(__file__).resolve().parents[2] / "data_detective_knowledge" / "correct_verified_knowledge.json"),
    )
    ap.add_argument(
        "--knowledge-add-path",
        dest="knowledge_add_path",
        default=str(Path(__file__).resolve().parents[2] / "data" / "knowledge_add_clean_list.json"),
    )
    ap.add_argument("--preview", dest="preview", action="store_true", default=False)
    ap.add_argument("--preview-chars", dest="preview_chars", type=int, default=1200)
    ap.add_argument("--preview-only", dest="preview_only", action="store_true", default=False)
    ap.add_argument("--dump-messages", dest="dump_messages", action="store_true", default=False)
    ap.add_argument(
        "--dump-messages-chars",
        dest="dump_messages_chars",
        type=int,
        default=0,
        help="If >0, truncate dumped messages to this many chars (0 means no truncation)",
    )
    args = ap.parse_args()

    base_dir = Path(__file__).resolve().parents[2]
    agent_dir = base_dir / "run" / "agent"
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))

    import agent as t2_agent  # noqa: E402

    sql_ids = _load_sql_ids(args.sql_ids, args.ids_file)
    if not sql_ids:
        raise SystemExit("No sql_id provided. Use --sql-id or --ids-file")

    correct_sql_map = _load_correct_sql_map(args.correct_sql_file)

    common_knowledge2 = _load_text(Path(args.common_knowledge2_path))
    verified_map = _load_verified_map(args.correct_verified_path)
    knowledge_add_map = _load_knowledge_add_map(args.knowledge_add_path)

    tasks = t2_agent.load_final_tasks()
    task_map: Dict[str, Dict[str, Any]] = {}
    for t in tasks:
        sid = str(t.get("sql_id") or "").strip()
        if sid:
            task_map[sid] = t

    schema_map = t2_agent.load_schema_map()
    # 注意：这里不再使用 agent 内置的 common/added/verified 路径，避免与用户指定输入源冲突。

    out_entries: List[Dict[str, Any]] = []

    for sid in sql_ids:
        task = task_map.get(sid)
        if not task:
            print(f"[WARN] sql_id not found in final_dataset: {sid}")
            continue

        verified_rec = verified_map.get(sid) or {}
        verified_final_sql = (verified_rec.get("final_sql") or "").strip()
        correct_sql = (verified_final_sql or (correct_sql_map.get(sid) or "")).strip()
        if not correct_sql:
            print(f"[WARN] correct SQL not found for sql_id={sid} in {args.correct_sql_file}")
            continue

        question = (task.get("question") or "").strip()
        table_list = task.get("table_list") or []
        schema_tables = t2_agent.select_schema_tables(schema_map, table_list)
        schema_block = t2_agent.t2sql_prompts.format_schema_block(schema_tables)

        task_knowledge = (task.get("knowledge") or "").strip()
        verified_logic = (verified_rec.get("verified_logic") or "").strip()
        k_add = (knowledge_add_map.get(sid) or "").strip()
        knowledge_text = _compose_knowledge_text(common_knowledge2, task_knowledge, verified_logic, k_add)

        if args.preview:
            print(f"\n--- [PREVIEW] {sid} ---")
            print(f"[Q] {question}")
            print(f"[Tables] {table_list}")
            if verified_final_sql:
                print("[Correct SQL Source] correct_verified_knowledge.json.final_sql")
            else:
                print("[Correct SQL Source] correct_59.json")
            print("[Knowledge Preview]\n" + _preview_text_head_tail(knowledge_text, int(args.preview_chars or 0)))

        # 允许在 preview-only 模式下仅打印 prompt（不调用模型）
        if args.preview_only and not args.dump_messages:
            continue

        messages = _build_reasoning_messages(schema_block, knowledge_text, question, correct_sql)

        if args.dump_messages:
            dump_n = int(args.dump_messages_chars or 0)
            sys_msg = messages[0].get("content") if messages else ""
            user_msg = messages[1].get("content") if len(messages) > 1 else ""
            if dump_n > 0:
                sys_msg = (sys_msg or "")[:dump_n]
                user_msg = (user_msg or "")[:dump_n]
            print(f"\n--- [PROMPT SYSTEM] {sid} ---\n{sys_msg}")
            print(f"\n--- [PROMPT USER] {sid} ---\n{user_msg}")

        if args.preview_only:
            continue

        last_text = ""
        obj: Optional[Dict[str, Any]] = None
        for _ in range(max(1, int(args.max_tries or 1))):
            last_text = t2_agent.chat_complete(messages)
            obj = _safe_json_loads(last_text)
            ok, _reason = _validate_reasoning_obj(obj)
            if ok:
                break
            obj = _repair_to_json(t2_agent, last_text)
            ok2, _reason2 = _validate_reasoning_obj(obj)
            if ok2:
                break

        ok_final, why = _validate_reasoning_obj(obj)
        if not ok_final or not obj:
            print(f"[WARN] Failed to get valid JSON for sql_id={sid} (reason={why}). Will store raw in cot.")
            plan = ""
            divide = ""
            cot = (last_text or "").strip()
        else:
            plan = str(obj.get("plan") or "").strip()
            divide = str(obj.get("divide") or "").strip()
            cot = str(obj.get("cot") or "").strip()

        entry: Dict[str, Any] = {
            "sql_id": sid,
            "question": question,
            "sql": correct_sql,
            "复杂度": task.get("复杂度"),
            "table_list": table_list,
            "knowledge": task_knowledge,
            "golden_sql": True,
            "plan": plan,
            "divide": divide,
            "cot": cot,
        }
        out_entries.append(entry)

    if args.preview_only:
        print(f"[*] Preview-only done for {len(sql_ids)} sql_id(s). No LLM calls, no files written.")
        return

    out_path = Path(args.out_path)
    _write_json(out_path, out_entries)
    print(f"[*] Wrote {len(out_entries)} entries to: {out_path}")

    if args.update_goldsql:
        gpath = Path(args.goldsql_path)
        gold_obj = _load_json(gpath)
        if not isinstance(gold_obj, list):
            raise SystemExit(f"goldsql file is not a list: {gpath}")

        gold_map: Dict[str, Dict[str, Any]] = {}
        for item in gold_obj:
            if isinstance(item, dict) and item.get("sql_id"):
                gold_map[str(item.get("sql_id"))] = item

        for e in out_entries:
            sid = str(e.get("sql_id"))
            if sid in gold_map and isinstance(gold_map[sid], dict):
                gold_map[sid].update({
                    "question": e.get("question"),
                    "sql": e.get("sql"),
                    "plan": e.get("plan"),
                    "divide": e.get("divide"),
                    "cot": e.get("cot"),
                    "golden_sql": True,
                })
            else:
                gold_obj.append(e)

        _write_json(gpath, gold_obj)
        print(f"[*] Updated goldsql.json: {gpath}")


if __name__ == "__main__":
    main()
