import json
import argparse
from decimal import Decimal
from datetime import date, datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple

class OfflineEvaluator:
    """
    线下评测核心类
    """
    def _normalize_value(self, value: Any) -> Any:
        if value is None: return None

        # 新增：如果是字符串，尝试看它是不是纯数字，如果是，转为 int 或 float 统一比较
        if isinstance(value, str):
            # 移除可能存在的 .00 (针对某些数据库浮点转字符串的情况)
            if value.replace('.', '', 1).isdigit():
                try:
                    val_float = float(value)
                    if val_float.is_integer():
                        return int(val_float)
                    return val_float
                except:
                    pass

        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(round(value, 2))
        if isinstance(value, float):
            return int(value) if value.is_integer() else float(round(value, 2))
        if isinstance(value, (date, datetime)):
            return value.isoformat()

        return value

    def _flatten_row(self, row_dict: Dict) -> Tuple:
        values = list(row_dict.values())
        normalized_values = [self._normalize_value(v) for v in values]
        return tuple(normalized_values)

    def evaluate_single_sql(self, golden_result: List[Dict], predict_result: List[Dict]) -> bool:
        if not golden_result and not predict_result: return True
        if not golden_result or not predict_result: return False
        if len(golden_result) != len(predict_result): return False

        try:
            golden_set = [self._flatten_row(row) for row in golden_result]
            predict_set = [self._flatten_row(row) for row in predict_result]
            # 排序对比，忽略行序
            golden_set.sort(key=lambda x: str(x))
            predict_set.sort(key=lambda x: str(x))
        except Exception:
            return False

        return golden_set == predict_set

    def evaluate_single_sql_with_id(self, sql_id: str, golden_result: List[Dict], predict_result: List[Dict]) -> bool:
        """带 sql_id 的评测入口，方便对个别题目加特殊规则。"""

        # 先做通用结果集比对（行数 / 行内容是否一致）
        base_ok = self.evaluate_single_sql(golden_result, predict_result)
        if not base_ok:
            return False

        # 特判 sql_111：题目要求坐标保留一位小数，如果结果里是整数就视为格式错误
        # 这里利用 JSON 反序列化后的类型信息：
        # - int: 认为是“整数输出”，直接判错；
        # - float: 认为是带小数的数值（无法区分 140.0 和 140.00，只要不是 int 即可）；
        # - str: 纯数字字符串（不含小数点）也判错，其它字符串放过。
        if sql_id == "sql_111":
            for row in predict_result or []:
                for key in ("x", "y", "z"):
                    if key not in row:
                        continue
                    v = row[key]
                    # JSON 数字：如果是 int，则坐标没有小数，判错
                    if isinstance(v, int):
                        return False
                    # float/Decimal 视为合格（已经带小数信息）
                    # 字符串再额外拦截纯整数形式
                    if isinstance(v, str):
                        # 纯整数字符串（不含小数点）同样视为格式不合格
                        if v.isdigit():
                            return False
            # 通过数值和格式双重校验
            return True

        # 其它题目沿用通用规则
        return True

def load_json_as_dict(file_path: str) -> Dict[str, List[Dict]]:
    """读取结果文件转为字典"""
    path = Path(file_path)
    if not path.exists():
        # 对于待测文件，如果不存在返回空字典，视为全错
        print(f"⚠️ 警告: 文件未找到 {file_path}")
        return {}
    try:
        content = json.loads(path.read_text(encoding='utf-8'))
        data_map = {}
        # 兼容 jsonl (每行一个json) 或 json list
        if isinstance(content, list):
            for item in content:
                if 'sql_id' in item:
                    data_map[item['sql_id']] = item.get('result', [])
        return data_map
    except json.JSONDecodeError:
        # 尝试处理 jsonl 格式 (如果你的 submit 是 jsonl)
        try:
            data_map = {}
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        if 'sql_id' in item:
                            data_map[item['sql_id']] = item.get('result', [])
            return data_map
        except:
            raise ValueError(f"文件 {file_path} 格式错误")

