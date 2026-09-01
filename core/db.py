"""
APILedger - SQLite 数据库管理

提供: 建表、贡献明细 + 聚合写入、冲突检测、筛选查询、去重取值。

数据模型:
  api_records     聚合表, 每 key (date, platform, project, model, type) 一行,
                  tokens/cost 为各来源文件贡献之和
  record_sources  贡献明细表, 每 (key, source_file) 一行, 记录每个账单文件
                  对该 key 的贡献。同 key 跨文件时:
                    - 文件名时间区段不重叠 → 互补数据, 贡献累加
                    - 文件名时间区段重叠   → 重复数据, 视为冲突交用户裁决
"""

import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

# 数据库文件路径 (项目根目录 / data / api_ledger.db)
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "api_ledger.db")

# ── 建表 SQL ─────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS api_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    date            TEXT NOT NULL,           -- 日期 (YYYY-MM-DD)
    platform        TEXT NOT NULL DEFAULT '',
    project         TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    type            TEXT NOT NULL DEFAULT '',
    tokens          INTEGER NOT NULL DEFAULT 0,
    cost            REAL NOT NULL DEFAULT 0.0,
    unit_price      REAL NOT NULL DEFAULT 0.0,  -- 单价/百万tokens

    source_file     TEXT NOT NULL DEFAULT '',

    UNIQUE(date, platform, project, model, type)
)
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_date ON api_records(date);",
    "CREATE INDEX IF NOT EXISTS idx_platform ON api_records(platform);",
    "CREATE INDEX IF NOT EXISTS idx_project ON api_records(project);",
    "CREATE INDEX IF NOT EXISTS idx_model ON api_records(model);",
    "CREATE INDEX IF NOT EXISTS idx_type ON api_records(type);",
]

# 贡献明细表: 每个账单文件对每个 key 的贡献
CREATE_SOURCES_SQL = """
CREATE TABLE IF NOT EXISTS record_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    platform    TEXT NOT NULL DEFAULT '',
    project     TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT '',
    source_file TEXT NOT NULL DEFAULT '',
    tokens      INTEGER NOT NULL DEFAULT 0,
    cost        REAL NOT NULL DEFAULT 0.0,
    unit_price  REAL NOT NULL DEFAULT 0.0,

    UNIQUE(date, platform, project, model, type, source_file)
)
"""

UPSERT_SOURCE_SQL = """
INSERT INTO record_sources (date, platform, project, model, type, source_file,
                            tokens, cost, unit_price)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(date, platform, project, model, type, source_file)
DO UPDATE SET
    tokens      = excluded.tokens,
    cost        = excluded.cost,
    unit_price  = excluded.unit_price
"""

UPSERT_AGGREGATE_SQL = """
INSERT INTO api_records (date, platform, project, model, type,
                         tokens, cost, unit_price, source_file)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(date, platform, project, model, type)
DO UPDATE SET
    tokens      = excluded.tokens,
    cost        = excluded.cost,
    unit_price  = excluded.unit_price,
    source_file = excluded.source_file
"""

# 从文件名提取日期 (如 paratera_2026-08-01_2026-08-31.csv → 两个日期)
_DATE_IN_FILENAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def parse_file_date_range(filename: str) -> Optional[Tuple[str, str]]:
    """
    从文件名解析时间区段 (起, 止)。
    无日期或仅一个日期时, 区段为 (d, d); 完全无日期返回 None。
    """
    dates = _DATE_IN_FILENAME_RE.findall(os.path.basename(filename))
    if not dates:
        return None
    return (min(dates), max(dates))


def ranges_overlap(
    a: Optional[Tuple[str, str]], b: Optional[Tuple[str, str]]
) -> bool:
    """
    判断两个文件名时间区段是否重叠。
    任一区段未知 (None) 时保守视为重叠 (走冲突流程, 交用户裁决)。
    """
    if a is None or b is None:
        return True
    return a[0] <= b[1] and b[0] <= a[1]


