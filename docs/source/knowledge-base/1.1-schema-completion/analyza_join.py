import mysql.connector
import json
import os
import glob
import itertools
from datasketch import MinHash
from tqdm import tqdm
import warnings
import decimal

# --- 1. 配置 ---

# 数据库连接配置 (从 profile 脚本复制而来)
DB_CONFIG = {
    'host': 'DB_HOST_FROM_ENV',
    'port': 9030,
    'user': 'root',
    'password': '',
    'database': 'final_algorithm_competition'  # 确保这个数据库名是正确的
}

# 你的 JSON 文件路径
DATASET_FILE = 'final_dataset.json'      # 包含 table_list 的 JSON 文件
PROFILING_DIR = 'profiling_output_per_table' # 从这个目录读取剖析数据
OUTPUT_FILE = 'join_candidates_verified.json' # 将结果保存到这个文件

# 算法配置
MINHASH_PERMUTATIONS = 128               # 必须与阶段1脚本中的值一致
JACCARD_THRESHOLD = 0.8                  # Jaccard 相似度高于 80% 才被认为是候选
# 1. 按照你的要求，将基数阈值改为 1
MIN_CARDINALITY_FOR_JOIN = 1             # 关键：字段的唯一值必须大于 1 才能被视为“连接键”

# --- 2. 加载所有剖析数据 ---

def load_all_profiles():
    """
    加载剖析目录中的所有 JSON 文件，并按表名组织。
    返回: dict[table_name, list_of_column_profiles]
    """
    all_profiles_by_table = {}
    json_files = glob.glob(os.path.join(PROFILING_DIR, "*.json"))

    if not json_files:
        print(f"!! 错误：在 '{PROFILING_DIR}' 目录中未找到任何 JSON 文件。")
        print("请先运行 'profile_database_per_table.py' 脚本。")
        return None

    print(f"正在从 {len(json_files)} 个表剖析文件中加载列信息...")

    for f_path in tqdm(json_files, desc="加载剖析文件"):
        try:
            with open(f_path, 'r', encoding='utf-8') as f:
                table_profile = json.load(f)

            table_name = table_profile.get("table_name")
            total_records = table_profile.get("total_records", 0)

            if total_records == 0 or not table_name:
                continue # 跳过空表

            table_columns = []
            for col_profile in table_profile.get("columns_profile", []):
                signature = col_profile.get("minhash_signature")
                cardinality = col_profile.get("cardinality")

                # ** 关键：只有当 MinHash 存在, 且基数大于阈值时，才将其视为候选键 **
                if signature and cardinality and cardinality > MIN_CARDINALITY_FOR_JOIN:
                    # 将 MinHash 签名（列表）转换回 MinHash 对象
                    try:
                        col_profile["minhash_obj"] = MinHash(num_perm=MINHASH_PERMUTATIONS, hashvalues=signature)
                        table_columns.append(col_profile)
                    except ValueError:
                        print(f"警告：{table_name}.{col_profile.get('column_name')} 的签名无效。")

            if table_columns:
                all_profiles_by_table[table_name] = table_columns

        except Exception as e:
            print(f"警告：无法加载或解析文件 {f_path}: {e}")

    print(f"加载完成。发现 {len(all_profiles_by_table)} 个非空表的候选连接键。")
    return all_profiles_by_table

# --- 3. 实时数据库验证 (新函数) ---

