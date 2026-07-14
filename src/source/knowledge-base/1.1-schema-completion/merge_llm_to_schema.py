import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional

from config import BASE_DIR


DATA_DIR = BASE_DIR / "data"
SCHEMA_FILE = DATA_DIR / "schema.json"
PROFILING_DIR = DATA_DIR / "profiling_output_merged"
OUTPUT_FILE = DATA_DIR / "schema_all.json"
RUN_AGENT_SCHEMA_FILE = BASE_DIR / "run" / "agent" / "schema.json"
COLUMN_MISS_REPORT_FILE = BASE_DIR / "run" / "agent" / "column_miss_empty_report.json"


def _load_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[warn] 文件不存在: {path}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 读取失败 {path}: {e}")
        return None


def _load_all_null_columns() -> Dict[str, List[str]]:
    """从 column_miss_empty_report.json 中加载被判定为全空的字段列表。

    返回结构: {table_name: [col1, col2, ...]}
    若文件不存在或格式异常，则返回空 dict，不做任何删除。
    """

    if not COLUMN_MISS_REPORT_FILE.exists():
        return {}

    data = _load_json(COLUMN_MISS_REPORT_FILE)
    if not isinstance(data, list):
        print(
            f"[warn] {COLUMN_MISS_REPORT_FILE} 格式异常(不是 list)，跳过全空字段删除。",
        )
        return {}

    result: Dict[str, List[str]] = {}
    for item in data:
        try:
            if not item.get("is_all_null"):
                continue
            t_name = item.get("table")
            c_name = item.get("column")
            if not t_name or not c_name:
                continue
            result.setdefault(t_name, []).append(c_name)
        except Exception:  # noqa: BLE001
            continue

    if result:
        total_cols = sum(len(v) for v in result.values())
        print(f"[info] 将从 schema 中删除 {total_cols} 个【全空】字段。")
    return result


def load_profiles(profiling_dir: Path) -> Dict[str, dict]:
    """读取指定目录下的 profile_* 文件，按 table_name 建索引。"""
    profiles: Dict[str, dict] = {}
    if not profiling_dir.exists():
        print(f"[error] 剖析目录不存在: {profiling_dir}")
        return profiles

    for f_path in sorted(profiling_dir.glob("profile_*.json")):
        data = _load_json(f_path)
        if not data:
            continue
        t_name = data.get("table_name")
        if not t_name:
            print(f"[warn] 文件缺少 table_name: {f_path}")
            continue
        profiles[t_name] = data
    print(f"[info] 已加载 {len(profiles)} 个 profile_* 文件。")
    return profiles


def merge_schema_with_llm(
    schema_path: Path = SCHEMA_FILE,
    profiling_dir: Path = PROFILING_DIR,
    output_path: Path = OUTPUT_FILE,
) -> None:
    schema = _load_json(schema_path)
    if not schema:
        print("[error] 无法加载 schema.json，终止。")
        return

    profiles = load_profiles(profiling_dir)
    if not profiles:
        print("[error] 未找到任何 profile_* 文件，终止。")
        return

    # 尝试加载全空字段列表（来自 check_column_miss_empty.py 的报告）
    all_null_cols_map = _load_all_null_columns()

    table_missing_profiles: List[str] = []
    column_miss: List[str] = []

    for table in schema:
        t_name = table.get("table_name")
        if not t_name:
            continue
        profile = profiles.get(t_name)
        if not profile:
            table_missing_profiles.append(t_name)
            continue

        # 如果该表存在被判定为全空的字段，先从 columns 中剔除
        if t_name in all_null_cols_map and table.get("columns"):
            to_drop = set(all_null_cols_map[t_name])
            old_cols = table.get("columns") or []
            new_cols: List[dict] = []
            for col in old_cols:
                c_name = col.get("col")
                if c_name and c_name in to_drop:
                    # 直接丢弃这些全空字段
                    continue
                new_cols.append(col)
            if len(new_cols) != len(old_cols):
                table["columns"] = new_cols

        # 表级 LLM 描述
        t_short = profile.get("llm_table_short_description")
        t_long = profile.get("llm_table_long_description")
        if t_short:
            table["table_description_llm_short"] = t_short
            # 若原始描述为空则回填
            if not table.get("table_description"):
                table["table_description"] = t_short
        if t_long:
            table["table_description_llm_long"] = t_long

        # 字段级 LLM 描述
        col_map = {
            c.get("column_name"): c for c in profile.get("columns_profile", []) or []
        }
        for col in table.get("columns", []) or []:
            c_name = col.get("col")
            if not c_name:
                continue
            p_col = col_map.get(c_name)
            if not p_col:
                column_miss.append(f"{t_name}.{c_name}")
                continue
            c_short = p_col.get("llm_short_description")
            c_long = p_col.get("llm_long_description")
            if c_short:
                col["llm_short_description"] = c_short
                if not col.get("description"):
                    col["description"] = c_short
            if c_long:
                col["llm_long_description"] = c_long

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)

    print(f"[done] 已写出合并结果(schema_all): {output_path}")

    # 额外同步一份到 run/agent/schema.json，供 agent 使用
    try:
        RUN_AGENT_SCHEMA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with RUN_AGENT_SCHEMA_FILE.open("w", encoding="utf-8") as f:
            json.dump(schema, f, ensure_ascii=False, indent=2)
        print(f"[done] 已同步写入运行端 schema: {RUN_AGENT_SCHEMA_FILE}")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 写入运行端 schema 失败 {RUN_AGENT_SCHEMA_FILE}: {e}")
    if table_missing_profiles:
        print(f"[warn] 下列表未找到 profile 文件: {len(table_missing_profiles)}")
        for name in table_missing_profiles:
            print(f"  - {name}")
    if column_miss:
        print(f"[warn] 下列表在 profile 中缺少字段映射: {len(column_miss)} 条")
        for name in column_miss[:20]:
            print(f"  - {name}")
        if len(column_miss) > 20:
            print(f"  ... 其余 {len(column_miss) - 20} 条省略")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="将 profiling_output_merged 下的 LLM 摘要合并回 schema.json，生成 schema_llm_merged.json",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=SCHEMA_FILE,
        help="原始 schema.json 路径",
    )
    parser.add_argument(
        "--profiling-dir",
        type=Path,
        default=PROFILING_DIR,
        help="profiling_output_merged 目录路径",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_FILE,
        help="输出合并后的 schema_llm_merged.json 路径",
    )
    args = parser.parse_args()
    merge_schema_with_llm(
        schema_path=args.schema,
        profiling_dir=args.profiling_dir,
        output_path=args.output,
    )
