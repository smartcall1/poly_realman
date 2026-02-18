"""
Polymarket CLOB 클라이언트 래퍼

기능:
- UPDOWN 마켓 탐색 (5분/15분)
- 호가창(Order Book) 조회
- Limit Order 주문 (실전 모드)
"""

from config import config
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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
                print(f"[Client] Authentication failed: {e}")
                self.client = None
        
        # === [Network Optimization] Session & Retry Strategy ===
        self.session = requests.Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HATEBOT/3.0",
            "Accept": "application/json",
        })

        # [CRITICAL CHECK] If Live Mode is on but client failed, we MUST stop.
        if not config.PAPER_TRADING and self.client is None:
            print("\n" + "="*60)
            print("🚨 CRITICAL ERROR: Live Trading is ENABLED but API Client failed to load.")
            print("Possible causes:")
            print("1. 'py-clob-client' library is not installed.")
            print("2. API Keys in .env are invalid or missing.")
            print("3. System packages (build-essential) missing on mobile (Termux).")
            print("="*60 + "\n")
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
        """실시간 호가창 데이터 조회 (세션 재사용)"""
        try:
            url = f"{self.clob_url}/book?token_id={market_id}"
            response = self.session.get(url, timeout=15)
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
            url = f"{self.gamma_url}/markets/{market_id}"
            r = self.session.get(url, timeout=10)
            
            if r.status_code == 200:
                m = r.json()
                
                def normalize_outcome(res_str):
                    if not res_str: return None
                    res = str(res_str).upper()
                    if any(k in res for k in ['YES', 'UP', 'ABOVE', 'HIGH']): return 'YES'
                    if any(k in res for k in ['NO', 'DOWN', 'BELOW', 'LOW']): return 'NO'
                    return res

                # 1. outcomePrices 분석 (1.0 근접 정산 확인)
                prices_raw = m.get('outcomePrices')
                outcomes_raw = m.get('outcomes')
                
                # [Deep Parsing] JSON 문자열 대응
                def robust_json_load(data):
                    if not isinstance(data, str): return data
                    try: return json.loads(data)
                    except: return data

                prices = robust_json_load(prices_raw)
                outcomes = robust_json_load(outcomes_raw)

                if isinstance(prices, list) and isinstance(outcomes, list):
                    for i, p_str in enumerate(prices):
                        try:
                            if float(p_str) > 0.99 and i < len(outcomes):
                                return normalize_outcome(outcomes[i])
                        except: pass

                # 2. winnerOutcome 필드 확인
                winner_outcome = m.get('winnerOutcome') or m.get('winner_outcome')
                if winner_outcome:
                    return normalize_outcome(winner_outcome)

                # 3. tokens 배열 분석
                tokens = robust_json_load(m.get('tokens', []))
                if isinstance(tokens, list):
                    for t in tokens:
                        if t.get('winner') is True:
                            return normalize_outcome(t.get('outcome'))
                        try:
                            p = t.get('price') or t.get('outcomePrice')
                            if p and float(p) > 0.99:
                                return normalize_outcome(t.get('outcome'))
                        except: pass
                
                return "WAITING"
            return None
        except Exception:
            return None

    def find_active_markets(self) -> list:
        """
        BTC(5m/15m) 및 ETH/SOL(15m) 타켓 UPDOWN 마켓 저격 탐색.
        """
        now = int(time.time())
        hunt_list = [
            ("btc-updown-5m", 300),     # BTC 5분
            ("btc-updown-15m", 900),    # BTC 15분
            ("eth-updown-15m", 900),    # ETH 15분
            ("sol-updown-15m", 900),    # SOL 15분
        ]

        found_markets = []
        # session의 headers를 사용하되 개별 요청 시 추가 가능
        
        from config import config
        if config.DEBUG_MODE:
            print(f"  [Gamma] Contacting {self.gamma_url}...", flush=True)

        for slug_prefix, interval in hunt_list:
            # surgical hunt는 아주 짧게만 시도 (2초)
            current_block = math.floor(now / interval) * interval
            slug = f"{slug_prefix}-{current_block}"
            
            try:
                url = f"{self.gamma_url}/events?slug={slug}"
                r = self.session.get(url, timeout=(15, 30)) # 대폭 인상
                events = r.json()
                if events and len(events) > 0:
                    ev = events[0]
                    markets_in_ev = ev.get('markets', [])
                    if config.DEBUG_MODE and markets_in_ev:
                        print(f"    Found! : {slug}", flush=True)
                    
                    for m in markets_in_ev:
                        tids_raw = m.get('clobTokenIds', [])
                        if isinstance(tids_raw, str): tids_raw = json.loads(tids_raw)
                        if tids_raw:
                            found_markets.append({
                                'question': m.get('question', ''),
                                'marketId': m.get('id', ''),
                                'conditionId': m.get('conditionId', ''),
                                'clobTokenIds': tids_raw,
                                'slug': slug,
                                'end_time': current_block + interval,
                            })
            except:
                continue

        # Surgical hunt로 못 찾았거나 부족하면 전체 목록 조회 (Fallback)
        if len(found_markets) < 2:
             if config.DEBUG_MODE:
                print("  [Scan] Specific hunt slow/failed. Trying ultimate global scan...", flush=True)
             
             # 서버 부하 방지를 위해 살짝 대기
             time.sleep(2)
             
             try:
                 url = f"{self.gamma_url}/events?active=true&limit=50&sort=volume24h:desc"
                 r = self.session.get(url, timeout=(20, 60)) # 최후의 수단: 60초까지 기다림
                 all_events = r.json()
                 for ev in all_events:
                     q_title = str(ev.get('title', '')).upper()
                     if any(k in q_title for k in ['BTC', 'ETH', 'SOL']):
                         for m in ev.get('markets', []):
                             # 이미 찾은 건 제외
                             if any(fm['marketId'] == m.get('id') for fm in found_markets): continue
                             
                             tids_raw = m.get('clobTokenIds', [])
                             if isinstance(tids_raw, str): tids_raw = json.loads(tids_raw)
                             if tids_raw:
                                 found_markets.append({
                                     'question': m.get('question', ''),
                                     'marketId': m.get('id', ''),
                                     'conditionId': m.get('conditionId', ''),
                                     'clobTokenIds': tids_raw,
                                     'slug': ev.get('slug', 'match'),
                                     'end_time': m.get('end_time', 0),
                                 })
                 if config.DEBUG_MODE and found_markets:
                     print(f"    ✅ Global Scan Success! Found {len(found_markets)} markets.", flush=True)
             except Exception as fe:
                 if config.DEBUG_MODE: print(f"    [Global Scan Error] {fe}")

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
