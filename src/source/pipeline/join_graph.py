"""Runtime helpers for verified table-level Join Graph evidence.

The offline discovery stage produces ``join_graph.json`` from high-similarity
column pairs that also have an observed database join result.  This module
keeps the runtime dependency-free: it validates that artifact, selects a small
high-confidence subgraph for the task tables, and renders evidence suitable for
the SQL generator and repair prompts.

The graph is intentionally advisory.  It represents observed equality joins,
not foreign-key direction, business semantics, or a mandatory join type.
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "no", "n"}:
            return False
        if normalized in {"1", "true", "yes", "y"}:
            return True
    return bool(value)


def _normalize_join_key(raw_key: Mapping[str, Any]) -> Dict[str, Any] | None:
    source_column = _text(raw_key.get("source_column") or raw_key.get("left_column"))
    target_column = _text(raw_key.get("target_column") or raw_key.get("right_column"))
    if not source_column or not target_column:
        return None
    if not _truthy(raw_key.get("sql_verified"), default=True):
        return None
    return {
        "source_column": source_column,
        "target_column": target_column,
        "jaccard_similarity": max(0.0, min(1.0, _number(raw_key.get("jaccard_similarity")))),
        "sql_verified": True,
    }


def _normalize_edge(raw_edge: Mapping[str, Any]) -> Dict[str, Any] | None:
    source = _text(raw_edge.get("source") or raw_edge.get("source_table"))
    target = _text(raw_edge.get("target") or raw_edge.get("target_table"))
    if not source or not target or source == target:
        return None

    raw_keys = raw_edge.get("join_keys") or []
    if not isinstance(raw_keys, list):
        return None
    join_keys = [key for item in raw_keys if isinstance(item, Mapping) if (key := _normalize_join_key(item))]
    if not join_keys:
        return None

    if source > target:
        source, target = target, source
        join_keys = [
            {
                "source_column": key["target_column"],
                "target_column": key["source_column"],
                "jaccard_similarity": key["jaccard_similarity"],
                "sql_verified": True,
            }
            for key in join_keys
        ]

    join_keys.sort(
        key=lambda item: (
            -item["jaccard_similarity"],
            item["source_column"],
            item["target_column"],
        ),
    )
    strongest_key = max(key["jaccard_similarity"] for key in join_keys)
    return {
        "source": source,
        "target": target,
        "weight": max(0.0, min(1.0, max(_number(raw_edge.get("weight")), strongest_key))),
        "verified_join_count": len(join_keys),
        "join_keys": join_keys,
    }


def normalize_join_graph(raw_graph: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a compact, deterministic, runtime-safe Join Graph."""
    if not isinstance(raw_graph, Mapping):
        raise ValueError("join graph root must be a JSON object")

    raw_edges = raw_graph.get("edges") or []
    if not isinstance(raw_edges, list):
        raise ValueError("join graph edges must be a list")

    edges_by_pair: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, Mapping):
            continue
        edge = _normalize_edge(raw_edge)
        if edge is None:
            continue
        pair = (edge["source"], edge["target"])
        existing = edges_by_pair.get(pair)
        if existing is None:
            edges_by_pair[pair] = edge
            continue

        existing["weight"] = max(existing["weight"], edge["weight"])
        existing["join_keys"].extend(edge["join_keys"])
        deduplicated: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for key in existing["join_keys"]:
            key_id = (key["source_column"], key["target_column"])
            previous = deduplicated.get(key_id)
            if previous is None or key["jaccard_similarity"] > previous["jaccard_similarity"]:
                deduplicated[key_id] = key
        existing["join_keys"] = sorted(
            deduplicated.values(),
            key=lambda item: (
                -item["jaccard_similarity"],
                item["source_column"],
                item["target_column"],
            ),
        )
        existing["verified_join_count"] = len(existing["join_keys"])

    edges = sorted(
        edges_by_pair.values(),
        key=lambda item: (-item["weight"], item["source"], item["target"]),
    )
    metadata = raw_graph.get("metadata") if isinstance(raw_graph.get("metadata"), Mapping) else {}
    return {
        "metadata": {
            "construction_method": metadata.get("construction_method", "unknown"),
            "node_count": len({node for edge in edges for node in (edge["source"], edge["target"])}),
            "edge_count": len(edges),
        },
        "edges": edges,
    }


