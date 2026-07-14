import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mysql.connector
from tqdm import tqdm
from openai import OpenAI

from config import (
    BASE_DIR,
    GEMINI_BASE_URL,
    GEMINI_MODEL,
    GEMINI_API_KEY_LIST,
    GEMINI_API_KEY,
    DB_HOST,
    DB_PORT,
    DB_USER,
    DB_PASSWORD,
    DB_DATABASE,
    ADDED_KNOWLEDGE_LIST_PATH,
)

DATA_DIR: Path = BASE_DIR / "data"
PROFILING_DIR: Path = DATA_DIR / "profiling_output_merged"
SCHEMA_FILE: Path = DATA_DIR / "schema.json"
TASK_FILE: Path = DATA_DIR / "final_dataset.json"
COMMON_KNOWLEDGE_FILE: Path = DATA_DIR / "common_knowledge.md"
KNOWLEDGE_ADD_FILE: Path = ADDED_KNOWLEDGE_LIST_PATH


GEMINI_KEYS: List[str] = [k for k in GEMINI_API_KEY_LIST if k] or (
    [GEMINI_API_KEY] if GEMINI_API_KEY else []
)
CURRENT_GEMINI_KEY_INDEX: int = 0


DB_CONFIG: Dict[str, Any] = {
    "host": DB_HOST,
    "port": DB_PORT,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "database": DB_DATABASE,
}


def _extract_message_text(resp: Any) -> str:
    try:
        choice = resp.choices[0]
    except Exception:
        return ""
    msg = getattr(choice, "message", None)
    if msg is not None:
        txt = getattr(msg, "content", None)
        if isinstance(txt, str):
            return txt
        if isinstance(txt, list):
            parts: List[str] = []
            for p in txt:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    parts.append(p["text"])
                else:
                    t = getattr(p, "text", None)
                    if isinstance(t, str):
                        parts.append(t)
            return "".join(parts).strip()
        return txt or ""
    txt = getattr(choice, "text", None)
    return txt or ""


