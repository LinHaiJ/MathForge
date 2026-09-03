"""MathForge 记忆层 v2 —— attempt_events 事件流 + 三投影（D024/D025/D026 定案）。

设计要点：
- attempt_events 为 append-only 唯一事实源；mastery / mistakes / patterns 为离线可重算的投影。
- 修复 v1 N=1 缺口：掌握度改用最近 N=5 滑窗加权（权重 0.5^(age_days/halflife)）。
- 本模块只读 v1 文件（db.py/policy.py/app.py 等），绝不修改；与 v1 共用同一 SQLite 文件但只新增表。

⚠️ sim 隔离（红线）：
  模拟器数据必须走独立 conn 指向独立库 mathforge_sim.db，绝不混入默认库 mathforge.db。
  本模块所有函数都显式接受 conn 参数，调用方负责传入正确的连接；mem2 自身不决定用哪个库。
  默认库（default_db_path）仅用于人类真实作答数据。
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# 与 v1 db.py 同一定义：mathforge.db 位于仓库根目录（mem2.py 同目录）。
# v1 db.py: DB_PATH = Path(__file__).resolve().parent / "mathforge.db"
DB_PATH = Path(__file__).resolve().parent / "mathforge.db"

# 默认半衰期（与 v1 db.HALF_LIFE_DAYS、pack_loader.DEFAULT_STRATEGY 对齐）。
DECAY_HALF_LIFE_DAYS = 7.0
DEFAULT_WINDOW = 5
DEFAULT_RECENCY_HALFLIFE_DAYS = 7.0

# 评分映射
SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}

_SCORE_RESULTS = ("correct", "wrong", "partial")


# --------------------------------------------------------------------------- #
# 连接 / schema
# --------------------------------------------------------------------------- #
def default_db_path() -> Path:
    """返回与 v1 db.py 同一默认库文件（仓库根目录 mathforge.db）。

    真实作答数据落此库；模拟器须自建 mathforge_sim.db 并传独立 conn。
    """
    return DB_PATH


def init_schema(conn: sqlite3.Connection) -> None:
    """在同库新增 attempt_events 与 patterns 两表（不动 v1 三表）。幂等（IF NOT EXISTS）。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attempt_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            day TEXT,
            sim INTEGER DEFAULT 0,
            pack TEXT,
            kp TEXT,
            qtype TEXT,
            mode TEXT,
            result TEXT,
            attribution TEXT,          -- JSON: {"type":..., "conf":...}
            user_override TEXT,       -- JSON: {"type":..., "conf":...}（人类修正）
            policy_snapshot TEXT,     -- JSON
            meta TEXT                 -- JSON: {"origin_event_id":...}
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY,
            pack TEXT NOT NULL,
            kp TEXT NOT NULL,
            pattern_md TEXT,
            source TEXT,
            confirmed INTEGER DEFAULT 0,
            created_ts REAL
        )
        """
    )
    # 索引：按 kp + ts 取最近事件
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_attempt_events_kp_ts ON attempt_events (kp, ts)"
    )
    # patterns 每张卡唯一对应 (pack, kp)，便于 upsert（v2 蒸馏器 TBD 占位）
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uniq_patterns_pack_kp ON patterns (pack, kp)"
    )
    conn.commit()


def connect(db_path=None) -> sqlite3.Connection:
    """便捷：打开连接 + 初始化 v2 schema（不影响 v1 三表）。"""
    path = str(db_path) if db_path is not None else str(default_db_path())
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


# --------------------------------------------------------------------------- #
# 策略参数解析（兼容 dict 形态 DEFAULT_STRATEGY 或属性对象）
# --------------------------------------------------------------------------- #
def _window(strategy) -> int:
    if not strategy:
        return DEFAULT_WINDOW
    m = _deep_get(strategy, ("mastery", "window"))
    return int(m) if m is not None else DEFAULT_WINDOW


def _recency_halflife(strategy) -> float:
    if not strategy:
        return DEFAULT_RECENCY_HALFLIFE_DAYS
    v = _deep_get(strategy, ("mastery", "recency_halflife_days"))
    return float(v) if v is not None else DEFAULT_RECENCY_HALFLIFE_DAYS


def _decay_halflife(strategy) -> float:
    if not strategy:
        return DECAY_HALF_LIFE_DAYS
    v = _deep_get(strategy, ("decay_half_life_days",))
    return float(v) if v is not None else DECAY_HALF_LIFE_DAYS


def _deep_get(obj, keys):
    cur = obj
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            cur = getattr(cur, k, None)
        if cur is None:
            return None
    return cur


# --------------------------------------------------------------------------- #
# 衰减
# --------------------------------------------------------------------------- #
def _decayed(value: float, updated_at: float, at: float, half_life: float) -> float:
    """对齐 v1 db.decayed：value * 0.5 ** (经过天数 / half_life)，天数下限为 0。"""
    days = max(0.0, (at - updated_at) / 86400.0)
    return value * math.pow(0.5, days / half_life)


# --------------------------------------------------------------------------- #
# 写入：append + override
# --------------------------------------------------------------------------- #
def append_event(
    conn: sqlite3.Connection,
    *,
    pack: str,
    kp: str,
    qtype: str,
    mode: str,
    result: str,
    attribution=None,
    user_override=None,
    policy_snapshot=None,
    meta=None,
    ts=None,
    day=None,
    sim: int = 0,
) -> int:
    """追加一条作答/评估事件到 attempt_events（append-only 事实源）。

    attribution 形如 {"type": "概念混淆", "conf": 0.9}。
    result ∈ {correct, wrong, partial}。
    sim=1 表示模拟器事件——调用方必须已把 conn 指向 mathforge_sim.db，切勿混入默认库。

    返回新事件 id。
    """
    if ts is None:
        ts = time.time()
    if day is None:
        # 真实时间戳 → 本地日历日（"今日/打卡/热力图"以用户本地日为准，修正 UTC 跨日错位）；
        # 合成/测试用 <1970 旧时间戳 → timedelta UTC 推算（规避 Windows localtime 截断）。
        if ts >= 10**9:
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        else:
            day = (date(1970, 1, 1) + timedelta(seconds=ts)).strftime("%Y-%m-%d")

    cur = conn.execute(
        """
        INSERT INTO attempt_events
            (ts, day, sim, pack, kp, qtype, mode, result,
             attribution, user_override, policy_snapshot, meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            day,
            int(sim),
            pack,
            kp,
            qtype,
            mode,
            result,
            _json_or_none(attribution),
            _json_or_none(user_override),
            _json_or_none(policy_snapshot),
            _json_or_none(meta),
        ),
    )
    conn.commit()
    return cur.lastrowid


