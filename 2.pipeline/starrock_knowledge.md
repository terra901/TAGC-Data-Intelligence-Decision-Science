# Core Guide to the StarRocks SQL Dialect

StarRocks is a high-performance MPP analytical database. Compared with MySQL or Hive, it imposes stricter requirements on syntax, alias scope, correlated subqueries, set-operation ordering, and execution-plan complexity. The following rules are intended to reduce execution failures in Text-to-SQL generation.

## 1. Basic Syntax, Quoting, and Reserved Words

### String Literals

Use single quotes for string values. Double quotes are reserved for database identifiers such as table or column names.

```sql
-- Incorrect
split_part(col, "_", 2)

-- Correct
split_part(col, '_', 2)
```

### Reserved Words

Do not use StarRocks reserved words as table aliases or column aliases. Avoid aliases such as `map`, `array`, `row`, `rank`, and `group`.

```sql
-- Incorrect
FROM table_name AS map

-- Correct
FROM table_name AS t_map
```

## 2. Column Qualification and Scope

### Ambiguity in Multi-Table Queries

In multi-table queries, qualify all columns in `SELECT`, `WHERE`, `GROUP BY`, and join predicates with explicit table aliases.

```sql
-- Incorrect
SELECT dt FROM t1 JOIN t2 ON ...

-- Correct
SELECT t1.dt FROM t1 JOIN t2 ON ...
```

### Alias Visibility

A column alias defined in a `SELECT` list cannot be referenced in the same query level. Use a CTE or subquery layer.

```sql
WITH t AS (
    SELECT count(*) AS cnt FROM table_name
)
SELECT cnt, cnt * 2 FROM t;
```

## 3. Set Operations and Sorting

Complex `CASE WHEN` expressions directly inside `ORDER BY` after `UNION ALL` may cause type-inference failures. Precompute a sorting field inside each branch, or wrap the union and sort by a column name.

```sql
SELECT * FROM (
    SELECT name, 0 AS sort_id FROM t1
    UNION ALL
    SELECT name, 1 AS sort_id FROM t2
) t_final
ORDER BY sort_id, name;
```

## 4. Aggregation and Logical Pitfalls

Do not group directly by aggregate expressions. If a grouping depends on an aggregate result, first compute the aggregate in a CTE, then perform the second-stage aggregation.

```sql
WITH user_stats AS (
    SELECT user_id, count(*) AS win_count
    FROM table_name
    GROUP BY user_id
)
SELECT
    CASE WHEN win_count > 10 THEN 'High' ELSE 'Low' END AS level,
    count(user_id) AS user_count
FROM user_stats
GROUP BY CASE WHEN win_count > 10 THEN 'High' ELSE 'Low' END;
```

Scalar subqueries in the `SELECT` list must return exactly one row and one column. If this cannot be guaranteed, rewrite the logic using joins and explicit aggregation.

## 5. Join and Subquery Restrictions

Non-equality correlated subqueries are difficult for MPP optimizers and should be avoided. Prefer window functions or explicit joins.

```sql
-- Prefer a window-function or join formulation over non-equality correlation.
AVG(value) OVER (PARTITION BY id ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
```

## 6. Function Substitutions

- Replace MySQL `FIELD(col, ...)` custom ordering with a `CASE WHEN` expression.
- Convert compact date strings before applying date functions, for example `last_day(str_to_date('20230101', '%Y%m%d'))`.
- Use `split_part(str, ',', 1)` instead of `substring_index(str, ',', 1)`; indexes start at 1.
- Avoid `count(DISTINCT user_id) OVER (...)`; deduplicate first with `GROUP BY`, or use approximate distinct counting if acceptable.

## 7. Structural Self-Check

After generating SQL, verify that:

- CTE clauses are comma-separated and the final CTE has no trailing comma.
- The SQL statement ends with a semicolon when required by the execution environment.
- Every non-aggregated column in `SELECT` appears in `GROUP BY`.
- In `UNION ALL` summaries, corresponding columns have identical data types across all branches. Cast numeric fields to `STRING` explicitly when they must align with literals such as `'Total'`.
