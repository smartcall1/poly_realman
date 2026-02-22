"""
+EV 초단타(Hyper-Short-Term) 베팅 전략 코어

전략 핵심 (5분/15분 마켓 특화):
1. 기존 이론적 공정가(Fair Value) 한계 극복, 매수/매도 수급(Order Imbalance) 분석
2. 만기가 15분 이하 남았을 때 발생하는 과평가 프리미엄(Theta Decay Premium) 공략
3. 순간적인 시장 틱 가속도(SPOT_VELOCITY_BPS) 민감 반영
4. 비상식적인 호가 갭(Spread Fee Ratio) 회피로 손실률 최소화

핵심 철학: "Hold-to-Maturity"
- 진입 후 절대 조기 청산하지 않는다
- 0 or 1로 결판. 매번의 개별 결과는 무의미.
- 비이성적인 단기 쏠림과 시간 가치의 소멸을 이용하여 대수의 법칙으로 승리한다.
"""

import re
import time
import os
import json
from datetime import datetime

from binance_feed import BinancePriceFeed
from probability_engine import (
    calculate_binary_probability,
    calculate_edge,
    get_probability_confidence,
    adjust_prob_by_expert_signals,
)
from kelly_sizing import kelly_bet_size, kelly_info
from config import config


class EVStrategy:
    """
    +EV 바이너리 옵션 베팅 전략.

    각 Polymarket UPDOWN 마켓에 대해:
    1. 코인종류, 스트라이크 가격, 만기시간 파싱
    2. Binance 스팟 + 변동성 데이터로 Fair Value 계산
    3. +EV 조건 충족 시 Limit Order 진입
    4. 만기까지 보유 → 정산
    """

    def __init__(self, client):
        self.client = client
        self.binance = BinancePriceFeed()
        
        # [Risk Control] 설정 확인 로그
        print(f"  💰 [Risk Control] 1회 최대 베팅 한도: ${config.MAX_BET_AMOUNT:.2f}")

        # 뱅크롤 관리
        if config.PAPER_TRADING:
            self.initial_bankroll = config.INITIAL_BANKROLL
            print(f"  [Paper] 초기 자본금 설정: ${self.initial_bankroll:.2f}")
        else:
            # 실전 모드: 실제 지갑 잔액 조회 시도
            try:
                real_bal = self.client.get_usdc_balance() if self.client else 0.0
            except Exception as e:
                print(f"  ⚠️ [Init] 잔액 조회 실패 (네트워크 지연): {e}")
                real_bal = 0.0
            
            # [DEBUG] 디버그 모드이거나 잔액이 충분할 경우
            if real_bal > 0.05: 
                self.initial_bankroll = real_bal
                print(f"  [Live] 💰 지갑 잔액 연동 완료: ${self.initial_bankroll:.2f}")
            else:
                # 잔액 조회 실패 or 0원인 경우
                if config.DEBUG_MODE:
                    # 디버그 모드에서는 0원으로 시작해도 됨 (나중에 싱크 맞춤)
                    self.initial_bankroll = real_bal if real_bal > 0 else 0.0
                    if real_bal == 0:
                        print(f"  [Debug] 잔액 정보 없음. 0원으로 시작 (루프에서 재시도)")
                else:
                    self.initial_bankroll = config.INITIAL_BANKROLL
                    print(f"  [Live] ⚠️ 잔액 조회 실패 (또는 0원). 설정된 초기값(${self.initial_bankroll:.2f})으로 시작합니다.")

        self.bankroll = self.initial_bankroll

        # [FACT-ONLY] Live 모드용 실제 잔액 추적
        self.real_balance_start = self.initial_bankroll  # 시작 잔액 (진짜)
        self._last_balance_sync = 0  # 마지막 잔액 동기화 시점
        self._balance_sync_interval = 30  # 30초마다 잔액 동기화

        # 활성 포지션: {tid: {entry_price, size_usdc, fair_prob, edge, coin, question, entry_time, end_time}}
        self.positions = {}
        
        # [NEW] Sustained Imbalance 추적을 위한 딕셔너리
        # 구조: { tid: {'first_seen': timestamp, 'side': 'YES'/'NO'} }
        self._imbalance_tracker = {}

        # 누적 통계 (Paper 모드에서만 의미 있음)
        self.stats = {
            'total_bets': 0,
            'wins': 0,
            'losses': 0,
            'total_wagered': 0.0,
            'total_pnl': 0.0,
            'max_drawdown': 0.0,
            'peak_bankroll': self.initial_bankroll,
        }

        self.start_time = time.time()
        self._last_render = 0
        
        # 거래 로그 파일 경로
        self.trade_log_path = os.path.join(os.path.dirname(__file__), "trade_history.jsonl")

    # ─── 마켓 파싱 ────────────────────────────────────────────

    def extract_coin(self, question: str) -> str:
        q = question.upper()
        if "BTC" in q or "BITCOIN" in q: return "BTC"
        if "ETH" in q or "ETHEREUM" in q: return "ETH"
        if "SOL" in q or "SOLANA" in q: return "SOL"
        if "XRP" in q or "RIPPLE" in q: return "XRP"
        return ''

    def extract_strike_price(self, question: str, coin: str = '') -> float:
        """강화된 스트라이크 가격 추출 로직 (날짜 필터 및 코인별 임계치 적용)"""
        q = question.replace('?', '').strip()

        # 1. $ 기호 뒤의 숫자 (가장 신뢰도 높음)
        dollar_matches = re.findall(r'\$\s*([\d,]+(?:\.\d+)?)', q)
        if dollar_matches:
            try:
                candidates = []
                for m in dollar_matches:
                    val = float(m.replace(',', ''))
                    if val > 0: candidates.append(val)
                if candidates: return max(candidates)
            except Exception: pass

        # 2. 날짜/시간 패턴 제거 (February 17, 12:05 등) 
        # "17" 이나 "11:00PM" 같은 숫자가 가격으로 오인되는 것 방지
        q_clean = re.sub(r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}', '', q, flags=re.IGNORECASE)
        q_clean = re.sub(r'\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?', '', q_clean, flags=re.IGNORECASE)
        
        # 3. 숫자 패턴 추출
        num_matches = re.findall(r'(\d+(?:,\d{3})*(?:\.\d+)?)', q_clean)
        
        spot = self.binance.get_spot_price(coin) if coin else 0.0
        candidates = []
        for n in num_matches:
            try:
                val = float(n.replace(',', ''))
                # 연도 필터링
                if 2024 <= val <= 2030 and val.is_integer(): continue
                # [CORE FIX] 현재가 대비 너무 낮은 숫자는 날짜일 확률이 높음 (예: BTC 90k인데 17추출)
                if spot > 0:
                    if coin == 'BTC' and val < 1000: continue
                    if coin == 'ETH' and val < 100: continue
                    if coin == 'SOL' and val < 10: continue
                
                candidates.append(val)
            except: continue
            
        if candidates:
            return max(candidates)

        return 0.0

    def is_above_market(self, question: str) -> bool:
        """
        마켓이 "위로" 갈 확률인지 "아래로" 갈 확률인지 판단.
        기본적으로 Polymarket UPDOWN은 "Will X be above K?" 형태.
        """
        q = question.lower()
        if 'below' in q:
            return False
        return True  # 기본: above

    # ─── 핵심 전략 루프 ───────────────────────────────────────

    def run_ev_step(self, market_data_list: list):
        """지상 최강 트레이더(Universal Best) 무조건 택일 및 진입 루프"""
        now = time.time()

        # [필수] 가격 갱신 및 정산
        self.binance.fetch_spot_prices()
        
        # [FACT-ONLY] Live 모드: 주기적으로 실제 잔액 동기화
        if not config.PAPER_TRADING and self.client:
            if now - self._last_balance_sync > self._balance_sync_interval:
                try:
                    real_bal = self.client.get_usdc_balance()
                    if real_bal > 0:
                        self.bankroll = real_bal
                        self._last_balance_sync = now
                except: pass
        
        if self._check_drawdown_halt(): return
        self._settle_expired_positions(now)

        analysis_results = []
        
        # 코인별 가장 매력적인 마켓 하나씩은 무조건 잡기 위한 트래커
        coin_best_pick = {}

        for data in market_data_list:
            tid = data['tid']
            order_book = data.get('order_book', {})
            
            # [보유 포지션 가격 갱신]
            if tid in self.positions:
                # 현재 시장가(매도할 수 있는 최선가 = Best Bid) 업데이트
                bid = self._get_best_bid(order_book)
                # [BUG FIX] 0원(호가 없음/패배 임박)도 시세로 반영해야 함
                # 기존: if bid > 0 (0원이면 업데이트 안 해서 직전 가격 유지 -> 좀비 포지션)
                # 수정: 항상 업데이트
                self.positions[tid]['current_price'] = bid
                continue

            side = data.get('side', 'YES')
            question = data['question']
            end_time = data.get('end_time', 0)

            coin = self.extract_coin(question)
            if not coin: continue

            # 스팟/변동성 수집
            spot = self.binance.get_spot_price(coin)
            if spot <= 0: continue

            strike = self.extract_strike_price(question, coin)
            is_above = self.is_above_market(question)
            
            # [UPDOWN FIX] 스트라이크 파싱 실패(17 등 날짜 필터링됨) 시 현재가로 대체
            # Polymarket UPDOWN 마켓은 특정 시점 대비 '위/아래'를 묻는 것이므로 
            # 가격이 명시되지 않으면 현재가가 기준점이 됨.
            if strike <= 0: strike = spot

            time_to_expiry = end_time - now
            if time_to_expiry < 10: continue

            self.binance.fetch_candles(coin)
            vol = self.binance.get_blended_volatility(coin)
            drift = self.binance.get_drift(coin)

            # 확률 및 엣지 계산
            base_prob = calculate_binary_probability(
                spot, strike, vol, time_to_expiry, drift,
                vol_scale=config.VOL_SCALE_FACTOR
            )
            
            actual_prob = base_prob if side == 'YES' else (1.0 - base_prob)
            if not is_above: actual_prob = 1.0 - actual_prob

            expert_signals = self.binance.get_expert_signals(coin)
            final_prob, alpha_log = adjust_prob_by_expert_signals(actual_prob, expert_signals)

            best_ask = self._get_best_ask(order_book)
            best_bid = self._get_best_bid(order_book)
            if best_ask <= 0: continue # [SAFETY] 가격 정보 없으면 진입 금지
            
            # [NEW] 스프레드 분석 (Spread Fee Ratio)
            spread = max(0.0, best_ask - best_bid)
            spread_ratio = spread / best_ask if best_ask > 0 else 1.0

            # [NEW] 오더북 수급 쏠림 분석 (Order Imbalance)
            bids_vol = sum([float(b['size']) for b in order_book.get('bids', [])])
            asks_vol = sum([float(a['size']) for a in order_book.get('asks', [])])
            order_imbalance = bids_vol / asks_vol if asks_vol > 0 else (999.0 if bids_vol > 0 else 1.0)
            
            # [NEW] 시간가치 프리미엄 (Theta Decay Premium)
            # 확률 모델이 극단적으로 낮다고(예: 5% 미만) 판단했는데, 실제 호가창 가격 비싼 경우
            theta_premium = max(0.0, best_ask - final_prob) if final_prob < 0.1 else 0.0

            edge = calculate_edge(final_prob, best_ask, config.FEE_RATE)
            sig_str = expert_signals.get('strength', 0.0)
            velocity = expert_signals.get('velocity', 0.0)

            # [코인별 베스트 픽 선별]
            if coin not in coin_best_pick or edge > coin_best_pick[coin]['edge']:
                coin_best_pick[coin] = {
                    'tid': tid, 'coin': coin, 'side': side, 'question': question,
                    'price': best_ask, 'prob': final_prob, 'edge': edge,
                    'end_time': end_time, 'strength': sig_str, 'alpha_log': alpha_log,
                    'marketId': data.get('marketId', ''), 'conditionId': data.get('conditionId', ''),
                    'strike': strike,  # [FIX] 진입 기준가 저장
                    'spread_ratio': spread_ratio,
                    'order_imbalance': order_imbalance,
                    'theta_premium': theta_premium,
                    'velocity': velocity
                }

            # 분석 기록 (UI 표시용)
            analysis_results.append({
                'tid': tid, 'coin': coin, 'side': side, 'prob': final_prob,
                'price': best_ask, 'edge': edge, 'strength': sig_str, 'alpha_log': alpha_log,
                'theta_premium': theta_premium, 'imbalance': order_imbalance
            })

        for coin, pick in coin_best_pick.items():
            if len(self.positions) >= config.MAX_CONCURRENT_BETS: break

            # [NEW] 페르소나별 분기 조건 진입 로직 (v5.0 Anti-Latency)
            strategy_name = config.STRATEGY_NAME
            should_enter = False
            
            p_imb = pick['order_imbalance']
            p_theta = pick['theta_premium']
            p_vel = pick['velocity']
            p_spread = pick['spread_ratio']
            p_edge = pick['edge']
            
            # [Imbalance 추적 로직]
            # Imbalance_Sniper 전략을 위함: 매수 대기 물량이 꽤 많음(Imbalance >= 2.0)
            if p_imb >= 2.0:
                if tid not in self._imbalance_tracker:
                    self._imbalance_tracker[tid] = {'first_seen': now}
            else:
                # 1.5 밑으로 깨지면 진짜 벽이 무너진 것으로 간주하고 초기화 (약간의 버퍼 제공)
                if p_imb < 1.5 and tid in self._imbalance_tracker:
                    del self._imbalance_tracker[tid]
            
            # --- 전략별 진입 통제 ---
            
            if strategy_name == 'Theta_Reaper':
                # [BUG FIX] 거품이 낀 반대편(5%확률에 15센트)을 사는게 아니라, 
                # 확실한 쪽을 사야 함.
                # [RELAX] 확률 85% 이상, 엣지가 1.5% 이상 존재할 때만 진입하여 기회 포착률 상승
                if pick['prob'] >= 0.85 and p_edge >= 0.015:
                    should_enter = True
                    
            elif strategy_name == 'Spread_Fisher':
                # [BUG FIX] 죽은 마켓(확률 0%)에서 스프레드가 높다고 1센트에 사는 현상 방지
                # 팽팽한 접전(40~60%)이면서 스프레드가 10% 이상 벌어진 곳에만 지정가 낚시
                if p_spread >= 0.10 and 0.40 <= pick['prob'] <= 0.60:
                    should_enter = True
                    # 지정가 후려치기 (Paper 모드에서는 이게 체결가로 간주됨)
                    pick['price'] = max(0.01, pick['prob'] - 0.05)
                    # 가격을 인위적으로 바꿨으므로 edge 재계산
                    # Spread_Fisher는 수동적으로 유리하게 샀다고 가정
                    pick['edge'] = calculate_edge(pick['prob'], pick['price'], config.FEE_RATE)
                    
            elif strategy_name == 'Imbalance_Sniper':
                # 스푸핑(가짜 벽) 방어: Imbalance가 15초 이상 유지되었는지 확인 (버퍼 제공)
                tracked = self._imbalance_tracker.get(tid)
                if tracked and (now - tracked['first_seen']) >= 15.0:
                    # 바이낸스 실물 가속도 동기화 확인 (0.5 BPS 이상 움직일 때만: 조건 완화)
                    if p_vel >= 0.5 and 0.20 <= pick['prob'] <= 0.80:
                        # 엣지가 최소 0%(손해는 아님)일 때 편승
                        if p_edge >= 0.0:
                            should_enter = True
            
            else:
                # 안전망: 정의되지 않은 기존 전략(Bal_Factory 등)이 켜져있다면 아무것도 하지 않습니다. (손실 방지)
                should_enter = False

            if should_enter:
                bet_size = kelly_bet_size(
                    bankroll=self.bankroll, win_prob=pick['prob'], market_price=pick['price'],
                    fee_rate=config.FEE_RATE, kelly_fraction=config.KELLY_FRACTION
                )
                
                # [수정] 강제 하드코딩 제거 및 Config 기반 유연한 베팅 사이즈 설정
                
                # 1. 최대 베팅 금액 제한 (Config 따름)
                max_bet = self.bankroll * config.MAX_BET_FRACTION
                
                # [NEW] 절대 금액 한도 적용 (예: 최대 $50)
                max_bet = min(max_bet, config.MAX_BET_AMOUNT)
                
                bet_size = min(bet_size, max_bet)

                # 2. 최소 베팅 금액 보장 (최소 주문 가능 금액)
                bet_size = max(bet_size, config.MIN_BET_USDC) 

                if bet_size >= config.MIN_BET_USDC:
                    self._place_bet(
                        tid=pick['tid'], coin=pick['coin'], question=pick['question'],
                        entry_price=pick['price'], size_usdc=bet_size,
                        fair_prob=pick['prob'], edge=pick['edge'],
                        end_time=pick['end_time'], side=pick['side'],
                        market_id=pick.get('marketId', ''),
                        strike=pick['strike'] # [FIX] 기준가 전달
                    )

        # === 대시보드 렌더링 ===
        self._render(analysis_results, market_count=len(market_data_list))

    # ─── 주문 실행 ────────────────────────────────────────────

    def _place_bet(self, tid, coin, question, entry_price, size_usdc, fair_prob, edge, end_time, side='YES', market_id='', strike=0.0):
        """베팅 실행 (Live Execution First Logic)"""
        
        # [CRITICAL FIX] 양방 배팅 방지 (Anti-Hedging)
        # 이미 같은 질문(Question)에 대한 포지션이 있으면 진입 금지
        for existing_tid, pos in self.positions.items():
            if pos['question'] == question:
                if existing_tid != tid: # 다른 토큰 ID인데 같은 질문 = 반대 포지션 (YES vs NO)
                    print(f"\n🚫 [SKIP] 양방 배팅 방지: 이미 진입한 마켓입니다. ({pos['side']} 보유 중)")
                    return

        # [Safety Check] 뱅크롤 초과 방지
        if size_usdc > self.bankroll:
            size_usdc = self.bankroll * 0.95
            
        # [ACCURACY FIX] Live 모드와 동일한 정밀도(Round) 적용
        # ClientWrapper.place_limit_order 로직과 일치시킴
        # Safe Price/Size = round(x, 2)
        entry_price = round(entry_price, 2)
        if entry_price <= 0: entry_price = 0.01
            
        size_usdc = round(size_usdc, 2)
        if size_usdc <= 0: size_usdc = 0.01
            
        shares = size_usdc / entry_price

        # === [CRITICAL UPDATE] 주문 집행 로직 ===
        # 1. 실전 모드(Live)인 경우:
        #    - 먼저 주문을 넣고 (API Call)
        #    - 성공하면 장부에 기록 (State Update)
        #    - 실패하면 기록하지 않음 (Rollback)
        
        if not config.PAPER_TRADING:
            if not self.client:
                print(f"\n❌ [SKIP] Client not ready. Cannot place LIVE bet on {coin}.")
                return

            print(f"\n📡 [LIVE] Placing Order: {coin} {side} ${size_usdc:.2f} (@ {entry_price:.3f})...")
            try:
                # 주문 실행
                self.client.place_limit_order(tid, entry_price, shares, 'BUY')
                print(f"  ✅ [LIVE] Order Filled/Placed Successfully!")
                
                # [LOG] 거래 로그 기록
                self._log_trade(tid, coin, side, question, entry_price, size_usdc, "OPEN")
                
            except Exception as e:
                print(f"  ❌ [LIVE] Order FAILED: {e}")
                print(f"  ⚠️  주문 실패로 인해 장부에 기록하지 않습니다. (No Phantom Trade)")
                # [DEBUG] 에러 메시지 확인용 대기
                print(f"  ⏳ 에러 확인을 위해 10초간 대기합니다...")
                time.sleep(10)
                return  # <--- 여기서 함수 종료! (장부 기록 안 함)

        # 2. 페이퍼 트레이딩 or (실전 성공 후)
        #    - 내부 상태(장부) 업데이트
        
        self.positions[tid] = {
            'coin': coin, 'question': question,
            'entry_price': entry_price, 'size_usdc': size_usdc,
            'shares': shares, 'fair_prob': fair_prob, 'edge': edge,
            'entry_time': time.time(), 'end_time': end_time, 'side': side,
            'entry_time': time.time(), 'end_time': end_time, 'side': side,
            'market_id': market_id,
            'strike': strike # [FIX] 기준가 저장
        }

        if config.PAPER_TRADING:
            # [REALITY CHECK] Paper Mode Slippage Simulation
            # 가상 매매는 "항상 최선가 체결"이라는 환상을 줍니다.
            # 이를 방지하기 위해 강제로 'Slippage(체결 오차)'를 부여합니다.
            
            # 1. 0.5% 가격 불리하게 체결 (시장가 긁을 때의 현실 반영)
            slippage_rate = 0.005 
            actual_price = entry_price * (1.0 + slippage_rate)
            
            # 2. 미세하게 잔액 더 차감 (수수료 외의 숨겨진 비용)
            slippage_cost = size_usdc * slippage_rate
            
            # [LOG] 거래 로그 기록 (Paper Mode에서도 베팅 규모 집계를 위해 기록)
            self._log_trade(tid, coin, side, question, entry_price, size_usdc, "OPEN", market_id=market_id)

            self.bankroll -= (size_usdc + slippage_cost)
            self.stats['total_wagered'] += size_usdc
            
            print(f"  📉 [Paper] Reality Slippage applied: Order @ {entry_price:.3f} -> Filled @ {actual_price:.3f} (-${slippage_cost:.3f})")
        else:
            # [FACT-ONLY] Live 모드: 주문 후 실제 잔액 동기화
            # (주문 체결로 인한 잔액 감소를 반영)
            if self.client:
                time.sleep(1.0) # 체결 대기
                try:
                    real_bal = self.client.get_usdc_balance()
                    if real_bal > 0:
                        self.bankroll = real_bal
                except: pass
        
        self.stats['total_bets'] += 1

        side_icon = "🟢BUY YES" if side == 'YES' else "🔴BUY NO"

        mode_str = "[LIVE]" if not config.PAPER_TRADING else "[PAPER]"
        
        print(f"\n  {mode_str} {side_icon} {coin} ${size_usdc:.1f}")
        print(f"  Prob:{fair_prob:.0%} Edge:{edge:+.1%} TTL:{end_time - time.time():.0f}s")
        print(f"  Bankroll: ${self.bankroll:.2f}")
        time.sleep(0.5)

    # ─── 만기 정산 ────────────────────────────────────────────

    def _settle_expired_positions(self, now: float):
        """만기 도달 포지션 처리"""
        to_remove = []

        for tid, pos in self.positions.items():
            if now >= pos['end_time']:
                # === [FACT-ONLY] Live/Paper: Real Resolution (Gamma API) 대기 ===
                coin = pos['coin']
                
                # API 호출 빈도 조절: 10초에 한 번만 체크
                last_check = pos.get('last_resolution_check', 0)
                if now - last_check < 10:
                    continue
                
                pos['last_resolution_check'] = now
                m_id = pos.get('market_id', tid) 
                winner = self.client.get_market_winner(m_id)
                
                print(f"  🔍 Checking {coin} Result... API says: {winner}")
                
                if winner == 'WAITING':
                    continue
                
                if winner is None:
                    continue
                    
                my_side = pos.get('side', 'YES')
                won = (winner == my_side)
                
                print(f"  ⚖️ [Oracle] 결과 확정: {winner} (My Side: {my_side})")

                if won:
                    self._settle_as_win(tid, pos)
                else:
                    self._settle_as_loss(tid, pos)

                to_remove.append(tid)

        for tid in to_remove:
            self.positions.pop(tid, None)

    def _settle_as_win(self, tid, pos):
        """승리 정산"""
        payout = pos['shares'] * 1.0
        fee = payout * config.FEE_RATE
        net_payout = payout - fee
        profit = net_payout - pos['size_usdc']

        # 실전 모드에서는 잔액을 직접 더하지 않음 (자동 동기화 루프가 따로 있음)
        if config.PAPER_TRADING:
            self.bankroll += net_payout
        self.stats['wins'] += 1
        self.stats['total_pnl'] += profit
        
        # [LOG] 승리 기록 (PnL 명시)
        self._log_trade(tid, pos['coin'], pos.get('side', 'YES'), pos['question'], 1.0, payout, "WIN", pnl=profit)

        if self.bankroll > self.stats['peak_bankroll']:
            self.stats['peak_bankroll'] = self.bankroll

        s = pos.get('side', '?')
        # [FIX] 저장된 strike 사용 (없으면 재추출 시도하되 coin 파라미터 전달)
        strike = pos.get('strike', self.extract_strike_price(pos['question'], pos['coin']))
        spot_final = self.binance.get_price_at_time(pos['coin'], pos['end_time'])
        
        from datetime import datetime
        time_str = datetime.fromtimestamp(pos['end_time']).strftime('%H:%M:%S')

        print(f"\n  ✅ WIN {pos['coin']} {s} +${profit:.1f}")
        if config.PAPER_TRADING:
             print(f"     ⚖️ {pos['coin']} ${spot_final:,.2f} vs ${strike:,.2f} (@ {time_str})")
        print(f"  Bankroll: ${self.bankroll:.2f}")

    def _settle_as_loss(self, tid, pos):
        """패배 정산"""
        loss = -pos['size_usdc']

        self.stats['losses'] += 1
        self.stats['total_pnl'] += loss

        # 실전 모드에서는 bankroll 차감을 패스 (실제 balance 싱크로 처리)
        if config.PAPER_TRADING:
            # Paper 모드에서도 bankroll은 이미 OPEN 시점에 전액 차감되었으므로 따로 뺄 필요 없음
            pass
        equity = self.bankroll + sum(p['size_usdc'] for p in self.positions.values())
        current_peak = max(self.stats['peak_bankroll'], equity)
        dd = 1.0 - (equity / current_peak) if current_peak > 0 else 0.0
        
        if dd > self.stats['max_drawdown']:
            self.stats['max_drawdown'] = dd

        s = pos.get('side', '?')
        # [FIX] 저장된 strike 사용
        strike = pos.get('strike', self.extract_strike_price(pos['question'], pos['coin']))
        spot_final = self.binance.get_price_at_time(pos['coin'], pos['end_time'])

        from datetime import datetime
        time_str = datetime.fromtimestamp(pos['end_time']).strftime('%H:%M:%S')

        print(f"\n  ❌ LOSS {pos['coin']} {s} -${pos['size_usdc']:.1f}")
        if config.PAPER_TRADING:
             print(f"     ⚖️ {pos['coin']} ${spot_final:,.2f} vs ${strike:,.2f} (@ {time_str})")
        print(f"  뱅크롤: ${self.bankroll:.2f}")
        print(f"{'='*48}")
        
        # [LOG] 패배 기록 (PnL 명시, size_usdc 기록)
        self._log_trade(tid, pos['coin'], s, pos['question'], 0.0, pos['size_usdc'], "LOSS", pnl=loss, market_id=pos.get('market_id', ''))

    # ─── 리스크 관리 ──────────────────────────────────────────

    # ─── 리스크 관리 ──────────────────────────────────────────

    def _check_drawdown_halt(self) -> bool:
        """드로다운 한도 초과 시 봇 정지 (투자금은 자산으로 인정)"""
        # 현재 투자 중인 금액 계산
        invested = sum(pos['size_usdc'] for pos in self.positions.values())
        equity = self.bankroll + invested
        
        if equity <= 0:
            print("\n🚨 파산! 대출이라도 받아오십쇼. 봇을 정지합니다.")
            return True

        # 최고점(Peak) 대비 현재 총자산(Equity) 하락률 계산
        # peak_bankroll은 현금 기준이므로, 현재 Equity와 비교하여 보정
        current_peak = max(self.stats['peak_bankroll'], equity, self.initial_bankroll)
        
        dd_pct = 1.0 - (equity / current_peak)

        if dd_pct >= config.DRAWDOWN_HALT_PCT:
            print(f"\n🚨 드로다운 {dd_pct:.1%} (Equity: ${equity:.2f}) — 한도 {config.DRAWDOWN_HALT_PCT:.1%} 초과! 봇 정지.")
            return True

        return False

    # ─── 호가 분석 ────────────────────────────────────────────

    def _get_best_ask(self, order_book: dict) -> float:
        """호가창에서 최우선 매도 호가 추출"""
        asks = order_book.get('asks', [])
        if not asks:
            return 0.0
        try:
            prices = [float(a['price']) for a in asks if float(a['price']) > 0]
            return min(prices) if prices else 0.0
        except (ValueError, KeyError):
            return 0.0

    def _get_best_bid(self, order_book: dict) -> float:
        """호가창에서 최우선 매수 호가 추출"""
        bids = order_book.get('bids', [])
        if not bids:
            return 0.0
        try:
            prices = [float(b['price']) for b in bids if float(b['price']) > 0]
            return max(prices) if prices else 0.0
        except (ValueError, KeyError):
            return 0.0

    # ─── 로깅 시스템 ──────────────────────────────────────────

    def _log_trade(self, tid, coin, side, question, price, size, action, **kwargs):
        """거래 내역을 JSONL 파일로 저장"""
        
        record = {
            "strategy": config.STRATEGY_NAME,
            "timestamp": datetime.now().isoformat(),
            "action": action, # OPEN / WIN / LOSS
            "coin": coin,
            "side": side,
            "size_usdc": round(size, 2),
            "pnl": round(kwargs.get('pnl', 0.0), 2), # 명시적 PnL 기록
            "price": round(price, 3),
            "question": question,
            "tid": tid,
            "marketId": kwargs.get('market_id', ''),
            "bankroll_after": round(self.bankroll, 2)
        }
        
        try:
            with open(self.trade_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush() # 즉시 파일에 쓰기 (대시보드 실시간 반영용)
        except Exception as e:
            print(f"Failed to write trade log: {e}")

    # ─── 상태 렌더링 ──────────────────────────────────────────

    def _render(self, analysis_results: list, market_count: int = 0):
        """터미널에 현재 상태 출력 (HATEBOT 모바일 최적화)"""
        now = time.time()
        if now - self._last_render < 3:
            return
        self._last_render = now

        elapsed = int(now - self.start_time)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        total, wins, losses = self.stats['total_bets'], self.stats['wins'], self.stats['losses']
        
        # [FIX] 승률 계산: 아직 결과 안 나온(Active) 베팅은 분모에서 제외
        settled = wins + losses
        win_rate = (wins / settled * 100) if settled > 0 else 0.0

        os.system('cls' if os.name == 'nt' else 'clear')

        print(f"== [ {config.STRATEGY_NAME} v3.0 ] ({h:02d}:{m:02d}:{s:02d}) ==")
        print(f"Mode: {'PAPER' if config.PAPER_TRADING else '💰 LIVE'} | Targets: btc/eth/sol | Scn:{market_count}")
        print("-" * 48)

        # [UI FIX] Paper/Live 구분 없이 동일한 "FACT-ONLY" 대시보드 사용
        # Net Equity = Balance (Cash) + Active Positions Value (시장가 평가)
        active_value = 0.0
        for p in self.positions.values():
            # Paper 모드여도 현재가(Market Price) 기반으로 평가가치 산출
            curr = p.get('current_price', 0.0)
            if curr == 0 and config.PAPER_TRADING:
                # Paper 모드 초기 진입 시 current_price가 없을 수 있음 -> entry_price로 대체 (임시)
                curr = p.get('entry_price', 0.0)
            active_value += (curr * p['shares'])

        net_equity = self.bankroll + active_value
        
        # 시작 자본금 대비 수익금 (Paper는 initial_bankroll이 기준, Live는 real_balance_start가 기준)
        start_cap = self.initial_bankroll if config.PAPER_TRADING else self.real_balance_start
        real_pnl = net_equity - start_cap
        
        # 수익률 (0으로 나누기 방지)
        roi = (real_pnl / start_cap * 100) if start_cap > 0 else 0.0
        
        print(f"💰 BALANCE:       ${self.bankroll:8.2f} (Cash)")
        print(f"📈 ACTIVE VALUE:  ${active_value:8.2f} (Positions)")
        print(f"💎 NET EQUITY:    ${net_equity:8.2f} (Total Asset)")
        print(f"📊 REAL PnL:      ${real_pnl:+8.2f} (Return: {roi:+.1f}%)")
        print(f"🎯 BETS {total} | activate : {len(self.positions)} | W {wins} L {losses}")
        print("-" * 48)

        # 전문가 직관 분석 (Pure Alpha)
        print("[ALPHA SIGNALS]")
        for coin in ['BTC', 'ETH', 'SOL']:
            sig = self.binance.get_expert_signals(coin)
            t_icon = "🚀" if sig['trend'] == 'bull' else "📉" if sig['trend'] == 'bear' else "↔️"
            e_icon = "!" if sig['strength'] > 0.7 else ""
            print(f" {coin:3s} {t_icon} {sig['trend'].upper():4s} | Str:{sig['strength']:4.2f}{e_icon} | RSI:{sig['rsi']:4.1f}")
        print("-" * 48)

        # 활성 포지션
        if self.positions:
            print("[ACTIVE POSITIONS]")
            for tid, pos in self.positions.items():
                ttl = max(0, pos['end_time'] - now)
                s_icon = "🟢" if pos['side'] == 'YES' else "🔴"
                # 사용자가 헷갈려하므로 Strike Price 대신 Size를 명확히 표시
                sz = pos['size_usdc']
                
                status_msg = ""
                price_info = ""
                
                # 순수익률 계산 (Current / Entry - 1)
                curr = pos.get('current_price', pos['entry_price'])
                entry = pos['entry_price']
                ret = (curr / entry - 1) * 100 if entry > 0 else 0.0
                
                price_info = f"${entry:.2f}→${curr:.2f}({ret:+.0f}%)"

                if now >= pos['end_time']:
                    status_msg = " [WAIT]"
                else:
                    status_msg = f"{ttl:3.0f}s"
                    
                print(f" {s_icon}{pos['coin']:3s} {pos['side']:3s} {price_info} Sz:${sz:3.0f} | P:{pos['fair_prob']:3.0%} / {status_msg}")
            print("-" * 48)

        # 시장 분석 결과
        if analysis_results:
            print("[TARGET SNIPER]")
            for r in analysis_results[:4]:
                mark = "*" if r['edge'] > 0 else " "
                alpha = f"({r['alpha_log'][:8]})" if r['alpha_log'] != "Neutral" else ""
                print(f"{mark}{r['coin']:3s} {r['side']:3s} | Pb:{r['prob']:3.0%} / Ed:{r['edge']:+5.1%} {alpha}")
            print("-" * 48)

        # === [NEW] 대시보드용 상태 스냅샷 저장 ===
        self._save_snapshot(real_pnl, net_equity, roi, win_rate)

    def _save_snapshot(self, real_pnl, net_equity, roi, win_rate):
        """대시보드 실시간 연동을 위한 스냅샷 파일 저장"""
        try:
            snapshot_path = os.path.join(os.path.dirname(__file__), f"status_{config.STRATEGY_NAME}.json")
            data = {
                "strategy": config.STRATEGY_NAME,
                "timestamp": datetime.now().isoformat(),
                "pnl": round(real_pnl, 2),
                "equity": round(net_equity, 2),
                "balance": round(self.bankroll, 2),
                "roi": round(roi, 1),
                "win_rate": round(win_rate, 1),
                "trades": self.stats['wins'] + self.stats['losses'],
                "active_bets": len(self.positions),
                "total_bet": round(sum(p['size_usdc'] for p in self.positions.values()), 2),
                "last_action": datetime.now().isoformat()[:19]
            }
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            pass # 렌더링 루프 방해 금지



    def show_status(self, msg: str):
        """간단한 상태 메시지 표시"""
        print(f"\r⏳ {msg}", end="", flush=True)
