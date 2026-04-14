"""
Flask API 라우트
- 방 생성/조회/종료
- LCU 상태 확인
- 매치 결과 조회
- 정산 계산
"""

from flask import Blueprint, request, jsonify
from models import (
    create_room, get_room, get_active_room, get_all_rooms,
    add_match_result, close_room
)
from betting import calculate_settlement

api = Blueprint("api", __name__)

# 전역 참조 (app.py에서 설정)
lcu_conn = None
game_monitor = None


def set_lcu(conn, monitor):
    global lcu_conn, game_monitor
    lcu_conn = conn
    game_monitor = monitor


# ── LCU 상태 ──

@api.route("/api/lcu/status")
def lcu_status():
    if not lcu_conn:
        return jsonify({"connected": False, "error": "LCU 모듈 미초기화"})

    connected = lcu_conn.connect()
    summoner = lcu_conn.get_current_summoner() if connected else None
    phase = lcu_conn.get_gameflow_phase() if connected else None

    return jsonify({
        "connected": connected,
        "summoner": summoner,
        "gameflow_phase": phase,
    })


# ── 방 관리 ──

@api.route("/api/room", methods=["POST"])
def create_room_api():
    data = request.json
    name = data.get("name", "내기")
    participants = data.get("participants", [])
    rules = data.get("rules", [])

    if len(participants) < 2 or len(participants) > 5:
        return jsonify({"error": "참여자는 2~5명이어야 합니다"}), 400

    if not rules:
        return jsonify({"error": "정산 규칙을 설정해주세요"}), 400

    room_id = create_room(name, len(participants), participants, rules)

    # 게임 모니터에 참여자 등록
    if game_monitor:
        game_monitor.start()

    return jsonify({"room_id": room_id, "message": "방이 생성되었습니다"})


@api.route("/api/room/active")
def active_room_api():
    room = get_active_room()
    if not room:
        return jsonify({"room": None})
    return jsonify({"room": room})


@api.route("/api/room/<int:room_id>")
def get_room_api(room_id):
    room = get_room(room_id)
    if not room:
        return jsonify({"error": "방을 찾을 수 없습니다"}), 404
    return jsonify({"room": room})


@api.route("/api/room/<int:room_id>/close", methods=["POST"])
def close_room_api(room_id):
    room = get_room(room_id)
    if not room:
        return jsonify({"error": "방을 찾을 수 없습니다"}), 404

    close_room(room_id)
    if game_monitor:
        game_monitor.stop()

    return jsonify({"message": "방이 종료되었습니다"})


@api.route("/api/rooms")
def list_rooms_api():
    rooms = get_all_rooms()
    return jsonify({"rooms": rooms})


# ── 매치 결과 ──

@api.route("/api/room/<int:room_id>/match", methods=["POST"])
def add_match_api(room_id):
    """수동으로 매치 결과 추가 (테스트/백업용)"""
    data = request.json
    results = data.get("results", [])
    game_id = data.get("game_id")

    if not results:
        return jsonify({"error": "결과 데이터가 없습니다"}), 400

    match_number = add_match_result(room_id, game_id, results)
    return jsonify({"match_number": match_number, "message": f"{match_number}판 결과 저장"})


# ── 정산 ──

@api.route("/api/room/<int:room_id>/settlement")
def settlement_api(room_id):
    room = get_room(room_id)
    if not room:
        return jsonify({"error": "방을 찾을 수 없습니다"}), 404

    if not room["matches"]:
        return jsonify({"error": "아직 진행된 게임이 없습니다"}), 400

    settlement = calculate_settlement(room["matches"], room["rules"])
    return jsonify({"settlement": settlement})
