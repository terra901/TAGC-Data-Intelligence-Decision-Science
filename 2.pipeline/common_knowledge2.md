# Common Business and Data-Warehouse Knowledge

## 1. Game-Business Assumptions

### Product Taxonomy

- **Core games**: Blade Envoy (`jordass`), Brave Covenant, and Canyon Operation are treated as first-person-shooter products in the analytical environment.
- **Playground mode**: within Blade Envoy (`jordass`), Playground denotes a collection of user-generated or custom gameplay modes, including Classic Mode, Casual Mode, Survival Mode, and related sub-modes.

### Population Hierarchies

- **Mobile/platform-wide population**: the union of users across all relevant mobile game products.
- **Single-game population**: all active users of a specific game, regardless of the gameplay mode in which they participated.
- **Default activity definition**: unless a question explicitly specifies a mode, the term `active` refers to game-login activity.

### Identifier Mapping

- `uid` denotes the account or device layer; a single `uid` may map to multiple `vplayerid` values.
- `vplayerid` denotes the in-game character layer and should be regarded as the primary statistical unit for gameplay and monetization analysis.

## 2. Warehouse Layers and Naming Conventions

- `DWD` tables represent detailed event streams and typically contain `tdbank_imp_date` in `yyyyMMddHH` format.
- `DWS` tables represent lightly aggregated summary data and typically contain `dtstatdate` in `yyyyMMdd` format.
- `DIM` tables represent dimensional configuration and mapping metadata.

### Suffix Semantics

- `_di` means daily increment: records are usually present only when behavior occurred on the given day.
- `_hi` means hourly increment: these tables can be very large and should always be queried with a bounded time range.
- `_df` nominally means daily full snapshot, but this database contains non-standard naming practices. Do not infer snapshot semantics from the suffix alone.

## 3. Empirical Database Rules

### Pseudo-Snapshot Tables

The following `_df` tables should be treated as incremental or event-like tables. For identifier mapping or attribute lookup, aggregate and deduplicate over an adequate historical window, commonly 180 days, rather than querying only the latest partition.

- `dws_jordass_uid_login_df`
- `dim_argothek_gplayerid2qqwxid_df`
- `dim_vplayerid_vies_df`
- `dws_jordass_login_df`

### True Snapshot Tables

The following tables have high observed coverage and may generally be queried on the latest partition when a full-state snapshot is needed.

- `dws_jordass_emulator_df`
- `dws_jordass_water_df`
- `dim_mgamejp_account_allinfo_nf`
- `dim_argothek_seasondate_df`

### Type-Safety Rule

`uid` may be represented as `STRING` in detailed tables and as `BIGINT` in summary tables. Join predicates must therefore use explicit casts, for example:

```sql
ON t1.uid = CAST(t2.uid AS STRING)
```

## 4. Field and Metric Conventions

- Prefer `tdbank_imp_date` for `dwd_` tables and `dtstatdate` for `dws_` or `dim_` tables.
- In cross-layer joins, align date formats explicitly, for example `SUBSTR(tdbank_imp_date, 1, 8) = dtstatdate`.
- The `cbitmap` field is interpreted as a 100-character binary activity bitmap. The first character denotes the statistical day, the second denotes the previous day, and so forth.
- `INSTR(SUBSTR(cbitmap, 1, 7), '1') > 0` indicates activity within the most recent seven-day window.

## 5. Standard Business Metrics

- **New user**: a user whose `dtstatdate` equals `dregdate`.
- **Retention**: a user active on an anchor day who remains active after a specified number of days.
- **Returning user**: a currently active user who was continuously inactive for a prior window, such as 14 days.
