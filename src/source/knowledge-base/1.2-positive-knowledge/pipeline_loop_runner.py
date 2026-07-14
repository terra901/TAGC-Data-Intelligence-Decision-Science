import json
import time
from pathlib import Path
from typing import List

import sys

from pipeline_runner import (
    step2_prepare_sql_exe_input,
    step3_run_sql_exe,
    step4_run_evaluation,
    step5_build_knowledge,
)


# 与 pipeline_runner / build_knowledge_prompts 约定的错误题列表路径保持一致
_HERE = Path(__file__).resolve()
_RUN_DIR = _HERE.parent.parent
_AGENT_DIR = _RUN_DIR / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from config import T2SQL_DIR, BASE_DIR, RESULTS_PATH, FAISS_INDEX_PATH, FINAL_DATASET_PATH

if str(T2SQL_DIR) not in sys.path:
    sys.path.insert(0, str(T2SQL_DIR))

import agent as t2_agent

RUN_DIR = BASE_DIR / "run"
EVAL_INCORRECT_PATH = RUN_DIR / "datafile" / "output" / "eval_incorrect_ids.json"

# 仅对这些 sql_id 进行循环优化（例如当前评测出的错误题目）
FOCUS_SQL_IDS = [
    "sql_1",
    "sql_2",
    "sql_16",
    "sql_50",
    "sql_60",
    "sql_65",
    "sql_68",
    "sql_73",
    "sql_74",
    "sql_78",
    "sql_101",
    "sql_92",
    "sql_94",
    "sql_111",
    "sql_113",
    "sql_116",
    "sql_118",
    "sql_119",
]

# "sql_16", "sql_50", "sql_60", "sql_65", "sql_68", "sql_73", "sql_74", "sql_101", "sql_113"
def load_incorrect_ids() -> int:
    """读取当前错题数量（根据 eval_incorrect_ids.json）。

    返回值为 incorrect_count 字段；若不存在该字段，则退化为 incorrect_ids 长度；
    若文件不存在或解析失败，则返回 -1 以示未知。
    """
    if not EVAL_INCORRECT_PATH.exists():
        print(f"[Loop] 未找到错题文件: {EVAL_INCORRECT_PATH}")
        return -1

    try:
        with EVAL_INCORRECT_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[Loop] 读取错题文件失败: {e}")
        return -1

    # 优先用 incorrect_count
    if isinstance(data, dict):
        cnt = data.get("incorrect_count")
        if isinstance(cnt, int):
            return cnt
        ids = data.get("incorrect_ids") or []
        if isinstance(ids, list):
            return len(ids)

    # 兼容纯列表结构
    if isinstance(data, list):
        return len(data)

    return -1


def load_incorrect_id_list() -> List[str]:
    if not EVAL_INCORRECT_PATH.exists():
        return []
    try:
        with EVAL_INCORRECT_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, dict):
        ids = data.get("incorrect_ids") or []
        if isinstance(ids, list):
            return [str(x) for x in ids if x]
    if isinstance(data, list):
        return [str(x) for x in data if x]
    return []


def load_dataset_ids() -> List[str]:
    try:
        tasks = t2_agent.load_final_tasks()
    except Exception:
        return []
    ids: List[str] = []
    for t in tasks:
        sid = t.get("sql_id")
        if isinstance(sid, str) and sid:
            ids.append(sid)
    return ids


