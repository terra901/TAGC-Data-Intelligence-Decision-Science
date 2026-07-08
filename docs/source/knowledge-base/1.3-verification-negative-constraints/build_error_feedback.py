from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# === 配置区域：在这里直接指定输入 / 错题列表 / 输出路径 ===
BASE_DIR = Path(__file__).resolve().parent.parent

# 评测结果文件（dataset_exe_result.json）
INPUT_PATH = Path("D:/Desktop/研究生/比赛/腾讯算法/error_source/13.95-12.13.json")

# 需要写入错题本的 sql_id 列表（可按需修改）
WRONG_IDS: List[str] = ['sql_1', 'sql_2', 'sql_3', 'sql_4', 'sql_6', 'sql_7', 'sql_8', 'sql_9', 'sql_10', 'sql_11', 'sql_13', 'sql_14', 'sql_15', 'sql_16', 'sql_18', 'sql_19', 'sql_20', 'sql_22', 'sql_23', 'sql_24', 'sql_26', 'sql_27', 'sql_29', 'sql_31', 'sql_32', 'sql_40', 'sql_41', 'sql_42', 'sql_43', 'sql_44', 'sql_47', 'sql_50', 'sql_51', 'sql_52', 'sql_53', 'sql_54', 'sql_55', 'sql_58', 'sql_60', 'sql_61', 'sql_62', 'sql_64', 'sql_65', 'sql_67', 'sql_68', 'sql_69', 'sql_70', 'sql_71', 'sql_73', 'sql_74', 'sql_75', 'sql_77', 'sql_78', 'sql_80', 'sql_82', 'sql_83', 'sql_84', 'sql_85', 'sql_87', 'sql_88', 'sql_89', 'sql_90', 'sql_91', 'sql_93', 'sql_98', 'sql_99', 'sql_100', 'sql_108', 'sql_109', 'sql_110', 'sql_111', 'sql_115', 'sql_117', 'sql_120']
OUTPUT_PATH = BASE_DIR / "data" / "error_feedback.json"


def load_dataset(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"找不到输入文件: {path}")
    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            # 尝试读取 JSONL
            f.seek(0)
            data = [json.loads(line) for line in f if line.strip()]

    if not isinstance(data, list):
        raise ValueError("输入 JSON 顶层必须是 list[dict]")
    return data


def normalize_batch(rows: List[Dict[str, Any]]) -> str:
    """
    将一个完整的结果集（Batch）序列化为唯一字符串，用于去重。
    逻辑：将每一行转为字符串 -> 对行字符串列表排序 -> 整体转为字符串。
    这样可以忽略结果集内部的行顺序差异。
    """
    if not rows:
        return ""
    # 1. 序列化每一行 (Sort keys 保证字典序一致)
    row_strs = [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows]
    # 2. 对行进行排序 (忽略行序影响)
    row_strs.sort()
    # 3. 序列化整个列表
    return json.dumps(row_strs, ensure_ascii=False)


def build_error_feedback(
    dataset: List[Dict[str, Any]], wrong_ids: Set[str]
) -> Dict[str, Dict[str, Any]]:
    """
    从评测结果中提取错题结果集。
    每个 sql_id 对应一个 batch (因为 dataset_exe_result.json 通常每题只有一条记录)。
    """
    feedback: Dict[str, Dict[str, Any]] = {}

    for item in dataset:
        sid = str(item.get("sql_id") or "").strip()
        if not sid or sid not in wrong_ids:
            continue

        # 这是一个完整的错误结果集（Batch）
        batch = item.get("result")

        # 只有当 result 是列表时才处理（null 或 空列表视为空结果）
        if not isinstance(batch, list):
            batch = []

        # 如果结果集为空，通常没有参考价值（因为无法规避“空”），打印警告但不存入历史
        if not batch:
            print(f"[WARN] sql_id={sid} 的结果为空 (0 rows)，跳过记录。")
            continue

        entry = feedback.setdefault(
            sid,
            {
                "status": "incorrect",
                "history_batches": [],
            },
        )
        entry["history_batches"].append(batch)

    return feedback


def merge_feedback(
    existing: Dict[str, Any], new_fb: Dict[str, Any]
) -> Tuple[Dict[str, Any], int, int]:
    """
    将新错题记录合并进已有错题本。
    返回: (合并后的字典, 新增批次数量, 重复批次数量)
    """
    added_count = 0
    duplicate_count = 0

    for sid, entry in new_fb.items():
        new_batches = entry.get("history_batches") or []

        target = existing.setdefault(
            sid,
            {"status": "incorrect", "history_batches": []}
        )
        existing_batches = target.get("history_batches", [])

        # 构建现有批次的指纹集合
        existing_sigs = set()
        for b in existing_batches:
            if isinstance(b, list):
                existing_sigs.add(normalize_batch(b))

        # 逐个合并新批次
        for batch in new_batches:
            if not isinstance(batch, list):
                continue

            sig = normalize_batch(batch)
            if sig in existing_sigs:
                duplicate_count += 1
            else:
                existing_batches.append(batch)
                existing_sigs.add(sig)
                added_count += 1

        target["history_batches"] = existing_batches
        target["status"] = "incorrect"

    return existing, added_count, duplicate_count


def main() -> None:
    if not WRONG_IDS:
        raise ValueError("WRONG_IDS 不能为空，请配置 sql_id 列表。")

    wrong_ids_set = {x.strip() for x in WRONG_IDS if x.strip()}
    print(f"[INFO] 目标错题数量: {len(wrong_ids_set)}")

    try:
        dataset = load_dataset(INPUT_PATH)
    except Exception as e:
        print(f"[FATAL] 加载输入文件失败: {e}")
        return

    # 1. 构建本次的反馈数据
    new_feedback = build_error_feedback(dataset, wrong_ids_set)
    if not new_feedback:
        print("[WARN] 未从输入文件中提取到任何有效错题记录（可能结果全为空或ID不匹配）。")
        return

    # 2. 读取现有错题本
    existing = {}
    if OUTPUT_PATH.exists() and OUTPUT_PATH.stat().st_size > 0:
        try:
            with OUTPUT_PATH.open("r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:
            print(f"[WARN] 读取旧错题本失败 ({e})，将创建新文件。")

    # 3. 合并并统计
    merged, added_cnt, dup_cnt = merge_feedback(existing, new_feedback)

    # 4. 写入文件
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print("/n" + "="*40)
    print(f"错题本更新完成: {OUTPUT_PATH}")
    print(f"涉及题目数: {len(new_feedback)}")
    print(f"➕ 新增错误结果集: {added_cnt} 个")
    print(f"♻️ 重复错误结果集: {dup_cnt} 个 (已忽略)")
    print("="*40 + "/n")


if __name__ == "__main__":
    main()
