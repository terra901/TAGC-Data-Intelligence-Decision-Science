import json
import os
import re
import unicodedata
import random
from pathlib import Path
from typing import List, Tuple, Dict, Any

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
import pymysql
import shutil

from config import (
    INDEX_DIR,
    FAISS_INDEX_PATH,
    FAISS_META_PATH,
    EMBEDDING_MODEL_NAME,
    DB_HOST,
    DB_PORT,
    DB_USER,
    DB_PASSWORD,
    DB_DATABASE,
    DB_CONNECT_TIMEOUT,
    DB_READ_TIMEOUT,
    DB_WRITE_TIMEOUT,
    LOGS_DIR,
    RANDOM_SEED,
)

_model = None
_index = None
_meta = None
_rng = random.Random(RANDOM_SEED)

def ensure_dirs():
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

def normalize_text(text: str) -> str:
    if text is None:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.lower()
    return text

_GUID_RE = re.compile(r"\b[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}\b", re.IGNORECASE)
_DATE_SEP_RE = re.compile(r"(?<!\d)\d{4}([./-])\d{1,2}\1\d{1,2}(?!\d)")
# 2️⃣ 紧凑8位日期：20250702
_DATE_8_RE = re.compile(r"(?<!\d)\d{8}(?!\d)")
# 3️⃣ 中文日期：2024年5月02日、25年4月
_DATE_CN_RE = re.compile(
    r"(?<!\d)(\d{2,4})年(\d{1,2})月(\d{1,2}日)?"
)
# 4️⃣ 范围日期（包含 “到”、“-”、“~”、中文破折号等）
_DATE_RANGE_RE = re.compile(
    r"("
    r"(?:(?:\d{2,4}[./-]\d{1,2}[./-]\d{1,2})|(?:\d{8})|(?:\d{2,4}年\d{1,2}月\d{0,2}日?))"
    r"\s*(?:-|~|—|到)\s*"
    r"(?:(?:\d{2,4}[./-]\d{1,2}[./-]\d{1,2})|(?:\d{8})|(?:\d{2,4}年\d{1,2}月\d{0,2}日?))"
    r")"
)
_DATETIME_10_RE = re.compile(r"\b\d{10}\b")
_DATETIME_12_RE = re.compile(r"\b\d{12}\b")
_DATETIME_14_RE = re.compile(r"\b\d{14}\b")
_LONG_ID_RE = re.compile(r"\b\d{9,}\b")
_NUM_3PLUS_RE = re.compile(r"(?<![a-zA-Z])\b\d{3,}\b")


def mask_text(text: str) -> str:
    s = normalize_text(text)
    s = _DATE_RANGE_RE.sub("[DATE_RANGE]", s)  # 范围要先替换，防止被拆分
    s = _DATE_SEP_RE.sub("[DATE]", s)
    s = _DATE_8_RE.sub("[DATE]", s)
    s = _DATE_CN_RE.sub("[DATE]", s)
    s = _GUID_RE.sub("[ID]", s)
    s = _DATETIME_14_RE.sub("[DATETIME]", s)
    s = _DATETIME_12_RE.sub("[DATETIME]", s)
    s = _DATETIME_10_RE.sub("[DATETIME]", s)

    s = _LONG_ID_RE.sub("[ID]", s)
    s = _NUM_3PLUS_RE.sub("[NUM]", s)
    return s


def get_embedder() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def embed_texts(texts: List[str]) -> np.ndarray:
    model = get_embedder()
    emb = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
    return emb.astype("float32")


