import mysql.connector
import json
import os
import re
from datasketch import MinHash
from tqdm import tqdm
import warnings
import decimal

# --- 1. 配置 ---

# !! 必须修改 !!
# !! 请在下面填入你的数据库名称 !!
DB_CONFIG = {
    'host': 'DB_HOST_FROM_ENV',
    'port': 9030,
    'user': 'root',
    'password': '',
    'database': 'final_algorithm_competition'  # <--- 你已经正确填写
}

SCHEMA_FILE = 'schema.json'
OUTPUT_DIR = 'profiling_output_per_table' # 改了输出目录名以避免混淆
MINHASH_PERMUTATIONS = 128
TOP_K_COUNT = 10
PREFIX_COUNT = 5
CHARSET_SAMPLE_SIZE = 100
MINHASH_SAMPLE_SIZE = 10000 # 论文中提到 N=10000

# --- 2. 帮助函数 ---

# 用于分析字符集的正则表达式
REGEX_NUMERIC = re.compile(r'^[0-9\.\,\-]+$') # 允许数字、小数点、逗号、负号
REGEX_ALPHA = re.compile(r'^[a-zA-Z]+$')
REGEX_ALPHANUM = re.compile(r'^[a-zA-Z0-9]+$')

def analyze_charset(samples):
    """
    对采样数据进行字符集分析。
    """
    stats = {
        "count": len(samples),
        "all_numeric": True,
        "all_alpha": True,
        "all_alphanumeric": True,
        "json_like_pct": 0.0,
        "other_special_char_pct": 0.0
    }
    if not samples:
        return {"error": "no samples found"}

    json_like_count = 0
    other_count = 0

    for s in samples:
        s_str = str(s) # 确保是字符串
        if not REGEX_NUMERIC.match(s_str):
            stats["all_numeric"] = False
        if not REGEX_ALPHA.match(s_str):
            stats["all_alpha"] = False
        if not s_str.isalnum():
            stats["all_alphanumeric"] = False

        if (s_str.startswith('{') and s_str.endswith('}')) or (s_str.startswith('[') and s_str.endswith(']')):
            json_like_count += 1

        # 如果不是字母数字，也不是纯数字，也不是纯字母
        if not s_str.isalnum() and not REGEX_NUMERIC.match(s_str) and not REGEX_ALPHA.match(s_str):
             other_count += 1

    # 确保在除法前检查 len(samples) > 0
    if len(samples) > 0:
        stats["json_like_pct"] = round((json_like_count / len(samples)) * 100, 2)
        stats["other_special_char_pct"] = round((other_count / len(samples)) * 100, 2)

    # 清理布尔值
    if stats["all_numeric"]:
        stats["all_alpha"] = False
        stats["all_alphanumeric"] = False

    return stats

def get_minhash(samples):
    """
    为采样数据计算 MinHash 签名。
    """
    m = MinHash(num_perm=MINHASH_PERMUTATIONS)
    if not samples:
        return None
    for d in samples:
        if d is not None:
            m.update(str(d).encode('utf8'))

    # 返回 hashvalues 列表，以便 JSON 序列化
    return m.hashvalues.tolist()

def safe_json_serialize(obj):
    """
    安全地将数据库类型（如 Decimal）转换为 JSON 兼容类型。
    """
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        try:
            return obj.decode('utf-8')
        except UnicodeDecodeError:
            return obj.hex()
    return str(obj)

# --- 3. 主剖析函数 ---