def override_attribution(
    conn: sqlite3.Connection,
    origin_event_id: int,
    new_type: str,
    conf: float = 1.0,
) -> int:
    """人类修正写回事件流：追加一条 mode='override' 事件。

    meta.origin_event_id = origin_event_id；user_override = {"type": new_type, "conf": conf}。
    投影时按 origin_event_id 取最近 override 覆盖原归因（user_override 优先）。
    """
    return append_event(
        conn,
        pack="",  # 修正事件不绑定具体 pack/kp 语义，靠 meta.origin_event_id 关联
        kp="",
        qtype="",
        mode="override",
        result="override",
        user_override={"type": new_type, "conf": conf},
        meta={"origin_event_id": origin_event_id},
    )


# --------------------------------------------------------------------------- #
# 投影：掌握度
# --------------------------------------------------------------------------- #
def project_mastery(conn, kp: str, at=None, strategy=None) -> dict | None:
    """滑窗 N=window(默认5) 加权掌握度；取该 kp 最近 N 条 result∈{correct,wrong,partial}
    （排除 mode='override'）。

    返回 {value, n, last_ts, decayed_value}；无事件返回 None。
    value = Σ w_i·s_i / Σ w_i；w_i = 0.5^(age_days_i / recency_halflife_days)。
    decayed_value = 对齐 v1 半衰期语义 decayed(value, last_ts, at)。
    """
    if at is None:
        at = time.time()
    window = _window(strategy)
    recency_hl = _recency_halflife(strategy)
    decay_hl = _decay_halflife(strategy)

    rows = conn.execute(
        """
        SELECT ts, result FROM attempt_events
        WHERE kp=? AND result IN ('correct','wrong','partial') AND mode<>'override'
        ORDER BY ts DESC, id DESC
        LIMIT ?
        """,
        (kp, window),
    ).fetchall()

    n = len(rows)
    if n == 0:
        return None

    num = 0.0
    den = 0.0
    last_ts = None
    for r in rows:
        age_days = (at - r["ts"]) / 86400.0
        w = math.pow(0.5, age_days / recency_hl)
        s = SCORE.get(r["result"], 0.0)
        num += w * s
        den += w
        if last_ts is None or r["ts"] > last_ts:
            last_ts = r["ts"]

    value = num / den if den > 0 else 0.0
    decayed_value = _decayed(value, last_ts, at, decay_hl)
    return {
        "value": value,
        "n": n,
        "last_ts": last_ts,
        "decayed_value": decayed_value,
    }


