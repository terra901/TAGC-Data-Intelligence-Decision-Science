#StarRocks SQL 方言核心指南 (Agent 专用版 v2.0)
核心原则：StarRocks 是一款高性能 MPP 数据库。与 MySQL/Hive 相比，它对语法严谨性、执行计划复杂性（如相关子查询、UNION 排序）以及别名作用域有严格限制。请严格遵守以下规则以避免执行错误。
1. 基础语法、引用与保留字
字符串字面量规范
规则：字符串值必须使用单引号 (')。双引号 (") 在 StarRocks 中仅用于引用数据库标识符（如表名、列名），混用会导致语法解析错误。
错误写法：split_part(col, "_", 2)
正确写法：split_part(col, '_', 2)
保留字冲突避让
规则：严禁将 StarRocks 的保留关键字用作表别名或列别名。
禁止列表：map、array、row、rank、group 等。
错误写法：FROM table_name AS map
正确写法：FROM table_name AS t_map
2. 列名引用与作用域 (Column Scope)
多表 JOIN 时的列名歧义
规则：多表查询时，所有 SELECT、WHERE、GROUP BY 中的列必须显式指定表别名。
报错信息：Column 'xxx' is ambiguous
错误写法：SELECT dt FROM t1 JOIN t2 ON ...
正确写法：SELECT t1.dt FROM t1 JOIN t2 ON ...
别名可见性 (同层不可引用)
规则：在同一层 SELECT 语句中定义的列别名，不能在同一层的 WHERE 子句或 SELECT 计算中直接引用。必须嵌套一层 CTE 或子查询。
报错信息：Column 'cnt' cannot be resolved
错误写法：SELECT count(*) AS cnt, cnt * 2 FROM table
正确写法：WITH t AS (SELECT count(*) AS cnt FROM table) SELECT cnt, cnt * 2 FROM t
3. 集合操作与排序 (UNION ALL & ORDER BY)
UNION 后排序的类型推断失败
规则：在 UNION ALL 后的 ORDER BY 中直接编写复杂的 CASE WHEN 表达式会导致优化器类型推断失败（报错 CaseWhenOperator cannot be cast...）。
修复策略：必须在每个子查询内部预先计算好用于排序的字段（如 sort_id），或者在外层包裹后基于列名排序。
错误写法：

SQL
SELECT name FROM t1
UNION ALL
SELECT name FROM t2
ORDER BY CASE WHEN name = 'Total' THEN 1 ELSE 0 END
正确写法：

SQL
SELECT * FROM (
    SELECT name, 0 AS sort_id FROM t1
    UNION ALL
    SELECT name, 1 AS sort_id FROM t2
) t_final
ORDER BY sort_id, name
1. 聚合与逻辑陷阱 (Aggregation Logic)
禁止对聚合结果直接分组 (二次聚合)
规则：不能在 GROUP BY 中使用聚合函数的结果（如 count(*)）。如果需要基于聚合结果（如“胜利场次”）进行分组统计（如“胜利场次分布”），必须使用 CTE 进行二次聚合。
报错信息：must be an aggregate expression or appear in GROUP BY clause
错误写法：
SQL
SELECT CASE WHEN count(*) > 10 THEN 'High' END, count(user_id)
FROM table
GROUP BY CASE WHEN count(*) > 10 THEN 'High' END
正确写法：

SQL
WITH user_stats AS (
    SELECT user_id, count(*) AS win_count FROM table GROUP BY user_id
)
SELECT
    CASE WHEN win_count > 10 THEN 'High' ELSE 'Low' END AS level,
    count(user_id)
FROM user_stats
GROUP BY CASE WHEN win_count > 10 THEN 'High' ELSE 'Low' END
标量子查询返回多行
规则：在 SELECT 列表中使用的子查询必须保证只返回一行一列。
报错信息：Expected LE 1 to be returned by expression
错误写法：SELECT id, (SELECT count(*) FROM t2 GROUP BY type) FROM t1
正确写法：改写为 LEFT JOIN 并通过 GROUP BY 聚合。
1. 连接与子查询限制 (Joins & Subqueries)
不支持非等值相关子查询
规则：MPP 数据库难以优化非等值的相关子查询（Correlated Subquery）。
报错信息：Not support Non-EQ correlated predicate
错误写法：

SQL
SELECT * FROM t1 WHERE val > (SELECT avg(val) FROM t2 WHERE t2.id = t1.id AND t2.date < t1.date)
正确写法：使用窗口函数（如 AVG() OVER (PARTITION BY id ORDER BY date ROWS BETWEEN ...)）或改写为 JOIN。
1. 常用函数方言替换 (Function Substitution)
自定义排序 (Custom Sorting)
错误写法 (MySQL)：FIELD(col, 'Bronze', 'Silver', 'Gold')
正确写法 (StarRocks)：CASE col WHEN 'Bronze' THEN 1 WHEN 'Silver' THEN 2 WHEN 'Gold' THEN 3 END
日期类型转换
规则：所有日期函数输入必须是日期类型，严禁传入 'YYYYMMDD' 字符串。
错误写法：last_day('20230101')
正确写法：last_day(str_to_date('20230101', '%Y%m%d'))
字符串分割
错误写法：substring_index(str, ',', 1)
正确写法：split_part(str, ',', 1) (注意：索引从 1 开始)
窗口去重
错误写法：count(DISTINCT user_id) OVER (...)
正确写法：先 GROUP BY 去重，再使用窗口函数；或使用 approx_count_distinct (如果允许误差)。
1. 结构完整性自检
在生成 SQL 后，请进行以下逻辑自检：
CTE 语法：检查 WITH 子句之间是否有逗号分隔，最后一个 CTE 后没有逗号。
结束符：确保 SQL 语句以分号 ; 结尾，防止截断。
聚合一致性：SELECT 中出现的非聚合列，必须全部出现在 GROUP BY 中。


在使用 UNION ALL 进行汇总统计时（如增加‘总计’行），必须确保所有 SELECT 分支中对应列的数据类型严格一致。如果原字段是数值型（如 awardidx），必须在所有分支中将其显式转换为字符串（CAST(awardidx AS STRING)），以匹配 '总计' 等字面量。不要依赖数据库的隐式转换。