class Database:
    """SQLite 数据库管理器"""

    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self):
        """打开数据库连接并初始化表结构"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=DELETE;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self._init_tables()

    def _init_tables(self):
        cur = self.conn.cursor()
        # 检测旧表结构 (旧版含 bill_start / bill_end, 新版用 date)
        old_cols = [
            r[1] for r in cur.execute("PRAGMA table_info(api_records)").fetchall()
        ] if self._table_exists("api_records") else []
        if old_cols and "date" not in old_cols:
            print("  [数据库] 检测到旧表结构, 重建为新 schema (date 字段)", flush=True)
            self._rebuild_table()

        cur.execute(CREATE_TABLE_SQL)
        for idx_sql in CREATE_INDEXES_SQL:
            cur.execute(idx_sql)
        cur.execute(CREATE_SOURCES_SQL)

        # 旧库迁移: api_records 已有数据但无贡献明细时, 整表回填
        # (每行视为其 source_file 的一次贡献, 数值不变)
        src_cnt = cur.execute("SELECT COUNT(*) FROM record_sources").fetchone()[0]
        rec_cnt = cur.execute("SELECT COUNT(*) FROM api_records").fetchone()[0]
        if rec_cnt > 0 and src_cnt == 0:
            print("  [数据库] 迁移: 按 api_records 回填来源明细 (record_sources)", flush=True)
            cur.execute(
                """INSERT INTO record_sources
                     (date, platform, project, model, type, source_file,
                      tokens, cost, unit_price)
                   SELECT date, platform, project, model, type, source_file,
                          tokens, cost, unit_price
                   FROM api_records"""
            )

        self.conn.commit()

    def _table_exists(self, name: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return cur.fetchone() is not None

    def _rebuild_table(self):
        """旧表 (bill_start/bill_end) → 新表 (date) 结构重建。
        数据不迁移 (由重新导入恢复); 仅删除旧表, 避免残留旧 schema。"""
        cur = self.conn.cursor()
        cur.execute("DROP TABLE IF EXISTS api_records")
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    # ═══════════════════════════════════════════════
    # 写入
    # ═══════════════════════════════════════════════

    def check_conflicts(
        self, records: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        两阶段导入的检测阶段：
        逐行检查这批记录与数据库中已有数据的冲突情况。

        判定规则 (按 key = date/platform/project/model/type 查贡献明细):
          - key 不存在                          → new (新增)
          - 同文件已有贡献, 数值一致             → same (无变化)
          - 同文件已有贡献, 数值不一致           → conflict (同文件数据变动)
          - 其他文件的贡献, 文件名区段不重叠     → merge (互补数据, 累加)
          - 其他文件的贡献, 文件名区段重叠       → conflict (重复数据)

        返回:
        {
            "new":    List[Dict],   # 库中没有, 可直接插入
            "same":   List[Dict],   # 数值完全一致, 可跳过
            "merges": List[Dict],   # 互补数据, 贡献累加
            "conflicts": [          # 数值不一致, 需要用户确认
                {
                    "row":        Dict,  # 本次导入的行
                    "existing":   Dict,  # 数据库中已有的聚合行
                    "old_file":   str,   # 已有数据的来源文件
                    "new_file":   str,   # 本次导入的来源文件
                }
            ]
        }
        """
        if not records:
            return {"new": [], "same": [], "merges": [], "conflicts": []}

        cur = self.conn.cursor()
        new_rows: List[Dict[str, Any]] = []
        same_rows: List[Dict[str, Any]] = []
        merge_rows: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []

        for row in records:
            key = _record_key(row)
            cur.execute(
                """SELECT source_file, tokens, cost FROM record_sources
                   WHERE date = ? AND platform = ? AND project = ?
                     AND model = ? AND type = ?""",
                key,
            )
            contributions = cur.fetchall()

            if not contributions:
                new_rows.append(row)
                continue

            tokens = int(row.get("tokens", 0) or 0)
            cost = float(row.get("cost", 0.0) or 0.0)
            src = row.get("source_file", "")

            # 同一文件再次导入: 比对该文件的贡献值
            mine = [c for c in contributions if c["source_file"] == src]
            if mine:
                if mine[0]["tokens"] == tokens and abs(mine[0]["cost"] - cost) < 1e-9:
                    same_rows.append(row)
                else:
                    conflicts.append(self._conflict_entry(cur, row, key))
                continue

            # 其他文件的贡献: 按文件名时间区段判断互补 / 重复
            new_range = parse_file_date_range(src)
            overlap = any(
                ranges_overlap(new_range, parse_file_date_range(c["source_file"]))
                for c in contributions
            )
            if overlap:
                conflicts.append(self._conflict_entry(cur, row, key))
            else:
                merge_rows.append(row)

        return {
            "new": new_rows,
            "same": same_rows,
            "merges": merge_rows,
            "conflicts": conflicts,
        }

    def _conflict_entry(
        self, cur: sqlite3.Cursor, row: Dict[str, Any], key: tuple
    ) -> Dict[str, Any]:
        """构造冲突条目, existing 取聚合行供 UI 展示"""
        cur.execute(
            """SELECT tokens, cost, unit_price, source_file
               FROM api_records
               WHERE date = ? AND platform = ? AND project = ?
                 AND model = ? AND type = ?""",
            key,
        )
        existing = cur.fetchone()
        old = dict(existing) if existing else {
            "tokens": 0, "cost": 0.0, "unit_price": 0.0, "source_file": "",
        }
        return {
            "row": row,
            "existing": old,
            "old_file": old.get("source_file", ""),
            "new_file": row.get("source_file", ""),
        }

    def upsert_batch(self, records: List[Dict[str, Any]]):
        """
        批量写入记录 (贡献累加)。

        每条记录作为其 source_file 对该 key 的一次贡献写入 record_sources,
        然后重算该 key 的聚合行 (tokens/cost = 各贡献之和)。
        同 key 跨文件多次导入自动累加; 同文件重复导入以最新值替换其贡献。

        records 中每项应包含:
          bill_start (日期), platform, project, model, type,
          tokens, cost, source_file
        """
        if not records:
            return 0

        cur = self.conn.cursor()
        for r in records:
            key = _record_key(r)
            cur.execute(UPSERT_SOURCE_SQL, key + (
                r.get("source_file", ""),
                int(r.get("tokens", 0) or 0),
                float(r.get("cost", 0.0) or 0.0),
                float(r.get("unit_price", 0.0) or 0.0),
            ))
            self._refresh_aggregate(cur, key)

        self.conn.commit()
        return len(records)

    def replace_keys_batch(self, records: List[Dict[str, Any]]):
        """
        强制覆盖: 清空这些 key 的全部贡献明细, 以本次记录为准重建。
        用于用户确认覆盖的冲突行 (重复数据语义)。
        """
        if not records:
            return 0

        cur = self.conn.cursor()
        for r in records:
            key = _record_key(r)
            cur.execute(
                """DELETE FROM record_sources
                   WHERE date = ? AND platform = ? AND project = ?
                     AND model = ? AND type = ?""",
                key,
            )
            cur.execute(UPSERT_SOURCE_SQL, key + (
                r.get("source_file", ""),
                int(r.get("tokens", 0) or 0),
                float(r.get("cost", 0.0) or 0.0),
                float(r.get("unit_price", 0.0) or 0.0),
            ))
            self._refresh_aggregate(cur, key)

        self.conn.commit()
        return len(records)

    def _refresh_aggregate(self, cur: sqlite3.Cursor, key: tuple):
        """按贡献明细重算某 key 的聚合行"""
        cur.execute(
            """SELECT SUM(tokens), SUM(cost), MAX(unit_price),
                      GROUP_CONCAT(DISTINCT source_file)
               FROM record_sources
               WHERE date = ? AND platform = ? AND project = ?
                 AND model = ? AND type = ?""",
            key,
        )
        total_tokens, total_cost, max_price, files = cur.fetchone()
        total_tokens = int(total_tokens or 0)
        total_cost = float(total_cost or 0.0)

        # 单价: 有 tokens 时按总量重算, 否则沿用贡献中的单价
        if total_tokens > 0:
            unit_price = round(total_cost * 1_000_000 / total_tokens, 4)
        else:
            unit_price = float(max_price or 0.0)

        cur.execute(UPSERT_AGGREGATE_SQL, key + (
            total_tokens, total_cost, unit_price, files or "",
        ))

    # ═══════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════

    def query(
        self,
        bill_start: Optional[str] = None,
        bill_end: Optional[str] = None,
        platform: Optional[str] = None,
        project: Optional[str] = None,
        model: Optional[str] = None,
        type_: Optional[str] = None,
        keyword: Optional[str] = None,
        order_by: str = "date DESC",
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        筛选查询。

        返回 (records_list, total_count)。
        records_list 中每项为 dict。
        bill_start/bill_end 参数映射到 date 列 (日期范围筛选)。
        """
        conditions: List[str] = []
        params: List[Any] = []

        if bill_start:
            conditions.append("date >= ?")
            params.append(bill_start)
        if bill_end:
            conditions.append("date <= ?")
            params.append(bill_end)
        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if project:
            conditions.append("project = ?")
            params.append(project)
        if model:
            conditions.append("model = ?")
            params.append(model)
        if type_:
            conditions.append("type = ?")
            params.append(type_)
        if keyword:
            conditions.append("(model LIKE ? OR project LIKE ? OR platform LIKE ? OR type LIKE ?)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw, kw])

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # 总数
        cur = self.conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM api_records {where_clause}", params)
        total: int = cur.fetchone()[0]

        # 数据
        sql = f"SELECT * FROM api_records {where_clause} ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {limit}"
            if offset is not None:
                sql += f" OFFSET {offset}"
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]

        return rows, total

    def get_all(self, order_by: str = "date DESC") -> List[Dict[str, Any]]:
        """获取全部记录"""
        cur = self.conn.cursor()
        cur.execute(f"SELECT * FROM api_records ORDER BY {order_by}")
        return [dict(r) for r in cur.fetchall()]

    # ═══════════════════════════════════════════════
    # 下拉选项 (去重)
    # ═══════════════════════════════════════════════

    def get_distinct(self, field: str) -> List[str]:
        """获取某字段的去重值 (用于下拉筛选)"""
        allowed = {"platform", "project", "model", "type"}
        if field not in allowed:
            return []
        cur = self.conn.cursor()
        cur.execute(f"SELECT DISTINCT {field} FROM api_records WHERE {field} != '' ORDER BY {field}")
        return [r[0] for r in cur.fetchall()]

    def get_date_range(self) -> Tuple[Optional[str], Optional[str]]:
        """获取记录中的最早和最晚日期"""
        cur = self.conn.cursor()
        cur.execute("SELECT MIN(date), MAX(date) FROM api_records")
        row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)

    # ═══════════════════════════════════════════════
    # 聚合统计 (用于图表)
    # ═══════════════════════════════════════════════

    def aggregate_by_date(
        self,
        value_field: str = "cost",
        group_by: str = "date",
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        按时间聚合。value_field: cost / tokens
        group_by: date 或 strftime('%Y-%m', date)
        """
        conditions, params = self._build_filter_conditions(filters)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        sql = f"""
            SELECT {group_by} AS period,
                   SUM({value_field}) AS total,
                   COUNT(*) AS record_count
            FROM api_records {where}
            GROUP BY period
            ORDER BY period ASC
        """
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def aggregate_by_field(
        self,
        value_field: str = "cost",
        group_field: str = "platform",
        filters: Optional[Dict[str, Any]] = None,
        top_n: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        按某字段 (platform/project/model/type) 聚合。
        返回前 top_n 项 + 余项合计。
        """
        conditions, params = self._build_filter_conditions(filters)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        sql = f"""
            SELECT {group_field} AS name,
                   SUM({value_field}) AS total,
                   COUNT(*) AS record_count
            FROM api_records {where}
            GROUP BY name
            ORDER BY total DESC
            LIMIT {top_n}
        """
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    # ── 内部 ─────────────────────────────────────

    @staticmethod
    def _build_filter_conditions(filters: Optional[Dict[str, Any]] = None):
        conditions: List[str] = []
        params: List[Any] = []
        if filters:
            if filters.get("bill_start"):
                conditions.append("date >= ?")
                params.append(filters["bill_start"])
            if filters.get("bill_end"):
                conditions.append("date <= ?")
                params.append(filters["bill_end"])
            if filters.get("platform"):
                conditions.append("platform = ?")
                params.append(filters["platform"])
            if filters.get("project"):
                conditions.append("project = ?")
                params.append(filters["project"])
            if filters.get("model"):
                conditions.append("model = ?")
                params.append(filters["model"])
            if filters.get("type_"):
                conditions.append("type = ?")
                params.append(filters["type_"])
        return conditions, params


def _record_key(row: Dict[str, Any]) -> tuple:
    """从标准记录提取唯一键 (date, platform, project, model, type)"""
    return (
        str(row.get("bill_start", ""))[:10],
        row.get("platform", ""),
        row.get("project", ""),
        row.get("model", ""),
        row.get("type", ""),
    )
