# 증강 칼바람 딜량 내기 정산기 — 기술 구조 설명서

이 문서는 프로젝트의 전체 동작 원리를 설명합니다.
Claude Code에서 작업할 때 이 파일을 참고하세요.

---

## 전체 아키텍처

```
┌─────────────────┐     lockfile      ┌──────────────┐
│  롤 클라이언트    │ ───────────────→ │              │
│  (LeagueClient)  │                  │   Python     │
│                  │ ← HTTPS(LCU) →  │   백엔드     │
│  - 게임 진행     │  127.0.0.1:포트   │   (Flask)    │
│  - EOG 스탯 제공  │                  │              │
└─────────────────┘                  │  - lcu.py    │
                                     │  - models.py │
┌─────────────────┐   HTTP :5000     │  - betting.py│
│   브라우저 UI     │ ← ──────── →    │  - routes.py │
│   (index.html)   │                  │  - app.py    │
│                  │                  │              │
│  - 방 생성       │   JSON API       │      ↕       │
│  - 결과 표시     │                  │  SQLite DB   │
│  - 정산 화면     │                  │              │
└─────────────────┘                  └──────────────┘
```

핵심 흐름: 롤 클라이언트 → LCU API → Python 백엔드 → SQLite 저장 → 브라우저에 표시

---

## 파일별 역할과 동작 원리

### 1. `lcu.py` — LCU 연동 (가장 중요한 파일)

롤 클라이언트는 실행 중일 때 로컬에서 HTTPS REST API를 노출합니다.
이걸 LCU(League Client Update) API라고 부릅니다.

#### lockfile이란?

롤이 설치된 폴더에 `lockfile`이라는 파일이 생깁니다.
클라이언트가 켜져 있을 때만 존재하고, 내용은 이렇게 생겼습니다:

```
LeagueClient:12345:8765:aBcDeFgHiJkL:https
```

콜론으로 구분된 5개 필드:
- `LeagueClient` — 고정값
- `12345` — 프로세스 ID
- `8765` — **API 포트** (매번 랜덤)
- `aBcDeFgHiJkL` — **인증 토큰**
- `https` — 프로토콜

이 포트와 토큰으로 `https://127.0.0.1:{포트}` 에 접속합니다.

#### lockfile 찾는 순서

```python
# 1순위: psutil로 LeagueClient 프로세스 찾기 → exe 경로에서 lockfile 위치 추론
# 2순위: 사용자가 지정한 커스텀 경로
# 3순위: 기본 경로 목록 순회 (C:\Riot Games\..., D:\Riot Games\... 등)
```

프로세스 기반 탐색이 가장 정확합니다. 롤 설치 위치가 어디든 찾을 수 있습니다.

#### LCU API 인증 방식

```python
requests.get(
    f"https://127.0.0.1:{port}/엔드포인트",
    auth=('riot', token),        # Basic Auth
    verify=False,                # self-signed cert이므로 검증 스킵
    timeout=5
)
```

#### 사용하는 LCU 엔드포인트

| 엔드포인트 | 용도 |
|-----------|------|
| `GET /lol-summoner/v1/current-summoner` | 현재 로그인된 소환사 정보 (이름, PUUID 등) |
| `GET /lol-gameflow/v1/gameflow-phase` | 현재 게임 상태 (문자열 하나 반환) |
| `GET /lol-end-of-game/v1/eog-stats-block` | 게임 종료 직후 상세 스탯 |

#### gameflow-phase 값 종류

```
None → Lobby → Matchmaking → ReadyCheck → ChampSelect → InProgress → PreEndOfGame → EndOfGame → None
```

우리가 감지하는 건 `InProgress` → `EndOfGame` 전환 시점입니다.

#### EOG(End of Game) 데이터 구조 (핵심!)

게임이 끝나면 `/lol-end-of-game/v1/eog-stats-block`에서 이런 구조를 반환합니다:

```json
{
  "gameId": 1234567890,
  "gameMode": "ARAM",
  "teams": [
    {
      "teamId": 100,
      "players": [
        {
          "gameName": "소환사명",
          "summonerName": "소환사명",
          "championName": "Lux",
          "stats": {
            "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS": 25000,
            "TOTAL_DAMAGE_DEALT": 45000,
            "KILLS": 5,
            "DEATHS": 3,
            "ASSISTS": 10
          }
        }
      ]
    },
    {
      "teamId": 200,
      "players": [ ... ]
    }
  ]
}
```

**주의**: 이 구조는 비공식이라 패치마다 바뀔 수 있습니다.
만약 필드명이 다르면 `parse_eog_damage()` 함수를 수정하세요.