def verify_join_with_db(t1_name, c1_name, t2_name, c2_name, conn):
    """
    2. 尝试在数据库中真实执行一个 JOIN LIMIT 1 查询来验证连接。
    """
    query = f"""
        SELECT 1
        FROM `{t1_name}` AS t1
        INNER JOIN `{t2_name}` AS t2 ON t1.`{c1_name}` = t2.`{c2_name}`
        LIMIT 1
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(query)
            cursor.fetchone() # 尝试获取数据
        return True # 查询成功
    except mysql.connector.Error as e:
        # 如果 JOIN 失败（例如类型不匹配），则返回 False
        # print(f"  验证失败: {t1_name}.{c1_name} x {t2_name}.{c2_name} | 错误: {e}")
        return False
    except Exception as e:
        # 其他未知错误
        # print(f"  验证时发生未知错误: {e}")
        return False

# --- 4. 比较 MinHash (修改后) ---

def analyze_join_candidates(table_a_name, table_a_cols, table_b_name, table_b_cols, conn):
    """
    比较两个特定表的所有候选键。
    """
    candidates = []

    for col_a in table_a_cols:
        for col_b in table_b_cols:

            col_a_name = col_a["column_name"]
            col_b_name = col_b["column_name"]

            try:
                # 计算 Jaccard 相似度
                jaccard = col_a["minhash_obj"].jaccard(col_b["minhash_obj"])

                # 如果相似度高于阈值...
                if jaccard >= JACCARD_THRESHOLD:

                    # 2. 启动数据库实时验证
                    is_verified = verify_join_with_db(
                        table_a_name, col_a_name,
                        table_b_name, col_b_name,
                        conn
                    )

                    if is_verified:
                        candidates.append({
                            "column_A": f"{table_a_name}.{col_a_name}",
                            "column_B": f"{table_b_name}.{col_b_name}",
                            "jaccard_similarity": round(jaccard, 4),
                            "cardinality_A": col_a["cardinality"],
                            "cardinality_B": col_b["cardinality"],
                            "verified_by_db_join": True
                        })
                    # else:
                        # (可选) 记录那些 MinHash 相似但 JOIN 失败的
                        # print(f"  MinHash 相似但 JOIN 失败: {table_a_name}.{col_a_name} <-> {table_b_name}.{col_b_name}")

            except Exception as e:
                print(f"警告：比较 {table_a_name}.{col_a_name} 和 {table_b_name}.{col_b_name} 时出错: {e}")

    return candidates

# --- 5. 主函数 (已重构) ---

def main():
    # 加载所有剖析数据到内存
    all_profiles_by_table = load_all_profiles()
    if not all_profiles_by_table:
        return

    # 3. 加载目标分析文件
    try:
        with open(DATASET_FILE, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
        # 假设文件内容是一个列表
        if not isinstance(tasks, list):
            tasks = [tasks]
    except FileNotFoundError:
        print(f"!! 错误：未找到 {DATASET_FILE}。请创建此文件。")
        print(f"文件示例内容:\n[ {json.dumps(json.loads(user_request_snippet), indent=4)} ]")
        return
    except Exception as e:
        print(f"!! 错误：加载 {DATASET_FILE} 失败: {e}")
        return

    all_join_candidates = []
    processed_pairs = set() # 用于避免重复分析相同的表对

    conn = None
    try:
        # 建立数据库连接，用于验证
        print(f"正在连接到 {DB_CONFIG['host']}:{DB_CONFIG['port']} 以验证 JOIN...")
        conn = mysql.connector.connect(**DB_CONFIG)
        print("连接成功。")

        # 3. 按目标遍历
        for task in tqdm(tasks, desc="分析任务"):
            table_list = task.get("table_list")
            if not table_list or len(table_list) < 2:
                continue

            # 为 list 中的所有表创建唯一的配对
            for table_a_name, table_b_name in itertools.combinations(table_list, 2):
                pair_key = tuple(sorted((table_a_name, table_b_name)))
                if pair_key in processed_pairs:
                    continue # 已经分析过这对
                processed_pairs.add(pair_key)

                # 从内存中获取剖析数据
                table_a_cols = all_profiles_by_table.get(table_a_name)
                table_b_cols = all_profiles_by_table.get(table_b_name)

                if not table_a_cols:
                    print(f"警告：未找到表 '{table_a_name}' 的剖析数据，跳过。")
                    continue
                if not table_b_cols:
                    print(f"警告：未找到表 '{table_b_name}' 的剖析数据，跳过。")
                    continue

                # 运行连接分析
                candidates = analyze_join_candidates(
                    table_a_name, table_a_cols,
                    table_b_name, table_b_cols,
                    conn
                )
                all_join_candidates.extend(candidates)

    except mysql.connector.Error as err:
        print(f"!! 严重错误: 数据库连接失败: {err}")
        return
    finally:
        if conn and conn.is_connected():
            conn.close()
            print("\n数据库连接已关闭。")

    # --- 6. 保存结果 ---

    # 最终去重（以防万一）
    unique_candidates = {json.dumps(dict(sorted(c.items()))): c for c in all_join_candidates}.values()
    sorted_candidates = sorted(unique_candidates, key=lambda x: x["jaccard_similarity"], reverse=True)

    print("="*50)
    print(f"分析完成。发现 {len(sorted_candidates)} 个 *已验证* 的潜在连接路径：")

    # 打印前 20 个结果
    for cand in sorted_candidates[:20]:
        print(f"  - {cand['jaccard_similarity'] * 100:.1f}%: {cand['column_A']} <-> {cand['column_B']}")

    if len(sorted_candidates) > 20:
        print(f"  ... (以及其他 {len(sorted_candidates) - 20} 个)")

    # 保存到 JSON
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(sorted_candidates, f, ensure_ascii=False, indent=4)
        print(f"\n完整结果已保存到: {os.path.abspath(OUTPUT_FILE)}")
    except Exception as e:
        print(f"\n!! 严重错误: 无法写入 JSON 文件 {OUTPUT_FILE}: {e}")

if __name__ == "__main__":
    # 用于在 main() 中访问用户请求的片段
    user_request_snippet = """
    {
        "sql_id": "sql_1",
        "question": "...",
        "table_list": [
            "dws_mgamejp_login_user_activity_di",
            "dim_vplayerid_vies_df"
        ]
    }
    """
    main()