def load_join_graph(path: str | Path) -> Dict[str, Any]:
    """Load and validate a graph artifact produced by the offline pipeline."""
    graph_path = Path(path)
    with graph_path.open("r", encoding="utf-8") as input_file:
        return normalize_join_graph(json.load(input_file))


def _build_adjacency(edges: Iterable[Mapping[str, Any]], min_weight: float) -> Dict[str, List[Dict[str, Any]]]:
    adjacency: Dict[str, List[Dict[str, Any]]] = {}
    for edge in edges:
        weight = _number(edge.get("weight"))
        if weight < min_weight:
            continue
        source = _text(edge.get("source"))
        target = _text(edge.get("target"))
        if not source or not target:
            continue
        adjacency.setdefault(source, []).append({"table": target, "edge": edge})
        adjacency.setdefault(target, []).append({"table": source, "edge": edge})

    for neighbors in adjacency.values():
        neighbors.sort(
            key=lambda item: (
                -_number(item["edge"].get("weight")),
                item["table"],
            ),
        )
    return adjacency


def _enumerate_paths(
    adjacency: Mapping[str, Sequence[Mapping[str, Any]]],
    source: str,
    target: str,
    max_hops: int,
) -> List[Dict[str, Any]]:
    paths: List[Dict[str, Any]] = []
    stack: List[Tuple[str, List[str], List[Mapping[str, Any]]]] = [(source, [source], [])]

    while stack:
        current, tables, edges = stack.pop()
        if current == target and edges:
            weights = [_number(edge.get("weight")) for edge in edges]
            paths.append(
                {
                    "tables": tables,
                    "edges": list(edges),
                    "bottleneck": min(weights),
                    "mean_weight": sum(weights) / len(weights),
                    "hop_count": len(edges),
                },
            )
            continue
        if len(edges) >= max_hops:
            continue

        for neighbor in adjacency.get(current, []):
            next_table = _text(neighbor.get("table"))
            edge = neighbor.get("edge")
            if not next_table or next_table in tables or not isinstance(edge, Mapping):
                continue
            stack.append((next_table, tables + [next_table], edges + [edge]))

    paths.sort(
        key=lambda item: (
            -item["bottleneck"],
            -item["mean_weight"],
            item["hop_count"],
            tuple(item["tables"]),
        ),
    )
    return paths


def build_join_context(
    graph: Mapping[str, Any] | None,
    seed_tables: Sequence[str],
    *,
    max_hops: int = 2,
    max_paths_per_pair: int = 2,
    min_weight: float = 0.8,
    max_edges: int = 8,
) -> Dict[str, Any]:
    """Select a bounded, high-confidence Join Graph subgraph for one task.

    Paths are ranked by their weakest edge first, then by average confidence,
    and finally by hop count.  This favors a short, reliable bridge over a
    longer path that contains a weak inferred join.
    """
    normalized_seed_tables = list(dict.fromkeys(_text(table) for table in seed_tables if _text(table)))
    result: Dict[str, Any] = {
        "enabled": False,
        "seed_tables": normalized_seed_tables,
        "bridge_tables": [],
        "paths": [],
        "edges": [],
        "unresolved_pairs": [],
        "graph_metadata": {},
    }
    if not graph or len(normalized_seed_tables) < 2:
        return result

    normalized_graph = normalize_join_graph(graph)
    adjacency = _build_adjacency(normalized_graph["edges"], max(0.0, min_weight))
    if not adjacency:
        return result

    selected_paths: List[Dict[str, Any]] = []
    selected_edge_ids: set[Tuple[str, str]] = set()
    unresolved_pairs: List[Tuple[str, str]] = []
    edge_by_id = {
        (edge["source"], edge["target"]): edge
        for edge in normalized_graph["edges"]
        if _number(edge.get("weight")) >= min_weight
    }

    max_hops = max(1, int(max_hops))
    max_paths_per_pair = max(1, int(max_paths_per_pair))
    max_edges = max(1, int(max_edges))
    for source, target in combinations(normalized_seed_tables, 2):
        candidate_paths = _enumerate_paths(adjacency, source, target, max_hops)
        if not candidate_paths:
            unresolved_pairs.append((source, target))
            continue
        for path in candidate_paths[:max_paths_per_pair]:
            path_edge_ids = {
                tuple(sorted((_text(edge.get("source")), _text(edge.get("target")))))
                for edge in path["edges"]
            }
            if len(selected_edge_ids | path_edge_ids) > max_edges:
                continue
            selected_edge_ids.update(path_edge_ids)
            selected_paths.append(path)

    if not selected_paths:
        result["unresolved_pairs"] = unresolved_pairs
        result["graph_metadata"] = normalized_graph["metadata"]
        return result

    selected_edges = [edge_by_id[edge_id] for edge_id in sorted(selected_edge_ids) if edge_id in edge_by_id]
    bridge_tables = sorted(
        {
            table
            for path in selected_paths
            for table in path["tables"][1:-1]
            if table not in normalized_seed_tables
        },
    )
    result.update(
        {
            "enabled": True,
            "bridge_tables": bridge_tables,
            "paths": [
                {
                    "tables": path["tables"],
                    "bottleneck": round(path["bottleneck"], 4),
                    "mean_weight": round(path["mean_weight"], 4),
                    "hop_count": path["hop_count"],
                }
                for path in selected_paths
            ],
            "edges": selected_edges,
            "unresolved_pairs": unresolved_pairs,
            "graph_metadata": normalized_graph["metadata"],
        },
    )
    return result


