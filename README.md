# +EV Binary Options Bot (Polymarket)

## 전략 개요
드리프트-확산 모델 기반 "공정 가치(Fair Value)" 계산 → 시장 가격 대비 +EV(양의 기대값) 조건에서만 진입 → 만기까지 보유(Hold-to-Maturity)

## 핵심 파일 구조
| 파일 | 역할 |
|---|---|
| `main.py` | 봇 진입점, 메인 루프 |
| `ev_strategy.py` | +EV 전략 코어 (분석, 진입, 정산) |
| `probability_engine.py` | Binary Option 확률 계산 (N(d2) 모델) |
| `kelly_sizing.py` | 분수 켈리 기준 포지션 사이징 |
| `binance_feed.py` | Binance HF 변동성 + 스팟 가격 |
| `client_wrapper.py` | Polymarket CLOB 주문 래퍼 |
| `config.py` | 설정 (엣지, 켈리, 리스크 한도) |

## 실행 방법
```bash
pip install -r requirements.txt
python main.py
```

## 주요 설정 (.env)
```
PAPER_TRADING=True          # True=가상, False=실전
MIN_EDGE=0.05               # 최소 엣지 5%
KELLY_FRACTION=0.25         # 켈리 25%
INITIAL_BANKROLL=50.0       # 시작 뱅크롤
MAX_BET_FRACTION=0.10       # 1회 최대 베팅 10%
```

## 대상 시장
BTC (5분/15분), ETH (15분), SOL (15분), XRP (15분) UPDOWN 바이너리 옵션

## 테스트
```bash
python test_ev_strategy.py
```
