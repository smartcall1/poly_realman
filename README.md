# 🏆 POLYMARKET HATEBOT (v3.0)
> **"The Fact-Only Sniper"**
> **"상상은 하지 않음. 오직 검증된 팩트(Fact)와 엣지(Edge)로만 사냥함."**

**POLYMARKET HATEBOT v3.0**은 Polymarket 5분/15분 바이너리 옵션 시장을 위한 **초정밀 스나이퍼 봇**임.
과거의 무지성 베팅을 버리고, **Fact-Only(진실 우선)** 원칙을 탑재하여 뇌피셜 수익률이 아닌 **진짜 꽂히는 돈**을 추적함.

---

## 🔥 핵심 기능 (Key Features v3.0)

### 1. 팩트 온리 시스템 (Fact-Only Mode)
- **No Hallucination**: 봇이 계산한 가상 PnL 따윈 믿지 않음. 오직 **Polymarket 실제 잔액(Real Balance)과 포트폴리오 가치**만 표시함.
- **Real-time Sync**: 진입 즉시 지갑 잔액을 동기화하여, 슬리피지와 수수료가 반영된 '진짜 현실'을 보여줌.

### 2. 정밀 타격 & 철통 방어 (Risk Control)
- **Absolute Cap (절대 한도)**: 비율 베팅이 위험하다고? **"한 판에 $50 이상 절대 금지"** 같은 절대 금액(`MAX_BET_AMOUNT`) 제한 기능 탑재.
- **Smart Entry**: 무조건 진입하지 않음. **최소 엣지(Min Edge) 3%** 이상 확실한 기회에만 방아쇠를 당김.
- **Anti-Hedging**: 같은 질문에 양방(Yes/No) 걸어서 수수료 낭비하는 짓 안 함.

### 3. 신뢰할 수 있는 모의 투자 (Verified Paper Trading)
- **Exact Maturity Settlement (정밀 만기 판정)**: 봇이 정산할 때(예: 3시 10분 30초)의 가격을 대충 쓰지 않음. 기다렸다가 **정확히 만기된 시점(3시 10분 0초)**의 캔들 데이터를 조회하여 판정함. (시간 차이에 따른 오차 0%)
- **Real Orderbook**: 가상이라도 체결은 **실제 폴리마켓 실시간 호가**를 가져와서 시뮬레이션함. (실전과 99% 유사)

---

## 🛠 설치 및 실행 (Setup)

### 1. 환경 설정
```bash
# 필수 라이브러리 설치 실시
pip install -r requirements.txt
```

### 2. 환경 변수 (.env)
`.env` 파일 생성 후 아래 내용 작성. (특히 `MAX_BET_AMOUNT` 조절 필수)

```ini
# === Polymarket API Configuration ===
PK=0x...                     # 본인의 Private Key
CLOB_API_KEY=...             # API Key
CLOB_API_SECRET=...          # API Secret
CLOB_API_PASSPHRASE=...      # API Passphrase
POLYMARKET_PROXY_ADDRESS=0x... # (실전 필수) Polymarket 프록시 지갑 주소

# === 기본 운영 설정 ===
PAPER_TRADING=True       # [강력 추천] True: 모의 투자 (돈 안 듦), False: 실전 (진짜 돈 삭제됨)
INITIAL_BANKROLL=3600.0  # 시작 자본금 설정
DEBUG_MODE=True          # True: 상세 로그

# === 전략 및 리스크 관리 (중요) ===
MIN_EDGE=0.03            # 최소 3% 이상 유리할 때만 진입
MAX_BET_AMOUNT=20.0      # [NEW] 1회 최대 베팅 절대 금액 ($달러) -> 안전장치!
MAX_BET_FRACTION=0.10    # 1회 최대 뱅크롤 대비 비율 (둘 중 작은 값 적용)
MIN_BET_USDC=1.0         # 최소 베팅 금액 ($1 미만 무시)
```

### 3. 실행
```bash
# 가상/실전 모드는 .env에서 설정함
python main.py
```
**명령 하달 끝. 즉시 실행할 것.**

---

## ⚠️ 주의사항 (Disclaimer)
- **Paper Trading(모의 투자)**으로 수익성을 충분히 검증한 뒤 실전에 투입할 것.
- **책임은 전적으로 사용자 본인에게 있음.** (돈 잃고 봇 탓하기 없기)

---
**Powered by donemoji** 🚀