def format_join_graph_context(context: Mapping[str, Any], max_keys_per_edge: int = 2) -> str:
    """Render compact Join Graph evidence for generation and repair prompts."""
    edges = context.get("edges") or []
    if not isinstance(edges, list) or not edges:
        return ""

    max_keys_per_edge = max(1, int(max_keys_per_edge))
    lines = [
        "[Verified Join Graph]",
        "以下是经数据验证的等值连接证据。它不代表外键方向、基数、业务口径或必须使用的 JOIN 类型；仅当题意需要多表关联时，优先从这些谓词中选择，并仍按题意决定 JOIN/LEFT JOIN、过滤与聚合。",
    ]
    bridge_tables = context.get("bridge_tables") or []
    if bridge_tables:
        lines.append(f"可选中间表（用于连通已给表）：{', '.join(map(str, bridge_tables))}")

    paths = context.get("paths") or []
    if isinstance(paths, list):
        lines.append("推荐表路径：")
        for path in paths[:4]:
            if not isinstance(path, Mapping):
                continue
            tables = path.get("tables") or []
            if len(tables) < 2:
                continue
            lines.append(
                "- "
                + " -> ".join(map(str, tables))
                + f"（最弱边置信度 {float(path.get('bottleneck', 0.0)):.2f}）",
            )

    lines.append("可用连接谓词：")
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        source = _text(edge.get("source"))
        target = _text(edge.get("target"))
        weight = _number(edge.get("weight"))
        join_keys = edge.get("join_keys") or []
        if not source or not target or not isinstance(join_keys, list):
            continue
        displayed = 0
        for key in join_keys:
            if not isinstance(key, Mapping) or not _truthy(key.get("sql_verified"), default=True):
                continue
            source_column = _text(key.get("source_column"))
            target_column = _text(key.get("target_column"))
            if not source_column or not target_column:
                continue
            similarity = _number(key.get("jaccard_similarity"))
            lines.append(
                f"- `{source}`.`{source_column}` = `{target}`.`{target_column}` "
                f"(edge={weight:.2f}, key={similarity:.2f})",
            )
            displayed += 1
            if displayed >= max_keys_per_edge:
                break

    unresolved = context.get("unresolved_pairs") or []
    if unresolved:
        display_pairs = [f"{pair[0]} <-> {pair[1]}" for pair in unresolved[:3] if len(pair) == 2]
        if display_pairs:
            lines.append(
                "未检索到高置信路径："
                + "; ".join(display_pairs)
                + "。缺失不等于禁止关联，需结合 Schema 与业务知识判断。",
            )
    return "\n".join(lines)
