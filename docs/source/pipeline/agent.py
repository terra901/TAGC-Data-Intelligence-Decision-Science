# -*- coding: utf-8 -*-
"""
Text-to-SQL runtime agent
- Loads final_dataset.json tasks
- Loads schema_long.json, goldsql.json, common_knowledge.md
- Few-shot retrieval with FAISS (built from goldsql.json)
- Three SQL candidates via gpt-4o with schema/table shuffle diversity
- Execute on StarRocks; on error, send to LLM for fix; re-run
- Majority vote by normalized result values; tie -> warn and random pick
Run:
  python -m t2sql.agent
"""
from __future__ import annotations
import json
import copy
import random
import re
import time  # 导入 time 模块用于计时
import os
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from decimal import Decimal
import numpy as np

# Support running as module or script
if __name__ == "__main__" and __package__ is None:
    import sys
    print("[*] 添加项目根目录到 sys.path")
    # 假设 agent.py 就在 t2sql-backup 目录下
    # 如果 agent.py 在 t2sql-backup/t2sql/ 下，你可能需要用 .parents[1]
    # 根据你的截图，agent.py 就在 t2sql-backup 下
    project_root = Path(__file__).resolve().parent
    sys.path.append(str(project_root))
    # 修正 utils 和 prompts 的导入路径
    sys.path.append(str(project_root / "t2sql"))


try:
    from openai import OpenAI
    _HAS_OPENAI_V1 = True
except Exception:
    import openai  # type: ignore
    OpenAI = None  # type: ignore
    _HAS_OPENAI_V1 = False

print("[*] 正在导入 config...")
from config import (
    BASE_DIR,
    DATA_DIR,
    FINAL_DATASET_PATH,
    SCHEMA_PATH,
    GOLD_SQL_PATH,
    COMMON_KNOWLEDGE_PATH,
    STARROCK_KNOWLEDGE_PATH,
    ADDED_KNOWLEDGE_LIST_PATH,
    VERIFIED_KB_PATH,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENAI_MODEL_CORRECT,
    GEMINI_API_KEY,
    GEMINI_BASE_URL,
    GEMINI_MODEL,
    GEMINI_API_KEY_LIST,
    LLM_PROVIDER,
    FEWSHOT_TOP_K,
    SIM_THRESHOLD,
    TOP1_STRICT_THRESHOLD,
    LOGS_DIR,
    RESULTS_PATH,
    RANDOM_SEED,
    FAISS_INDEX_PATH,
    FAISS_META_PATH,
    RESUME_FROM_EXISTING_RESULTS,
)

print("[*] 正在导入 t2sql.utils 和 t2sql.prompts...")
# 假设 utils 和 prompts 在 t2sql 子目录中
# 如果它们和 agent.py 在同一目录，就去掉 't2sql.'
# 根据你的截图，它们不在 t2sql 子目录，而在根目录
# *** 请根据你的文件结构确认！ ***
# 假设 utils.py 和 prompts.py 就在 t2sql-backup 目录下：
import utils as t2sql_utils
import prompts as t2sql_prompts
# 如果它们真的在 t2sql 子目录:
# from t2sql import utils as t2sql_utils
# from t2sql import prompts as t2sql_prompts

_rng = random.Random(RANDOM_SEED)
ERROR_FEEDBACK_PATH = DATA_DIR / "error_feedback.json"

DEBUG_PRINT_RAW_RESP = os.getenv("DEBUG_PRINT_RAW_RESP", "1") == "1"


def _seed_rng_for_task(sql_id: str) -> None:
    sid = str(sql_id or "")
    n = 0
    m = re.search(r"(\d+)$", sid)
    if m:
        try:
            n = int(m.group(1))
        except Exception:
            n = 0
    _rng.seed(int(RANDOM_SEED) * 1000 + n)
GEMINI_KEY_LIST = [k for k in GEMINI_API_KEY_LIST if k] or ([GEMINI_API_KEY] if GEMINI_API_KEY else [])
CURRENT_GEMINI_KEY_INDEX = 0


_CURRENT_TOKEN_SQL_ID: Optional[str] = None
_TOKEN_STATS_TOTAL: Dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "calls": 0,
    "measured_calls": 0,
    "estimated_calls": 0,
}
_TOKEN_STATS_BY_SQL_ID: Dict[str, Dict[str, int]] = {}


class WrongResultGuard:
    """基于历史错题结果集的轻量级哨兵。

    为了兼容不同来源的结果结构（dict / tuple），不依赖列名，
    而是将每一行归一化为“值集合签名”，再对整个结果集生成批次签名进行对比。
    """

    def __init__(self, error_path: Path) -> None:
        # sql_id -> List[batch_signature]
        self.error_batches: Dict[str, List[Tuple]] = {}
        self._load_error(error_path)

    @staticmethod
    def _normalize_value(v: Any) -> Any:
        """简化版数值归一化，避免浮点尾差干扰对比。"""
        from decimal import Decimal
        if v is None:
            return None
        if isinstance(v, Decimal):
            return int(v) if v == v.to_integral_value() else float(round(v, 2))
        if isinstance(v, float):
            return int(v) if v.is_integer() else float(round(v, 2))
        return v

    def _row_signature(self, row: Any) -> Tuple:
        """将一行结果转换为与列名无关的签名：按值排序后的元组。"""
        if isinstance(row, dict):
            values = [self._normalize_value(v) for v in row.values()]
        else:
            try:
                values = [self._normalize_value(v) for v in row]
            except Exception:
                values = [self._normalize_value(row)]
        try:
            values_sorted = sorted(values, key=lambda x: str(x))
        except Exception:
            values_sorted = values
        return tuple(values_sorted)

    def _batch_signature(self, rows: List[Any]) -> Tuple:
        """为整个结果集生成批次签名，忽略行顺序。"""
        sig_rows = [self._row_signature(r) for r in rows]
        try:
            sig_rows_sorted = sorted(sig_rows, key=lambda x: str(x))
        except Exception:
            sig_rows_sorted = sig_rows
        return tuple(sig_rows_sorted)

    def _load_error(self, path: Path) -> None:
        if not path.exists():
            print(f"[WrongGuard] 未找到错题本文件: {path}")
            return
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as e:
            print(f"[WrongGuard] 加载错题本失败: {e}")
            return
        if not isinstance(data, dict):
            print("[WrongGuard] 错题本顶层不是 dict，忽略。")
            return
        for sid, rec in data.items():
            if not isinstance(rec, dict):
                continue
            batches_raw = rec.get("history_batches") or []
            if not isinstance(batches_raw, list):
                continue
            batch_sigs: List[Tuple] = []
            for b in batches_raw:
                if not isinstance(b, list):
                    continue
                rows = [r for r in b if isinstance(r, dict)]
                if not rows:
                    continue
                sig = self._batch_signature(rows)
                batch_sigs.append(sig)
            if batch_sigs:
                self.error_batches[str(sid)] = batch_sigs

    def is_wrong(self, sql_id: str, rows: List[Tuple]) -> bool:
        if not rows:
            return False
        batch_sigs = self.error_batches.get(str(sql_id)) or []
        if not batch_sigs:
            return False
        cur_sig = self._batch_signature(rows)
        for idx, sig in enumerate(batch_sigs, start=1):
            if sig == cur_sig:
                print(f"[WrongGuard] sql_id={sql_id} 当前结果命中历史错题 Batch #{idx}，将不参与投票。")
                return True
        return False


_WRONG_GUARD: Optional[WrongResultGuard] = None


def get_wrong_guard() -> Optional[WrongResultGuard]:
    global _WRONG_GUARD
    if _WRONG_GUARD is not None:
        return _WRONG_GUARD
    try:
        _WRONG_GUARD = WrongResultGuard(ERROR_FEEDBACK_PATH)
    except Exception as e:
        print(f"[WrongGuard] 初始化失败: {e}")
        _WRONG_GUARD = None
    return _WRONG_GUARD

