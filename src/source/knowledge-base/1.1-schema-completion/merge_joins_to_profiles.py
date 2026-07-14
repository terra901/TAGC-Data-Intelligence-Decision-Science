import json
import os
import glob
from tqdm import tqdm
from collections import defaultdict

# --- 1. 配置 ---

PROFILING_DIR = 'profiling_output_per_table'     # 剖析文件所在的目录
JOIN_CANDIDATES_FILE = 'join_candidates_verified.json' # 连接键分析的结果文件
OUTPUT_DIR = 'profiling_output_merged'           # 【新】将合并后的文件保存到这个新目录

# --- 2. 主函数 ---

def merge_join_info_to_profiles():
    """
    将 join_candidates_verified.json 的结果合并回剖析文件，
    并保存到新的输出目录。
    """

    # --- 步骤 1: 加载连接键数据 ---
    try:
        with open(JOIN_CANDIDATES_FILE, 'r', encoding='utf-8') as f:
            join_candidates = json.load(f)
    except FileNotFoundError:
        print(f"!! 错误：未找到 {JOIN_CANDIDATES_FILE}。")
        print("请先运行 'analyze_joins.py' 脚本。")
        return
    except Exception as e:
        print(f"!! 错误：加载 {JOIN_CANDIDATES_FILE} 失败: {e}")
        return

    print(f"已加载 {len(join_candidates)} 条已验证的连接候选。")

    # --- 步骤 2: 构建连接地图 ---
    join_map = defaultdict(list)

    for cand in join_candidates:
        col_a = cand["column_A"]
        col_b = cand["column_B"]

        # 为 A 创建链接信息（指向 B）
        join_map[col_a].append({
            "link_column": col_b,
            "jaccard_similarity": cand["jaccard_similarity"],
            "cardinality_A": cand["cardinality_A"],
            "cardinality_B": cand["cardinality_B"],
            "verified_by_db_join": cand["verified_by_db_join"]
        })

        # 为 B 创建链接信息（指向 A）
        join_map[col_b].append({
            "link_column": col_a,
            "jaccard_similarity": cand["jaccard_similarity"],
            "cardinality_A": cand["cardinality_B"], # 注意 A/B 颠倒
            "cardinality_B": cand["cardinality_A"], # 注意 A/B 颠倒
            "verified_by_db_join": cand["verified_by_db_join"]
        })

    print(f"已构建 {len(join_map)} 个字段的连接地图。")

    # --- 步骤 3: 遍历、更新并写入新文件 ---

    # 【新】创建新的输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"合并后的文件将保存到: {os.path.abspath(OUTPUT_DIR)}")

    json_files = glob.glob(os.path.join(PROFILING_DIR, "*.json"))

    if not json_files:
        print(f"!! 错误：在 '{PROFILING_DIR}' 中未找到任何剖析文件。")
        return

    print(f"正在读取 {len(json_files)} 个表剖析文件并生成新文件...")

    for f_path in tqdm(json_files, desc="合并剖析文件"):
        try:
            # 读取原始文件
            with open(f_path, 'r', encoding='utf-8') as f:
                table_profile = json.load(f)

            table_name = table_profile.get("table_name")
            if not table_name:
                continue

            # 遍历该表的每一列
            for col_profile in table_profile.get("columns_profile", []):
                col_name = col_profile.get("column_name")
                full_name = f"{table_name}.{col_name}"

                # (可选) 清理旧的 "link_column" 键
                if "link_column" in col_profile:
                    del col_profile["link_column"]

                # 检查此列是否有连接信息
                if full_name in join_map:
                    # 添加“potential_links”列表
                    sorted_links = sorted(join_map[full_name], key=lambda x: x["jaccard_similarity"], reverse=True)
                    col_profile["potential_links"] = sorted_links # 存储为列表

                # 无论是否有连接，都删除 MinHash
                if "minhash_signature" in col_profile:
                    del col_profile["minhash_signature"]

            # 5. 【新】写入 *新* 文件
            # 从原始路径中获取文件名
            original_filename = os.path.basename(f_path)
            # 构建新的输出路径
            output_filename = os.path.join(OUTPUT_DIR, original_filename)

            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(table_profile, f, ensure_ascii=False, indent=4)

        except Exception as e:
            print(f"警告：处理文件 {f_path} 失败: {e}")

    print("="*50)
    print("合并完成！")
    print(f"'{OUTPUT_DIR}' 目录下的所有新 JSON 文件均已创建。")
    print(f"原始目录 '{PROFILING_DIR}' 未被修改。")
    print("="*50)

if __name__ == "__main__":
    merge_join_info_to_profiles()