#### GameMonitor 동작 방식

```
메인 스레드: Flask 웹 서버
백그라운드 스레드: GameMonitor (5초 간격 폴링)

GameMonitor 루프:
  1. gameflow-phase 조회
  2. 이전 상태와 비교
  3. "EndOfGame"로 바뀌면:
     a. 2초 대기 (스탯 로딩)
     b. eog-stats-block 조회
     c. game_id 중복 체크
     d. on_game_end 콜백 호출
  4. 5초 sleep 후 반복
```

---

### 2. `models.py` — 데이터베이스

SQLite를 사용합니다. `mayhem_bet.db` 파일이 자동 생성됩니다.

#### 테이블 구조

```
room (내기 방)
├── id (PK)
├── name (방 이름)
├── player_count (참여 인원수)
├── status ('active' | 'closed')
└── created_at

participant (참여자)
├── id (PK)
├── room_id (FK → room)
└── summoner_name (소환사명)

settlement_rule (정산 규칙)
├── id (PK)
├── room_id (FK → room)
├── from_rank (지불하는 등수, 예: 3)
├── to_rank (받는 등수, 예: 2)
└── amount (금액, 원)

match_result (판별 결과)
├── id (PK)
├── room_id (FK → room)
├── game_id (LCU의 gameId)
├── match_number (1, 2, 3...)
├── results_json (딜량 순위 JSON 문자열)
└── created_at
```

#### results_json 형식

```json
[
  {"name": "Player1", "champion": "럭스", "damage": 25000},
  {"name": "Player2", "champion": "이즈리얼", "damage": 20000},
  {"name": "Player3", "champion": "가렌", "damage": 15000}
]
```

딜량 내림차순 정렬 (1등이 인덱스 0).

---

### 3. `betting.py` — 정산 로직

#### 매 판 정산 (calculate_match_transfers)

규칙 예시 (3명):
```
rules = [
  {from_rank: 3, to_rank: 2, amount: 1000},  # 3등→2등 1000원
  {from_rank: 2, to_rank: 1, amount: 1000},  # 2등→1등 1000원
]
```

1판 결과가 [Player1, Player2, Player3] (딜량순) 이면:
- 3등(Player3) → 2등(Player2)에게 1000원
- 2등(Player2) → 1등(Player1)에게 1000원

이 판의 수지: Player1 +1000, Player2 ±0 (받고 내고), Player3 -1000

#### 다판 누적 (calculate_settlement)

모든 판의 transfers를 누적해서 각 플레이어의 총 수지를 계산합니다.

```
totals = {
  "Player1": +3000,   # 3판 동안 누적으로 3000원 받을 사람
  "Player2": -1000,   # 1000원 낼 사람
  "Player3": -2000,   # 2000원 낼 사람
}
```

#### 부채 간소화 (simplify_debts)

최종 정산에서 불필요한 거래를 줄입니다.

```
예시:
  A: -3000, B: +1000, C: +2000

간소화 결과:
  A → B에게 1000원
  A → C에게 2000원

(A→B→C 같은 중간 거래를 없앰)
```

알고리즘: 빚진 사람(음수)과 받을 사람(양수)을 각각 정렬한 뒤,
큰 금액부터 매칭하여 최소 거래 횟수로 정산합니다.

---

### 4. `routes.py` — API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/lcu/status` | LCU 연결 상태, 소환사 정보, 게임 상태 |
| POST | `/api/room` | 새 내기 방 생성 |
| GET | `/api/room/active` | 현재 활성 방 조회 |
| GET | `/api/room/<id>` | 특정 방 상세 조회 |
| POST | `/api/room/<id>/close` | 방 종료 |
| POST | `/api/room/<id>/match` | 수동 매치 결과 추가 |
| GET | `/api/room/<id>/settlement` | 정산 계산 결과 |
| GET | `/api/rooms` | 전체 방 목록 |

#### 요청/응답 예시

**방 생성 (POST /api/room)**
```json
// Request
{
  "name": "오늘 내기",
  "participants": ["Hide", "Faker", "Zeus"],
  "rules": [
    {"from_rank": 3, "to_rank": 2, "amount": 1000},
    {"from_rank": 2, "to_rank": 1, "amount": 1000}
  ]
}

// Response
{"room_id": 1, "message": "방이 생성되었습니다"}
```