def save_faiss_index(vectors: np.ndarray, meta: List[Dict[str, Any]]):
    # 1. 确保目标文件夹存在 (双重保险)
    target_dir = FAISS_INDEX_PATH.parent
    if not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)

    dim = vectors.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vectors)

    # --- 【核心修复】解决 Faiss 不支持中文路径的问题 ---
    # 策略：先保存到一个纯英文的临时路径，再移动过去

    # 尝试构建一个安全的临时路径，例如 D:\Desktop\temp_faiss.index
    # 我们利用 pathlib 获取驱动器和根目录
    try:
        # 获取类似 "D:\\Desktop" 这样的前缀（假设它是英文的）
        # 如果你的 Desktop 也是中文，代码会自动尝试放在 D 盘根目录
        safe_root = Path(os.path.abspath(os.sep)) # 获取根目录，如 D:\
        if len(FAISS_INDEX_PATH.parts) > 2 and FAISS_INDEX_PATH.parts[1].isascii():
             safe_root = Path(FAISS_INDEX_PATH.parts[0]) / FAISS_INDEX_PATH.parts[1]

        temp_path = safe_root / "temp_few_shot.faiss"
        temp_path_str = str(temp_path)

        print(f"[Info] 由于 Faiss 不支持中文路径，正在写入临时文件: {temp_path_str}")
        faiss.write_index(index, temp_path_str)

        print(f"[Info] 正在将索引移动到最终位置: {FAISS_INDEX_PATH}")
        # 如果目标文件已存在，先删除，防止 move 报错
        if FAISS_INDEX_PATH.exists():
            os.remove(FAISS_INDEX_PATH)

        shutil.move(temp_path_str, str(FAISS_INDEX_PATH))
        print("[Success] 索引保存成功！")

    except Exception as e:
        print(f"[Error] 保存索引失败: {e}")
        # 如果上面的方法失败，尝试在当前运行目录生成一个 temp.faiss (如果当前目录含中文也可能失败)
        raise e

    # 保存元数据 (JSON 不受中文路径影响)
    with open(FAISS_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def load_faiss_index():
    global _index, _meta
    if _index is None:
        try:
            _index = faiss.read_index(str(FAISS_INDEX_PATH))
        except Exception as e:
            # 读失败时（多为中文路径问题），尝试复制到纯英文临时路径后再读
            if FAISS_INDEX_PATH.exists():
                try:
                    safe_root = Path(os.path.abspath(os.sep))
                    if len(FAISS_INDEX_PATH.parts) > 2 and FAISS_INDEX_PATH.parts[1].isascii():
                        safe_root = Path(FAISS_INDEX_PATH.parts[0]) / FAISS_INDEX_PATH.parts[1]
                    temp_read = safe_root / "temp_few_shot_read.faiss"
                    shutil.copyfile(str(FAISS_INDEX_PATH), str(temp_read))
                    print(f"[Info] 由于 Faiss 不支持中文路径，已复制到临时文件读取: {temp_read}")
                    _index = faiss.read_index(str(temp_read))
                    try:
                        os.remove(temp_read)
                    except Exception:
                        pass
                except Exception as e2:
                    print(f"[Error] 读取索引失败: {e2}")
                    raise e
            else:
                raise e
    if _meta is None:
        with open(FAISS_META_PATH, "r", encoding="utf-8") as f:
            _meta = json.load(f)
    return _index, _meta


def faiss_search(query_emb: np.ndarray, top_k: int) -> List[Tuple[int, float]]:
    index, _ = load_faiss_index()
    D, I = index.search(query_emb.reshape(1, -1), top_k)
    return [(int(I[0][i]), float(D[0][i])) for i in range(len(I[0])) if int(I[0][i]) != -1]


def connect_db():
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_DATABASE,
        connect_timeout=DB_CONNECT_TIMEOUT,
        read_timeout=DB_READ_TIMEOUT,
        write_timeout=DB_WRITE_TIMEOUT,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
    )
    return conn


def execute_sql(conn, sql: str) -> Tuple[bool, List[Tuple], str]:
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            return True, list(rows), ""
    except Exception as e:
        return False, [], str(e)


def normalize_cell(v: Any) -> Any:
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="ignore")
    # Align with evaluation normalization to reduce false candidate disagreements
    try:
        from decimal import Decimal
        from datetime import date, datetime
        if isinstance(v, Decimal):
            return int(v) if v == v.to_integral_value() else float(round(float(v), 2))
        if isinstance(v, float):
            return int(v) if v.is_integer() else float(round(v, 2))
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        if isinstance(v, str):
            s = v.strip()
            if s and s.replace('.', '', 1).isdigit():
                try:
                    f = float(s)
                    return int(f) if f.is_integer() else float(f)
                except Exception:
                    return v
    except Exception:
        return v
    return v


def compute_result_hash(rows: List[Tuple]) -> Tuple:
    norm_rows = [tuple(normalize_cell(x) for x in r) for r in rows]
    try:
        sortable = sorted(norm_rows)
    except Exception:
        sortable = norm_rows
    return tuple(sortable)


def append_log(path: Path, text: str):
    ensure_dirs()
    with open(path, "a", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")


def strip_sql_fences(s: str) -> str:
    s = s.strip()

    # 特判：LLM 按 Query Plan 风格输出，包含 "Final Optimized SQL Query:"
    # 优先在该标记之后的子串中抽取真正的 SQL，避免误抓前面的英文说明里的 "Select ..."。
    m_final = re.search(r"Final\s+Optimized\s+SQL\s+Query\s*:?", s, re.IGNORECASE)
    if m_final:
        sub = s[m_final.end():].lstrip()

        # 1) 先在子串中找 ```sql ... ``` / ```...``` 代码块
        m = re.search(r"```(?:sql|SQL)?\s*([\s\S]*?)\s*```", sub)
        if m:
            return m.group(1).strip()
        if sub.startswith("```"):
            sub2 = re.sub(r"^```(?:sql|SQL)?", "", sub).strip()
            if sub2.endswith("```"):
                sub2 = sub2[:-3].strip()
            return sub2.strip()

        # 2) 再在子串中搜索以 SELECT / WITH 开头的 SQL
        m2 = re.search(r"(?is)\b(select|with)\b[\s\S]*", sub)
        if m2:
            return m2.group(0).strip()
        # 如果子串里没找到，就继续走下面对原串的通用逻辑

    # 通用逻辑：优先解析 ```sql ... ``` 代码块
    m = re.search(r"```(?:sql|SQL)?\s*([\s\S]*?)\s*```", s)
    if m:
        return m.group(1).strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:sql|SQL)?", "", s).strip()
        if s.endswith("```"):
            s = s[:-3].strip()
        return s.strip()

    # 回退：从文本中找到以 SELECT / WITH 开头的片段
    m2 = re.search(r"(?is)\b(select|with)\b[\s\S]*", s)
    if m2:
        return m2.group(0).strip()
    return s