def _chat_complete_gemini(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    max_attempts: int = 6,
    json_mode: bool = True,
) -> str:
    if not GEMINI_KEYS:
        raise RuntimeError("GEMINI_API_KEY_LIST / GEMINI_API_KEY 未配置")

    global CURRENT_GEMINI_KEY_INDEX
    use_model = model or GEMINI_MODEL
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        key_index = CURRENT_GEMINI_KEY_INDEX % len(GEMINI_KEYS)
        key = GEMINI_KEYS[key_index]
        print(
            f"    ... 调用 Gemini: model={use_model}, key #{key_index + 1}, "
            f"尝试 {attempt}/{max_attempts}"
        )
        start = time.time()
        try:
            client = OpenAI(api_key=key, base_url=GEMINI_BASE_URL)
            kwargs: Dict[str, Any] = {
                "model": use_model,
                "messages": messages,
                "temperature": 1,

            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
            text = _extract_message_text(resp) or ""
            cost = time.time() - start
            print(f"    ... 调用成功，耗时 {cost:.2f}s")
            if not text.strip():
                raise RuntimeError("模型返回空文本")
            return text
        except Exception as e:  # noqa: BLE001
            last_error = e
            msg = str(e)
            print(f"    !!! Gemini 调用出错: {e}")
            if ("insufficient_user_quota" in msg) or ("额度不足" in msg):
                if len(GEMINI_KEYS) > 1:
                    CURRENT_GEMINI_KEY_INDEX = (key_index + 1) % len(GEMINI_KEYS)
                    print("    !!! 当前 Key 配额不足，切换到下一个 Key 继续重试...")
                else:
                    print("    !!! 仅配置了 1 个 Gemini Key，无法切换其他 Key")
            time.sleep(1.0)

    raise RuntimeError(f"Gemini 多次重试仍失败: {last_error}")


def _call_llm_json(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    raw = _chat_complete_gemini(messages, json_mode=True)
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"解析 LLM JSON 失败: {e}; 原始输出前 200 字符: {text[:200]}")


def load_sme_metadata(schema_file: Path) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    try:
        with schema_file.open("r", encoding="utf-8") as f:
            schema = json.load(f)
        sme_meta: Dict[str, str] = {}
        table_neighbors: Dict[str, List[str]] = {}
        for table in schema:
            t_name = table.get("table_name")
            if not t_name:
                continue
            neighbors: List[str] = []
            for col in table.get("columns", []):
                c_name = col.get("col")
                if not c_name:
                    continue
                full_name = f"{t_name}.{c_name}"
                sme_meta[full_name] = col.get("description", "")
                neighbors.append(c_name)
            table_neighbors[t_name] = neighbors
        return sme_meta, table_neighbors
    except Exception as e:  # noqa: BLE001
        print(f"!! 无法加载或解析 schema 文件 {schema_file}: {e}")
        return {}, {}


def load_table_descriptions(schema_file: Path) -> Dict[str, str]:
    """从 schema.json 中加载表级 SME 描述 (table_description)。"""

    try:
        with schema_file.open("r", encoding="utf-8") as f:
            schema = json.load(f)
        table_desc_map: Dict[str, str] = {}
        for table in schema:
            t_name = table.get("table_name")
            if not t_name:
                continue
            t_desc = (table.get("table_description") or "").strip()
            if t_desc:
                table_desc_map[t_name] = t_desc
        print(f"已从 schema 加载 {len(table_desc_map)} 个表的 SME 描述。")
        return table_desc_map
    except Exception as e:  # noqa: BLE001
        print(f"!! 无法加载或解析表级 schema 描述 {schema_file}: {e}")
        return {}


def load_common_knowledge(filename: Path) -> str:
    try:
        with filename.open("r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"!! 警告：未找到通用知识文件 {filename}")
        return "N/A"
    except Exception as e:  # noqa: BLE001
        print(f"!! 加载通用知识失败 {filename}: {e}")
        return "N/A"


def load_advanced_knowledge(task_file: Path, add_knowledge_file: Path) -> Dict[str, str]:
    """高级知识加载器。

    1. 从 final_dataset.json 加载 (sql_id -> table_list) 和原始 knowledge（题目里的业务背景）。
    2. 从 knowledge_add_clean_list.json 加载 (sql_id -> 清洗后的重要规则)。
    3. 将上述所有 knowledge 聚合到「表」维度，供字段级 / 表级 Prompt 使用。
    """

    print("--- 开始加载并聚合知识库 ---")

    # 1) 加载原始任务文件（包含 table_list 与原始 knowledge）
    try:
        with task_file.open("r", encoding="utf-8") as f:
            raw_tasks = json.load(f)
        if not isinstance(raw_tasks, list):
            raw_tasks = [raw_tasks]
    except Exception as e:  # noqa: BLE001
        print(f"!! 加载 {task_file} 失败: {e}")
        return {}

    table_knowledge_map: defaultdict[str, List[str]] = defaultdict(list)
    sql_id_to_tables: Dict[str, List[str]] = {}

    for task in raw_tasks:
        sid = task.get("sql_id")
        tables = task.get("table_list", []) or []
        raw_k = (task.get("knowledge") or "").strip()

        # 记录 sql_id -> tables 的映射，供增强知识库使用
        if sid:
            sql_id_to_tables[sid] = tables

        # 将原始 knowledge 作为「场景示例」挂载到对应表
        if raw_k and tables:
            for tbl in tables:
                k_entry = f"【场景示例 ({sid})】: {raw_k}"
                if k_entry not in table_knowledge_map[tbl]:
                    table_knowledge_map[tbl].append(k_entry)

    # 2) 加载增强知识库（清洗后的重要规则），如果存在
    if add_knowledge_file.exists():
        try:
            with add_knowledge_file.open("r", encoding="utf-8") as f:
                add_tasks = json.load(f)
            if not isinstance(add_tasks, list):
                add_tasks = [add_tasks]

            print(f"检测到增强知识库 {add_knowledge_file}，包含 {len(add_tasks)} 条规则。")

            for item in add_tasks:
                sid = item.get("sql_id")
                clean_k = (item.get("knowledge") or "").strip()

                if not sid or not clean_k:
                    continue

                # 找到该 sql_id 对应的表
                target_tables = sql_id_to_tables.get(sid, [])
                if not target_tables:
                    print(
                        f"  [Warn] 增强知识库中有 sql_id={sid}，但在原数据集中找不到对应的 table_list，跳过。",
                    )
                    continue

                # 将清洗后的重要规则挂到对应表（前插，权重更高）
                for tbl in target_tables:
                    k_entry = f"⚠️【重要规则 ({sid})】: {clean_k}"
                    if k_entry not in table_knowledge_map[tbl]:
                        table_knowledge_map[tbl].insert(0, k_entry)
        except Exception as e:  # noqa: BLE001
            print(f"!! 加载增强知识库失败: {e}")
    else:
        print(f"提示：未找到增强知识库 {add_knowledge_file}，仅使用原数据。")

    # 3) 将列表格式化为字符串，按表输出
    final_map: Dict[str, str] = {}
    for tbl, k_list in table_knowledge_map.items():
        final_map[tbl] = "\n\n".join(k_list)

    print(f"知识聚合完成：共覆盖 {len(final_map)} 张表。")
    return final_map


def is_time_column(col_name: str, col_type: Any) -> bool:
    """根据字段名和类型做一个粗略判断：是否为时间/日期字段。"""

    name_lower = (col_name or "").lower()
    if any(x in name_lower for x in ["date", "time", "day", "dt", "ds", "ymd"]):
        return True

    type_lower = str(col_type or "").lower()
    if ("date" in type_lower) or ("time" in type_lower):
        return True

    return False


def get_deep_stats(conn: mysql.connector.MySQLConnection, table_name: str, col_profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从数据库中为单个字段获取更深入的统计信息。

    - 时间字段：MIN/MAX + COUNT 非空 + COUNT DISTINCT
    - 低基数字段：枚举全集（最多 50 个值）
    """

    col_name = col_profile.get("column_name")
    if not col_name:
        return None

    col_type = col_profile.get("type", "")

    # 1) 时间字段增强分析
    if is_time_column(col_name, col_type):
        try:
            with conn.cursor() as cursor:
                query = f"""
                    SELECT
                        MIN(`{col_name}`),
                        MAX(`{col_name}`),
                        COUNT(`{col_name}`),
                        COUNT(DISTINCT `{col_name}`)
                    FROM `{table_name}`
                """
                cursor.execute(query)
                row = cursor.fetchone()
                if not row:
                    return None
                min_val, max_val, total_count, distinct_count = row
                return {
                    "is_time": True,
                    "min": str(min_val),
                    "max": str(max_val),
                    "total_present": int(total_count or 0),
                    "distinct_count": int(distinct_count or 0),
                }
        except Exception as e:  # noqa: BLE001
            print(f"时间统计失败 {table_name}.{col_name}: {e}")
            return None

    # 2) 枚举字段全集（小基数且不是时间字段）
    cardinality = col_profile.get("cardinality", 1000)
    if cardinality is not None and cardinality < 50 and not is_time_column(col_name, col_type):
        try:
            with conn.cursor() as cursor:
                query = f"SELECT DISTINCT `{col_name}` FROM `{table_name}` LIMIT 50"
                cursor.execute(query)
                values = [str(row[0]) for row in cursor.fetchall()]
                return {
                    "is_enum": True,
                    "all_values": values,
                }
        except Exception as e:  # noqa: BLE001
            print(f"枚举获取失败 {table_name}.{col_name}: {e}")
            return None

    return None


def verify_table_continuity(
    conn: mysql.connector.MySQLConnection,
    table_name: str,
    date_col: str,
    id_col: str,
    check_days: int = 14,
) -> Optional[Dict[str, Any]]:
    """验证一张表更像全量快照(_df)还是增量日志(_di)。

    逻辑：
    - 取最近若干分区日期（按 date_col 降序），得到 date_count
    - 在最新分区中抽样若干 ID
    - 统计这些 ID 在最近 date_count 天内出现的 distinct 天数
    - 覆盖率 = freq / date_count，取样本平均覆盖率
    """

    cursor = conn.cursor()
    try:
        # 1) 获取最近 check_days 个分区日期
        query_dates = (
            f"SELECT DISTINCT `{date_col}` FROM `{table_name}` "
            f"ORDER BY `{date_col}` DESC LIMIT {int(check_days)}"
        )
        cursor.execute(query_dates)
        recent_dates = [row[0] for row in cursor.fetchall()]
        if len(recent_dates) < 3:
            return {
                "type": "UNKNOWN",
                "reason": "数据分区太少，无法判断",
            }

        latest_date = recent_dates[0]
        earliest_date = recent_dates[-1]
        date_count = len(recent_dates)

        # 2) 抽样：从最新分区中随机取若干 ID（这里用 LIMIT 10 简单抽样）
        query_sample = (
            f"SELECT `{id_col}` FROM `{table_name}` "
            f"WHERE `{date_col}` = '{latest_date}' LIMIT 10"
        )
        cursor.execute(query_sample)
        sample_ids = [str(row[0]) for row in cursor.fetchall()]
        if not sample_ids:
            return {
                "type": "UNKNOWN",
                "reason": "最新分区无数据",
            }

        # 3) 统计这些 ID 在最近 date_count 天内出现的天数
        ids_formatted = ",".join([f"'{sid}'" for sid in sample_ids])
        check_query = f"""
            SELECT `{id_col}`, COUNT(DISTINCT `{date_col}`) AS freq
            FROM `{table_name}`
            WHERE `{id_col}` IN ({ids_formatted})
              AND `{date_col}` >= '{earliest_date}'
            GROUP BY `{id_col}`
        """
        cursor.execute(check_query)
        results = cursor.fetchall()

        total_coverage = 0.0
        valid_samples = 0
        for _id_val, freq in results:
            try:
                freq_int = int(freq or 0)
            except Exception:  # noqa: BLE001
                continue
            if date_count <= 0:
                continue
            coverage = freq_int / float(date_count)
            total_coverage += coverage
            valid_samples += 1

        avg_coverage = total_coverage / valid_samples if valid_samples > 0 else 0.0

        inferred_type = "UNKNOWN"
        confidence = "low"
        if avg_coverage > 0.9:
            inferred_type = "SNAPSHOT_FULL (_df)"
            confidence = "high"
        elif avg_coverage > 0.7:
            inferred_type = "SNAPSHOT_LIKELY (_df)"
            confidence = "medium"
        else:
            inferred_type = "INCREMENT_LOG (_di)"
            confidence = "high"

        return {
            "type": inferred_type,
            "avg_coverage": f"{avg_coverage:.1%}",
            "sample_size": valid_samples,
            "check_period_days": date_count,
            "id_col_used": id_col,
            "date_col_used": date_col,
            "confidence": confidence,
        }
    except Exception as e:  # noqa: BLE001
        print(f"验证失败 {table_name}: {e}")
        return None
    finally:
        try:
            cursor.close()
        except Exception:  # noqa: BLE001
            pass


def _infer_date_and_id_columns(table_profile: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """基于字段名和 deep_stats 粗略推断 date_col 和 id_col。"""

    cols = table_profile.get("columns_profile", []) or []
    date_col: Optional[str] = None
    id_col: Optional[str] = None

    # 1) 推断日期列：优先 deep_stats.is_time，其次名字启发
    candidate_dates: List[str] = []
    for col in cols:
        name = col.get("column_name") or ""
        ds = col.get("deep_stats") or {}
        if ds.get("is_time") or is_time_column(name, col.get("type")):
            candidate_dates.append(name)

    if candidate_dates:
        # 按名字启发式排序
        def _date_priority(n: str) -> int:
            n = n.lower()
            if "dtstatdate" in n:
                return 0
            if n in ("dt", "ds"):
                return 1
            if "dt" in n or "ds" in n:
                return 2
            if "date" in n or "day" in n:
                return 3
            if "time" in n:
                return 4
            return 5

        candidate_dates.sort(key=_date_priority)
        date_col = candidate_dates[0]

    # 2) 推断 ID 列：基于字段名
    for col in cols:
        name = (col.get("column_name") or "").lower()
        if any(k in name for k in ["roleid", "role_id", "userid", "user_id", "playerid", "player_id", "uid"]):
            id_col = col.get("column_name")
            break
    if id_col is None:
        for col in cols:
            name = (col.get("column_name") or "").lower()
            if name.endswith("id") or name == "id":
                id_col = col.get("column_name")
                break

    return date_col, id_col


def enrich_profiles_with_db() -> None:
    """连接数据库，为 profiling_output_merged 中的每列补充 deep_stats 信息。

    - 该步骤只会处理尚未包含 deep_stats 的字段，可多次安全运行。
    - 如果数据库连接失败，将直接返回，不影响后续 LLM 摘要逻辑。
    """

    print("=== 第 0 步：从数据库增强字段统计（deep_stats） ===")

    if not PROFILING_DIR.exists():
        print(f"!! 错误：剖析目录不存在: {PROFILING_DIR}")
        return

    json_files = sorted(PROFILING_DIR.glob("*.json"))
    if not json_files:
        print(f"!! 提示：在 '{PROFILING_DIR}' 中未找到任何剖析文件，跳过 deep_stats 增强。")
        return

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
    except Exception as e:  # noqa: BLE001
        print(f"!! 无法连接数据库，跳过 deep_stats 增强: {e}")
        return

    enhanced_cols = 0
    try:
        for f_path in tqdm(json_files, desc="deep_stats 增强 (表)"):
            try:
                with f_path.open("r", encoding="utf-8") as f:
                    table_profile = json.load(f)
            except Exception as e:  # noqa: BLE001
                print(f"!! 读取表剖析文件失败 {f_path}: {e}")
                continue

            table_name = table_profile.get("table_name")
            if not table_name:
                continue

            is_modified = False
            for col in table_profile.get("columns_profile", []):
                # 如果已经有 deep_stats，默认不覆盖，避免反复查询 DB
                if "deep_stats" in col:
                    continue

                stats = get_deep_stats(conn, table_name, col)
                if stats:
                    col["deep_stats"] = stats
                    is_modified = True
                    enhanced_cols += 1

            # 为整张表做一次覆盖率验证（只在尚未有 verification_stats 时进行）
            if "verification_stats" not in table_profile:
                date_col, id_col = _infer_date_and_id_columns(table_profile)
                if date_col and id_col:
                    vstats = verify_table_continuity(conn, table_name, date_col, id_col)
                    if vstats:
                        table_profile["verification_stats"] = vstats
                        is_modified = True

            if is_modified:
                try:
                    with f_path.open("w", encoding="utf-8") as f:
                        json.dump(table_profile, f, ensure_ascii=False, indent=4)
                except Exception as e:  # noqa: BLE001
                    print(f"!! 写回表剖析文件失败 {f_path}: {e}")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    print(f"=== deep_stats 增强完成，共增强 {enhanced_cols} 个字段 ===")


def build_mechanical_description(col_profile: Dict[str, Any], total_records: Optional[int]) -> str:
    desc: List[str] = []
    col_name = col_profile.get("column_name")

    if col_profile.get("null_values") is not None and total_records is not None:
        null_pct = 0.0
        if total_records > 0:
            null_pct = round(
                (col_profile["null_values"] / total_records) * 100,
                1,
            )
        desc.append(
            f"字段 '{col_name}' 在 {total_records} 条记录中有 "
            f"{col_profile['null_values']} 个 NULL 值 ({null_pct}%)。",
        )

    if col_profile.get("cardinality") is not None:
        desc.append(f"它有 {col_profile['cardinality']} 个唯一值。")

    shape = col_profile.get("shape", {}) or {}
    if (
        shape.get("min_length") is not None
        and shape.get("max_length") is not None
    ):
        if shape["min_length"] == shape["max_length"]:
            desc.append(f"值的长度总是 {shape['min_length']}。")
        else:
            avg_len = shape.get("avg_length", 0) or 0
            desc.append(
                f"值的长度范围从 {shape['min_length']} 到 {shape['max_length']} "
                f"(平均 {avg_len:.1f})。",
            )

    if (
        shape.get("min_value") not in [None, "None"]
        and shape.get("max_value") not in [None, "None"]
    ):
        desc.append(
            f"值范围从 '{shape['min_value']}' 到 '{shape['max_value']}'。",
        )

    charset = shape.get("charset_analysis") or {}
    if charset and charset.get("count", 0) > 0:
        if charset.get("all_numeric"):
            desc.append("所有采样值都是纯数字。")
        elif charset.get("json_like_pct", 0) > 50:
            desc.append(f"有 {charset['json_like_pct']}% 的采样值看起来像 JSON。")
        elif charset.get("other_special_char_pct", 0) > 10:
            desc.append("值中经常包含特殊字符。")

    top_k = col_profile.get("top_k_values")
    if top_k:
        top_k_list = [
            f"'{val[0]}' (出现 {val[1]} 次)" for val in top_k[:5]
        ]
        desc.append(
            f"最常见的非空值包括: {', '.join(top_k_list)}。",
        )

    return " ".join(desc)


def build_field_llm_prompt(
    table_profile: Dict[str, Any],
    col_profile: Dict[str, Any],
    sme_description: str,
    neighbors: List[str],
    common_knowledge: str,
    task_knowledge: str,
) -> tuple[str, str]:
    table_name = table_profile.get("table_name")
    col_name = col_profile.get("column_name")

    mechanical_desc = build_mechanical_description(
        col_profile,
        table_profile.get("total_records"),
    )
    context_lines = [
        f"表名 (Table): `{table_name}`",
        f"表描述 (Table Description): \"{table_profile.get('table_description', 'N/A')}\"",
        f"字段名 (Column): `{col_name}`",
        f"SME 提供的描述 (SME Description): \"{sme_description}\"",
        f"表中的其他字段 (Neighbors): {', '.join(neighbors[:10])}{'...' if len(neighbors) > 10 else ''}",
    ]
    context = "\n".join(context_lines)

    link_info_lines: List[str] = []
    potential_links = col_profile.get("potential_links")
    if potential_links and isinstance(potential_links, list):
        link_info_lines.append("\n**连接分析**: 此字段已验证可以与以下字段连接：")
        for link in potential_links[:3]:
            try:
                jacc = float(link.get("jaccard_similarity", 0) or 0) * 100
            except Exception:  # noqa: BLE001
                jacc = 0.0
            link_info_lines.append(
                f"  - `{link.get('link_column')}` (Jaccard 相似度: {jacc:.1f}%)",
            )
        if len(potential_links) > 3:
            link_info_lines.append(
                f"  ... (以及其他 {len(potential_links) - 3} 个)",
            )
    link_info = "\n".join(link_info_lines)

    # deep_stats 增强描述
    deep_stats = col_profile.get("deep_stats") or {}
    deep_desc = ""
    special_instruction = ""

    if deep_stats.get("is_enum"):
        all_vals = deep_stats.get("all_values") or []
        all_vals_str = ", ".join(str(v) for v in all_vals)
        deep_desc = (
            f"【枚举全集】该字段可能为枚举，所有枚举值列表: [{all_vals_str}]"
        )
        special_instruction = """
[特殊指令 - 枚举解释]
检测到该字段包含具体的枚举值。你必须结合 [知识库] 对列表中的**每一个值**进行解释。
格式要求：`值` = 含义。
如果知识库没有提及，请根据英文单词或常识推断，并标记为(推断)。
"""
    elif deep_stats.get("is_time"):
        min_val = deep_stats.get("min")
        max_val = deep_stats.get("max")
        total_present = deep_stats.get("total_present")
        distinct_count = deep_stats.get("distinct_count")
        deep_desc = (
            "【时间分布增强统计】 "
            f"范围 {min_val} 到 {max_val}; "
            f"非空记录数: {total_present}; "
            f"不同时间点数: {distinct_count}"
        )
        special_instruction = """
[特殊指令 - 时间字段辨析]
检测到该字段为时间/日期字段，并提供了增强统计信息（总记录数与不同时间点数量）。
你必须结合这些统计，推断它更像是“分区时间（统计/分区字段）”还是“属性时间（如注册时间、登录时间）”，并在描述中明确给出结论。
"""

    system_prompt = f"""
[任务]
你是一个专业的数据库元数据分析师和业务专家。
请基于“SME 描述”、“剖析报告”和“知识库”，为该字段生成一个“简短描述”和一个“详细描述”。

[知识库 - 通用]
{common_knowledge}

[知识库使用指南 - 重要]
提供的【业务知识库】是基于历史 SQL 案例按「表」聚合而来的，其中既包含**强约束规则**，也包含**具体场景示例**：
1. 当看到前缀为 `⚠️【重要规则`，或文字中包含“必须”“严禁”“固定过滤条件”等措辞时，请视为**全局强约束**，在描述中明确强调这些规则，不要随意弱化或忽略。
2. 当看到前缀为 `【场景示例` 的多条知识在取值或过滤条件上互相不一致时，说明该字段在不同 SQL 中的用途不同。此时不要武断选择某一个场景，而是要总结为：“该字段用于区分业务/场景，常见取值包括 A、B、C 等，具体过滤条件需根据用户问题和业务场景决定。”
3. 如果同一个取值既出现在重要规则中，又出现在不同场景示例中，请优先服从“重要规则”中的约束，并在描述中指出这是**通用前置条件**。

[字段分析指令]
- “简短描述” (llm_short_description):
  用一句话推断该字段的**核心业务含义**、**内容格式**和**主要用途**。
  **[关键指令 1 (业务逻辑)]：** 你**必须**结合**字段名 (Column Name)** 和 **[知识库 - 通用]** 来推断该字段的**核心业务逻辑**。
  例如：如果 SME 描述是“日期”，但字段名是 `dtstatdate` 或 `ds`，并且表名后缀是 `_di` 或 `_df`，你必须从[知识库 - 通用]中推断出这**不是**“注册日期”，而是“数据统计的分区日期，格式 YYYYMMDD”。
  例如：如果字段名是 `cbitmap`，你必须从[知识库 - 通用]中推断出这是一个“100位活跃位图字符串”。

  **[关键指令 2 (格式)]：** 如果 SME 描述过于简单（例如 "日期", "业务", "预留字段"），你**必须**使用 [剖析报告] 中的统计数据（尤其是 Top-K 值、长度和字符集分析）来推断并补充更详细的格式。特别关注 [知识库 - 任务相关] 中关于数据类型（例如 String vs Bigint/Int）的描述；如果存在类型不一致或比较方式错误（例如对 BIGINT 字段加引号）的风险，必须在描述中作出明确预警。

{special_instruction}

- “详细描述” (llm_long_description):
  以“简短描述”开头，然后补充“剖析报告”中的关键细节（例如 Top-K 值、数据范围）。
  **[关键指令 3 (连接)]：** 详细描述中**必须包含** [剖析报告] 中提供的所有 "连接分析" 信息（如果存在）。
  **[关键指令 4 (知识)]：** 如果 [知识库 - 任务相关] 提到了这个字段（例如 `sgamecode`），你**必须**将该知识（例如“竞品业务”的固定过滤条件）包含在描述中；当多条【场景示例】在取值/过滤条件上互相冲突时，应按照上面的“知识库使用指南”进行**归纳总结**，而不是强行选出唯一取值。此外，请注意，[知识库 - 任务相关] 中可能同时包含多个表的规则。你目前正在分析的是表 `{table_name}`，请只提取与本表（或本表字段）直接相关的规则和连接逻辑，忽略仅适用于其他表的过滤条件。

[输出格式]
请严格按照以下 JSON 格式返回，不要添加任何其他文字：
{{
  "llm_short_description": "...",
  "llm_long_description": "..."
}}
"""

    user_prompt = f"""
[知识库 - 任务相关]
{task_knowledge if task_knowledge else "N/A"}

---
[待分析的字段数据]

[上下文]
{context}

[剖析报告 (Profiling Report)]
{mechanical_desc}
{link_info}

"""

    if deep_desc:
        user_prompt += f"\n\n[增强统计 (Deep Stats)]\n{deep_desc}"

    return system_prompt, user_prompt


def build_table_llm_prompt(
    table_profile: Dict[str, Any],
    columns_short_descriptions: List[str],
    common_knowledge: str,
    task_knowledge: str,
) -> tuple[str, str]:
    table_name = table_profile.get("table_name")
    table_desc = table_profile.get("table_description", "N/A")
    columns_block = "\n".join(columns_short_descriptions) if columns_short_descriptions else "N/A"

    # 汇总该表所有时间字段的 deep_stats 信息
    time_cols_info: List[str] = []
    for col in table_profile.get("columns_profile", []):
        ds = col.get("deep_stats") or {}
        if ds.get("is_time"):
            time_cols_info.append(
                "- `{}.`{}: 范围[{} ~ {}], 非空记录数={}, 不同时间点数={}".format(
                    table_name,
                    col.get("column_name"),
                    ds.get("min"),
                    ds.get("max"),
                    ds.get("total_present"),
                    ds.get("distinct_count"),
                )
            )
    time_context = "\n".join(time_cols_info) if time_cols_info else "无时间字段或尚未进行时间增强统计 (deep_stats)"

    # 读取 Python 侧覆盖率验证结果
    verify_info = table_profile.get("verification_stats") or {}
    inferred_type = verify_info.get("type", "UNKNOWN")
    coverage = verify_info.get("avg_coverage", "N/A")
    check_days = verify_info.get("check_period_days", "N/A")
    confidence = verify_info.get("confidence", "unknown")

    suffix = ""
    if table_name and isinstance(table_name, str) and "_" in table_name:
        suffix = table_name.split("_")[-1].lower()

    warning_msg = ""
    if suffix == "df" and "INCREMENT" in inferred_type:
        warning_msg = (
            "[严重警告：命名与数据特征不一致]\n"
            f"该表后缀为 `_df`（通常指全量快照），但根据最近 {check_days} 天的覆盖率（{coverage}，置信度 {confidence}），\n"
            f"推断其实际行为更接近增量日志表：{inferred_type}。\n"
            "在编写 SQL 时，不可假设本表每天包含“全量用户”。\n"
            "在计算留存、活跃基数等指标时，通常需要先对历史分区进行聚合或与真正的全量表关联。"
        )
    elif suffix == "di" and "SNAPSHOT" in inferred_type:
        warning_msg = (
            "[提示：命名可能不规范]\n"
            f"该表后缀为 `_di`，但根据最近 {check_days} 天的覆盖率（{coverage}，置信度 {confidence}），\n"
            f"推断其更接近全量快照表：{inferred_type}。\n"
            "在使用本表时，可以考虑将最新分区视作接近全量快照，但仍需结合业务确认。"
        )
    elif inferred_type != "UNKNOWN":
        warning_msg = (
            "[数据验证结果]\n"
            f"该表最近 {check_days} 天的覆盖率为 {coverage}，推断类型为 {inferred_type}，"
            f"与表名后缀 `{suffix}` 基本一致。"
        )
    else:
        warning_msg = (
            "[数据验证结果不确定]\n"
            "由于分区过少或数据异常，当前无法可靠判断该表是全量快照还是增量日志。\n"
            "在使用本表作为基表时，请谨慎检查分区与覆盖率。"
        )

    system_prompt = f"""
[任务]
你是一个专业的数据库元数据分析师和业务专家。
你的任务是基于整张表的SME描述、所有列的摘要和知识库，为**整个表**生成一个“简短描述”和一个“详细描述”。

[知识库 - 通用]
{common_knowledge}

[知识库使用指南 - 重要]
提供的【业务知识库】是基于历史 SQL 问题按「表」聚合而来的：
- 带有前缀 `⚠️【重要规则` 的内容通常是**全局强约束**（例如固定过滤条件、必须/严禁的用法），在描述表的使用方法和注意事项时必须明确强调；
- 带有前缀 `【场景示例` 的内容是具体业务场景下的查询示例，可能彼此存在不同甚至冲突的过滤条件。

在生成表级 Profile 时：
1. 请将所有“重要规则”汇总为本表在任何查询中都应该遵守的**基础约束/前置条件**（例如必须加上的 where 过滤、聚合要求等）。
2. 对于多个互相不一致的“场景示例”，不要简单选取某一个，而是要总结为“该表可用于多种业务分析场景，常见场景包括 A、B、C，不同场景会对字段 X/Y 施加不同过滤条件，需根据实际问题选择适当过滤”。
3. 如果某条重要规则仅对特定后缀表（如 `_di`、`_df`）或特定业务场景生效，请在描述中说明其适用范围，避免误导为绝对规则。

[特别任务：时间字段辨析]
该表包含以下时间字段及其统计信息：
{time_context}

请根据统计信息和业务常识，在描述中明确区分它们：
1. **统计/分区时间**：通常是**记录数最多**且**唯一值数量适中**（如365天）的字段。请标记为“查询必备过滤字段”。
2. **属性时间**：如注册时间、登录时间等，通常跨度很大。
3. **数据完整性对比**：指出哪个时间字段的数据最全（记录数最多）。

[数据完整性验证]
以下是基于最近若干天分区覆盖率的 Python 验证结果：
{warning_msg}

[分析指令]
- “简短描述” (llm_table_short_description):
  用一句话推断该表的**核心业务目的**。
  **[关键指令 1 (表类型)]：** 结合表名（尤其是 `_di`, `_df`, `_nf` 后缀）和 [知识库 - 通用] 来推断表的**类型**（例如：全量维度表, 每日快照表, 事实表, 映射表）。
  **[关键指令 2 (业务)]：** 结合表描述和 [知识库 - 任务相关] 来推断**核心业务实体**（例如：玩家, 角色, 登录）。

- “详细描述” (llm_table_long_description):
  以“简短描述”开头，然后：
  1. 详细说明表的**角色和类型**（例如 "这是一个每日快照表..."）。
  2. 根据 [列摘要] 指出该表的**关键字段**（例如：主键, 分区键, 核心度量）。
  3. 推断表的**数据粒度**（例如：“每行代表一个玩家的一次登录”，或“每行代表一个玩家在某天的全天汇总”）。
  4. 结合 [知识库 - 任务相关] 补充任何相关的业务背景，并在描述中**明确给出时间字段的角色与数据完整性对比结论**。
  5. 如果数据验证给出了“严重警告”，必须在描述开头用醒目的语言指出命名与数据特征不一致，并给出**正确的使用方式**（例如：先聚合增量再分析）。

[输出格式]
请严格按照以下 JSON 格式返回，不要添加任何其他文字：
{{
  "llm_table_short_description": "...",
  "llm_table_long_description": "..."
}}
"""

    user_prompt = f"""
[表信息]
表名: `{table_name}`
表描述: "{table_desc}"

[知识库 - 任务相关]
{task_knowledge if task_knowledge else "N/A"}

[字段级摘要汇总]
{columns_block}
"""

    return system_prompt, user_prompt


def summarize_fields_with_llm() -> None:
    """对 profiling_output_merged 下所有表的 *字段* 做 LLM 摘要并直接回写。"""

    print("=== 字段级摘要：准备加载知识与配置 ===")
    sme_meta, table_neighbors = load_sme_metadata(SCHEMA_FILE)
    if not sme_meta:
        print("!! schema 元数据为空，无法进行字段级摘要。")
        return

    common_knowledge = load_common_knowledge(COMMON_KNOWLEDGE_FILE)
    task_knowledge_map = load_advanced_knowledge(TASK_FILE, KNOWLEDGE_ADD_FILE)
    schema_table_desc_map = load_table_descriptions(SCHEMA_FILE)

    if not PROFILING_DIR.exists():
        print(f"!! 错误：剖析目录不存在: {PROFILING_DIR}")
        return

    json_files = sorted(PROFILING_DIR.glob("*.json"))
    if not json_files:
        print(f"!! 错误：在 '{PROFILING_DIR}' 中未找到任何剖析文件。")
        return

    print(f"将对 {len(json_files)} 个表执行字段级摘要（仅处理未完成字段）...")

    total_called = 0
    total_success = 0
    total_failed = 0

    for f_path in tqdm(json_files, desc="字段级摘要 (表)"):
        try:
            with f_path.open("r", encoding="utf-8") as f:
                table_profile = json.load(f)
        except Exception as e:  # noqa: BLE001
            print(f"!! 严重错误：无法读取表剖析文件 {f_path}: {e}")
            continue

        table_name = table_profile.get("table_name")
        if not table_name:
            continue

        # 将 schema.json 中的表级 SME 描述并入当前表描述
        schema_desc = schema_table_desc_map.get(table_name)
        if schema_desc:
            existing_desc = (table_profile.get("table_description") or "").strip()
            if existing_desc:
                # 如果原有描述中不包含 schema 描述，则拼接两者
                if schema_desc not in existing_desc:
                    table_profile["table_description"] = f"{existing_desc}；{schema_desc}"
            else:
                table_profile["table_description"] = schema_desc

        neighbors = table_neighbors.get(table_name, [])
        task_knowledge = task_knowledge_map.get(table_name, "")
        modified = False

        for col_profile in table_profile.get("columns_profile", []):
            col_name = col_profile.get("column_name")
            if not col_name:
                continue

            # 跳过已经成功完成的字段；允许重新覆盖之前的 "摘要失败"
            if (
                col_profile.get("llm_short_description")
                and col_profile.get("llm_short_description") != "摘要失败"
            ):
                continue

            full_name = f"{table_name}.{col_name}"
            sme_description = sme_meta.get(full_name, "")

            system_prompt, user_prompt = build_field_llm_prompt(
                table_profile,
                col_profile,
                sme_description,
                neighbors,
                common_knowledge,
                task_knowledge,
            )

            total_called += 1

            try:
                result = _call_llm_json(system_prompt, user_prompt)
                short_desc = (result.get("llm_short_description") or "").strip()
                long_desc = (result.get("llm_long_description") or "").strip()
                if not short_desc or not long_desc:
                    raise RuntimeError("LLM 返回缺少 llm_short_description / llm_long_description")

                col_profile["llm_short_description"] = short_desc
                col_profile["llm_long_description"] = long_desc
                modified = True
                total_success += 1
            except Exception as e:  # noqa: BLE001
                print(f"!! 字段摘要失败: {full_name}: {e}")
                col_profile["llm_short_description"] = "摘要失败"
                col_profile["llm_long_description"] = "摘要失败"
                modified = True
                total_failed += 1

        if modified:
            try:
                with f_path.open("w", encoding="utf-8") as f:
                    json.dump(table_profile, f, ensure_ascii=False, indent=4)
            except Exception as e:  # noqa: BLE001
                print(f"!! 写回表剖析文件失败 {f_path}: {e}")

    print("=" * 60)
    print("字段级摘要完成。")
    print(f"共调用 LLM {total_called} 次，其中成功 {total_success} 次，失败 {total_failed} 次。")
    print("=" * 60)


def summarize_tables_with_llm() -> None:
    """在字段摘要完成的基础上，对每张表做 LLM 摘要并直接回写。"""

    print("=== 表级摘要：准备加载知识与配置 ===")

    common_knowledge = load_common_knowledge(COMMON_KNOWLEDGE_FILE)
    task_knowledge_map = load_advanced_knowledge(TASK_FILE, KNOWLEDGE_ADD_FILE)
    schema_table_desc_map = load_table_descriptions(SCHEMA_FILE)

    if not PROFILING_DIR.exists():
        print(f"!! 错误：剖析目录不存在: {PROFILING_DIR}")
        return

    json_files = sorted(PROFILING_DIR.glob("*.json"))
    if not json_files:
        print(f"!! 错误：在 '{PROFILING_DIR}' 中未找到任何剖析文件。")
        return

    print(f"将对 {len(json_files)} 个表执行 *表级* 摘要（仅处理未完成表）...")

    total_called = 0
    total_success = 0
    total_failed = 0

    for f_path in tqdm(json_files, desc="表级摘要 (表)"):
        try:
            with f_path.open("r", encoding="utf-8") as f:
                table_profile = json.load(f)
        except Exception as e:  # noqa: BLE001
            print(f"!! 严重错误：无法读取表剖析文件 {f_path}: {e}")
            continue

        table_name = table_profile.get("table_name")
        if not table_name:
            continue

        # 跳过已经成功完成的表摘要
        if (
            table_profile.get("llm_table_short_description")
            and table_profile.get("llm_table_short_description") != "摘要失败"
        ):
            continue

        # 将 schema.json 中的表级 SME 描述并入当前表描述
        schema_desc = schema_table_desc_map.get(table_name)
        if schema_desc:
            existing_desc = (table_profile.get("table_description") or "").strip()
            if existing_desc:
                if schema_desc not in existing_desc:
                    table_profile["table_description"] = f"{existing_desc}；{schema_desc}"
            else:
                table_profile["table_description"] = schema_desc

        task_knowledge = task_knowledge_map.get(table_name, "")

        columns_short_descriptions: List[str] = []
        for c in table_profile.get("columns_profile", []):
            sd = c.get("llm_short_description")
            if sd and sd != "摘要失败":
                columns_short_descriptions.append(f"{c.get('column_name')}: {sd}")

        if not columns_short_descriptions:
            print(f"警告：表 {table_name} 没有任何已完成的字段摘要，跳过表摘要。")
            continue

        system_prompt, user_prompt = build_table_llm_prompt(
            table_profile,
            columns_short_descriptions,
            common_knowledge,
            task_knowledge,
        )

        total_called += 1
        modified = False

        try:
            result = _call_llm_json(system_prompt, user_prompt)
            short_desc = (result.get("llm_table_short_description") or "").strip()
            long_desc = (result.get("llm_table_long_description") or "").strip()
            if not short_desc or not long_desc:
                raise RuntimeError(
                    "LLM 返回缺少 llm_table_short_description / llm_table_long_description",
                )

            table_profile["llm_table_short_description"] = short_desc
            table_profile["llm_table_long_description"] = long_desc
            modified = True
            total_success += 1
        except Exception as e:  # noqa: BLE001
            print(f"!! 表摘要失败: {table_name}: {e}")
            table_profile["llm_table_short_description"] = "摘要失败"
            table_profile["llm_table_long_description"] = "摘要失败"
            modified = True
            total_failed += 1

        if modified:
            try:
                with f_path.open("w", encoding="utf-8") as f:
                    json.dump(table_profile, f, ensure_ascii=False, indent=4)
            except Exception as e:  # noqa: BLE001
                print(f"!! 写回表剖析文件失败 {f_path}: {e}")

    print("=" * 60)
    print("表级摘要完成。")
    print(f"共调用 LLM {total_called} 次，其中成功 {total_success} 次，失败 {total_failed} 次。")
    print("=" * 60)


def main() -> None:
    """顺序执行：先 DB 增强 deep_stats，再进行字段级和表级摘要。"""

    enrich_profiles_with_db()
    summarize_fields_with_llm()
    summarize_tables_with_llm()


if __name__ == "__main__":
    main()