**정산 조회 (GET /api/room/1/settlement)**
```json
{
  "settlement": {
    "per_match": [
      {
        "match_number": 1,
        "rankings": [
          {"rank": 1, "name": "Faker", "champion": "럭스", "damage": 25000},
          {"rank": 2, "name": "Hide", "champion": "이즈", "damage": 20000},
          {"rank": 3, "name": "Zeus", "champion": "가렌", "damage": 15000}
        ],
        "transfers": [
          {"from": "Zeus", "to": "Hide", "amount": 1000},
          {"from": "Hide", "to": "Faker", "amount": 1000}
        ]
      }
    ],
    "totals": {"Faker": 1000, "Hide": 0, "Zeus": -1000},
    "final_transfers": [{"from": "Zeus", "to": "Faker", "amount": 1000}]
  }
}
```

---

### 5. `app.py` — 메인 서버

모든 것을 연결하는 진입점입니다.

```
실행 순서:
1. Flask 앱 생성
2. LCUConnection 인스턴스 생성
3. on_game_end 콜백 함수 정의
4. GameMonitor 생성 (콜백 연결)
5. DB 초기화 (init_db)
6. LCU 연결 시도 (실패해도 서버는 시작됨)
7. Flask 서버 시작 (0.0.0.0:5000)
```

#### on_game_end 콜백 흐름

```
게임 종료 감지 (GameMonitor)
  ↓
on_game_end(eog_data) 호출
  ↓
get_active_room()으로 현재 활성 방 조회
  ↓
방의 참여자 소환사명 목록 추출
  ↓
parse_eog_damage(eog_data, names)로 딜량 추출 & 순위 정렬
  ↓
add_match_result()로 DB에 저장
  ↓
콘솔에 결과 출력
```

---

### 6. `templates/index.html` — 프론트엔드

싱글 페이지 앱(SPA)입니다. 3개의 뷰를 전환합니다.

```
viewSetup (방 생성)
  ↓ 방 생성 성공
viewActive (게임 진행중)
  ├── tabGames (게임 결과 목록)
  └── tabSettlement (실시간 정산)
  ↓ 내기 종료
viewFinal (최종 정산)
```

#### 폴링 (3초 간격)

방이 활성 상태일 때 3초마다:
1. `/api/lcu/status` — LCU 연결 상태 + 게임 상태 업데이트
2. `/api/room/{id}` — 새 매치가 추가됐는지 확인 → UI 갱신

---

## 디버깅 가이드

### LCU 연결이 안 될 때

```python
# lcu.py에서 직접 테스트
from lcu import LCUConnection
lcu = LCUConnection()
print(lcu.find_lockfile())  # None이면 lockfile을 못 찾는 것
print(lcu.connect())        # False면 lockfile 파싱 실패
```

lockfile을 못 찾으면:
1. 롤 클라이언트가 켜져 있는지 확인
2. 탐색기에서 롤 설치 폴더에 `lockfile`이 있는지 직접 확인
3. `lcu.py`의 `DEFAULT_LOCKFILE_PATHS`에 해당 경로 추가

### EOG 데이터 필드가 다를 때

게임 끝난 직후, 브라우저 콘솔이나 Python에서:

```python
from lcu import LCUConnection
import json

lcu = LCUConnection()
lcu.connect()
eog = lcu.get_eog_stats()
print(json.dumps(eog, indent=2, ensure_ascii=False))
```

이걸로 실제 데이터 구조를 확인하고 `parse_eog_damage()`의 필드명을 맞추세요.

주로 바뀔 수 있는 부분:
- `player.get("gameName")` → 다른 필드명일 수 있음
- `stats.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS")` → 키 이름이 다를 수 있음
- `teams` → `players` 구조가 다를 수 있음

### 정산이 이상할 때

```python
from betting import calculate_settlement
import json

# 테스트 데이터로 직접 확인
matches = [
    {"results": [
        {"name": "A", "champion": "럭스", "damage": 30000},
        {"name": "B", "champion": "이즈", "damage": 20000},
    ]}
]
rules = [{"from_rank": 2, "to_rank": 1, "amount": 1000}]
result = calculate_settlement(matches, rules)
print(json.dumps(result, indent=2, ensure_ascii=False))
```

---

## Claude Code에서 작업할 때

이 프로젝트를 Claude Code로 이어서 개발하려면:

```bash
# 1. 프로젝트 폴더로 이동
cd mayhem-bet

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 서버 실행
python app.py

# 4. 다른 터미널에서 API 테스트
curl http://localhost:5000/api/lcu/status
```

Claude Code에게 요청할 수 있는 작업들:
- "롤 클라이언트 켜고 LCU 연결 테스트해줘"
- "EOG 데이터 구조 확인하고 parse_eog_damage 수정해줘"
- "게임 한 판 끝난 뒤 자동 기록 되는지 확인해줘"
- "UI에서 XX 기능 추가해줘"
