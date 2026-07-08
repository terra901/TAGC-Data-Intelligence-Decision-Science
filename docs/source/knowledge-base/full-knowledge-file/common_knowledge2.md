## [Common Knowledge] 业务与数仓规则 (v2.0 实战修正版)

### 1. 游戏业务常识
* **游戏产品体系**：
    * **核心游戏**：砺刃使者 (`jordass`)、勇者盟约、峡谷行动（均为 FPS 类型）。
    * **乐园模式**：砺刃使者 (`jordass`) 下的 UGC 玩法集合，包含传统模式、休闲模式、生存模式等子玩法。
* **层级定义**：
    * **手游/平台大盘**：指所有游戏产品的用户集合。
    * **单游戏大盘**：指该游戏下的所有活跃用户（无论玩什么模式）。
    * **默认活跃**：若问题未明确指定模式，"活跃"默认指**游戏登录活跃**（Game Login）。
* **ID 映射关系**：
    * `uid` (账号/设备层)：一个 `uid` 可能对应多个 `vplayerid`。
    * `vplayerid` (角色层)：游戏内的唯一角色标识，是付费和玩法的统计基准。

### 2. 数仓分层与命名规范
* **分层定义**：
    * `DWD` (Detail)：明细流水层。通常包含 `tdbank_imp_date` (yyyyMMddHH)。
    * `DWS` (Summary)：轻度汇总层。通常包含 `dtstatdate` (yyyyMMdd)。
    * `DIM` (Dimension)：维度配置表。
* **后缀含义与风险提示**：
    * `_di` (Daily Increment)：**每日增量**。仅包含当天有行为的数据。
    * `_hi` (Hourly Increment)：**小时级流水**。量级巨大，查询时建议限定时间范围。
    * `_df` (Daily Full)：**名义上的全量快照**。
        * 🛑 **高危警告**：本数据库存在命名不规范情况。**不要盲目信任 `_df` 后缀！** 很多以 `_df` 结尾的表实际上是**增量表**。请务必参考下方的“特殊规则”进行查询。

### 3. ⚠️ 数据库特殊规则 (基于真实数据探查)
**A. “假快照表”黑名单 (The Liars) - 必须范围聚合**
以下表后缀虽为 `_df`，但实为**流水/增量表**。涉及 ID Mapping 或属性查找时，**严禁**只查最新分区 (`dt = '${LATEST}'`)，必须聚合过去 **180天** 的数据并去重：
* `dws_jordass_uid_login_df` (核心映射表) -> **最易错点！只查单日会丢失 60%+ 数据。**
* `dim_argothek_gplayerid2qqwxid_df`
* `dim_vplayerid_vies_df`
* `dws_jordass_login_df` (名为 df 实为登录流水)

**B. “真快照表”白名单 (The Honest) - 可查最新分区**
以下表覆盖率 >90%，可以直接查询最新分区获取全量状态：
* `dws_jordass_emulator_df` (模拟器信息)
* `dws_jordass_water_df` (历史累计付费快照，注意与 `_di` 区分)
* `dim_mgamejp_account_allinfo_nf`
* `dim_argothek_seasondate_df`

**C. ID 类型安全铁律 (Type Safety)**
* `DWD` 层表 (如 Alliance) 的 `uid` 通常为 **STRING** 类型。
* `DWS` 层表 (如 Login) 的 `uid` 可能为 **BIGINT** 类型。
* **强制动作**：在进行 JOIN 操作时，必须显式转换类型，例如：
    `ON t1.uid = CAST(t2.uid AS STRING)`

### 4. 字段与指标规范
* **日期字段处理**：
    * `dwd_` 表优先使用 `tdbank_imp_date` (String, 格式 `2025022600`)。
    * `dws_` / `dim_` 表优先使用 `dtstatdate` (String, 格式 `20250226`)。
    * **注意**：跨层关联时，必须处理日期格式对齐（如 `SUBSTR(tdbank_imp_date, 1, 8) = dtstatdate`）。
* **cbitmap 字段**：
    * 定义：100位 0/1 字符串，左起第一位代表当天（统计日），第二位代表前一天，以此类推。
    * 用法：`INSTR(SUBSTR(cbitmap, 1, 7), '1') > 0` 表示最近7天有活跃。
* **常用业务指标逻辑**：
    * **新进 (New)**：`dtstatdate = dregdate`。
    * **留存 (Retention)**：某日活跃用户在 N 天后依然活跃。
    * **回流 (Return)**：当前活跃，且此前连续 N 天（如14天）未活跃。
