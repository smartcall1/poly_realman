"""
+EV(양의 기대값) 베팅 전략 설정

핵심 철학:
- 확률 모델로 계산한 Fair Value가 시장 가격보다 높을 때만 진입
- 만기까지 보유 (0 or 1)
- 분수 켈리 기준으로 뱅크롤 보호
"""

import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # === Polymarket API ===
    PK = os.getenv("PK")
    CLOB_API_KEY = os.getenv("CLOB_API_KEY")
    CLOB_API_SECRET = os.getenv("CLOB_API_SECRET")
    CLOB_API_PASSPHRASE = os.getenv("CLOB_API_PASSPHRASE")
    POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS")  # Proxy Wallet 주소

    # === 마찰 및 모델 보정 (Personas) ===
    STRATEGY_NAME = os.getenv("STRATEGY_NAME", "DefaultMaster")
    ALPHA_BOOST_WEIGHT = float(os.getenv("ALPHA_BOOST_WEIGHT", "0.25")) # 전문가 지표 반영 비중
    VOL_SCALE_FACTOR = float(os.getenv("VOL_SCALE_FACTOR", "1.2"))      # 변동성 보정 (보수성)
    
    # === +EV 전략 핵심 파라미터 ===
    MIN_EDGE = float(os.getenv("MIN_EDGE", "0.03"))           # 진입 최소 엣지 (3%)
    KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))  # 켈리 25% 적용 (보수적)
    MAX_BET_FRACTION = float(os.getenv("MAX_BET_FRACTION", "0.10"))  # 1회 최대 뱅크롤의 10%
    MAX_BET_AMOUNT = float(os.getenv("MAX_BET_AMOUNT", "50.0"))      # [NEW] 1회 최대 베팅 금액 (절대값)
    MIN_BET_USDC = float(os.getenv("MIN_BET_USDC", "1.0"))    # 최소 베팅 $1
    INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "4000.0"))  # 시작 뱅크롤

    # === 변동성 엔진 ===
    VOL_WINDOW = int(os.getenv("VOL_WINDOW", "30"))            # 1분봉 30개 (최근 30분)
    VOL_MIN_SAMPLES = 10                                        # 변동성 계산 최소 샘플
    DRIFT_WINDOW = int(os.getenv("DRIFT_WINDOW", "10"))        # 드리프트 계산 윈도우

    # === 모델 파라미터 ===
    RISK_FREE_RATE = 0.0    # 초단기이므로 무위험이자율 0
    FEE_RATE = float(os.getenv("FEE_RATE", "0.02"))            # Polymarket 거래 수수료 2%

    # === 시장 탐색 ===
    MARKET_SCAN_INTERVAL = int(os.getenv("MARKET_SCAN_INTERVAL", "30"))
    MAIN_LOOP_INTERVAL = int(os.getenv("MAIN_LOOP_INTERVAL", "5"))
    BINANCE_KLINE_INTERVAL = 60

    # === 리스크 관리 ===
    MAX_CONCURRENT_BETS = int(os.getenv("MAX_CONCURRENT_BETS", "5"))
    DRAWDOWN_HALT_PCT = float(os.getenv("DRAWDOWN_HALT_PCT", "0.50"))

    # === 시스템 ===
    PAPER_TRADING = os.getenv("PAPER_TRADING", "True").lower() == "true"
    DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"


config = Config()
