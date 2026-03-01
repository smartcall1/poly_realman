# Polymarket Whale Copy Trading Bot

> **고래(Whale)를 찾아라. 그들의 알파를 차용하라.**
> Polymarket 예측 시장에서 검증된 고수익 트레이더의 거래를 실시간으로 자동 복사하는 트레이딩 봇.

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [시스템 아키텍처](#2-시스템-아키텍처)
3. [6단계 트레이딩 파이프라인](#3-6단계-트레이딩-파이프라인)
4. [파일 구조](#4-파일-구조)
5. [설치 및 환경 설정](#5-설치-및-환경-설정)
6. [실행 방법](#6-실행-방법)
7. [모니터링](#7-모니터링)
8. [설정 파라미터 (config.py / .env)](#8-설정-파라미터)
9. [안전장치 총람](#9-안전장치-총람)
10. [수익 시나리오 및 리스크](#10-수익-시나리오-및-리스크)
11. [관련 문서](#11-관련-문서)

---

## 1. 프로젝트 개요

**Whale Copy Trading**은 직접 시장을 예측하는 대신, 이미 일관된 수익을 기록 중인 검증된 트레이더(고래)의 행동을 미러링(Mirroring)하여 그들의 정보 우위(Alpha)를 간접적으로 차용하는 전략이다.

### 왜 Polymarket인가?

Polymarket은 블록체인(Polygon) 기반 **예측 시장**으로, 모든 거래 내역이 온체인에 투명하게 기록된다.
이 투명성 덕분에 누가 얼마나 어떤 방향에 베팅했는지 실시간으로 추적할 수 있다.

```
참가자들은 YES / NO 토큰을 $0 ~ $1 사이에서 거래한다.
  - YES 토큰 현재가 $0.30 = 시장이 30% 확률로 YES를 예측한다는 의미
  - 마켓 종료 시: 정답 토큰 → $1.00 / 오답 토큰 → $0.00 으로 정산
```

봇이 집중하는 마켓:

| 유형 | 설명 | 왜 이 마켓인가 |
|------|------|--------------|
| **5분 UP/DOWN** | BTC/ETH/SOL/XRP 5분 가격 방향 | 결과가 빠름, 유동성 높음 |
| **15분 UP/DOWN** | 동일, 15분 단위 | 반복 패턴 → 고래 실력 검증 가능 |

### 현재 운용 모드

| 항목 | 값 |
|------|----|
| 모드 | `PAPER_TRADING = True` (페이퍼 트레이딩) |
| 초기 자본 | $5,000 USDC |
| 최대 동시 포지션 | 10개 |
| 건당 최대 베팅 | $200 (Kelly 복리 폭주 방지 캡) |

---

## 2. 시스템 아키텍처

```
┌──────────────────────────────────────────────────────────────┐
│                       whale_copy_bot.py                      │
│                                                              │
│   ┌─────────────────┐  ┌──────────────────┐  ┌───────────┐  │
│   │ _maintenance    │  │   run_loop()     │  │ _pending  │  │
│   │   _loop()       │  │   (3초 주기)     │  │  _order   │  │
│   │ [백그라운드]     │  │   메인 루프      │  │   loop()  │  │
│   └────────┬────────┘  └────────┬─────────┘  └─────┬─────┘  │
│            │                   │                   │        │
│            ▼                   ▼                   ▼        │
│   [whale_manager]   [_check_whale_activity]   [지정가 큐]   │
│   [whale_scorer ]   [_settle_positions   ]                  │
│                     [_update_dashboard   ]                  │
└──────────────────────────────────────────────────────────────┘
            │                   │
            ▼                   ▼
   ┌─────────────────┐  ┌────────────────────┐
   │   whales.json   │  │  trade_history.    │
   │  (고래 데이터   │  │  jsonl (거래 로그) │
   │    베이스)      │  └────────────────────┘
   └─────────────────┘
            │
            ▼
   ┌─────────────────┐
   │  dashboard.py   │
   │ (실시간 모니터링)│
   └─────────────────┘
```

### 비동기 병렬 스캐닝

고래 30명의 최신 거래를 순차가 아닌 **동시에(병렬)** 조회하여 감지 지연을 최소화한다.

```python
# asyncio.gather로 전원 동시 조회
tasks = [_check_whale_activity(addr) for addr in active_whales]
await asyncio.gather(*tasks)
```

### 백그라운드 태스크 3종

| 태스크 | 주기 | 역할 |
|--------|------|------|
| `_pending_order_loop` | 1초 | 대기 중인 지정가 주문 체결 재시도 |
| `whale_scorer` | 1시간 | 고래 점수 및 전공 카테고리 갱신 |
| `whale_manager` | 4시간 | 리더보드 Top 500 스캔, 고래 풀 최신화 |

---

## 3. 6단계 트레이딩 파이프라인

```
[Phase 1] 고래 발굴      → whale_manager.py
[Phase 2] 실시간 감시    → 3초 폴링, transactionHash 기반 중복 방지
[Phase 3] 진입 필터      → 6중 안전망 (날짜/가격/만기/포지션 수 등)
[Phase 4] 베팅 규모 산출 → Half Kelly + Scout Bet
[Phase 5] 체결          → VWAP 시뮬레이션, 동적 슬리피지
[Phase 6] 포지션 관리   → TP/SL/Trailing Stop/Mirror Exit/Timeout
```

### Phase 1 — 고래 발굴

1. Polymarket 리더보드 API로 상위 500명 조회
2. 각 후보의 최근 거래 이력 분석 → ROI, 승률 계산
3. **기준 통과**: `ROI > 50%` AND `승률 > 60%`
4. 통과 고래 → `whales.json`에 `status: "active"` 등록
5. **자동 퇴출**: 48시간 내 활동 없거나 성과 기준 미달 시 `inactive` 전환

### Phase 2 — 실시간 감시

- **폴링 주기**: 3초
- **중복 방지**: `transactionHash`를 `seen_txs` 집합에 추가 (10,000건 초과 시 절반 삭제)
- **감지 즉시**: 고래의 `type=TRADE`, `side=BUY` 확인 후 필터 파이프라인 진입

### Phase 3 — 진입 필터 (6단계)

| 순서 | 필터 | 설명 |
|------|------|------|
| 1 | **백로그 방지** | 봇 시작 이전 거래 소급 금지 |
| 2 | **시간 창** | 고래 거래 발생 30분 이내만 처리 |
| 3 | **날짜 파싱** ⭐ | 마켓 제목에서 날짜 직접 파싱 → 어제 마켓 완전 차단 |
| 4 | **가격 상한** | 고래 체결가 ≥ 0.95 → 정산 직전 마켓 스킵 |
| 5 | **Gamma API 확인** | conditionId 불일치/만료/종료 마켓 스킵 (Fail Closed) |
| 6 | **포지션 상한** | 동시 보유 ≥ 10개 → 신규 진입 중단 |

> **날짜 필터가 핵심인 이유**: 일부 고래는 이미 결과가 확정된 "어제 마켓"의 당첨 토큰을 헐값에 사서 정산받는 차익거래를 구사한다. 이를 복사하면 재현 불가능한 허수 수익이 발생하므로 완전 차단한다.

### Phase 4 — 베팅 규모 산출 (Kelly Criterion)

```
공식: f* = p - (1-p) / b
  p = 고래 점수 기반 승률 (score / 100)
  b = 배당률 = (1 - 목표 진입가) / 목표 진입가
```

| 구분 | 조건 | 베팅 규모 |
|------|------|---------|
| **Kelly 베팅** | EV+ (`f > 0`) | `min(Half Kelly, 15%) × Bankroll`, 최대 $200 |
| **Scout 베팅** | EV- (`f ≤ 0`) | `min(1% × Bankroll, $50)` |

동적 슬리피지 (고래 거래 규모 기준):

| 고래 거래 규모 | 슬리피지 허용치 |
|-------------|--------------|
| $100 이하   | 0.5% |
| $1,000 이상 | 8%   |
| $5,000 이상 | 10%  |
| VIP (점수 90+) | 최소 15% 보장 |

### Phase 5 — 체결 (VWAP 시뮬레이션)

```
CLOB API 호가창(asks)을 가격 오름차순으로 소화:
  투자금($200)을 다 쓸 때까지 체결 시뮬레이션 → 예상 VWAP 산출

  VWAP ≤ target_price → 즉시 체결 (FAST EXECUTE)
  VWAP > target_price → 대기열 등록 (10분간 재시도)
  VWAP = None (호가창 비어있음) → Gamma API 현재가로 fallback
    └─ 고래가 대비 20% 이상 괴리 → SKIP (이미 시장 이동)
```

### Phase 6 — 포지션 관리 (Hybrid Exit)

| 우선순위 | 청산 규칙 | 조건 |
|--------|---------|------|
| 1 | **자연 정산** | 마켓 종료 시 WIN/LOSS 정산 |
| 2 | **Take Profit** | ROI ≥ +30% → 즉시 전량 익절 |
| 3 | **Trailing Stop** | 고점 +10% 달성 후 고점 대비 -15% 하락 |
| 4 | **Stop Loss** | ROI ≤ -30% → 즉시 전량 손절 |
| 5 | **Mirror Exit** | 고래가 SELL 발생 → 동반 매도 |
| 6 | **Timeout** | 3일 초과 보유 → 강제 청산 |

재진입 쿨다운: **청산 후 동일 마켓 10분 금지** (무한루프 방지)

---

## 4. 파일 구조

```
polymarket_trader_bot/
│
├── whale_copy_bot.py          # 메인 봇 — 핵심 트레이딩 로직 전체
├── whale_manager.py           # 고래 발굴 및 자동 유지관리
├── whale_scorer.py            # 고래 점수 산출 (ROI/WR/전공 태그)
├── client_wrapper.py          # Polymarket CLOB API 클라이언트 래퍼
├── dashboard.py               # 실시간 터미널 대시보드
├── config.py                  # 환경 변수 로드 및 설정 관리
│
├── whales.json                # 고래 데이터베이스 (자동 생성/갱신)
├── trade_history.jsonl        # 체결된 모든 거래 이력 (1건 = 1줄 JSON)
├── status_WhaleCopy.json      # 대시보드용 봇 상태 스냅샷
├── bot_live.log               # 실시간 봇 실행 로그
│
├── strategy.md                # 전략 상세 설명서 (이 봇의 '설계 철학')
├── docs/
│   └── architecture_and_projection.md  # 아키텍처 및 30일 수익 시나리오
├── tasks/
│   ├── todo.md                # 개발 진행 상황 및 태스크 목록
│   ├── lessons.md             # 디버깅 교훈 및 패턴 정리
│   └── next_steps.md          # 다음 단계 로드맵
│
├── deep_backtester.py         # 전략 백테스팅 엔진
├── whale_backtester.py        # 고래별 성과 백테스팅
├── plot_performance.py        # 수익 곡선 시각화
├── test_api.py                # API 연결 테스트
│
├── .env                       # API 키 및 환경 변수 (Git 제외)
└── requirements.txt           # Python 의존성 패키지 목록
```

---

## 5. 설치 및 환경 설정

### 요구사항

- Python 3.10+
- Polymarket 계정 및 API 자격증명 (CLOB API Key, Secret, Passphrase)
- Polygon 지갑 Private Key

### 설치

```bash
# 1. 가상환경 생성 및 활성화
python -m venv venv
source venv/Scripts/activate        # Windows
# source venv/bin/activate          # Linux/macOS

# 2. 패키지 설치
pip install -r requirements.txt
```

### .env 파일 설정

프로젝트 루트에 `.env` 파일을 생성하고 아래 항목을 설정한다:

```env
# Polymarket API 자격증명
PK=your_polygon_private_key
CLOB_API_KEY=your_clob_api_key
CLOB_API_SECRET=your_clob_api_secret
CLOB_API_PASSPHRASE=your_clob_api_passphrase
POLYMARKET_PROXY_ADDRESS=your_proxy_wallet_address

# 운용 설정
PAPER_TRADING=True            # True = 페이퍼 트레이딩 / False = 실전
INITIAL_BANKROLL=5000.0       # 초기 가상 자본 (페이퍼 트레이딩 시)
DEBUG_MODE=True               # True = 상세 로그 출력
```

---

## 6. 실행 방법

### 메인 봇 실행 (통합 실행)

```bash
python whale_copy_bot.py
```

이 한 줄로 아래 모든 프로세스가 자동 시작된다:

- **실시간 감시**: 3초 간격으로 활성 고래의 최신 거래 추적
- **자동 고래 탐색**: 4시간마다 리더보드 Top 500 스캔
- **자동 스코어링**: 1시간마다 고래 승률/ROI/전공 카테고리 갱신

### 백그라운드 실행 (로그 기록)

```bash
# Windows (Git Bash / WSL)
PYTHONIOENCODING=utf-8 python -u whale_copy_bot.py > bot_live.log 2>&1 &

# 로그 실시간 확인
tail -f bot_live.log
```

### API 연결 테스트

```bash
python test_api.py
```

---

## 7. 모니터링

### 실시간 대시보드

```bash
python dashboard.py
```

현재 보유 포지션, 누적 PnL, 활성 고래 목록을 터미널에서 실시간으로 확인한다.

### 상태 파일

| 파일 | 내용 |
|------|------|
| `status_WhaleCopy.json` | 봇 현재 상태, 잔고, 포지션 요약 |
| `trade_history.jsonl` | 체결된 모든 거래 (진입가, 청산가, PnL 등) |
| `bot_live.log` | 실시간 봇 실행 로그 (필터 판단 근거 포함) |

### 성과 시각화

```bash
python plot_performance.py
```

`trade_history.jsonl`을 기반으로 누적 수익 곡선, 승률, 평균 홀딩 시간 등을 차트로 출력한다.

---

## 8. 설정 파라미터

`config.py` 및 `.env`에서 조정 가능한 핵심 파라미터:

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `PAPER_TRADING` | `True` | 실전/페이퍼 트레이딩 전환 |
| `INITIAL_BANKROLL` | `$5,000` | 초기 가상 자본 |
| `MAX_BET_SIZE` | `$200` | 건당 최대 베팅 한도 (Kelly 폭주 방지) |
| `MAX_POSITIONS` | `10` | 동시 보유 포지션 최대 수 |
| `POLL_INTERVAL` | `3초` | 고래 활동 감지 주기 |
| `PENDING_EXPIRY` | `10분` | 대기열 주문 만료 시간 |
| `COOLDOWN_MINUTES` | `10분` | 청산 후 동일 마켓 재진입 금지 시간 |
| `WHALE_MIN_ROI` | `50%` | 고래 등록 최소 ROI 기준 |
| `WHALE_MIN_WINRATE` | `60%` | 고래 등록 최소 승률 기준 |

---

## 9. 안전장치 총람

| 안전장치 | 목적 |
|---------|------|
| `startup_time` 백로그 필터 | 봇 시작 이전 거래 소급 복사 방지 |
| 날짜 직접 파싱 필터 | 어제 마켓 정산 차익거래(허수 수익) 완전 차단 |
| `price >= 0.95` 필터 | 정산 직전 마켓(호가창 고갈) 진입 방지 |
| Gamma API Fail Closed | conditionId 불일치 → 무조건 SKIP |
| VWAP 시뮬레이션 | 실제 체결 가능한 가격만 진입 허용 |
| Fallback 20% 괴리 필터 | 고래 진입가와 현재가 크게 다를 때 SKIP |
| `MAX_BET_SIZE = $200` 캡 | Kelly 복리 기하급수 증가 방지 |
| `MAX_POSITIONS = 10` | 과도한 자본 분산 방지 |
| 재진입 쿨다운 10분 | 동일 마켓 무한루프 진입 방지 |
| `transactionHash` 중복 방지 | 동일 거래 반복 처리 완전 차단 |
| Trailing Stop | 수익 보호 (고점 대비 -15%) |
| Hard Stop Loss -30% | 대형 손실 조기 차단 |
| 3일 타임아웃 | 장기 부진 포지션 강제 청산 |

---

## 10. 수익 시나리오 및 리스크

### 단일 거래 예시

```
고래 BTC UP 매수, 가격 $0.30
우리 진입: 슬리피지 5% → VWAP $0.312 → 체결
투자금 $200, 수수료 1% 차감 → 확보 shares = 634.6

케이스 A (YES 정산): 634.6 × $0.98 = +$421.9 수익 (+211%)
케이스 B (Take Profit): 634.6 × $0.397 = +$51.9 수익 (+26%)
케이스 C (Stop Loss): 634.6 × $0.215 = -$63.6 손실 (-32%)
```

### 30일 시나리오 (자세한 내용: `docs/architecture_and_projection.md`)

| 시나리오 | 30일 PnL | ROI | 전제 조건 |
|---------|---------|-----|---------|
| Ultra Conservative | -$873 | -17.5% | Kelly 유효 WR 55% |
| **Base Case** | **-$196** | **-3.9%** | Kelly 유효 WR 60% |
| Realistic Upside | +$481 | +9.6% | Kelly 유효 WR 65% |
| Best Case | +$1,158 | +23.2% | Kelly 유효 WR 70% |

### 왕복 비용 구조

| 비용 항목 | 비율 |
|---------|------|
| 거래 수수료 | 1% |
| 매수 슬리피지 | 3% ~ 15% |
| 매도 슬리피지 | 2% |
| 정산 수수료 | 2% |
| **총 왕복 비용** | **~8% ~ 20%** |

### 알려진 구조적 리스크

| 리스크 | 내용 |
|--------|------|
| 진입 지연 | 고래 감지 후 최소 3~10초 경과 → 가격 이미 이동 |
| 고래 의존성 | 활동하는 고래가 없으면 체결 0건 |
| API 의존성 | Polymarket API 다운 시 신호 감지 불가 |
| Paper vs 실전 | 실전에서는 실제 유동성, 가스비, Oracle 지연 추가 |

---

## 11. 관련 문서

| 문서 | 설명 |
|------|------|
| [`strategy.md`](./strategy.md) | 전략 전문 설명서 — 6단계 파이프라인 상세 로직 및 수식 |
| [`docs/architecture_and_projection.md`](./docs/architecture_and_projection.md) | 아키텍처 심화 분석 및 데이터 기반 30일 수익 예측 |
| [`tasks/lessons.md`](./tasks/lessons.md) | 디버깅 교훈 및 핵심 버그 해결 패턴 |
| [`tasks/todo.md`](./tasks/todo.md) | 개발 진행 상황 및 체크리스트 |

---

> *"시장에서 가장 똑똑한 플레이어를 찾아내어, 그 위에 올라타라."*