def load_master_list(file_path: str) -> List[str]:
    """读取 final_dataset.json 获取所有题目 ID"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"题目全集文件未找到: {file_path}")
    content = json.loads(path.read_text(encoding='utf-8'))
    return [item['sql_id'] for item in content if 'sql_id' in item]

def natural_sort_key(sql_id: str):
    try:
        return int(sql_id.split('_')[1])
    except:
        return sql_id

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--predict-file", "--predict_file", dest="predict_file", default=None)
    parser.add_argument("--output-dir", "--output_dir", dest="output_dir", default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    base_path = "D:/Desktop/研究生/比赛/腾讯算法"

    dataset_file_path = f"{base_path}/data/final_dataset.json"
    golden_file_path = f"{base_path}/data/correct_60.json"
    #default_predict_file_path = "D:/Desktop/研究生/比赛/腾讯算法/t2sql-backup/run/datafile/output/results-finalize-result-strategy4.jsonl"  D:\Desktop\研究生\比赛\腾讯算法\run\datafile\output\agent_exe_results.json
    default_predict_file_path = "D:/Desktop/研究生/比赛/腾讯算法/run/datafile/output/agent_exe_results.json"

    predict_file_path = args.predict_file or default_predict_file_path
    # 默认输出目录同步到当前项目的 run/datafile/output，手动运行时也与 pipeline 保持一致
    output_dir = args.output_dir or f"{base_path}/run/datafile/output"

    print(f"正在加载题目全集: {dataset_file_path} ...")
    try:
        all_sql_ids = load_master_list(dataset_file_path)
        # 去重并排序
        all_sql_ids = sorted(list(set(all_sql_ids)), key=natural_sort_key)
        TOTAL_QUESTIONS = len(all_sql_ids)
        print(f"共发现 {TOTAL_QUESTIONS} 道赛题。")
    except Exception as e:
        print(f"加载失败: {e}")
        return

    print(f"正在加载正确答案: {golden_file_path} ...")
    golden_map = load_json_as_dict(golden_file_path)

    print(f"正在加载待测文件: {predict_file_path} ...")
    predict_map = load_json_as_dict(predict_file_path)

    evaluator = OfflineEvaluator()

    # 统计分类
    correct_ids = []        # 逻辑正确
    incorrect_ids = []      # 逻辑错误
    missing_golden_ids = [] # 待测有结果，但正确答案缺失，无法评测
    not_submitted_ids = []  # 待测文件中完全没有这个 ID (漏题)

    # 遍历全集
    for sql_id in all_sql_ids:
        # 1. 检查是否提交
        if sql_id not in predict_map:
            not_submitted_ids.append(sql_id)
            continue

        # 2. 检查是否有标准答案
        if sql_id not in golden_map:
            missing_golden_ids.append(sql_id)
            continue

        # 3. 进行评测
        golden_res = golden_map[sql_id]
        predict_res = predict_map[sql_id]

        if evaluator.evaluate_single_sql_with_id(sql_id, golden_res, predict_res):
            correct_ids.append(sql_id)
        else:
            incorrect_ids.append(sql_id)

    # ================= 输出报告 =================
    print("\n" + "="*30)
    print("       评测报告 (Evaluation Report)")
    print("="*30)

    print(f"\n[正确题目] 共 {len(correct_ids)} 题:")
    print(f"IDs: {', '.join(correct_ids) if correct_ids else '无'}")

    print(f"\n[错误题目] 共 {len(incorrect_ids)} 题:")
    print(f"IDs: {', '.join(incorrect_ids) if incorrect_ids else '无'}")

    print(f"\n[未提交/缺失] (模型未生成结果) 共 {len(not_submitted_ids)} 题:")
    print(f"IDs: {', '.join(not_submitted_ids) if not_submitted_ids else '无'}")

    print(f"\n[无法评测] (缺乏正确答案) 共 {len(missing_golden_ids)} 题:")
    print(f"IDs: {', '.join(missing_golden_ids) if missing_golden_ids else '无'}")

    # 校验总数
    chk_sum = len(correct_ids) + len(incorrect_ids) + len(not_submitted_ids) + len(missing_golden_ids)
    print(f"\n(校验: 覆盖 {chk_sum}/{TOTAL_QUESTIONS} 题)")

    print("-" * 30)

    # 1. 有效评测准确率 (只看有答案且已提交的)
    valid_base = len(correct_ids) + len(incorrect_ids)
    if valid_base > 0:
        acc_eval = (len(correct_ids) / valid_base) * 100
        print(f"有效评测准确率: {acc_eval:.2f}% ({len(correct_ids)}/{valid_base})")
    else:
        print("有效评测准确率: N/A")

    # 2. 赛题整体准确率 (基于总题数 86)
    if TOTAL_QUESTIONS > 0:
        acc_total = (len(correct_ids) / TOTAL_QUESTIONS) * 100
        print(f"赛题整体准确率: {acc_total:.2f}% ({len(correct_ids)}/{TOTAL_QUESTIONS})")

    try:
        out_dir_path = Path(output_dir)
        out_dir_path.mkdir(parents=True, exist_ok=True)

        # 正确题目列表（结构化 JSON）
        correct_output_path = out_dir_path / "eval_correct_ids.json"
        correct_payload = {
            "predict_file": predict_file_path,
            "total_questions": TOTAL_QUESTIONS,
            "correct_count": len(correct_ids),
            "correct_ids": correct_ids,
        }
        with open(correct_output_path, "w", encoding="utf-8") as f:
            json.dump(correct_payload, f, ensure_ascii=False, indent=2)
        print(f"\n正确题目列表已写入: {correct_output_path}")

        # 错误题目列表（严格区分错误 / 无法评测 / 未提交）
        incorrect_output_path = out_dir_path / "eval_incorrect_ids.json"
        incorrect_payload = {
            "predict_file": predict_file_path,
            "total_questions": TOTAL_QUESTIONS,
            "incorrect_count": len(incorrect_ids),
            "incorrect_ids": incorrect_ids,
            "missing_golden_count": len(missing_golden_ids),
            "missing_golden_ids": missing_golden_ids,
            "not_submitted_count": len(not_submitted_ids),
            "not_submitted_ids": not_submitted_ids,
        }
        with open(incorrect_output_path, "w", encoding="utf-8") as f:
            json.dump(incorrect_payload, f, ensure_ascii=False, indent=2)
        print(f"错误题目列表已写入: {incorrect_output_path}")
    except Exception as e:
        print(f"\n写入评测结果列表失败: {e}")

if __name__ == '__main__':
    main()
