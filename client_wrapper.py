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
    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType, ApiCreds, OrderArgs
except ImportError:
    ClobClient = None
    BalanceAllowanceParams = None
    AssetType = None
    ApiCreds = None
    OrderArgs = None


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
                    signature_type=1,  # Polymarket Proxy Wallet
                    funder=config.POLYMARKET_PROXY_ADDRESS,  # Proxy 지갑 주소 (maker)
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
        """지갑의 USDC 잔액 조회 (실패 시 0.0 반환, 재시도 로직 포함)"""
        if not self.client or not BalanceAllowanceParams:
            return 0.0
            
        # [Retry Logic] 네트워크 불안정 대비 3회 시도
        for attempt in range(3):
            try:
                # AssetType.COLLATERAL = USDC
                params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                res = self.client.get_balance_allowance(params)
                
                # 응답 구조에 따라 파싱 (balance가 포함된 경우)
                if isinstance(res, dict):
                    bal_str = res.get('balance', '0')
                    return float(bal_str) / 1_000_000  # USDC 6 decimals
                
                # 응답 포맷이 다를 경우도 있을 수 있으므로 로깅
                # print(f"[DEBUG] Balance Response: {res}")
                return 0.0
            
            except Exception as e:
                if attempt < 2:
                    time.sleep(1) # 1초 대기 후 재시도
                    continue
                print(f"[Balance] Fetch error (final): {e}")
                return 0.0
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

    def get_market_winner(self, market_id: str) -> str:
        """
        Gamma API를 통해 마켓의 승자(Winner) 조회.
        Args:
            market_id: Gamma Market ID (e.g., "239826")
        Return: 'YES', 'NO', 'WAITING' or None (Error)
        """
        try:
            # Market ID로 직접 조회 (가장 정확)
            url = f"{self.gamma_url}/markets/{market_id}"
            headers = {"Accept": "application/json"}
            r = requests.get(url, headers=headers, timeout=5)
            
            if r.status_code == 200:
                m = r.json()
                
                # 결과 도출 (가격 1.0 = 승자)
                outcomes_raw = m.get('outcomes')
                prices_raw = m.get('outcomePrices')
                
                # [Deep Parsing] Gamma API는 가끔 JSON 속에 JSON 문자열을 넣음 (예: "[\"0\", \"1\"]")
                def robust_json_load(data):
                    if not isinstance(data, str): return data
                    try:
                        parsed = json.loads(data)
                        if isinstance(parsed, str):
                            try: return json.loads(parsed)
                            except: return parsed
                        return parsed
                    except: return data

                outcomes = robust_json_load(m.get('outcomes'))
                prices = robust_json_load(m.get('outcomePrices'))

                # 1. outcomePrices 분석 (1.0 근접 정산 확인) - 가장 빠르고 정확
                if isinstance(outcomes, list) and isinstance(prices, list):
                    for i, p_str in enumerate(prices):
                        try:
                            if float(p_str) > 0.99 and i < len(outcomes):
                                res = str(outcomes[i]).upper()
                                if 'YES' in res: return 'YES'
                                if 'NO' in res: return 'NO'
                                return res
                        except: pass

                # 2. 개별 winner 필드 (루트 레벨) 확인
                # 가끔 마켓 루트에 winnerOutcome 필드가 직접 있을 수 있음
                winner_outcome = m.get('winnerOutcome') or m.get('winner_outcome')
                if winner_outcome:
                    res = str(winner_outcome).upper()
                    if 'YES' in res or 'UP' == res: return 'YES'
                    if 'NO' in res or 'DOWN' == res: return 'NO'
                    return res

                # 3. tokens 배열 분석 (기존 로직 유지)
                tokens = m.get('tokens', [])
                if isinstance(tokens, str): tokens = robust_json_load(tokens)
                
                if isinstance(tokens, list):
                    for t in tokens:
                        if t.get('winner') is True:
                            res = str(t.get('outcome', '')).upper()
                            if 'YES' in res or 'UP' == res: return 'YES'
                            if 'NO' in res or 'DOWN' == res: return 'NO'
                            return res
                        try:
                            p = t.get('price') or t.get('outcomePrice')
                            if p and float(p) > 0.99:
                                res = str(t.get('outcome', '')).upper()
                                if 'YES' in res or 'UP' == res: return 'YES'
                                if 'NO' in res or 'DOWN' == res: return 'NO'
                                return res
                        except: pass
                
                # 3. resolved 필드가 True인데 위에서 안 걸린 경우 (드문 케이스)
                if m.get('resolved') is True:
                    # outcome 필드가 'Yes'나 'No'면 그걸 그대로 믿음 (단, 가격 확인이 안 될 때만)
                    # 하지만 WAITING이 더 안전함
                    pass

                return "WAITING"
            return None
        except Exception as e:
            # print(f"[Resolution] Error: {e}") 
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
                                    'marketId': m.get('id', ''),
                                    'conditionId': m.get('conditionId', ''),
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

            # [Rounding Fix] EIP-712 서명 오류 방지를 위한 정밀도 제한
            # 가격은 소수점 2자리(또는 마켓 틱 사이즈), 사이즈는 소수점 2자리로 반올림
            safe_price = round(price, 2)
            safe_size = round(size, 2)

            # 0이 되면 최소값으로 보정
            if safe_price <= 0: safe_price = 0.01
            if safe_size <= 0: safe_size = 0.01

            order_args = OrderArgs(
                price=safe_price,
                size=safe_size,
                side=order_side,
                token_id=token_id,
            )
            
            order = self.client.create_and_post_order(order_args)
            return order
        except Exception as e:
            raise RuntimeError(f"Order failed: {e}")
