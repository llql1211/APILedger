"""
APILedger - SQLite 数据库管理

提供: 建表、UPSERT 批量写入、筛选查询、去重取值。
"""

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# 数据库文件路径 (项目根目录 / data / api_ledger.db)
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "api_ledger.db")

# ── 建表 SQL ─────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS api_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    bill_start      TEXT NOT NULL,          -- ISO-8601
    bill_end        TEXT NOT NULL,          -- ISO-8601
    platform        TEXT NOT NULL DEFAULT '',
    project         TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    type            TEXT NOT NULL DEFAULT '',
    tokens          INTEGER NOT NULL DEFAULT 0,
    call_volume     INTEGER NOT NULL DEFAULT 0,
    cost            REAL NOT NULL DEFAULT 0.0,

    extra           TEXT NOT NULL DEFAULT '{}',   -- JSON
    source_file     TEXT NOT NULL DEFAULT '',
    imported_at     TEXT NOT NULL DEFAULT '',

    UNIQUE(bill_start, bill_end, platform, project, model, type)
)
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_dates ON api_records(bill_start, bill_end);",
    "CREATE INDEX IF NOT EXISTS idx_platform ON api_records(platform);",
    "CREATE INDEX IF NOT EXISTS idx_project ON api_records(project);",
    "CREATE INDEX IF NOT EXISTS idx_model ON api_records(model);",
    "CREATE INDEX IF NOT EXISTS idx_type ON api_records(type);",
]

UPSERT_SQL = """
INSERT INTO api_records (bill_start, bill_end, platform, project, model, type,
                         tokens, call_volume, cost, extra, source_file, imported_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(bill_start, bill_end, platform, project, model, type)
DO UPDATE SET
    tokens      = excluded.tokens,
    call_volume = excluded.call_volume,
    cost        = excluded.cost,
    extra       = excluded.extra,
    source_file = excluded.source_file,
    imported_at = excluded.imported_at
"""


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
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self._init_tables()

    def _init_tables(self):
        cur = self.conn.cursor()
        cur.execute(CREATE_TABLE_SQL)
        for idx_sql in CREATE_INDEXES_SQL:
            cur.execute(idx_sql)
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

        返回:
        {
            "new":  List[Dict],   # 库中没有, 可直接插入
            "same": List[Dict],   # 数值完全一致, 可跳过
            "conflicts": [        # 数值不一致, 需要用户确认
                {
                    "row":        Dict,  # 本次导入的行
                    "existing":   Dict,  # 数据库中已有的行
                    "old_file":   str,   # 已有行的来源文件
                    "new_file":   str,   # 本次导入的来源文件
                }
            ]
        }
        """
        if not records:
            return {"new": [], "same": [], "conflicts": []}

        cur = self.conn.cursor()
        new_rows: List[Dict[str, Any]] = []
        same_rows: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []

        for row in records:
            # 按唯一键查找
            cur.execute(
                """SELECT tokens, call_volume, cost, source_file
                   FROM api_records
                   WHERE bill_start = ? AND bill_end = ?
                     AND platform = ? AND project = ?
                     AND model = ? AND type = ?""",
                (
                    row.get("bill_start", ""),
                    row.get("bill_end", ""),
                    row.get("platform", ""),
                    row.get("project", ""),
                    row.get("model", ""),
                    row.get("type", ""),
                ),
            )
            existing = cur.fetchone()

            if existing is None:
                new_rows.append(row)
                continue

            old = dict(existing)

            # 比较数值字段
            same = (
                int(row.get("tokens", 0) or 0) == old["tokens"]
                and int(row.get("call_volume", 0) or 0) == old["call_volume"]
                and abs(float(row.get("cost", 0.0) or 0.0) - old["cost"]) < 1e-9
            )

            if same:
                same_rows.append(row)
            else:
                conflicts.append({
                    "row": row,
                    "existing": old,
                    "old_file": old.get("source_file", ""),
                    "new_file": row.get("source_file", ""),
                })

        return {"new": new_rows, "same": same_rows, "conflicts": conflicts}

    def upsert_batch(self, records: List[Dict[str, Any]]):
        """
        批量 UPSERT 写入记录。

        records 中每项应包含:
          bill_start, bill_end, platform, project, model, type,
          tokens, call_volume, cost, extra, source_file, imported_at
        """
        if not records:
            return 0

        now = datetime.now().isoformat(timespec="seconds")
        rows = []
        for r in records:
            rows.append((
                r.get("bill_start", ""),
                r.get("bill_end", ""),
                r.get("platform", ""),
                r.get("project", ""),
                r.get("model", ""),
                r.get("type", ""),
                int(r.get("tokens", 0) or 0),
                int(r.get("call_volume", 0) or 0),
                float(r.get("cost", 0.0) or 0.0),
                r.get("extra", "{}"),
                r.get("source_file", ""),
                r.get("imported_at", now),
            ))

        cur = self.conn.cursor()
        cur.executemany(UPSERT_SQL, rows)
        self.conn.commit()
        return len(rows)

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
        order_by: str = "bill_start DESC",
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        筛选查询。

        返回 (records_list, total_count)。
        records_list 中每项为 dict。
        """
        conditions: List[str] = []
        params: List[Any] = []

        if bill_start:
            conditions.append("bill_start >= ?")
            params.append(bill_start)
        if bill_end:
            conditions.append("bill_end <= ?")
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

    def get_all(self, order_by: str = "bill_start DESC") -> List[Dict[str, Any]]:
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
        cur.execute("SELECT MIN(bill_start), MAX(bill_end) FROM api_records")
        row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)

    # ═══════════════════════════════════════════════
    # 聚合统计 (用于图表)
    # ═══════════════════════════════════════════════

    def aggregate_by_date(
        self,
        value_field: str = "cost",
        group_by: str = "date(bill_start)",
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        按时间聚合。value_field: cost / tokens / call_volume
        group_by: date(bill_start) 或 strftime('%Y-%m', bill_start)
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
                conditions.append("bill_start >= ?")
                params.append(filters["bill_start"])
            if filters.get("bill_end"):
                conditions.append("bill_end <= ?")
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