def profile_database():
    """
    连接到数据库并对 schema.json 中定义的每个表/列进行剖析。
    """
    # 忽略 mysql-connector 的特定警告
    # (已根据你之前的反馈注释掉)
    # warnings.filterwarnings("ignore", category=mysql.connector.errors.MySQLWarning)

    # 检查数据库名是否已填写 (已根据你之前的反馈修复)
    if DB_CONFIG['database'] == 'PLEASE_FILL_IN_DATABASE_NAME':
        print("="*50)
        print("!! 错误：请在脚本中设置 `DATABASE_NAME` 变量。")
        print("="*50)
        return

    # 创建输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"剖析结果将保存到: {os.path.abspath(OUTPUT_DIR)}")

    # 加载 schema
    try:
        with open(SCHEMA_FILE, 'r', encoding='utf-8') as f:
            schema = json.load(f)
    except FileNotFoundError:
        print(f"!! 错误：未找到 {SCHEMA_FILE}。请确保它与脚本在同一目录中。")
        return
    except json.JSONDecodeError:
        print(f"!! 错误：{SCHEMA_FILE} 不是一个有效的 JSON 文件。")
        return

    print(f"已加载 schema，包含 {len(schema)} 个表。")

    conn = None
    try:
        # --- 建立连接 ---
        print(f"正在连接到 {DB_CONFIG['host']}:{DB_CONFIG['port']}...")
        conn = mysql.connector.connect(**DB_CONFIG)
        print("连接成功。")

        # --- 表循环 ---
        for table in tqdm(schema, desc="总进度 (表)"):
            table_name = table['table_name']

            # --- 表级查询：总行数 ---
            total_records = 0
            try:
                with conn.cursor() as cursor:
                    # 使用反引号 ` ` 来转义表名
                    cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
                    total_records = cursor.fetchone()[0]
            except Exception as e:
                print(f"错误：无法获取表 {table_name} 的总行数: {e}")
                print("跳过此表...")

                # *** 新增：即使表出错，也为该表生成一个错误报告 ***
                error_profile = {
                    "table_name": table_name,
                    "table_description": table.get("table_description", ""),
                    "total_records": 0,
                    "errors": [f"无法获取表总行数: {e}"],
                    "columns_profile": []
                }
                output_filename = os.path.join(OUTPUT_DIR, f"profile_{table_name}.json")
                try:
                    with open(output_filename, 'w', encoding='utf-8') as f:
                        json.dump(error_profile, f, ensure_ascii=False, indent=4)
                except Exception as write_e:
                    print(f"!! 严重错误: 无法写入错误 JSON 文件 {output_filename}: {write_e}")

                continue # 跳到下一个表

            # *** 新增：用于存储该表所有列剖析结果的列表 ***
            all_columns_profile = []

            # --- 列循环 ---
            for column in tqdm(table['columns'], desc=f"剖析 {table_name}", leave=False):
                col_name = column['col']
                col_type = column['type']

                # 为 StarRocks 的复杂类型（如 BITMAP）跳过大多数分析
                is_complex_type = col_type.lower() in ['bitmap', 'hll', 'percentile', 'json']

                # 初始化 *单个列* 的剖析结果字典
                column_profile = {
                    "column_name": col_name,
                    "column_type": col_type,
                    "null_values": None,
                    "cardinality": None,
                    "shape": {
                        "min_length": None,
                        "max_length": None,
                        "avg_length": None,
                        "min_value": None,
                        "max_value": None,
                        "charset_analysis": None,
                        "top_prefixes": None
                    },
                    "top_k_values": None,
                    "minhash_signature": None,
                    "errors": []
                }

                # --- 组合查询（NULL, 基数, 长度统计） ---
                if not is_complex_type:
                    try:
                        q1_sql = f"""
                            SELECT
                                COUNT(CASE WHEN `{col_name}` IS NULL THEN 1 END),
                                COUNT(DISTINCT `{col_name}`),
                                MIN(LENGTH(CAST(`{col_name}` AS STRING))),
                                MAX(LENGTH(CAST(`{col_name}` AS STRING))),
                                AVG(LENGTH(CAST(`{col_name}` AS STRING)))
                            FROM `{table_name}`
                        """
                        with conn.cursor() as cursor:
                            cursor.execute(q1_sql)
                            res = cursor.fetchone()
                            column_profile["null_values"] = int(res[0])
                            column_profile["cardinality"] = int(res[1])
                            column_profile["shape"]["min_length"] = int(res[2]) if res[2] is not None else None
                            column_profile["shape"]["max_length"] = int(res[3]) if res[3] is not None else None
                            column_profile["shape"]["avg_length"] = float(res[4]) if res[4] is not None else None
                    except Exception as e:
                        error_msg = f"查询 1 (Combined Stats) 失败: {e}"
                        print(f"警告: {table_name}.{col_name} - {error_msg}")
                        column_profile["errors"].append(error_msg)

                # --- Min/Max 查询（不 CAST，以获取正确的数字/日期排序） ---
                if not is_complex_type:
                    try:
                        q_minmax_sql = f"SELECT MIN(`{col_name}`), MAX(`{col_name}`) FROM `{table_name}` WHERE `{col_name}` IS NOT NULL"
                        with conn.cursor() as cursor:
                            cursor.execute(q_minmax_sql)
                            res = cursor.fetchone()
                            column_profile["shape"]["min_value"] = safe_json_serialize(res[0])
                            column_profile["shape"]["max_value"] = safe_json_serialize(res[1])
                    except Exception as e:
                        error_msg = f"查询 1.5 (Min/Max) 失败: {e}"
                        print(f"警告: {table_name}.{col_name} - {error_msg}")
                        column_profile["errors"].append(error_msg)

                # --- Top-K 查询 ---
                try:
                    q2_sql = f"SELECT `{col_name}`, COUNT(*) as Freq FROM `{table_name}` WHERE `{col_name}` IS NOT NULL GROUP BY `{col_name}` ORDER BY Freq DESC LIMIT {TOP_K_COUNT}"
                    with conn.cursor() as cursor:
                        cursor.execute(q2_sql)
                        column_profile["top_k_values"] = [
                            [safe_json_serialize(row[0]), int(row[1])] for row in cursor.fetchall()
                        ]
                except Exception as e:
                    error_msg = f"查询 2 (Top-K) 失败: {e}"
                    print(f"警告: {table_name}.{col_name} - {error_msg}")
                    column_profile["errors"].append(error_msg)

                # --- 前缀查询 ---
                if not is_complex_type:
                    try:
                        q3_sql = f"SELECT LEFT(CAST(`{col_name}` AS STRING), 1) as Prefix, COUNT(*) as Freq FROM `{table_name}` WHERE `{col_name}` IS NOT NULL GROUP BY Prefix ORDER BY Freq DESC LIMIT {PREFIX_COUNT}"
                        with conn.cursor() as cursor:
                            cursor.execute(q3_sql)
                            column_profile["shape"]["top_prefixes"] = [
                                [safe_json_serialize(row[0]), int(row[1])] for row in cursor.fetchall()
                            ]
                    except Exception as e:
                        error_msg = f"查询 3 (Prefixes) 失败: {e}"
                        print(f"警告: {table_name}.{col_name} - {error_msg}")
                        column_profile["errors"].append(error_msg)

                # --- 采样查询 (用于 Charset 和 MinHash) ---
                samples_for_charset = []
                samples_for_minhash = []
                try:
                    q4_sql = f"SELECT `{col_name}` FROM `{table_name}` WHERE `{col_name}` IS NOT NULL LIMIT {CHARSET_SAMPLE_SIZE}"
                    with conn.cursor() as cursor:
                        cursor.execute(q4_sql)
                        samples_for_charset = [row[0] for row in cursor.fetchall() if row[0] is not None]

                    q5_sql = f"SELECT DISTINCT `{col_name}` FROM `{table_name}` WHERE `{col_name}` IS NOT NULL LIMIT {MINHASH_SAMPLE_SIZE}"
                    with conn.cursor() as cursor:
                        cursor.execute(q5_sql)
                        samples_for_minhash = [row[0] for row in cursor.fetchall() if row[0] is not None]

                except Exception as e:
                    error_msg = f"查询 4/5 (Sampling) 失败: {e}"
                    print(f"警告: {table_name}.{col_name} - {error_msg}")
                    column_profile["errors"].append(error_msg)

                # --- Python 后处理 ---
                if samples_for_charset:
                    column_profile["shape"]["charset_analysis"] = analyze_charset(samples_for_charset)

                if samples_for_minhash:
                    column_profile["minhash_signature"] = get_minhash(samples_for_minhash)

                # *** 新增：将此列的剖析结果添加到表的总列表中 ***
                all_columns_profile.append(column_profile)

            # --- (列循环结束) ---

            # *** 新增：创建该表的最终聚合 JSON ***
            final_table_profile = {
                "table_name": table_name,
                "table_description": table.get("table_description", ""), # 从 schema.json 中获取描述
                "total_records": int(total_records),
                "errors": [], # 记录表级别的错误（这里是空的，因为我们在行数检查时 continue 了）
                "columns_profile": all_columns_profile
            }

            # *** 新增：写入此 *表* 的 JSON 文件 ***
            output_filename = os.path.join(OUTPUT_DIR, f"profile_{table_name}.json")
            try:
                with open(output_filename, 'w', encoding='utf-8') as f:
                    json.dump(final_table_profile, f, ensure_ascii=False, indent=4)
            except Exception as e:
                print(f"!! 严重错误: 无法写入 JSON 文件 {output_filename}: {e}")

        # --- (表循环结束) ---

    except mysql.connector.Error as err:
        print(f"!! 严重错误: 数据库连接失败: {err}")
        if err.errno == 1045: # Access denied
            print("请检查你的用户名和密码。")
        elif err.errno == 1049: # Unknown database
             print(f"数据库 '{DB_CONFIG['database']}' 不存在。请检查 `DATABASE_NAME` 变量。")
        elif err.errno == 2003: # Can't connect
            print(f"无法连接到 {DB_CONFIG['host']}:{DB_CONFIG['port']}。请检查主机、端口和网络防火墙。")
    finally:
        if conn and conn.is_connected():
            conn.close()
            print("\n数据库连接已关闭。")

    print("="*50)
    print("数据库剖析完成！")
    print(f"请在 '{OUTPUT_DIR}' 目录中查看生成的 JSON 文件。")
    print("="*50)

# --- 4. 运行脚本 ---
if __name__ == "__main__":
    profile_database()
