# -*- coding: utf-8 -*-
"""Public configuration template for the sanitized TGAC Text-to-SQL snapshot.

Copy this file to config.py for local experiments. Do not hardcode secrets in
the repository; provide credentials and private paths through environment
variables or a local-only configuration layer.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("TGAC_DATA_DIR", BASE_DIR / "data"))

FINAL_DATASET_PATH = DATA_DIR / "final_dataset.json"
SCHEMA_PATH = DATA_DIR / "schema_all_gemini.json"
GOLD_SQL_PATH = DATA_DIR / "goldsql.json"
COMMON_KNOWLEDGE_PATH = DATA_DIR / "common_knowledge2.md"
STARROCK_KNOWLEDGE_PATH = DATA_DIR / "starrock_knowledge.md"
ADDED_KNOWLEDGE_LIST_PATH = DATA_DIR / "knowledge_add_clean_list.json"
VERIFIED_KB_PATH = DATA_DIR / "correct_verified_knowledge.json"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")
OPENAI_MODEL_CORRECT = os.getenv("OPENAI_MODEL_CORRECT", OPENAI_MODEL)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_KEY_LIST = [
    key.strip()
    for key in os.getenv("GEMINI_API_KEY_LIST", "").split(",")
    if key.strip()
]
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")

DB_HOST = os.getenv("SR_HOST", "")
DB_PORT = int(os.getenv("SR_PORT", "9030"))
DB_USER = os.getenv("SR_USER", "")
DB_PASSWORD = os.getenv("SR_PASSWORD", "")
DB_DATABASE = os.getenv("SR_DATABASE", "")
DB_CONFIG = {
    "host": DB_HOST,
    "port": DB_PORT,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "database": DB_DATABASE,
}

FEWSHOT_TOP_K = int(os.getenv("FEWSHOT_TOP_K", "5"))
SIM_THRESHOLD = float(os.getenv("SIM_THRESHOLD", "0.2"))
TOP1_STRICT_THRESHOLD = float(os.getenv("TOP1_STRICT_THRESHOLD", "0.92"))
RANDOM_SEED = int(os.getenv("RANDOM_SEED", "42"))

LOGS_DIR = BASE_DIR / "logs"
RESULTS_PATH = BASE_DIR / "results.json"
FAISS_INDEX_PATH = BASE_DIR / "indexes" / "few_shot.faiss"
FAISS_META_PATH = BASE_DIR / "indexes" / "few_shot_meta.json"
RESUME_FROM_EXISTING_RESULTS = os.getenv("RESUME_FROM_EXISTING_RESULTS", "1") == "1"
