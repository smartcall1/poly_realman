# 🏆 POLYMARKET HATEBOT (v2.1)
> **"The Universal Best Trader"**
> **"애매함은 죄악임. 단 0.1%라도 유리하면 무조건 진입함."**

**POLYMARKET HATEBOT**은 Polymarket 5분/15분 바이너리 옵션(Up/Down) 시장을 위한 초고빈도/공격적 트레이딩 봇임.
수학적 확률 모델(Black-Scholes)과 Binance 기반 전문가 직관(Expert Signal)을 결합, **모든 시장 주기마다 승률 높은 쪽(YES or NO)을 필히 선택**하고 공격적으로 배팅함.

---

## 🔥 핵심 기능 (Key Features)

### 1. 지상 최강 트레이더 모드 (Universal Best Mode)
- **Absolution Decision (절대 택일)**: 관망 따윈 없음. 갱신 주기마다 승률 우위인 쪽을 '필승 방향'으로 간주, 즉각 진입함.
- **Force Entry (강제 진입)**: 추세 강도(Strength) **0.4 이상**이면, 수학적 기대값(Edge)이 마이너스(-30%)라도 "결국 추세가 이김" 판단 하에 진입 강행함.
- **No Idle Time**: 5분/15분마다 열리는 타겟 마켓(BTC, ETH, SOL, XRP) 끊임없이 사냥함.

### 2. 공격적 사이징 & 리스크 관리
- **Aggressive Kelly Sizing**: 전문가 신호 강력(>0.7) 시, 기본 켈리 배팅의 **4배(4x)**까지 풀 레버리지 당김.
- **Force Minimum**: 짤짤이는 거절함. 판당 최소 **$10** 또는 **뱅크롤의 1%** 중 큰 금액 무조건 태움.
- **Equity-Based Drawdown**: 현금이 아닌 **(현금 + 투자금)** 합산 **총자산(Equity)** 기준 리스크 관리. 풀-시드 배팅 중에도 봇 멈추지 않음.
- **Safety Cap**: 봇 과열 방지용 단일 배팅 최대 **$30** 제한 걸어둠. (안전장치)

### 3. 모바일 최적화 UI
- Termux 등 모바일 환경에서도 편안하게 볼 수 있게 **48자 폭** 최적화 대시보드 제공함.
- **Real-time PnL**: 확정 수익뿐 아니라, 현재 보유 포지션 평가 손익(**Unrealized PnL**) 실시간 표시함.
- **Strike Price Marking**: 동일 코인 다중 포지션 구분 위해 행사 가격($) 명확히 표시함.

---

## 🛠 설치 및 실행 (Setup)

### 1. 환경 설정
```bash
# 필수 라이브러리 설치 실시
pip install -r requirements.txt
```

### 2. 환경 변수 (.env)
`.env` 파일 생성 후 클럽(CLOB) API 키 입력할 것.
```ini
# Polymarket Proxy Key (필수)
PK=YOUR_PRIVATE_KEY_HERE
CLOB_API_KEY=YOUR_API_KEY
CLOB_API_SECRET=YOUR_API_SECRET
CLOB_API_PASSPHRASE=YOUR_PASSPHRASE

# 기본 운영 설정
PAPER_TRADING=False      # True: 모의 투자, False: 실전(Real Money)
INITIAL_BANKROLL=1100.0  # 시작 자본금 (API 연결 실패 시 사용됨)
DEBUG_MODE=True          # True: 상세 로그 출력

# 전략 튜닝 (고급 사용자용)
MIN_EDGE=0.03            # 최소 우위 (Edge) 3% 이상일 때만 진입
KELLY_FRACTION=0.25      # 켈리 공식의 25%만 배팅 (리스크 관리)
MAX_BET_FRACTION=0.10    # 1회 최대 배팅 금액 (뱅크롤 대비 10% 제한)
MIN_BET_USDC=1.0         # 최소 베팅 금액 ($1 미만 무시)

# 리스크 관리
MAX_CONCURRENT_BETS=10   # 동시 최대 포지션 수
DRAWDOWN_HALT_PCT=0.50   # 뱅크롤 50% 이상 손실 시 봇 정지
```

### 3. 실행
```bash
python main.py
```
**명령 하달 끝. 실행할 것.**

---

## ⚠️ 주의사항 (Disclaimer)
본 소프트웨어는 **매우 공격적(Aggressive)** 전략을 사용함.
- '지상 최강 트레이더' 모드는 높은 변동성을 동반함.
- 원금 손실 위험 있으며, 모든 책임은 사용자 본인에게 있음.
- 충분한 **Paper Trading(모의 투자)** 후 실전 투입 요망.

---
**Powered by donemoji** 🚀