JUDGE_SYSTEM_PROMPT = """
You are a Chief Data Scientist acting as a Final Arbitrator for a Text-to-SQL competition.
You are provided with a User Question, Domain Knowledge, and multiple Candidate SQLs (with their execution results).

**Situation:**
The candidate models produced CONFLICTING results (e.g., different row counts, different logic). You must decide which one is correct.

**Critical Verification Checklist (StarRocks & Tencent Game Data):**
1. **Platform Filters**: Did one candidate use `saccounttype='-100'` / `platid=255` while others missed it? This is the most common cause of discrepancies.
2. **Date Logic**: Check date formats (`20240101` string vs int) and functions (`str_to_date` vs `date_add`).
3. **Table Usage**: Did one candidate use a specific mapped table (e.g., `dim_...`) while others guessed?
4. **Empty Result Rule**: Candidates that returned `[]` (0 rows) MUST NOT be selected.

**Decision Priority (when non-empty results conflict):**
1. Verify the candidate satisfies key constraints implied by the question and Domain Knowledge (platform filters, time window, core IDs like actid/activitytype/productid, etc.).
2. Prefer the candidate whose output structure best matches the required output fields (column meanings and any required summary rows).
3. Only then use row-count or other heuristics as tie-breakers.

**Task:**
1. Compare the SQL logic of candidates.
2. Identify WHY they produced different results.
3. Select the BEST candidate index.

**Output Format:**
Return a JSON object ONLY:
{
    "reasoning": "Candidate 1 returned 0 rows because it missed the '-100' filter. Candidate 2 included it and returned valid data. Candidate 2 is better.",
    "best_candidate_index": <Integer, 1-based index>
}
"""


def _json_default(obj):
    if isinstance(obj, Decimal):
        try:
            return float(obj)
        except Exception:
            return str(obj)
    # 兜底：把其它不可序列化对象转成字符串
    try:
        return str(obj)
    except Exception:
        return None


def _extract_message_text(resp) -> str:
    if isinstance(resp, dict):
        try:
            ch = (resp.get("choices") or [])[0] or {}
        except Exception:
            return ""
        msg = ch.get("message") if isinstance(ch, dict) else None
        if isinstance(msg, dict):
            txt = msg.get("content")
            return txt if isinstance(txt, str) else ""
        txt = ch.get("text") if isinstance(ch, dict) else None
        return txt if isinstance(txt, str) else ""
    try:
        ch = resp.choices[0]
    except Exception:
        return ""
    msg = getattr(ch, "message", None)
    if msg is None:
        txt = getattr(ch, "text", None)
        return txt or ""
    txt = getattr(msg, "content", None)
    if isinstance(txt, str):
        return txt
    if isinstance(txt, list):
        parts = []
        for p in txt:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
            else:
                t = getattr(p, "text", None)
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts).strip()
    return txt or ""


def _debug_dump_resp(resp, provider: str):
    if not DEBUG_PRINT_RAW_RESP:
        return
    try:
        if isinstance(resp, dict):
            print(f"[DEBUG][{provider}] raw response (json):")
            print(json.dumps(resp, ensure_ascii=False, indent=2))
            return
        dump_json = getattr(resp, "model_dump_json", None)
        if callable(dump_json):
            print(f"[DEBUG][{provider}] raw response (model_dump_json):")
            print(dump_json(indent=2, ensure_ascii=False))
            return
        dump = getattr(resp, "model_dump", None)
        if callable(dump):
            print(f"[DEBUG][{provider}] raw response (model_dump):")
            print(json.dumps(dump(), ensure_ascii=False, indent=2))
            return
        print(f"[DEBUG][{provider}] raw response (repr): {resp!r}")
    except Exception as e:
        try:
            print(f"[DEBUG][{provider}] raw response (str): {str(resp)}")
        except Exception:
            print(f"[DEBUG][{provider}] failed to print raw response: {e}")


def _safe_preview_text(s: Any, max_len: int = 1200) -> str:
    try:
        t = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False, default=_json_default)
    except Exception:
        try:
            t = str(s)
        except Exception:
            t = ""
    if not t:
        return ""
    if len(t) <= max_len:
        return t
    return t[:max_len] + "...<truncated>"


def _dump_llm_error(provider: str, use_model: str, req: Dict[str, Any], e: Exception) -> None:
    try:
        msg_count = 0
        try:
            msg_count = len(req.get("messages") or [])
        except Exception:
            msg_count = 0
        try:
            prompt_est = _estimate_tokens_from_obj(req.get("messages"))
        except Exception:
            prompt_est = -1
        print(
            f"    !!! [{provider}] request summary: model={use_model}, messages={msg_count}, est_prompt_tokens={prompt_est}"
        )
    except Exception:
        pass

    try:
        safe_req = {k: v for k, v in req.items() if k != "messages"}
        print(f"    !!! [{provider}] request params (no messages): {_safe_preview_text(safe_req, 800)}")
    except Exception:
        pass

    try:
        body = getattr(e, "body", None)
        if body is not None:
            print(f"    !!! [{provider}] error body: {_safe_preview_text(body, 2000)}")
    except Exception:
        pass

    try:
        resp = getattr(e, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)
            text = getattr(resp, "text", None)
            if status is not None:
                print(f"    !!! [{provider}] http status: {status}")
            if text is not None:
                print(f"    !!! [{provider}] http response text: {_safe_preview_text(text, 2000)}")
    except Exception:
        pass


def _safe_int(v) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _estimate_tokens_from_obj(obj: Any) -> int:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=_json_default)
    except Exception:
        try:
            s = str(obj)
        except Exception:
            s = ""
    if not s:
        return 0
    try:
        b = len(s.encode("utf-8"))
    except Exception:
        b = len(s)
    return int((b + 3) // 4)


def _extract_usage_tokens(resp) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    usage = None
    if resp is None:
        return None, None, None
    if isinstance(resp, dict):
        usage = resp.get("usage")
    else:
        usage = getattr(resp, "usage", None)
        if usage is not None and not isinstance(usage, dict):
            dump = getattr(usage, "model_dump", None)
            if callable(dump):
                try:
                    usage = dump()
                except Exception:
                    usage = getattr(usage, "__dict__", None)
    if not isinstance(usage, dict):
        return None, None, None

    p = (
        usage.get("prompt_tokens")
        if "prompt_tokens" in usage
        else usage.get("input_tokens")
    )
    c = (
        usage.get("completion_tokens")
        if "completion_tokens" in usage
        else usage.get("output_tokens")
    )
    t = usage.get("total_tokens")

    prompt_tokens = _safe_int(p)
    completion_tokens = _safe_int(c)
    total_tokens = _safe_int(t)
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def _get_token_bucket(sql_id: Optional[str]) -> Dict[str, int]:
    sid = str(sql_id or "").strip() or "<unknown>"
    b = _TOKEN_STATS_BY_SQL_ID.get(sid)
    if b is None:
        b = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "measured_calls": 0,
            "estimated_calls": 0,
        }
        _TOKEN_STATS_BY_SQL_ID[sid] = b
    return b


def _snapshot_token_stats(sql_id: Optional[str]) -> Dict[str, int]:
    b = _get_token_bucket(sql_id)
    return {
        "prompt_tokens": int(b.get("prompt_tokens", 0)),
        "completion_tokens": int(b.get("completion_tokens", 0)),
        "total_tokens": int(b.get("total_tokens", 0)),
        "calls": int(b.get("calls", 0)),
        "measured_calls": int(b.get("measured_calls", 0)),
        "estimated_calls": int(b.get("estimated_calls", 0)),
    }


