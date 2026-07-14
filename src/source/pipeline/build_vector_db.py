# -*- coding: utf-8 -*-
"""
Build a FAISS few-shot index from goldsql.json
- Normalize + mask questions
- Embed with BAAI/bge-large-zh-v1.5
- Store FAISS index and a meta json with sql_id list
Run:
  python -m t2sql.build_vector_db
"""
from __future__ import annotations
import json
import shutil
import os
from pathlib import Path
from typing import List, Dict

# Support running as module or script
if __name__ == "__main__" and __package__ is None:
    import sys
    # 将当前脚本的父目录的父目录添加到 sys.path，确保能找到 config
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from config import (
    GOLD_SQL_PATH,
    FAISS_INDEX_PATH,
    FAISS_META_PATH,
)
# 这里我们只导入必要的处理函数，不依赖 utils 里的 ensure_dirs
from utils import mask_text, embed_texts, save_faiss_index

def main():
    print("="*30)
    print("开始构建向量数据库...")

    # --- 【核心修复】在此处强制创建目录 ---
    # 直接获取 FAISS_INDEX_PATH 的父目录 (即 indexes 文件夹)
    target_dir = FAISS_INDEX_PATH.parent
    print(f"目标索引目录: {target_dir}")

    if not target_dir.exists():
        print(f"目录不存在，正在强制创建: {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        print("目录已存在，准备写入。")

    # 再次确认目录是否真的创建成功
    if not target_dir.exists():
        raise RuntimeError(f"无法创建目录: {target_dir}，请检查磁盘权限！")
    # ------------------------------------

    if not GOLD_SQL_PATH.exists():
        raise FileNotFoundError(f"goldsql.json not found at {GOLD_SQL_PATH}")

    print(f"正在读取数据: {GOLD_SQL_PATH}")
    with open(GOLD_SQL_PATH, "r", encoding="utf-8") as f:
        data: List[Dict] = json.load(f)

    masked_questions: List[str] = []
    meta: List[Dict] = []

    print("正在处理文本数据...")
    for item in data:
        if not item:
            continue
        # 仅索引 golden_sql 为 true 的样本，确保 few-shot 都来自金标数据
        if not item.get("golden_sql", True):
            continue
        q = item.get("question", "")
        sql_id = item.get("sql_id", None)
        if not q or not sql_id:
            continue
        mq = mask_text(q)
        masked_questions.append(mq)
        meta.append({"sql_id": sql_id})

    if not masked_questions:
        raise RuntimeError("No valid questions to index from goldsql.json")

    print(f"正在生成 Embedding (共 {len(masked_questions)} 条数据)...")
    vectors = embed_texts(masked_questions)

    print(f"正在保存 Index 到: {FAISS_INDEX_PATH}")
    save_faiss_index(vectors, meta)

    print("-" * 30)
    print(f"成功! 已索引 {len(masked_questions)} 条数据")
    print(f"FAISS 文件: {FAISS_INDEX_PATH}")
    print(f"META  文件: {FAISS_META_PATH}")
    print("="*30)


if __name__ == "__main__":
    main()