def run_agent_for_ids(target_ids: List[str]) -> None:
    if not target_ids:
        return

    target_set = set(target_ids)

    try:
        all_tasks = t2_agent.load_final_tasks()
    except Exception:
        all_tasks = []

    tasks = []
    for task in all_tasks:
        sid = task.get("sql_id")
        if isinstance(sid, str) and sid in target_set:
            tasks.append(task)

    if not tasks:
        print(f"[Loop] 在数据集中未找到目标 sql_id: {sorted(target_set)}")
        return

    current_ids = sorted({str(t.get("sql_id")) for t in tasks})
    print(f"[Loop] 本轮 agent 将处理 {len(current_ids)} 个 sql_id: {current_ids}")

    schema_map = t2_agent.load_schema_map()
    common_knowledge = t2_agent.load_common_knowledge()
    gold_map = t2_agent.load_gold_map()
    added_knowledge_map = t2_agent.load_added_knowledge_map()
    verified_kb_map = t2_agent.load_verified_kb_map()

    if not FAISS_INDEX_PATH.exists():
        try:
            import build_vector_db

            build_vector_db.main()
        except Exception as e:
            print(f"[Loop] 自动构建 FAISS 索引失败: {e}")
    t2_agent.t2sql_utils.load_faiss_index()

    conn = t2_agent.t2sql_utils.connect_db()

    results_map = {}
    if RESULTS_PATH.exists():
        try:
            with RESULTS_PATH.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    sid = obj.get("sql_id")
                    if isinstance(sid, str) and sid and sid not in target_set:
                        results_map[sid] = obj
        except Exception as e:
            print(f"[Loop] 读取已有 agent 结果失败，将重新生成: {e}")
            results_map = {}

    try:
        with RESULTS_PATH.open("w", encoding="utf-8") as out:
            for task in tasks:
                sid = str(task.get("sql_id"))
                res = t2_agent.process_task(
                    conn,
                    task,
                    schema_map,
                    gold_map,
                    common_knowledge,
                    added_knowledge_map,
                    verified_kb_map,
                )
                results_map[sid] = res

            for sid in sorted(results_map.keys()):
                out.write(
                    json.dumps(
                        results_map[sid], ensure_ascii=False, default=t2_agent._json_default
                    )
                    + "\n"
                )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main(max_rounds: int = 20, sleep_seconds: float = 0.0) -> None:
    """多轮运行 pipeline，直至错题清零或达到 max_rounds 上限。

    - 每一轮都会完整执行 pipeline_runner.main()（Agent 生成 -> 执行 SQL -> 评测 -> 更新知识）。
    - 每轮结束后读取 eval_incorrect_ids.json，若 incorrect_count 为 0，则提前终止。
    - 为避免日志难看，可选在轮次之间 sleep 若干秒。
    """
    print("================ Pipeline Loop Runner 开始 ================")
    print(f"[Loop] 最大轮次: {max_rounds}")

    dataset_ids = load_dataset_ids()
    if not dataset_ids:
        print("[Loop] 未从数据集中加载到任何 sql_id，结束。")
        return
    print(f"[Loop] 数据集中共有 {len(dataset_ids)} 个 sql_id: {dataset_ids}")

    # 如果配置了关注集合，只在这些 sql_id 上进行多轮循环
    focus_set = set(FOCUS_SQL_IDS) if FOCUS_SQL_IDS else set()
    if focus_set:
        dataset_ids = [sid for sid in dataset_ids if sid in focus_set]
        print(f"[Loop] 仅对以下 {len(dataset_ids)} 个 sql_id 进行循环: {dataset_ids}")
        if not dataset_ids:
            print("[Loop] 指定的 FOCUS_SQL_IDS 在数据集中不存在，结束。")
            return

    for round_idx in range(1, max_rounds + 1):
        print("\n" + "=" * 30)
        print(f"[Loop] 开始第 {round_idx} 轮 Pipeline 运行")
        print("=" * 30)

        if round_idx == 1:
            target_ids = dataset_ids
        else:
            incorrect_all = load_incorrect_id_list()
            if not incorrect_all:
                print("[Loop] 未在 eval_incorrect_ids.json 中找到错误题目，结束循环。")
                break
            dataset_set = set(dataset_ids)
            target_ids = [sid for sid in incorrect_all if sid in dataset_set]
            if not target_ids:
                print("[Loop] 当前数据集内已无错题，结束循环。")
                break

        print(f"[Loop] 本轮目标 sql_id: {target_ids}")

        run_agent_for_ids(target_ids)

        if not step2_prepare_sql_exe_input():
            print("[Loop] 无法准备 sql_exe 输入，结束循环。")
            break

        step3_run_sql_exe()
        step4_run_evaluation()
        step5_build_knowledge()

        # 按最新评测结果，统计关注集合中的错题情况
        incorrect_all = load_incorrect_id_list()
        if not incorrect_all:
            print("[Loop] 本轮评测后未在 eval_incorrect_ids.json 中找到任何错误题目。")
        else:
            print(
                f"[Loop] 本轮评测后全量错题数量: {len(incorrect_all)}, IDs: {incorrect_all}"
            )

        focus_set = set(dataset_ids)
        focus_incorrect = [sid for sid in incorrect_all if sid in focus_set]
        print(
            f"[Loop] 本轮评测后关注集合中的错题数量: {len(focus_incorrect)}, IDs: {focus_incorrect}"
        )

        if not focus_incorrect:
            print(
                f"[Loop] 关注的 sql_id 已全部评测为正确，在第 {round_idx} 轮提前结束。"
            )
            break

        if round_idx < max_rounds and sleep_seconds > 0:
            print(f"[Loop] 等待 {sleep_seconds} 秒后进入下一轮...")
            time.sleep(sleep_seconds)

    print("================ Pipeline Loop Runner 结束 ================")


if __name__ == "__main__":
    main()
