"""
LCU (League Client Update) API 연동 모듈
- 롤 클라이언트 lockfile에서 포트/토큰 자동 감지
- End of Game 스탯 조회
- 현재 소환사 정보 조회
"""

import os
import time
import threading
import requests
import psutil
import urllib3
import json

# self-signed cert 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 기본 lockfile 경로 (사용자 설정 가능)
DEFAULT_LOCKFILE_PATHS = [
    r"C:\Riot Games\League of Legends\lockfile",
    r"D:\Riot Games\League of Legends\lockfile",
    r"C:\Program Files\Riot Games\League of Legends\lockfile",
    r"D:\Program Files\Riot Games\League of Legends\lockfile",
]


class LCUConnection:
    def __init__(self, custom_lockfile_path=None):
        self.port = None
        self.token = None
        self.base_url = None
        self.auth = None
        self.connected = False
        self.custom_lockfile_path = custom_lockfile_path

    def find_lockfile(self):
        """lockfile 경로를 찾는다. 프로세스 기반 탐색 우선."""
        # 방법 1: 프로세스에서 롤 클라이언트 경로 찾기
        for proc in psutil.process_iter(['name', 'exe']):
            try:
                if proc.info['name'] and 'LeagueClient' in proc.info['name']:
                    exe_path = proc.info.get('exe', '')
                    if exe_path:
                        league_dir = os.path.dirname(exe_path)
                        lockfile = os.path.join(league_dir, 'lockfile')
                        if os.path.exists(lockfile):
                            return lockfile
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # 방법 2: 사용자 지정 경로
        if self.custom_lockfile_path and os.path.exists(self.custom_lockfile_path):
            return self.custom_lockfile_path

        # 방법 3: 기본 경로들 순회
        for path in DEFAULT_LOCKFILE_PATHS:
            if os.path.exists(path):
                return path

        return None

    def connect(self):
        """lockfile을 읽고 LCU API에 연결한다."""
        lockfile_path = self.find_lockfile()
        if not lockfile_path:
            self.connected = False
            return False

        try:
            with open(lockfile_path, 'r') as f:
                content = f.read().strip()
            # lockfile 형식: LeagueClient:pid:port:token:protocol
            parts = content.split(':')
            if len(parts) < 5:
                self.connected = False
                return False

            self.port = int(parts[2])
            self.token = parts[3]
            self.base_url = f"https://127.0.0.1:{self.port}"
            self.auth = ('riot', self.token)
            self.connected = True
            return True
        except Exception as e:
            print(f"[LCU] lockfile 읽기 실패: {e}")
            self.connected = False
            return False

    def get(self, endpoint):
        """LCU API GET 요청"""
        if not self.connected:
            if not self.connect():
                return None
        try:
            resp = requests.get(
                f"{self.base_url}{endpoint}",
                auth=self.auth,
                verify=False,
                timeout=5
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except requests.exceptions.ConnectionError:
            self.connected = False
            return None
        except Exception as e:
            print(f"[LCU] API 요청 실패: {e}")
            return None

    def get_current_summoner(self):
        """현재 로그인된 소환사 정보"""
        data = self.get("/lol-summoner/v1/current-summoner")
        if data:
            return {
                "puuid": data.get("puuid"),
                "name": data.get("gameName", data.get("displayName", "")),
                "tag": data.get("tagLine", ""),
                "summoner_id": data.get("summonerId"),
            }
        return None

    def get_eog_stats(self):
        """End of Game 스탯 블록 (게임 종료 직후 사용 가능)"""
        return self.get("/lol-end-of-game/v1/eog-stats-block")

    def get_gameflow_phase(self):
        """현재 게임 상태 (None, Lobby, ChampSelect, InProgress, EndOfGame 등)"""
        data = self.get("/lol-gameflow/v1/gameflow-phase")
        return data if isinstance(data, str) else None


class GameMonitor:
    """게임 종료를 감지하고 콜백을 호출하는 모니터"""

    def __init__(self, lcu: LCUConnection, on_game_end=None, poll_interval=5):
        self.lcu = lcu
        self.on_game_end = on_game_end
        self.poll_interval = poll_interval
        self.running = False
        self.thread = None
        self.last_phase = None
        self.last_game_id = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        print("[Monitor] 게임 감지 시작")

    def stop(self):
        self.running = False
        print("[Monitor] 게임 감지 중지")

    def _monitor_loop(self):
        while self.running:
            try:
                phase = self.lcu.get_gameflow_phase()

                # EndOfGame 상태 진입 감지
                if phase == "EndOfGame" and self.last_phase != "EndOfGame":
                    print("[Monitor] 게임 종료 감지!")
                    time.sleep(2)  # 스탯 로딩 대기
                    eog = self.lcu.get_eog_stats()
                    if eog and self.on_game_end:
                        game_id = eog.get("gameId")
                        if game_id != self.last_game_id:
                            self.last_game_id = game_id
                            self.on_game_end(eog)

                self.last_phase = phase
            except Exception as e:
                print(f"[Monitor] 에러: {e}")

            time.sleep(self.poll_interval)


NAME_FIELDS = (
    "RIOT_ID_GAME_NAME", "riotIdGameName",
    "gameName", "GAME_NAME",
    "summonerName", "SUMMONER_NAME",
    "displayName",
    "NAME",
)

DAMAGE_KEYS = (
    "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS",
    "totalDamageDealtToChampions",
    "CHAMPIONS_DAMAGE_DEALT",
    "totalDamageDealtToChampionsTotal",
)

CHAMPION_FIELDS = ("championName", "CHAMPION_NAME", "skinName", "SKIN")


def _norm(s):
    """이름 정규화: 소문자 + 공백 제거 + 태그(#KR1) 제거."""
    if not s:
        return ""
    s = str(s).split("#")[0]
    return "".join(s.lower().split())


def _pick(d, keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", 0):
            return v
    return default


def _iter_players(eog_data):
    """EOG 구조 변형 대응: teams[].players[] / players[] / participants[] 모두 시도."""
    if not isinstance(eog_data, dict):
        return
    for team in eog_data.get("teams", []) or []:
        for p in team.get("players", []) or []:
            yield p
    for p in eog_data.get("players", []) or []:
        yield p
    for p in eog_data.get("participants", []) or []:
        yield p


def _player_name(player):
    name = _pick(player, NAME_FIELDS)
    if name:
        return name
    # stats 안에 박혀있는 케이스도 있음
    stats = player.get("stats", {}) or {}
    return _pick(stats, NAME_FIELDS)


def _player_damage(player):
    stats = player.get("stats", {}) or {}
    val = _pick(stats, DAMAGE_KEYS, 0)
    if not val:
        # stats 없이 평탄화된 경우
        val = _pick(player, DAMAGE_KEYS, 0)
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _dump_eog(eog_data):
    """이름 매칭 실패 시 실제 EOG 구조를 사용자 데이터 폴더에 저장 (디버깅용)."""
    try:
        from models import DB_PATH
        out = os.path.join(os.path.dirname(DB_PATH), "last_eog_dump.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(eog_data, f, ensure_ascii=False, indent=2)
        print(f"[LCU] 매칭 실패 — 실제 EOG 구조 저장: {out}")
    except Exception as e:
        print(f"[LCU] EOG 덤프 실패: {e}")


def parse_eog_damage(eog_data, participant_names):
    """
    EOG 데이터에서 참여자들의 챔피언 딜량을 추출한다.
    이름은 태그(#KR1) 제거 + 공백 제거 + 소문자 비교.
    매칭 실패 시 실제 EOG JSON을 디스크에 덤프한다.
    """
    name_set = {_norm(n) for n in participant_names if n}
    results = []
    seen_names = []

    for player in _iter_players(eog_data):
        raw_name = _player_name(player)
        seen_names.append(raw_name)
        if _norm(raw_name) in name_set:
            results.append({
                "name": raw_name,
                "champion": _pick(player, CHAMPION_FIELDS, "Unknown"),
                "damage": _player_damage(player),
            })

    if not results:
        print(f"[LCU] 참여자 매칭 실패. 입력: {list(name_set)} / EOG 이름들: {seen_names}")
        _dump_eog(eog_data)

    results.sort(key=lambda x: x["damage"], reverse=True)
    return results
