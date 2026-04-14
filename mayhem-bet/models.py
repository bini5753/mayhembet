"""
데이터베이스 모델 (SQLite)
- Room: 내기 방
- Participant: 참여자
- SettlementRule: 순위별 정산 규칙
- MatchResult: 판별 결과
"""

import sqlite3
import json
import os
import sys
from pathlib import Path


def _resolve_db_path():
    """OS별 사용자 데이터 디렉토리에 DB를 둔다.
    Windows: %APPDATA%\\MayhemBet
    macOS:   ~/Library/Application Support/MayhemBet
    Linux:   ~/.local/share/MayhemBet
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    data_dir = base / "MayhemBet"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "mayhem_bet.db")


DB_PATH = _resolve_db_path()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS room (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            player_count INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS participant (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            summoner_name TEXT NOT NULL,
            FOREIGN KEY (room_id) REFERENCES room(id)
        );

        CREATE TABLE IF NOT EXISTS settlement_rule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            from_rank INTEGER NOT NULL,
            to_rank INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            FOREIGN KEY (room_id) REFERENCES room(id)
        );

        CREATE TABLE IF NOT EXISTS match_result (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            game_id INTEGER,
            match_number INTEGER NOT NULL,
            results_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (room_id) REFERENCES room(id)
        );
    """)
    conn.commit()
    conn.close()


# ── Room CRUD ──

def create_room(name, player_count, participants, rules):
    """
    내기 방 생성
    participants: ["소환사1", "소환사2", ...]
    rules: [{"from_rank": 2, "to_rank": 1, "amount": 1000}, ...]
    """
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO room (name, player_count) VALUES (?, ?)",
        (name, player_count)
    )
    room_id = cur.lastrowid

    for name_ in participants:
        conn.execute(
            "INSERT INTO participant (room_id, summoner_name) VALUES (?, ?)",
            (room_id, name_)
        )

    for rule in rules:
        conn.execute(
            "INSERT INTO settlement_rule (room_id, from_rank, to_rank, amount) VALUES (?, ?, ?, ?)",
            (room_id, rule["from_rank"], rule["to_rank"], rule["amount"])
        )

    conn.commit()
    conn.close()
    return room_id


def get_room(room_id):
    conn = get_db()
    room = conn.execute("SELECT * FROM room WHERE id = ?", (room_id,)).fetchone()
    if not room:
        conn.close()
        return None

    participants = conn.execute(
        "SELECT * FROM participant WHERE room_id = ?", (room_id,)
    ).fetchall()

    rules = conn.execute(
        "SELECT * FROM settlement_rule WHERE room_id = ? ORDER BY from_rank DESC",
        (room_id,)
    ).fetchall()

    matches = conn.execute(
        "SELECT * FROM match_result WHERE room_id = ? ORDER BY match_number",
        (room_id,)
    ).fetchall()

    conn.close()
    return {
        "id": room["id"],
        "name": room["name"],
        "player_count": room["player_count"],
        "status": room["status"],
        "created_at": room["created_at"],
        "participants": [{"id": p["id"], "summoner_name": p["summoner_name"]} for p in participants],
        "rules": [{"from_rank": r["from_rank"], "to_rank": r["to_rank"], "amount": r["amount"]} for r in rules],
        "matches": [
            {
                "id": m["id"],
                "game_id": m["game_id"],
                "match_number": m["match_number"],
                "results": json.loads(m["results_json"]),
                "created_at": m["created_at"],
            }
            for m in matches
        ],
    }


def get_active_room():
    """현재 활성 방 (한 번에 하나만)"""
    conn = get_db()
    room = conn.execute(
        "SELECT id FROM room WHERE status = 'active' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if room:
        return get_room(room["id"])
    return None


def get_all_rooms():
    conn = get_db()
    rooms = conn.execute("SELECT * FROM room ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rooms]


def add_match_result(room_id, game_id, results):
    """
    매치 결과 저장
    results: [{"name": str, "champion": str, "damage": int}, ...] (순위순)
    """
    conn = get_db()
    # 다음 매치 번호
    row = conn.execute(
        "SELECT COALESCE(MAX(match_number), 0) + 1 as next FROM match_result WHERE room_id = ?",
        (room_id,)
    ).fetchone()
    match_number = row["next"]

    conn.execute(
        "INSERT INTO match_result (room_id, game_id, match_number, results_json) VALUES (?, ?, ?, ?)",
        (room_id, game_id, match_number, json.dumps(results, ensure_ascii=False))
    )
    conn.commit()
    conn.close()
    return match_number


def close_room(room_id):
    conn = get_db()
    conn.execute("UPDATE room SET status = 'closed' WHERE id = ?", (room_id,))
    conn.commit()
    conn.close()