# --------------------------------------------------------------------------- #
# 投影：错题 + 归因（含 override 优先）
# --------------------------------------------------------------------------- #
def project_mistakes(conn, pack=None, kp=None, limit: int = 20) -> list[dict]:
    """错题事件（result='wrong' 或 mode='selfassess' 且 result='partial'），
    每条带最新归因（user_override 优先：取该源事件之后最近的 override 事件）。

    返回 list[dict]，按 ts 倒序（最近在前）。
    """
    clauses = ["(result='wrong' OR (mode='selfassess' AND result='partial'))"]
    params: list = []
    if pack is not None:
        clauses.append("pack=?")
        params.append(pack)
    if kp is not None:
        clauses.append("kp=?")
        params.append(kp)
    where = " AND ".join(clauses)

    rows = conn.execute(
        f"SELECT * FROM attempt_events WHERE {where} ORDER BY ts DESC, id DESC LIMIT ?",
        params + [limit],
    ).fetchall()

    # 预取该范围下的 override 事件映射 origin_event_id -> 最新 user_override
    override_map = _latest_overrides(conn, pack, kp)

    out = []
    for r in rows:
        rid = r["id"]
        attr = _resolve_attribution(r, override_map.get(rid))
        out.append(
            {
                "id": rid,
                "ts": r["ts"],
                "day": r["day"],
                "pack": r["pack"],
                "kp": r["kp"],
                "qtype": r["qtype"],
                "mode": r["mode"],
                "result": r["result"],
                "attribution": attr,
            }
        )
    return out


def _latest_overrides(conn, pack, kp) -> dict:
    """返回 {origin_event_id: user_override_dict}，取每个源事件之后最近的 override。"""
    clauses = ["mode='override'"]
    params: list = []
    if pack is not None:
        # pack 过滤仅在 override 事件也记录了对应 pack 时有意义；此处宽松处理
        pass
    rows = conn.execute(
        "SELECT id, ts, meta, user_override FROM attempt_events WHERE mode='override' "
        "ORDER BY ts ASC, id ASC"
    ).fetchall()
    # 按 origin_event_id 保留最新（ts 最大）的 override
    best: dict = {}
    for r in rows:
        meta = _parse_json(r["meta"])
        if not isinstance(meta, dict):
            continue
        oid = meta.get("origin_event_id")
        if oid is None:
            continue
        # 仅当比已记录更新时覆盖（rows 已按 ts 升序，后写的更大）
        best[oid] = _parse_json(r["user_override"])
    return best


def _resolve_attribution(event_row, override_user_override) -> dict:
    """合并：独立 override 事件 > 事件自身 user_override > 事件 attribution。

    注意：user_override 列无类型亲和，调用方可能写入 0 / "0"（表示“无修正”）。
    按 `int(row['user_override'] or 0)` 语义，0/"0"/None/"" 一律视为无修正，
    仅当解析结果为非空（真实修正 dict 或类型字符串）才采用。
    """
    if override_user_override is not None:
        return _override_to_attr(override_user_override)
    own = _parse_json(event_row["user_override"])
    if own:  # 非空：真实修正 dict 或类型字符串；0/"0"/None/"" 视为无修正
        return _override_to_attr(own)
    attr = _parse_json(event_row["attribution"])
    if isinstance(attr, dict):
        return attr
    return {"type": None, "conf": None}


