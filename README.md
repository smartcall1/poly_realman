# 🏆 POLYMARKET HATEBOT (v2.1)
> **"The Universal Best Trader"**  
> **"애매함은 죄악이다. 단 0.1%라도 유리하면 무조건 진입한다."**

**POLYMARKET HATEBOT**은 Polymarket의 5분/15분 바이너리 옵션(Up/Down) 시장을 위한 초고빈도/공격적 트레이딩 봇입니다.  
수학적 확률 모델(Black-Scholes)과 Binance 기반의 전문가 직관(Expert Signal)을 결합하여, **모든 시장 주기마다 승률이 높은 쪽(YES or NO)을 반드시 선택**하고 공격적으로 베팅합니다.

---

## 🔥 핵심 기능 (Key Features)

### 1. 지상 최강 트레이더 모드 (Universal Best Mode)
- **Absolution Decision (절대 택일)**: 시장을 관망하지 않습니다. 모든 갱신 주기마다 YES와 NO의 확률을 비교하여, 승률 우위가 있는 쪽을 '필승 방향'으로 간주하고 즉시 진입합니다.
- **Force Entry (강제 진입)**: 추세 강도(Strength)가 **0.4 이상**이면, 수학적 기대값(Edge)이 마이너스(-30%)여도 "결국 추세가 이긴다"는 판단 하에 진입합니다.
- **No Idle Time**: 5분/15분마다 열리는 타겟 마켓(BTC, ETH, SOL, XRP)을 쉴 새 없이 사냥합니다.

### 2. 공격적 사이징 & 리스크 관리
- **Aggressive Kelly Sizing**: 전문가 신호가 강력할 경우(>0.7), 기본 켈리 베팅의 **4배(4x)**까지 레버리지를 일으킵니다.
- **Force Minimum**: 짤짤이는 거절합니다. 판당 최소 **$20** 또는 **뱅크롤의 1%** 중 큰 금액을 무조건 태웁니다.
- **Equity-Based Drawdown**: 현금이 아닌 **(현금 + 투자금)**을 합산한 **총자산(Equity)**을 기준으로 리스크를 관리하여, 풀-시드 베팅 중에도 봇이 멈추지 않습니다.
- **Safety Cap**: 봇의 과열 방지를 위해 단일 베팅 최대 **$100** 제한이 걸려 있습니다. (조정 가능)

### 3. 모바일 최적화 UI
- Termux 등 모바일 환경에서도 편안하게 볼 수 있도록 **48자 폭**으로 최적화된 대시보드를 제공합니다.
- **Real-time PnL**: 확정 수익뿐만 아니라, 현재 보유 포지션의 평가 손익(**Unrealized PnL**)을 실시간으로 보여줍니다.
- **Strike Price Marking**: 동일 코인에 대한 다중 포지션을 명확히 구분하기 위해 행사 가격($)을 표시합니다.

---

## 🛠 설치 및 실행 (Setup)

### 1. 환경 설정
```bash
# 필수 라이브러리 설치
pip install -r requirements.txt
```

### 2. 환경 변수 (.env)
`.env` 파일을 생성하고 Polymarket API 키를 입력하세요.
```ini
# Polymarket Proxy Key (필수)
PK=YOUR_PRIVATE_KEY_HERE
CLOB_API_KEY=YOUR_API_KEY
CLOB_API_SECRET=YOUR_API_SECRET
CLOB_API_PASSPHRASE=YOUR_PASSPHRASE

# 모드 설정
PAPER_TRADING=True       # True: 모의 투자, False: 실전(Real Money)
INITIAL_BANKROLL=4000.0  # 시작 자본금

# 전략 튜닝 (기본값 추천)
MIN_EDGE=0.03            # 최소 엣지 (Force Mode에선 무시됨)
MAX_CONCURRENT_BETS=10   # 동시 포지션 수 제한 (유동적)
```

### 3. 실행
```bash
python main.py
```

---

## ⚠️ 주의사항 (Disclaimer)
이 소프트웨어는 **매우 공격적인(Aggressive)** 트레이딩 전략을 사용합니다.
- '지상 최강 트레이더' 모드는 높은 변동성을 동반합니다.
- 원금 손실의 위험이 있으며, 모든 투자의 책임은 사용자 본인에게 있습니다.
- 충분한 **Paper Trading(모의 투자)** 후 실전에 투입하시기 바랍니다.

---
**Powered by Antigravity & David's Alpha** 🚀