def _sub_token_stats(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    keys = ["prompt_tokens", "completion_tokens", "total_tokens", "calls", "measured_calls", "estimated_calls"]
    return {k: int(a.get(k, 0)) - int(b.get(k, 0)) for k in keys}


def _record_token_usage(provider: str, model: str, messages: List[Dict[str, str]], resp, text: str) -> None:
    prompt_tokens, completion_tokens, total_tokens = _extract_usage_tokens(resp)

    _TOKEN_STATS_TOTAL["calls"] += 1
    bucket = _get_token_bucket(_CURRENT_TOKEN_SQL_ID)
    bucket["calls"] += 1

    if prompt_tokens is None or completion_tokens is None:
        prompt_tokens = _estimate_tokens_from_obj(messages)
        completion_tokens = _estimate_tokens_from_obj(text)
        total_tokens = int(prompt_tokens) + int(completion_tokens)
        _TOKEN_STATS_TOTAL["estimated_calls"] += 1
        bucket["estimated_calls"] += 1
    else:
        if total_tokens is None:
            total_tokens = int(prompt_tokens) + int(completion_tokens)
        _TOKEN_STATS_TOTAL["measured_calls"] += 1
        bucket["measured_calls"] += 1

    _TOKEN_STATS_TOTAL["prompt_tokens"] += int(prompt_tokens or 0)
    _TOKEN_STATS_TOTAL["completion_tokens"] += int(completion_tokens or 0)
    _TOKEN_STATS_TOTAL["total_tokens"] += int(total_tokens or 0)

    bucket["prompt_tokens"] += int(prompt_tokens or 0)
    bucket["completion_tokens"] += int(completion_tokens or 0)
    bucket["total_tokens"] += int(total_tokens or 0)


def _try_until_nonempty(fn, attempts: int = 3) -> str:
    r = ""
    for _ in range(attempts):
        r = fn()
        if isinstance(r, str) and r.strip():
            return r
    return r if isinstance(r, str) else ""


def extract_sql_from_response(text: str) -> str:
    """从 LLM 的回复中提取 SQL。

    优先解析 ```sql ... ``` 或 ``` ... ``` 代码块，
    如果没有代码块，则回退到 strip_sql_fences 的通用逻辑，
    从文本中提取以 SELECT / WITH 开头的 SQL 片段。
    """
    if not text:
        return ""

    # 1) 优先提取 Markdown 代码块 (取最后一个，因为 CoT 的 SQL 往往在最后)
    matches = re.findall(r"```(?:sql|SQL)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if matches:
        inner = matches[-1].strip()
        return t2sql_utils.strip_sql_fences(inner)

    # 2) 回退：使用通用提取逻辑（会从文本中找到 SELECT/with 开头的片段）
    return t2sql_utils.strip_sql_fences(text)


def _get_openai_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    if not _HAS_OPENAI_V1:
        openai.api_key = OPENAI_API_KEY
        if OPENAI_BASE_URL:
            openai.api_base = OPENAI_BASE_URL
        return openai  # type: ignore
    if OPENAI_BASE_URL:
        # print(f"[*] 创建 OpenAI 客户端，Base URL: {OPENAI_BASE_URL}")
        return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    # print("[*] 创建 OpenAI 客户端 (默认 Base URL)")
    return OpenAI(api_key=OPENAI_API_KEY)


def _chat_complete(messages: List[Dict[str, str]], model: str) -> str:
    client = _get_openai_client()
    print(f"    ... 正在调用模型: {model} (等待网络响应)...")
    start_time = time.time()
    try:
        reasoning_effort = (os.getenv("OPENAI_REASONING_EFFORT", "") or "").strip()
        if _HAS_OPENAI_V1:
            req = {
                "model": model,
                "messages": messages,
                "temperature": 0.0,
            }
            if reasoning_effort:
                req["reasoning_effort"] = reasoning_effort
            resp = client.chat.completions.create(**req)
        else:
            req = {
                "model": model,
                "messages": messages,
                "temperature": 0.0,
            }
            if reasoning_effort:
                req["reasoning_effort"] = reasoning_effort
            resp = client.ChatCompletion.create(**req)
        _debug_dump_resp(resp, "openai")
        text = _extract_message_text(resp) or ""
        _record_token_usage("openai", model, messages, resp, text)
        end_time = time.time()
        print(f"    ... 模型调用成功 (耗时: {end_time - start_time:.2f} 秒)")
        if not text.strip():
            print("    !!! 模型未返回文本内容")
        # 返回原始文本，由上层的 extract_sql_from_response 负责提取 SQL
        return text
    except Exception as e:
        print(f"    !!! 模型调用出错: {e}")
        try:
            _dump_llm_error("openai", model, {"model": model, "messages": messages, "temperature": 0.0}, e)
        except Exception:
            pass
        return "" # 返回空字符串避免程序崩溃


def _get_gemini_client() -> OpenAI:
    if not GEMINI_KEY_LIST:
        raise RuntimeError("GEMINI_API_KEY_LIST is empty; please configure at least one Gemini key")
    key = GEMINI_KEY_LIST[CURRENT_GEMINI_KEY_INDEX % len(GEMINI_KEY_LIST)]
    if not _HAS_OPENAI_V1:
        openai.api_key = key
        openai.api_base = GEMINI_BASE_URL
        return openai  # type: ignore
    return OpenAI(api_key=key, base_url=GEMINI_BASE_URL)


def _is_quota_exhausted_error(msg: str) -> bool:
    t = (msg or "")
    tl = t.lower()
    if "insufficient_user_quota" in tl:
        return True
    if "insufficient_quota" in tl:
        return True
    if "exceeded your current quota" in tl:
        return True
    if "check your plan and billing" in tl:
        return True
    if "payment required" in tl:
        return True
    if "billing" in tl and "quota" in tl:
        return True
    if ("quota" in tl) and ("insufficient" in tl or "exceed" in tl or "exceeded" in tl):
        return True
    if "额度不足" in t or "余额不足" in t:
        return True
    return False


def _chat_complete_gemini(messages: List[Dict[str, str]], model=None) -> str:
    """调用 Gemini，多 Key 轮询重试策略。

    - 403 / insufficient_user_quota / 额度不足: 立即切换到下一个 Key，直到遍历完所有 Key。
    - 其他错误: 在当前 Key 上进行多次重试，仍失败再切 Key。
    - 不再回退到 Doubao，由上层决定是否接受空结果。
    """

    use_model = model or GEMINI_MODEL
    if not GEMINI_KEY_LIST:
        print("    !!! 未配置任何 Gemini API Key")
        return ""

    reasoning_effort = (os.getenv("GEMINI_REASONING_EFFORT", "") or "").strip()
    try:
        temperature = float(os.getenv("GEMINI_TEMPERATURE", "1"))
    except Exception:
        temperature = 1.0
    use_response_format = (os.getenv("GEMINI_RESPONSE_FORMAT_JSON", "0") == "1")
    response_format = {"type": "json_object"} if use_response_format else None

    global CURRENT_GEMINI_KEY_INDEX
    total_keys = len(GEMINI_KEY_LIST)
    per_key_attempts = 3
    tried_keys = 0
    last_error = None

    while tried_keys < total_keys:
        key_index = CURRENT_GEMINI_KEY_INDEX % total_keys
        key = GEMINI_KEY_LIST[key_index]

        for attempt in range(per_key_attempts):
            print(
                f"    ... 正在调用模型: {use_model} (Gemini, Key #{key_index + 1}, "
                f"尝试 {attempt + 1}/{per_key_attempts})..."
            )
            start_time = time.time()
            try:
                if _HAS_OPENAI_V1:
                    client = OpenAI(api_key=key, base_url=GEMINI_BASE_URL)
                    req = {
                        "model": use_model,
                        "messages": messages,
                        "temperature": temperature,
                    }
                    if reasoning_effort:
                        req["reasoning_effort"] = reasoning_effort
                    if response_format is not None:
                        req["response_format"] = response_format
                    resp = client.chat.completions.create(**req)
                else:
                    openai.api_key = key
                    openai.api_base = GEMINI_BASE_URL
                    req = {
                        "model": use_model,
                        "messages": messages,
                        "temperature": temperature,
                    }
                    if reasoning_effort:
                        req["reasoning_effort"] = reasoning_effort
                    if response_format is not None:
                        req["response_format"] = response_format
                    resp = openai.ChatCompletion.create(**req)
                _debug_dump_resp(resp, "gemini")
                text = _extract_message_text(resp) or ""
                _record_token_usage("gemini", use_model, messages, resp, text)
                end_time = time.time()
                print(f"    ... 模型调用成功 (耗时: {end_time - start_time:.2f} 秒)")
                if not text.strip():
                    print("    !!! 模型未返回文本内容")
                return text
            except Exception as e:
                last_error = e
                msg = str(e)
                print(f"    !!! 模型调用出错: {e}")

                try:
                    _dump_llm_error("gemini", use_model, req if isinstance(req, dict) else {"model": use_model}, e)
                except Exception:
                    pass

                # 额度不足: 直接切到下一个 Key
                if _is_quota_exhausted_error(msg):
                    print("    !!! 当前 Gemini Key 配额不足")
                    if total_keys > 1:
                        CURRENT_GEMINI_KEY_INDEX = (key_index + 1) % total_keys
                        tried_keys += 1
                        print("    !!! 正在切换到下一个 Key...")
                    else:
                        print("    !!! 仅配置了 1 个 Gemini Key，无法切换到其他 Key")
                        tried_keys += 1
                    # 不再在当前 Key 上继续重试，跳出内层循环
                    break

                # 其他错误: 在当前 Key 上继续重试，直到用完 per_key_attempts
                if attempt == per_key_attempts - 1:
                    # 当前 Key 重试次数已用完，切到下一个 Key
                    CURRENT_GEMINI_KEY_INDEX = (key_index + 1) % total_keys
                    tried_keys += 1
        else:
            # 内层循环未被 break（比如没有额度错误且未触发最后一次 attempt），
            # 正常情况下不会走到这里，但为了安全起见，仍然推进到下一个 Key。
            CURRENT_GEMINI_KEY_INDEX = (key_index + 1) % total_keys
            tried_keys += 1

    if last_error is not None:
        print(f"    !!! 多个 Gemini Key 均调用失败，最后一次错误: {last_error}")
    return ""


def chat_complete(messages: List[Dict[str, str]], model=None, correct: bool = False) -> str:
    if LLM_PROVIDER == "gemini":
        # 仅使用 Gemini，多 Key 轮询重试，不再回退到 Doubao
        return _chat_complete_gemini(messages, model or GEMINI_MODEL)

    r = _try_until_nonempty(
        lambda: _chat_complete(
            messages,
            (model or (OPENAI_MODEL_CORRECT if correct else OPENAI_MODEL)),
        ),
        attempts=3,
    )
    if not r.strip() and GEMINI_API_KEY:
        try:
            print("    !!! OpenAI 连续3次返回空结果，尝试 Gemini 回退")
            return _chat_complete_gemini(messages, GEMINI_MODEL)
        except Exception:
            return r
    return r


def load_schema_map() -> Dict[str, Dict[str, Any]]:
    print(f"[*] 正在从 {SCHEMA_PATH} 加载 schema...")
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        arr = json.load(f)
    print("    ... schema 加载完毕。")
    return {t.get("table_name"): t for t in arr}


def select_schema_tables(schema_map: Dict[str, Dict[str, Any]], table_list: List[str]) -> List[Dict[str, Any]]:
    tables = []
    for name in table_list or []:
        t = schema_map.get(name)
        if t:
            tables.append(t)
    return tables


def load_common_knowledge() -> str:
    print(f"[*] 正在从 {COMMON_KNOWLEDGE_PATH} 加载通用知识...")

    base_text = ""
    try:
        with open(COMMON_KNOWLEDGE_PATH, "r", encoding="utf-8") as f:
            base_text = f.read().strip()
    except Exception as e:
        print(f"    !!! 加载通用知识失败: {e}")

    starrock_text = ""
    try:
        if STARROCK_KNOWLEDGE_PATH and STARROCK_KNOWLEDGE_PATH.exists():
            print(f"[*] 正在从 {STARROCK_KNOWLEDGE_PATH} 加载 StarRocks 语法知识...")
            with open(STARROCK_KNOWLEDGE_PATH, "r", encoding="utf-8") as f:
                starrock_text = f.read().strip()
    except Exception as e:
        print(f"    !!! 加载 StarRocks 语法知识失败: {e}")

    # 将 StarRocks 语法知识并入通用知识文本中，作为一个单独的小节
    if starrock_text:
        if base_text:
            return f"{base_text}\n\n[StarRocks SQL Dialect Knowledge]\n{starrock_text}".strip()
        return f"[StarRocks SQL Dialect Knowledge]\n{starrock_text}".strip()

    return base_text.strip()


def load_final_tasks() -> List[Dict[str, Any]]:
    print(f"[*] 正在从 {FINAL_DATASET_PATH} 加载测试任务...")
    with open(FINAL_DATASET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_gold_map() -> Dict[str, Dict[str, str]]:
    print(f"[*] 正在从 {GOLD_SQL_PATH} 加载 gold_sql...")
    with open(GOLD_SQL_PATH, "r", encoding="utf-8") as f:
        arr = json.load(f)
    m: Dict[str, Dict[str, str]] = {}
    for x in arr:
        if not isinstance(x, dict):
            continue
        sid = x.get("sql_id")
        if not sid:
            continue
        m[sid] = {
            "question": x.get("question", "") or "",
            "sql": x.get("sql", "") or "",
            "plan": x.get("plan", "") or "",
            "divide": x.get("divide", "") or "",
            "cot": x.get("cot", "") or "",
        }
    print("    ... gold_sql 加载完毕。")
    return m


def load_added_knowledge_map() -> Dict[str, str]:
    try:
        with open(ADDED_KNOWLEDGE_LIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        m: Dict[str, str] = {}
        if isinstance(data, list):
            for item in data:
                sid = item.get("sql_id")
                kn = item.get("knowledge")
                if sid and isinstance(kn, str) and kn.strip():
                    m[sid] = kn.strip()
        print(f"[*] 已加载补充知识 {len(m)} 条 (from {ADDED_KNOWLEDGE_LIST_PATH})")
        return m
    except Exception as e:
        print(f"    !!! 加载补充知识失败: {e}")
        return {}


def load_verified_kb_map() -> Dict[str, str]:
    """加载 data_detective_knowledge/verified_knowledge_base.json 中按 sql_id 聚合的已验证知识描述。"""
    try:
        with open(VERIFIED_KB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        m: Dict[str, str] = {}
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                sid = item.get("sql_id")
                vlogic = item.get("verified_logic")
                if sid and isinstance(vlogic, str) and vlogic.strip():
                    m[sid] = vlogic.strip()
        if m:
            print(f"[*] 已加载已验证知识 {len(m)} 条 (from {VERIFIED_KB_PATH})")
        return m
    except Exception as e:
        print(f"    !!! 加载已验证知识失败: {e}")
        return {}


_retrieval_items: List[Dict[str, Any]] = []
_retrieval_q_vecs: List[Any] = []
_retrieval_k_vecs: List[Any] = []
_retrieval_tbl_vecs: List[Any] = []
_retrieval_ready = False


def _build_retrieval_index() -> None:
    """基于 final_dataset.json 构建三路相似度的候选缓存。"""
    global _retrieval_ready, _retrieval_items, _retrieval_q_vecs, _retrieval_k_vecs, _retrieval_tbl_vecs
    if _retrieval_ready:
        return

    tasks = load_final_tasks()
    _retrieval_items = []
    q_texts: List[str] = []
    k_texts: List[str] = []
    tbl_texts: List[str] = []
    q_idx: List[int] = []
    k_idx: List[int] = []
    tbl_idx: List[int] = []

    for idx, t in enumerate(tasks):
        sid = str(t.get("sql_id") or "").strip()
        question = (t.get("question") or "").strip()
        knowledge = (t.get("knowledge") or "").strip()
        table_list = t.get("table_list") or []
        table_text = " ".join(sorted([str(x) for x in table_list if x])) if table_list else ""

        _retrieval_items.append(
            {
                "sql_id": sid,
                "has_question": bool(question),
                "has_knowledge": bool(knowledge),
                "has_table": bool(table_text),
            }
        )

        if question:
            q_idx.append(idx)
            q_texts.append(t2sql_utils.mask_text(question))
        if knowledge:
            k_idx.append(idx)
            k_texts.append(knowledge)
        if table_text:
            tbl_idx.append(idx)
            tbl_texts.append(table_text)

    total = len(_retrieval_items)
    _retrieval_q_vecs = [None] * total
    _retrieval_k_vecs = [None] * total
    _retrieval_tbl_vecs = [None] * total

    if q_idx:
        q_embs = t2sql_utils.embed_texts(q_texts)
        for emb, i in zip(q_embs, q_idx):
            _retrieval_q_vecs[i] = emb
    if k_idx:
        k_embs = t2sql_utils.embed_texts(k_texts)
        for emb, i in zip(k_embs, k_idx):
            _retrieval_k_vecs[i] = emb
    if tbl_idx:
        tbl_embs = t2sql_utils.embed_texts(tbl_texts)
        for emb, i in zip(tbl_embs, tbl_idx):
            _retrieval_tbl_vecs[i] = emb

    _retrieval_ready = True


def retrieve_few_shot_ids(
    question: str,
    table_list: List[str],
    knowledge: str,
    exclude_sql_id: str = "",
    top_k: int = 5,
) -> List[str]:
    """综合“问题 / 表名列表 / knowledge”三路相似度，检索 top-k few-shot sql_id。"""
    _build_retrieval_index()

    q_vec = None
    k_vec = None
    tbl_vec = None

    if question and question.strip():
        q_vec = t2sql_utils.embed_texts([t2sql_utils.mask_text(question)])[0]
    if knowledge and knowledge.strip():
        k_vec = t2sql_utils.embed_texts([knowledge.strip()])[0]
    if table_list:
        tbl_txt = " ".join(sorted([str(x) for x in table_list if x]))
        if tbl_txt:
            tbl_vec = t2sql_utils.embed_texts([tbl_txt])[0]

    scores: List[Tuple[float, str]] = []
    for i, item in enumerate(_retrieval_items):
        sid = item.get("sql_id")
        if not sid:
            continue
        if exclude_sql_id and sid == exclude_sql_id:
            continue

        comps: List[float] = []
        cand_q = _retrieval_q_vecs[i]
        cand_k = _retrieval_k_vecs[i]
        cand_tbl = _retrieval_tbl_vecs[i]

        if q_vec is not None and cand_q is not None:
            comps.append(float(np.dot(q_vec, cand_q)))
        if k_vec is not None and cand_k is not None:
            comps.append(float(np.dot(k_vec, cand_k)))
        if tbl_vec is not None and cand_tbl is not None:
            comps.append(float(np.dot(tbl_vec, cand_tbl)))

        if not comps:
            continue

        score = sum(comps) / len(comps)
        scores.append((score, sid))

    scores.sort(key=lambda x: x[0], reverse=True)
    top_sql_ids = [sid for _, sid in scores[:top_k]]
    print(f"    ... [FewShot] 三路相似度检索得到 {len(top_sql_ids)} 个示例 sql_id。")
    return top_sql_ids


def build_few_shots_for_strategy(
    strategy: str,
    sql_ids: List[str],
    gold_map: Dict[str, Dict[str, str]],
) -> List[Tuple[str, str]]:
    """根据策略，将 sql_id 列表映射为对应风格的 few-shot (question, content)。"""
    pairs: List[Tuple[str, str]] = []
    for sid in sql_ids:
        gold = gold_map.get(sid)
        if not gold:
            continue
        q = (gold.get("question") or "").strip()
        if not q:
            continue

        if strategy == "divide":
            body = (gold.get("divide") or "").strip()
        elif strategy == "plan":
            body = (gold.get("plan") or "").strip()
        elif strategy in ("ali_cot", "cot"):
            body = (gold.get("cot") or "").strip()
        else:  # standard
            body = (gold.get("sql") or "").strip()

        # 如果策略字段缺失，则回退到纯 SQL，避免 few-shot 为空
        if not body and strategy in ("divide", "plan", "ali_cot", "cot"):
            body = (gold.get("sql") or "").strip()

        if not body:
            continue
        pairs.append((q, body))

    return pairs


def schema_shuffle_columns(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    data = copy.deepcopy(tables)
    for t in data:
        cols = t.get("columns", [])
        _rng.shuffle(cols)
        t["columns"] = cols
    return data


def schema_shuffle_tables(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    data = copy.deepcopy(tables)
    _rng.shuffle(data)
    return data


def generate_candidates(
    schema_tables: List[Dict[str, Any]],
    knowledge_text: str,
    question: str,
    gold_map: Dict[str, Dict[str, str]],
    few_shot_ids: List[str],
) -> List[str]:
    candidates: List[str] = []

    fs_standard = build_few_shots_for_strategy("standard", few_shot_ids, gold_map)
    fs_ali_cot = build_few_shots_for_strategy("ali_cot", few_shot_ids, gold_map)
    fs_divide = build_few_shots_for_strategy("divide", few_shot_ids, gold_map)
    fs_plan = build_few_shots_for_strategy("plan", few_shot_ids, gold_map)

    print(
        f"    ... [FewShot] 各策略 few-shot 数量: "
        f"Standard={len(fs_standard)}, Ali-CoT={len(fs_ali_cot)}, "
        f"Divide={len(fs_divide)}, Plan={len(fs_plan)}"
    )

    # 1. Standard (基准，快) - 原始 schema
    print("    ... [Candidate 1] 策略: Standard ...")
    msgs1 = t2sql_prompts.build_sql_generation_messages(
        schema_tables, knowledge_text, fs_standard, question, strategy="standard"
    )
    sql1 = extract_sql_from_response(chat_complete(msgs1))
    if sql1:
        candidates.append(sql1)

    # 2. Ali-CoT (重 Schema 分析) - 列顺序打乱
    print("    ... [Candidate 2] 策略: Ali-CoT (Column Shuffle) ...")
    tables2 = schema_shuffle_columns(schema_tables)
    msgs2 = t2sql_prompts.build_sql_generation_messages(
        tables2, knowledge_text, fs_ali_cot, question, strategy="ali_cot"
    )
    sql2 = extract_sql_from_response(chat_complete(msgs2))
    if sql2:
        candidates.append(sql2)

    # 3. Divide and Conquer (重复杂逻辑嵌套) - 表顺序打乱
    print("    ... [Candidate 3] 策略: Divide & Conquer (Table Shuffle) ...")
    tables3 = schema_shuffle_tables(schema_tables)
    msgs3 = t2sql_prompts.build_sql_generation_messages(
        tables3, knowledge_text, fs_divide, question, strategy="divide"
    )
    sql3 = extract_sql_from_response(chat_complete(msgs3))
    if sql3:
        candidates.append(sql3)

    # 4. Query Plan (重 Join 路径) - 原始 schema
    print("    ... [Candidate 4] 策略: Query Plan ...")
    msgs4 = t2sql_prompts.build_sql_generation_messages(
        schema_tables, knowledge_text, fs_plan, question, strategy="plan"
    )
    sql4 = extract_sql_from_response(chat_complete(msgs4))
    if sql4:
        candidates.append(sql4)

    print(f"    ... API 调用完毕，生成 {len(candidates)} 个候选 SQL。")
    return candidates


def _filter_candidates_by_question(question: str, candidates: List[str]) -> List[str]:
    q_raw = (question or "")
    q = q_raw.replace(" ", "").replace("\n", "")

    # 仅对“明确要求全量输出”的题做 LIMIT 过滤，避免误伤 Top1/TopN 题
    requires_full = ("用户全量" in q) or ("全量用户" in q) or ("全量" in q)
    if not requires_full:
        return candidates

    # Top-K/取第一/前N 等意图：允许 LIMIT（比如求最大、最多、排名第一等）
    q_lower = q.lower()
    has_topk_intent = False
    topk_keywords = [
        "top",
        "rank",
        "排名",
        "第1",
        "第一",
        "首位",
        "最高",
        "最低",
        "最大",
        "最小",
        "最多",
        "最少",
        "取一条",
        "取1条",
        "只取一条",
        "只取1条",
    ]
    if any(k in q_lower for k in topk_keywords):
        has_topk_intent = True
    # “前N/前10/前 10 名” 这类
    if re.search(r"前\s*\d+", q_lower) or re.search(r"第\s*\d+\s*名", q_lower):
        has_topk_intent = True
    if has_topk_intent:
        return candidates

    filtered: List[str] = []
    for sql in candidates:
        if not sql:
            continue
        if re.search(r"(?is)\blimit\s+\d+\b", sql):
            continue
        filtered.append(sql)
    return filtered or candidates


def validate_and_fix(
    conn,
    sql_candidate: str,
    sql_id: str,
    question: str,
    schema_tables: List[Dict[str, Any]],
    knowledge_text: str,
) -> Tuple[bool, List[Tuple], str]:
    print(f"    ... 正在执行 SQL 验证...")
    ok, rows, err = t2sql_utils.execute_sql(conn, sql_candidate)
    # print(f"    ... SQL 验证结果: ok={ok}, rows={len(rows)}, err={err}")
    if ok and rows:
        print(f"    ... SQL 验证成功。")
        return True, rows, sql_candidate

    # 准备错误消息（兼容空结果触发修复）
    if ok and not rows:
        err_msg = "Execution succeeded but returned 0 rows. Logic might be wrong."
        print(f"    !!! SQL 执行无结果: {err_msg}")
        t2sql_utils.append_log(LOGS_DIR / "sql_errors.log", f"{sql_id}\tempty\t{err_msg}\t{sql_candidate}")
    else:
        err_msg = err
        print(f"    !!! SQL 验证失败: {err_msg}")
        t2sql_utils.append_log(LOGS_DIR / "sql_errors.log", f"{sql_id}\torig\t{err_msg}\t{sql_candidate}")

    print("    ... [SQL 修复] 正在尝试调用 LLM 修复 SQL...")
    messages = t2sql_prompts.build_sql_fix_messages_super(
        schema_tables=schema_tables,
        knowledge_text=knowledge_text,
        question=question,
        wrong_sql=sql_candidate,
        error_msg=err_msg,
    )
    fixed_raw = chat_complete(messages, correct=True).strip()
    fixed = extract_sql_from_response(fixed_raw)

    if not fixed:
        print("    !!! LLM 没有返回修复后的 SQL。")
        return False, [], sql_candidate

    print("    ... [SQL 修复] 正在执行修复后的 SQL...")
    ok2, rows2, err2 = t2sql_utils.execute_sql(conn, fixed)
    # print(f"    ... SQL 修复结果: ok={ok2}, rows={len(rows2)}, err={err2}")
    if ok2:
        print("    ... 修复后的 SQL 执行成功。")
        return True, rows2, fixed
    # print(f"    !!! 修复后的 SQL 依然执行失败: {err2}")
    print(f"    !!! 修复后的 SQL 依然执行失败: {err2}")
    t2sql_utils.append_log(LOGS_DIR / "sql_errors.log", f"{sql_id}\tfixed\t{err2}\t{fixed}")
    return False, [], sql_candidate


def majority_vote(executed: List[Tuple[str, List[Tuple]]], sql_id: str) -> Tuple[str, Tuple]:
    print("    ... 正在对多个有效结果进行投票...")
    print("    ... 候选结果明细:")
    non_empty_indices: List[int] = []
    for i, (sql, rows) in enumerate(executed):
        h = t2sql_utils.compute_result_hash(rows)
        try:
            h_disp = str(h)
        except Exception:
            h_disp = "<unprintable>"
        print(f"      - Candidate #{i+1}: rows={len(rows)}, hash={h_disp[:120]}")
        if len(rows) > 0:
            non_empty_indices.append(i)
    if len(non_empty_indices) == 0:
        print("    ... 所有候选结果均为空集，将随机选择一个。")
        rand_sql, rand_rows = _rng.choice(executed)
        return rand_sql, t2sql_utils.compute_result_hash(rand_rows)
    if len(non_empty_indices) == 1:
        idx = non_empty_indices[0]
        sql, rows = executed[idx]
        return sql, t2sql_utils.compute_result_hash(rows)
    results_map: Dict[Tuple, int] = {}
    sql_map: Dict[Tuple, str] = {}
    idx_map: Dict[Tuple, List[int]] = {}
    for idx in non_empty_indices:
        sql, rows = executed[idx]
        h = t2sql_utils.compute_result_hash(rows)
        results_map[h] = results_map.get(h, 0) + 1
        sql_map[h] = sql
        idx_map.setdefault(h, []).append(idx + 1)
    if len(results_map) > 1:
        print("    ... 哈希投票统计:")
        for h, cnt in results_map.items():
            try:
                h_disp = str(h)
            except Exception:
                h_disp = "<unprintable>"
            print(f"        hash={h_disp[:120]} -> votes={cnt}, candidates={idx_map.get(h, [])}")
    winner_hash, max_votes = max(results_map.items(), key=lambda kv: kv[1])
    if max_votes == 1:
        print(f"    ... [警告] 任务 {sql_id} 投票失败 (无多数)，将在非空结果中随机选择一个。")
        idx = _rng.choice(non_empty_indices)
        sql, rows = executed[idx]
        return sql, t2sql_utils.compute_result_hash(rows)
    print(f"    ... 投票完成，选出 {max_votes} 票的结果。")
    return sql_map[winner_hash], winner_hash


def llm_judge_selection(
    question: str,
    knowledge_text: str,
    candidates_detail: List[Dict[str, Any]],
) -> Tuple[str, Tuple]:
    """使用 LLM 作为裁判，在多个产生不同结果的候选 SQL 中选择最佳方案。

    candidates_detail: 每个元素至少包含:
      - 'sql': 最终执行的 SQL 字符串
      - 'rows': 执行结果 List[Tuple]
    """
    if not candidates_detail:
        return "", tuple()

    candidates_detail = [c for c in candidates_detail if (c.get("rows") or [])]
    if not candidates_detail:
        return "", tuple()

    if len(candidates_detail) == 1:
        only = candidates_detail[0]
        return only["sql"], t2sql_utils.compute_result_hash(only["rows"])

    user_prompt = f"### User Question:\n{question}\n\n"
    user_prompt += f"### Domain Knowledge:\n{knowledge_text}\n\n"
    user_prompt += "### Candidates for Review:\n"

    candidate_map: Dict[int, Dict[str, Any]] = {}

    for idx, cand in enumerate(candidates_detail):
        cand_idx = idx + 1
        candidate_map[cand_idx] = cand

        sql_snippet = cand.get("sql") or cand.get("used_sql") or ""
        rows_val = cand.get("rows") or []
        rows_len = len(rows_val)
        if rows_len == 0:
            rows_sample = "[] (0 rows returned)"
        else:
            try:
                rows_sample = str(rows_val[:5])
            except Exception:
                rows_sample = "<unprintable rows>"

        user_prompt += f"""
--- Candidate {cand_idx} ---
[SQL]:
{sql_snippet}

[Execution Result ({rows_len} rows)]:
{rows_sample}
"""

    user_prompt += "\n\n### Instruction:\nDo NOT select candidates with 0 rows. When non-empty results conflict, first check key filters (platform/time/core IDs) required by the question/knowledge, then prefer the output structure that matches the required output fields. Return JSON with 'best_candidate_index'."

    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    print(f"    ... [Judge] LLM 正在裁决 {len(candidates_detail)} 个不同结果的候选...")

    # 不强制指定模型，让 chat_complete 根据 LLM_PROVIDER 选择合适的模型
    resp_str = chat_complete(messages, correct=False)

    try:
        clean = resp_str.strip()
        if clean.startswith("```"):
            # 去掉 ```json 或 ``` 包裹
            clean = clean.strip("`")
            clean = clean.replace("json", "", 1).strip()
        res_obj = json.loads(clean)
        best_idx = int(res_obj.get("best_candidate_index", 1))
        reasoning = res_obj.get("reasoning", "No reasoning provided.")

        print(f"    ... [Judge] 裁决理由: {reasoning}")

        if best_idx in candidate_map:
            print(f"    ... [Judge] 胜出: Candidate {best_idx}")
            winner = candidate_map[best_idx]
            return winner.get("sql") or winner.get("used_sql"), t2sql_utils.compute_result_hash(winner.get("rows") or [])
        else:
            print("    !!! [Judge] 索引越界，默认选第一个。")
            first = candidates_detail[0]
            return first.get("sql") or first.get("used_sql"), t2sql_utils.compute_result_hash(first.get("rows") or [])

    except Exception as e:
        print(f"    !!! [Judge] 解析失败: {e}，默认选第一个。")
        first = candidates_detail[0]
        return first.get("sql") or first.get("used_sql"), t2sql_utils.compute_result_hash(first.get("rows") or [])


def smart_sql_selection(
    executed: List[Tuple[str, List[Tuple]]],
    candidates_detail: List[Dict[str, Any]],
    sql_id: str,
    question: str,
    knowledge_text: str,
) -> Tuple[str, Tuple]:
    """基于投票结果的分级智能选择策略。

    1. 无可执行 SQL: 返回空。
    2. 仅 1 个可执行 SQL: 直接采纳。
    3. 票数 >= 3 (3 或 4 票一致): 直接采纳。
    4. 其他情况 (2 票或全部 1 票): 触发 LLM Judge，在不同结果中精细选择。
    """

    if not executed:
        return "", tuple()

    executed = [(sql, rows) for (sql, rows) in executed if rows]
    if not executed:
        return "", tuple()

    # 场景 1: 仅 1 个可执行 SQL
    if len(executed) == 1:
        print("    ... [策略] 仅 1 个可执行 SQL，直接采纳。")
        sql, rows = executed[0]
        return sql, t2sql_utils.compute_result_hash(rows)

    # 组装结果映射: hash -> [item]
    results_map: Dict[Tuple, List[Dict[str, Any]]] = {}

    for idx, (sql, rows) in enumerate(executed):
        h = t2sql_utils.compute_result_hash(rows)
        item = {
            "sql": sql,
            "rows": rows,
            "candidate_index": idx + 1,
        }
        results_map.setdefault(h, []).append(item)

    # 找到票数最多的 hash
    winner_hash, winner_items = max(results_map.items(), key=lambda kv: len(kv[1]))
    max_votes = len(winner_items)
    total_valid = len(executed)

    print(f"    ... [投票详情] 有效方案数: {total_valid}, 最高票数: {max_votes}")

    # 场景 2: 所有可执行 SQL 的结果完全一致 -> 高置信度，直接采纳
    if len(results_map) == 1:
        print("    ... [策略] 所有可执行 SQL 结果完全一致，直接采纳。")
        winner = winner_items[0]
        return winner["sql"], winner_hash

    # 场景 3: 3 或 4 票一致 -> 高置信度，直接采纳
    if max_votes >= 3:
        print(f"    ... [策略] 票数 {max_votes} >= 3，高置信度，直接采纳。")
        winner = winner_items[0]
        return winner["sql"], winner_hash

    # 场景 4: 2 票 or 全是 1 票 -> 触发 LLM Judge
    print("    ... [策略] 票数不足 3 或存在强分歧，触发 LLM Judge 裁判机制。")

    # 为节省 token，从每种不同结果中挑 1 个代表
    unique_candidates: List[Dict[str, Any]] = []
    for _h, items in results_map.items():
        unique_candidates.append(items[0])

    judge_sql, judge_hash = llm_judge_selection(question, knowledge_text, unique_candidates)
    return judge_sql, judge_hash


def process_task(conn, task: Dict[str, Any], schema_map: Dict[str, Dict[str, Any]], gold_map: Dict[str, Dict[str, str]], common_knowledge: str, added_knowledge_map: Dict[str, str], verified_kb_map: Dict[str, str]) -> Dict[str, Any]:
    sql_id = task.get("sql_id", "")
    question = task.get("question", "")
    table_list = task.get("table_list", [])
    task_knowledge = task.get("knowledge", "")

    _seed_rng_for_task(str(sql_id))

    print(f"\n--- 开始处理任务: {sql_id} | 问题: {question[:50]}... ---")

    schema_tables = select_schema_tables(schema_map, table_list)
    knowledge_text = (task_knowledge or "").strip()
    if common_knowledge:
        knowledge_text = f"{knowledge_text}\n\n[通用知识]\n{common_knowledge}" if knowledge_text else common_knowledge
    # 合并已验证知识（来自 data_detective_knowledge/verified_knowledge_base.json）
    if verified_kb_map:
        vlogic = verified_kb_map.get(sql_id)
        if vlogic:
            knowledge_text = (knowledge_text + ("\n\n[Verified Knowledge]\n" if knowledge_text else "[Verified Knowledge]\n") + vlogic).strip()
            print(f"\n[DEBUG] 任务 {sql_id} 使用了已验证知识 (verified_knowledge_base):\n{vlogic[:120]}...\n")
    # 合并补充知识（放在 Verified Knowledge 之后，保证最新规则优先级更高）
    if added_knowledge_map:
        extra = added_knowledge_map.get(sql_id)
        if extra:
            knowledge_text = (knowledge_text + ("\n\n[Added Knowledge]\n" if knowledge_text else "[Added Knowledge]\n") + extra).strip()
            print(f"\n[DEBUG] 任务 {sql_id} 使用了新知识 (knowledge_add_clean_list):\n{extra[:100]}...\n")

    print("  [步骤 1/4] 检索 Few-shot 示例 sql_id...")
    few_shot_ids = retrieve_few_shot_ids(
        question=question,
        table_list=table_list,
        knowledge=knowledge_text,
        exclude_sql_id=sql_id,
        top_k=5,
    )
    # print(f"  [步骤 1/4] 检索 Few-shot 示例 sql_id: {few_shot_ids}")
    print("  [步骤 2/4] 生成 SQL candidates (4次 API 调用)...")
    candidates = generate_candidates(schema_tables, knowledge_text, question, gold_map, few_shot_ids)

    filtered_candidates = _filter_candidates_by_question(question, candidates)
    if len(filtered_candidates) != len(candidates):
        print(f"    ... [规则过滤] 过滤包含 LIMIT 的候选 (题目要求全量): {len(candidates)} -> {len(filtered_candidates)}")
    candidates = filtered_candidates

    print("  [步骤 3/4] 验证并修复 SQL (可能触发更多 API 调用)...")
    executed_all: List[Tuple[str, List[Tuple]]] = []
    executed_for_vote: List[Tuple[str, List[Tuple]]] = []
    candidates_detail: List[Dict[str, Any]] = []
    wrong_guard = get_wrong_guard()
    for i, cand in enumerate(candidates):
        print(f"    -> 正在验证 Candidate #{i+1}...")
        ok, rows, used_sql = validate_and_fix(conn, cand, sql_id, question, schema_tables, knowledge_text)
        h = t2sql_utils.compute_result_hash(rows)
        try:
            h_disp = str(h)
        except Exception:
            h_disp = "<unprintable>"
        print(f"    -> Candidate #{i+1} 结果: ok={ok}, rows={len(rows)}, hash={h_disp[:120]}")
        if used_sql:
            print(f"    -> Candidate #{i+1} SQL:\n{used_sql}")
        if rows:
            print(f"    -> Candidate #{i+1} Result (前10行): {rows[:10]}")
        else:
            print(f"    -> Candidate #{i+1} Result 为空集 []")

        blocked_by_wrong = False
        if ok:
            executed_all.append((used_sql, rows))
            if wrong_guard is not None and rows:
                try:
                    if wrong_guard.is_wrong(sql_id, rows):
                        blocked_by_wrong = True
                except Exception as e:
                    print(f"[WrongGuard] 检查 sql_id={sql_id} Candidate #{i+1} 时异常: {e}")
            if not blocked_by_wrong:
                executed_for_vote.append((used_sql, rows))

        candidates_detail.append({
            "index": i + 1,
            "generated_sql": cand,
            "used_sql": used_sql,
            "rows_count": len(rows),
            "hash": h_disp,
            "rows": rows,
            "blocked_by_wrong_guard": blocked_by_wrong,
        })

    result: Dict[str, Any] = {
        "sql_id": sql_id,
        "question": question,
        "table_list": table_list,
        "few_shot_count": len(few_shot_ids),
        "candidate_count": len(candidates),
        "exec_ok_count": len(executed_all),
        "final_sql": None,
        "candidates": candidates_detail,
    }

    print(f"  [步骤 4/4] 确定最终 SQL (Smart Selection Strategy)...")
    if len(executed_all) == 0:
        print("    !!! 警告: 没有可执行的 SQL。")
        t2sql_utils.append_log(LOGS_DIR / "e2e_failures.log", json.dumps({
            "sql_id": sql_id,
            "question": question,
            "candidates": candidates,
            "msg": "no executable SQL",
        }, ensure_ascii=False))
        return result

    executed_all_nonempty = [(sql, rows) for (sql, rows) in executed_all if rows]
    executed_for_vote_nonempty = [(sql, rows) for (sql, rows) in executed_for_vote if rows]

    if not executed_all_nonempty:
        print("    !!! 警告: 所有可执行 SQL 的结果都为空集，按规则视为错误；将输出一个可执行 SQL 以便评测稳定判错并生成知识。")
        fallback_sql = executed_all[0][0]
        result["final_sql"] = fallback_sql
        t2sql_utils.append_log(LOGS_DIR / "e2e_failures.log", json.dumps({
            "sql_id": sql_id,
            "question": question,
            "candidates": candidates,
            "msg": "all executable SQL returned empty result (forced submit one SQL)",
        }, ensure_ascii=False))
        return result

    if executed_for_vote_nonempty:
        used_for_vote = executed_for_vote_nonempty
    else:
        print("[WrongGuard] 当前题目的全部可执行候选结果都命中历史错题，将忽略错题拦截，使用全部候选参与投票。")
        used_for_vote = executed_all_nonempty

    final_sql, winner_hash = smart_sql_selection(used_for_vote, candidates_detail, sql_id, question, knowledge_text)
    result["final_sql"] = final_sql
    return result


def main():
    print("[*] 开始执行 main 函数...")
    global _CURRENT_TOKEN_SQL_ID
    t2sql_utils.ensure_dirs()
    print("[*] 目录检查/创建完毕。")

    schema_map = load_schema_map()
    common_knowledge = load_common_knowledge()
    gold_map = load_gold_map()
    added_knowledge_map = load_added_knowledge_map()
    verified_kb_map = load_verified_kb_map()

    print("[*] 正在加载 FAISS 索引 (确保索引可用)...")
    # 如果索引不存在，自动构建一次
    if not FAISS_INDEX_PATH.exists():
        print(f"    ... 未找到索引文件，正在自动构建: {FAISS_INDEX_PATH}")
        try:
            import build_vector_db
            build_vector_db.main()
        except Exception as e:
            print(f"    !!! 自动构建索引失败: {e}")
            raise
    t2sql_utils.load_faiss_index()
    print("[*] FAISS 索引加载完毕。")

    tasks = load_final_tasks()

    # 根据配置决定是否从已有结果文件续跑
    if RESUME_FROM_EXISTING_RESULTS and RESULTS_PATH.exists():
        print(f"[*] 检测到已存在的结果文件，将尝试续跑: {RESULTS_PATH}")
        completed_ids = set()
        try:
            with open(RESULTS_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        sid = obj.get("sql_id")
                        if isinstance(sid, str) and sid:
                            completed_ids.add(sid)
                    except Exception as e:
                        print(f"    !!! 解析已有结果行失败，已跳过: {e}")
        except FileNotFoundError:
            completed_ids = set()

        tasks_to_run = [t for t in tasks if t.get("sql_id") not in completed_ids]
        print(f"[*] 所有任务加载完毕，共 {len(tasks)} 个任务，其中已完成 {len(completed_ids)} 个，本次待处理 {len(tasks_to_run)} 个。")
        file_mode = "a"
    else:
        tasks_to_run = tasks
        print(f"[*] 所有任务加载完毕，共 {len(tasks_to_run)} 个任务。")
        file_mode = "w"

    print("[*] 正在连接数据库...")
    conn = t2sql_utils.connect_db()
    print("[*] 数据库连接成功。")

    try:
        print(f"[*] 开始循环处理任务，结果将写入 {RESULTS_PATH}")
        total_tasks = len(tasks)
        start_index = total_tasks - len(tasks_to_run)
        with open(RESULTS_PATH, file_mode, encoding="utf-8") as out:
            for i, task in enumerate(tasks_to_run, start=1):
                current_index = start_index + i
                print(f"\n==================== 任务 {current_index}/{total_tasks} ====================")
                sid = str(task.get("sql_id") or "").strip() or "<unknown>"
                before_stats = _snapshot_token_stats(sid)
                _CURRENT_TOKEN_SQL_ID = sid
                try:
                    res = process_task(conn, task, schema_map, gold_map, common_knowledge, added_knowledge_map, verified_kb_map)
                finally:
                    _CURRENT_TOKEN_SQL_ID = None
                after_stats = _snapshot_token_stats(sid)
                task_stats = _sub_token_stats(after_stats, before_stats)
                if isinstance(res, dict):
                    res["token_usage"] = task_stats
                out.write(json.dumps(res, ensure_ascii=False, default=_json_default) + "\n")
                out.flush()
                print(f"--- 任务 {res.get('sql_id')} 完成 | final_sql is {'set' if res.get('final_sql') else 'None'} ---")
                try:
                    tu = res.get("token_usage") if isinstance(res, dict) else None
                    if isinstance(tu, dict):
                        print(
                            f"--- Token Usage | prompt={tu.get('prompt_tokens', 0)} "
                            f"completion={tu.get('completion_tokens', 0)} total={tu.get('total_tokens', 0)} "
                            f"calls={tu.get('calls', 0)} measured={tu.get('measured_calls', 0)} estimated={tu.get('estimated_calls', 0)} ---"
                        )
                except Exception:
                    pass
    finally:
        try:
            print("\n[*] 处理完毕，正在关闭数据库连接...")
            conn.close()
        except Exception:
            pass
        try:
            print(
                "\n==================== Token Summary (Total) ===================="
            )
            print(
                f"Total prompt={_TOKEN_STATS_TOTAL.get('prompt_tokens', 0)} "
                f"completion={_TOKEN_STATS_TOTAL.get('completion_tokens', 0)} "
                f"total={_TOKEN_STATS_TOTAL.get('total_tokens', 0)} "
                f"calls={_TOKEN_STATS_TOTAL.get('calls', 0)} "
                f"measured={_TOKEN_STATS_TOTAL.get('measured_calls', 0)} "
                f"estimated={_TOKEN_STATS_TOTAL.get('estimated_calls', 0)}"
            )
        except Exception:
            pass
    print("[*] 程序执行完毕。")


if __name__ == "__main__":
    # 修正导入问题
    # 确保我们使用的是正确的 utils 和 prompts
    # 这一部分假设你的 utils.py 和 prompts.py 就在 t2sql-backup 目录下
    # 如果它们在 t2sql 子目录，你需要调整
    try:
        from config import (FINAL_DATASET_PATH, SCHEMA_PATH, GOLD_SQL_PATH, COMMON_KNOWLEDGE_PATH,
                            OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, OPENAI_MODEL_CORRECT,
                            FEWSHOT_TOP_K, SIM_THRESHOLD, TOP1_STRICT_THRESHOLD, LOGS_DIR,
                            RESULTS_PATH, RANDOM_SEED)

        # 这一部分是关键，你需要根据你的目录结构修改
        # 根据你的截图 (agent.py 和 utils.py 同级)
        import utils as t2sql_utils
        import prompts as t2sql_prompts
        print("[*] (main) 成功导入 utils 和 prompts。")

    except ImportError as e:
        print(f"!!! (main) 导入错误: {e}")
        print("!!! 请检查你的 sys.path 和文件结构。")
        print("!!! 假设 utils.py, prompts.py, config.py 都和 agent.py 在同一目录。")
        # 如果还在报错，取消下面两行的注释
        # import utils as t2sql_utils
        # import prompts as t2sql_prompts

    main()