def _override_to_attr(uo) -> dict:
    if isinstance(uo, dict):
        t = uo.get("type")
        c = uo.get("conf", 1.0)
    else:
        t = uo
        c = 1.0
    return {"type": t, "conf": c, "overridden": True}


# --------------------------------------------------------------------------- #
# 投影：全量
# --------------------------------------------------------------------------- #
def project_all(conn, strategy=None) -> dict:
    """全 kp 投影：{kp: {mastery, decayed_value, n, last_ts, streak_correct, last_attribution}}。

    streak_correct = 按 ts 从最近往前连续 correct 次数（N≥1 且最近一条为 correct 才有值）。
    """
    kps = conn.execute(
        "SELECT DISTINCT kp FROM attempt_events "
        "WHERE result IN ('correct','wrong','partial') AND mode<>'override'",
    ).fetchall()
    result = {}
    for (kp,) in kps:
        m = project_mastery(conn, kp, at=None, strategy=strategy)
        if m is None:
            continue
        streak = _streak_correct(conn, kp)
        last_attr = _last_attribution(conn, kp)
        result[kp] = {
            "mastery": m["value"],
            "decayed_value": m["decayed_value"],
            "n": m["n"],
            "last_ts": m["last_ts"],
            "streak_correct": streak,
            "last_attribution": last_attr,
        }
    return result


def _streak_correct(conn, kp: str) -> int:
    rows = conn.execute(
        "SELECT result FROM attempt_events "
        "WHERE kp=? AND result IN ('correct','wrong','partial') AND mode<>'override' "
        "ORDER BY ts DESC, id DESC",
        (kp,),
    ).fetchall()
    if not rows:
        return 0
    if rows[0]["result"] != "correct":
        return 0
    streak = 0
    for r in rows:
        if r["result"] == "correct":
            streak += 1
        else:
            break
    return streak


def _last_attribution(conn, kp: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM attempt_events "
        "WHERE kp=? AND result IN ('correct','wrong','partial') AND mode<>'override' "
        "ORDER BY ts DESC, id DESC LIMIT 1",
        (kp,),
    ).fetchone()
    if row is None:
        return None
    override_map = _latest_overrides(conn, None, kp)
    return _resolve_attribution(row, override_map.get(row["id"]))


# --------------------------------------------------------------------------- #
# 薄弱模式卡（v2 蒸馏器 TBD，先建表 + 存取）
# --------------------------------------------------------------------------- #
def patterns_set(conn, pack: str, kp: str, pattern_md: str, source: str,
                 confirmed: bool = False) -> int:
    """写入/更新 (pack, kp) 对应的薄弱模式卡（upsert）。返回 id。"""
    ts = time.time()
    cur = conn.execute(
        """
        INSERT INTO patterns (pack, kp, pattern_md, source, confirmed, created_ts)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(pack, kp) DO UPDATE SET
            pattern_md=excluded.pattern_md,
            source=excluded.source,
            confirmed=excluded.confirmed,
            created_ts=excluded.created_ts
        """,
        (pack, kp, pattern_md, source, int(bool(confirmed)), ts),
    )
    conn.commit()
    return cur.lastrowid


def patterns_get(conn, pack: str, kp: str) -> dict | None:
    """读取 (pack, kp) 对应的薄弱模式卡（最新一条）。无则返回 None。"""
    row = conn.execute(
        "SELECT * FROM patterns WHERE pack=? AND kp=? ORDER BY created_ts DESC, id DESC LIMIT 1",
        (pack, kp),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["confirmed"] = bool(d["confirmed"])
    return d


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _json_or_none(obj):
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, ensure_ascii=False)


def _parse_json(s):
    if s is None:
        return None
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
