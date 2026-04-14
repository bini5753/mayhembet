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


def parse_eog_damage(eog_data, participant_names):
    """
    EOG 데이터에서 참여자들의 챔피언 딜량을 추출한다.
    
    Args:
        eog_data: LCU EOG stats block
        participant_names: 내기 참여자 소환사명 리스트 (소문자 비교)
    
    Returns:
        list of dict: [{"name": str, "champion": str, "damage": int}, ...]
        순위 정렬됨 (딜량 내림차순)
    """
    results = []
    teams = eog_data.get("teams", [])

    # 모든 플레이어 데이터 수집
    all_players = []
    for team in teams:
        for player in team.get("players", []):
            all_players.append(player)

    # 참여자 이름 매칭 (대소문자 무시)
    name_set = {n.lower() for n in participant_names}

    for player in all_players:
        # 소환사명 확인 (여러 필드 시도)
        summoner_name = (
            player.get("gameName", "") or
            player.get("summonerName", "") or
            ""
        )
        if summoner_name.lower() in name_set:
            stats = player.get("stats", {})
            damage = stats.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS", 0)
            champion = player.get("championName", "Unknown")
            results.append({
                "name": summoner_name,
                "champion": champion,
                "damage": int(damage),
            })

    # 딜량 내림차순 정렬
    results.sort(key=lambda x: x["damage"], reverse=True)
    return results
