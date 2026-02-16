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

# Try importing types safely
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType, ApiCreds
except ImportError:
    ClobClient = None
    BalanceAllowanceParams = None
    AssetType = None
    ApiCreds = None


class PolymarketClient:
    def __init__(self):
        self.gamma_url = "https://gamma-api.polymarket.com"
        self.clob_url = "https://clob.polymarket.com"
        
        # Check if keys are valid (not default placeholders)
        self.authenticated = (
            config.CLOB_API_KEY is not None
            and "YOUR_" not in config.CLOB_API_KEY
            and config.CLOB_API_KEY != ""
        )
        self.client = None

        if self.authenticated and ClobClient and ApiCreds:
            try:
                # Create credentials object
                creds = ApiCreds(
                    api_key=config.CLOB_API_KEY,
                    api_secret=config.CLOB_API_SECRET,
                    api_passphrase=config.CLOB_API_PASSPHRASE
                )
                
                # Initialize client with correct arguments
                self.client = ClobClient(
                    host=self.clob_url,
                    key=config.PK,  # Private Key
                    chain_id=137,   # Polygon Mainnet
                    creds=creds,    # API Credentials
                    signature_type=1 # EOA (Externally Owned Account)
                )
                print("[Client] Polymarket Client Initialized Successfully (Live Mode Ready)")
            except Exception as e:
                print(f"[Client] Init failed: {e}")
                self.client = None
        
        # [CRITICAL CHECK] If Live Mode is on but client failed, we MUST stop.
        if not config.PAPER_TRADING and self.client is None:
            print("\n" + "="*60)
            print("🚨 CRITICAL ERROR: Live Trading is ENABLED but API Client failed to load.")
            print("Possible causes:")
            print("1. 'py-clob-client' library is not installed.")
            print("2. API Keys in .env are invalid or missing.")
            print("3. System packages (build-essential) missing on mobile (Termux).")
            print("="*60 + "\n")
            if not ClobClient:
                print("⚠️  'py-clob-client' is NOT detected. Please install it:")
                print("   pip install py-clob-client")
            raise RuntimeError("Live Trading Aborted: No API Client")

    def get_usdc_balance(self) -> float:
        """지갑의 USDC 잔액 조회 (실패 시 0.0 반환)"""
        if not self.client or not BalanceAllowanceParams:
            return 0.0
            
        try:
            # AssetType.COLLATERAL = USDC
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            res = self.client.get_balance_allowance(params)
            
            # 응답 구조에 따라 파싱 (balance가 포함된 경우)
            # 보통 {'balance': '123456789', 'allowance': ...} 형태 (str)
            if isinstance(res, dict):
                bal_str = res.get('balance', '0')
                return float(bal_str) / 1_000_000  # USDC 6 decimals
            
            return 0.0
        except Exception as e:
            print(f"[Balance] Fetch error: {e}")
            return 0.0

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
