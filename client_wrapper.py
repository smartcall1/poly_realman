"""
Polymarket CLOB 클라이언트 래퍼

기능:
- UPDOWN 마켓 탐색 (5분/15분)
- 호가창(Order Book) 조회
- Limit Order 주문 (실전 모드)
"""

from config import config
import requests
import time
import math
import json


class PolymarketClient:
    def __init__(self):
        self.gamma_url = "https://gamma-api.polymarket.com"
        self.clob_url = "https://clob.polymarket.com"
        self.authenticated = (
            config.CLOB_API_KEY is not None
            and config.CLOB_API_KEY != "dummy"
            and config.CLOB_API_KEY != ""
        )
        self.client = None

        if self.authenticated:
            try:
                from py_clob_client.client import ClobClient
                self.client = ClobClient(
                    host=self.clob_url,
                    key=config.CLOB_API_KEY,
                    secret=config.CLOB_API_SECRET,
                    passphrase=config.CLOB_API_PASSPHRASE,
                    private_key=config.PK,
                )
            except Exception as e:
                print(f"[Client] Auth init failed: {e}")

    def get_order_book(self, market_id: str) -> dict:
        """실시간 호가창 데이터 조회"""
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }
        try:
            url = f"{self.clob_url}/book?token_id={market_id}"
            response = requests.get(url, headers=headers, timeout=20)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None

    def find_active_markets(self) -> list:
        """
        BTC(5m/15m) 및 ETH/SOL/XRP(15m) 타겟 UPDOWN 마켓 저격 탐색.
        Gamma API의 /events 엔드포인트를 사용하여 특정 슬러그를 직접 조회합니다.
        """
        now = int(time.time())
        hunt_list = [
            ("btc-updown-5m", 300),     # BTC 5분
            ("btc-updown-15m", 900),    # BTC 15분
            ("eth-updown-15m", 900),    # ETH 15분
            ("sol-updown-15m", 900),    # SOL 15분
            ("xrp-updown-15m", 900),    # XRP 15분
        ]

        found_markets = []
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }

        for slug_prefix, interval in hunt_list:
            # 현재 블록과 다음 블록 시도
            current_block = math.floor(now / interval) * interval
            next_block = current_block + interval

            for ts in [current_block, next_block]:
                slug = f"{slug_prefix}-{ts}"
                try:
                    url = f"{self.gamma_url}/events?slug={slug}"
                    r = requests.get(url, headers=headers, timeout=10)
                    events = r.json()
                    if events and len(events) > 0:
                        ev = events[0]
                        # 이벤트 내의 모든 마켓(상승/하락 등) 수집
                        for m in ev.get('markets', []):
                            tids_raw = m.get('clobTokenIds', [])
                            if isinstance(tids_raw, str):
                                tids_raw = json.loads(tids_raw)
                            
                            if tids_raw:
                                found_markets.append({
                                    'question': m.get('question', ''),
                                    'clobTokenIds': tids_raw,
                                    'slug': slug,
                                    'end_time': ts + interval,
                                })
                except Exception:
                    continue

        return found_markets

    def place_limit_order(self, token_id: str, price: float, size: float, side: str = 'BUY'):
        """
        Limit Order 주문 실행.
        """
        if config.PAPER_TRADING:
            print(f"  [PAPER] Limit {side} {size:.2f} shares @ {price:.4f}")
            return {'status': 'paper', 'filled': True}

        if not self.client:
            raise RuntimeError("Client not authenticated")

        try:
            from py_clob_client.order_builder.constants import BUY, SELL
            order_side = BUY if side.upper() == 'BUY' else SELL

            order = self.client.create_and_post_order({
                'tokenID': token_id,
                'price': price,
                'size': size,
                'side': order_side,
            })
            return order
        except Exception as e:
            raise RuntimeError(f"Order failed: {e}")
