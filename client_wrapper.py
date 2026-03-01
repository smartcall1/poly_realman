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

    def simulate_market_buy_vwap(self, market_id: str, buy_usdc_amount: float) -> float:
        """
        주어진 USDC 금액만큼 시장가 매수(Market Buy)를 진행했을 때의
        가상 체결 가중평균가(VWAP, Volume-Weighted Average Price)를 계산합니다.
        
        Args:
            market_id: 마켓의 Token ID (해당 진영의 토큰)
            buy_usdc_amount: 투자하려는 USDC 규모
            
        Returns:
            예상 체결 평단가 (0~1 사이). 
            물량이 부족하여 전체 금액을 체결할 수 없거나 에러 발생 시 None 반환.
        """
        try:
            # 1. 호가창 조회
            orderbook = self.get_order_book(market_id)
            if not orderbook or 'asks' not in orderbook:
                return None
                
            asks = orderbook['asks'] # 매도 물량(우리가 사야할 물량)
            if not asks:
                return None
                
            # 가격 오름차순 정렬 (싼 것부터 체결)
            asks.sort(key=lambda x: float(x['price']))
            
            remaining_usdc = buy_usdc_amount
            total_shares_bought = 0.0
            total_usdc_spent = 0.0
            
            for ask in asks:
                price = float(ask['price'])
                size_shares = float(ask['size'])
                
                # 이 호가에 있는 물량을 전부 샀을 때 필요한 USDC
                cost_for_this_ask = price * size_shares
                
                if remaining_usdc >= cost_for_this_ask:
                    # 물량 전부 소화
                    total_shares_bought += size_shares
                    total_usdc_spent += cost_for_this_ask
                    remaining_usdc -= cost_for_this_ask
                else:
                    # 돈이 부족해서 일부만 매수
                    shares_to_buy = remaining_usdc / price
                    total_shares_bought += shares_to_buy
                    total_usdc_spent += remaining_usdc
                    remaining_usdc = 0
                    break
                    
                if remaining_usdc <= 0:
                    break
                    
            # 2. 결과 계산
            if remaining_usdc > 0.01:
                # 호가창에 존재하는 모든 물량을 다 사도 내가 원하는 금액을 못 채운 경우 (유동성 부족)
                print(f"[Warning] 호가창 유동성 부족 (남은 주문 잔액: ${remaining_usdc:.2f})")
                return None
                
            if total_shares_bought > 0:
                vwap_price = total_usdc_spent / total_shares_bought
                return round(vwap_price, 4)
            return None
            
        except Exception as e:
            print(f"[Error] VWAP calculation failed: {e}")
            return None

    def simulate_market_sell_vwap(self, token_id: str, shares_to_sell: float):
        """
        보유한 shares를 시장가로 매도했을 때 실제 수령 USDC와 평균 체결가(VWAP) 반환.
        bid-side 오더북 기반으로 실제 유동성 반영.

        Returns:
            (total_usdc_received, vwap_price) 튜플, 또는 None (오더북 조회 실패)
        """
        try:
            orderbook = self.get_order_book(token_id)
            if not orderbook or 'bids' not in orderbook:
                return None

            bids = orderbook['bids']
            if not bids:
                return None

            # 가격 내림차순 정렬 (비싼 bid부터 체결)
            bids.sort(key=lambda x: float(x['price']), reverse=True)

            remaining_shares = shares_to_sell
            total_usdc_received = 0.0
            total_shares_sold = 0.0

            for bid in bids:
                price = float(bid['price'])
                size_shares = float(bid['size'])

                if remaining_shares >= size_shares:
                    # 이 bid 물량 전부 소화
                    total_usdc_received += price * size_shares
                    total_shares_sold += size_shares
                    remaining_shares -= size_shares
                else:
                    # 마지막 bid에서 일부만 체결
                    total_usdc_received += price * remaining_shares
                    total_shares_sold += remaining_shares
                    remaining_shares = 0
                    break

                if remaining_shares <= 0:
                    break

            if total_shares_sold <= 0:
                return None

            # 유동성 부족: 팔 수 없는 shares는 최저 bid 가격으로 강제 체결
            if remaining_shares > 0.01:
                lowest_bid_price = float(bids[-1]['price']) if bids else 0.0
                total_usdc_received += lowest_bid_price * remaining_shares
                total_shares_sold += remaining_shares
                print(f"[Warning] bid 유동성 부족 — 잔여 {remaining_shares:.1f}shares를 최저가 ${lowest_bid_price:.4f}에 강제 체결")

            vwap = total_usdc_received / total_shares_sold
            return (round(total_usdc_received, 4), round(vwap, 4))

        except Exception as e:
            print(f"[Error] simulate_market_sell_vwap failed: {e}")
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
