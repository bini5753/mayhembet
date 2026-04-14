"""
증강 칼바람 딜량 내기 정산기
메인 서버 (Flask)
"""

import os
import sys
import threading
import webbrowser
from flask import Flask, render_template
from models import init_db, get_active_room, add_match_result, DB_PATH
from routes import api, set_lcu
from lcu import LCUConnection, GameMonitor, parse_eog_damage


def _resource_path(rel):
    """PyInstaller --onefile 환경에서 templates/static 경로 보정."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


app = Flask(
    __name__,
    static_folder=_resource_path("static"),
    template_folder=_resource_path("templates"),
)
app.register_blueprint(api)

lcu = LCUConnection()


def on_game_end(eog_data):
    """게임 종료 시 콜백 - 딜량 데이터 자동 저장"""
    room = get_active_room()
    if not room:
        print("[App] 활성 방이 없어 결과를 저장하지 않습니다")
        return

    participant_names = [p["summoner_name"] for p in room["participants"]]
    results = parse_eog_damage(eog_data, participant_names)

    if not results:
        print("[App] 참여자 딜량 데이터를 찾을 수 없습니다")
        return

    game_id = eog_data.get("gameId")
    match_number = add_match_result(room["id"], game_id, results)
    print(f"[App] {match_number}판 결과 저장 완료:")
    for i, r in enumerate(results):
        print(f"  {i+1}등: {r['name']} ({r['champion']}) - {r['damage']:,} 딜량")


monitor = GameMonitor(lcu, on_game_end=on_game_end, poll_interval=5)
set_lcu(lcu, monitor)


@app.route("/")
def index():
    return render_template("index.html")


PORT = 5001


def _open_browser():
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    print("=" * 50)
    print("  증강 칼바람 딜량 내기 정산기")
    print(f"  http://localhost:{PORT} 에서 접속하세요")
    print(f"  데이터 저장 위치: {DB_PATH}")
    print("=" * 50)

    init_db()

    if lcu.connect():
        summoner = lcu.get_current_summoner()
        if summoner:
            print(f"  롤 클라이언트 연결됨: {summoner['name']}#{summoner['tag']}")
        monitor.start()
    else:
        print("  롤 클라이언트 미감지 (게임 시작 후 자동 연결됩니다)")
        monitor.start()

    print("=" * 50)
    print("  창을 닫으면 종료됩니다. 브라우저가 자동으로 열립니다.")
    print("=" * 50)

    threading.Timer(1.5, _open_browser).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
