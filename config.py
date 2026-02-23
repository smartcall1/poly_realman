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

    # === 기본 운영 설정 ===
    INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "4000.0"))  # 시작 뱅크롤

    # === 시스템 ===
    PAPER_TRADING = os.getenv("PAPER_TRADING", "True").lower() == "true"
    DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"


config = Config()
