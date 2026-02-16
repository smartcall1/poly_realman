"""
+EV(양의 기대값) 베팅 전략 코어

전략 핵심:
1. Binance 실시간 가격 + HF 변동성으로 각 마켓의 "Fair Value" 계산
2. Fair Value vs 시장 가격 비교 → Edge > MIN_EDGE면 진입
3. 분수 켈리로 정확한 베팅 사이즈 결정
4. 만기까지 무조건 보유 (NO stop-loss, NO take-profit)
5. 대수의 법칙으로 장기 수익 실현

핵심 철학: "Hold-to-Maturity"
- 진입 후 절대 조기 청산하지 않는다
- 0 or 1로 결판. 매번의 개별 결과는 무의미.
- +EV 베팅을 수백 번 반복하면 기대값에 수렴한다.
"""

import re
import time
import os

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

        # 뱅크롤 관리
        self.initial_bankroll = config.INITIAL_BANKROLL
        self.bankroll = self.initial_bankroll

        # 활성 포지션: {tid: {entry_price, size_usdc, fair_prob, edge, coin, question, entry_time, end_time}}
        self.positions = {}

        # 누적 통계
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

    # ─── 마켓 파싱 ────────────────────────────────────────────

    def extract_coin(self, question: str) -> str:
        q = question.upper()
        if "BTC" in q or "BITCOIN" in q: return "BTC"
        if "ETH" in q or "ETHEREUM" in q: return "ETH"
        if "SOL" in q or "SOLANA" in q: return "SOL"
        if "XRP" in q or "RIPPLE" in q: return "XRP"
        return ''

    def extract_strike_price(self, question: str) -> float:
        """강화된 스트라이크 가격 추출 로직"""
        # 1. $ 기호 뒤의 숫자 (소수점 포함)
        dollar_matches = re.findall(r'\$\s*([\d,]+(?:\.\d+)?)', question)
        if dollar_matches:
            try:
                return float(dollar_matches[0].replace(',', ''))
            except Exception: pass

        # 2. 숫자만 있는 패턴 (예: "XRP ... 0.65 ...")
        num_matches = re.findall(r'(\d+\.\d+|\d+)', question)
        if num_matches:
            # 5분 마켓의 경우 큰 숫자는 시간일 수 있으므로 적절한 값 필터링
            for n in num_matches:
                val = float(n.replace(',', ''))
                if val > 10: return val # 가격으로 추정

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

        # [필수] 가격 갱신 및 정산 (상남자는 기본에 충실함)
        self.binance.fetch_spot_prices()
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
                if bid > 0:
                    self.positions[tid]['current_price'] = bid
                continue

            side = data.get('side', 'YES')
            question = data['question']
            end_time = data.get('end_time', 0)

            coin = self.extract_coin(question)
            strike = self.extract_strike_price(question)
            is_above = self.is_above_market(question)
            if not coin: continue

            # 스팟/변동성 수집
            spot = self.binance.get_spot_price(coin)
            if spot <= 0: continue
            
            # 스트라이크 파싱 실패 시 현재가로 대체
            if strike <= 0: strike = spot

            time_to_expiry = end_time - now
            if time_to_expiry < 10: continue

            self.binance.fetch_candles(coin)
            vol = self.binance.get_blended_volatility(coin)
            drift = self.binance.get_drift(coin)

            # 확률 및 엣지 계산
            base_prob = calculate_binary_probability(spot, strike, vol, time_to_expiry, drift)
            
            actual_prob = base_prob if side == 'YES' else (1.0 - base_prob)
            if not is_above: actual_prob = 1.0 - actual_prob

            expert_signals = self.binance.get_expert_signals(coin)
            final_prob, alpha_log = adjust_prob_by_expert_signals(actual_prob, expert_signals)

            best_ask = self._get_best_ask(order_book)
            if best_ask <= 0: best_ask = 0.50 # 보수적 중간값
            
            edge = calculate_edge(final_prob, best_ask, config.FEE_RATE)
            sig_str = expert_signals.get('strength', 0.0)

            # [코인별 베스트 픽 선별]
            if coin not in coin_best_pick or edge > coin_best_pick[coin]['edge']:
                coin_best_pick[coin] = {
                    'tid': tid, 'coin': coin, 'side': side, 'question': question,
                    'price': best_ask, 'prob': final_prob, 'edge': edge,
                    'end_time': end_time, 'strength': sig_str, 'alpha_log': alpha_log
                }

            # 분석 기록 (UI 표시용)
            analysis_results.append({
                'tid': tid, 'coin': coin, 'side': side, 'prob': final_prob,
                'price': best_ask, 'edge': edge, 'strength': sig_str, 'alpha_log': alpha_log
            })

        # === 지상 최강의 사냥: 선택된 마켓 강제 집행 ===
        for coin, pick in coin_best_pick.items():
            if len(self.positions) >= (config.MAX_CONCURRENT_BETS + 25): break

            # 엣지가 -30% 이상이면(사실상 모든 판) 상남자의 직관으로 진입
            if pick['edge'] >= -0.30:
                k_mult = 4.0 if pick['strength'] > 0.7 else 2.0
                bet_size = kelly_bet_size(
                    bankroll=self.bankroll, win_prob=pick['prob'], market_price=pick['price'],
                    fee_rate=config.FEE_RATE, kelly_fraction=config.KELLY_FRACTION * k_mult
                )
                
                # 강제 금액: 기본 $10, 뱅크롤 1% (적당한 긴장감)
                force_min = max(10.0, self.bankroll * 0.01)
                bet_size = max(bet_size, force_min)
                
                # [안전장치] 실전 투입용 쫄보 모드 ($30 제한)
                bet_size = min(bet_size, 30.0) 

                if bet_size >= config.MIN_BET_USDC:
                    self._place_bet(
                        tid=pick['tid'], coin=pick['coin'], question=pick['question'],
                        entry_price=pick['price'], size_usdc=bet_size,
                        fair_prob=pick['prob'], edge=pick['edge'],
                        end_time=pick['end_time'], side=pick['side']
                    )

        # === 대시보드 렌더링 ===
        self._render(analysis_results, market_count=len(market_data_list))

    # ─── 주문 실행 ────────────────────────────────────────────

    def _place_bet(self, tid, coin, question, entry_price, size_usdc, fair_prob, edge, end_time, side='YES'):
        """베팅 실행 (HATEBOT 모드)"""
        if size_usdc > self.bankroll:
            size_usdc = self.bankroll * 0.95

        shares = size_usdc / entry_price

        self.positions[tid] = {
            'coin': coin, 'question': question,
            'entry_price': entry_price, 'size_usdc': size_usdc,
            'shares': shares, 'fair_prob': fair_prob, 'edge': edge,
            'entry_time': time.time(), 'end_time': end_time, 'side': side,
        }

        self.bankroll -= size_usdc
        self.stats['total_bets'] += 1
        self.stats['total_wagered'] += size_usdc

        side_icon = "🟢BUY YES" if side == 'YES' else "🔴BUY NO"
        print(f"\n  [{side_icon}] {coin} ${size_usdc:.1f}")
        print(f"  Prob:{fair_prob:.0%} Edge:{edge:+.1%} TTL:{end_time - time.time():.0f}s")
        print(f"  Bankroll: ${self.bankroll:.2f}")
        time.sleep(0.5)

        if not config.PAPER_TRADING and self.client:
            try:
                self.client.place_limit_order(tid, entry_price, shares, 'BUY')
            except Exception as e:
                print(f"  [Order Error] {e}")

    # ─── 만기 정산 ────────────────────────────────────────────

    def _settle_expired_positions(self, now: float):
        """만기 도달 포지션 자동 정산"""
        to_remove = []

        for tid, pos in self.positions.items():
            if now >= pos['end_time']:
                # Paper Trading: 현재 스팟과 스트라이크 비교로 결과 판정
                coin = pos['coin']
                spot_now = self.binance.get_spot_price(coin)
                strike = self.extract_strike_price(pos['question'])

                if strike <= 0 or spot_now <= 0:
                    # 가격 데이터 없으면 다음 루프에서 재시도
                    # 만기 후 10초까지만 대기
                    if now - pos['end_time'] > 10:
                        # 타임아웃 → 패배로 처리 (보수적)
                        self._settle_as_loss(tid, pos)
                        to_remove.append(tid)
                    continue

                is_above = self.is_above_market(pos['question'])

                # 결과 판정
                if is_above:
                    won = spot_now > strike
                else:
                    won = spot_now < strike

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

        self.bankroll += net_payout
        self.stats['wins'] += 1
        self.stats['total_pnl'] += profit

        if self.bankroll > self.stats['peak_bankroll']:
            self.stats['peak_bankroll'] = self.bankroll

        s = pos.get('side', '?')
        print(f"\n  ✅ WIN {pos['coin']} {s} +${profit:.1f}")
        print(f"  Bankroll: ${self.bankroll:.2f}")

    def _settle_as_loss(self, tid, pos):
        """패배 정산"""
        loss = -pos['size_usdc']

        self.stats['losses'] += 1
        self.stats['total_pnl'] += loss

        dd = (self.stats['peak_bankroll'] - self.bankroll) / self.stats['peak_bankroll']
        if dd > self.stats['max_drawdown']:
            self.stats['max_drawdown'] = dd

        s = pos.get('side', '?')
        print(f"\n  ❌ LOSS {pos['coin']} {s} -${pos['size_usdc']:.1f}")
        print(f"  뱅크롤: ${self.bankroll:.2f}")
        print(f"{'='*60}")

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
        win_rate = (wins / total * 100) if total > 0 else 0

        os.system('cls' if os.name == 'nt' else 'clear')

        print(f"== [ POLYMARKET HATEBOT v2.1 ] ({h:02d}:{m:02d}:{s:02d}) ==")
        print(f"Mode: {'PAPER' if config.PAPER_TRADING else 'LIVE'} | Targets: btc/eth/sol/xrp | Scn:{market_count}")
        print("-" * 48)
        
        
        # 미확정 수익 계산
        unrealized_pnl = 0.0
        for pos in self.positions.values():
            # 현재가 없으면 진입가로 가정 (손익 0)
            curr = pos.get('current_price', pos['entry_price'])
            val = curr * pos['shares']
            cost = pos['size_usdc']
            unrealized_pnl += (val - cost)

        print(f"BANKROLL: ${self.bankroll:8.2f} | PnL: {self.stats['total_pnl']:+8.2f} (Unreal: {unrealized_pnl:+8.2f})")
        print(f"STATS: {total:3d} Bets ({wins}W {losses}L) | Win: {win_rate:4.1f}%")
        print("-" * 48)

        # 전문가 직관 분석 (Pure Alpha)
        print("[ALPHA SIGNALS]")
        for coin in ['BTC', 'ETH', 'SOL', 'XRP']:
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
                print(f" {s_icon}{pos['coin']:3s} {pos['side']:3s} Sz:${sz:3.0f} | Prob:{pos['fair_prob']:3.0%} / {ttl:3.0f}s left")
            print("-" * 48)

        # 시장 분석 결과
        if analysis_results:
            print("[TARGET SNIPER]")
            for r in analysis_results[:4]:
                mark = "*" if r['edge'] > 0 else " "
                alpha = f"({r['alpha_log'][:8]})" if r['alpha_log'] != "Neutral" else ""
                print(f"{mark}{r['coin']:3s} {r['side']:3s} | Pb:{r['prob']:3.0%} / Ed:{r['edge']:+5.1%} {alpha}")
            print("-" * 48)



    def show_status(self, msg: str):
        """간단한 상태 메시지 표시"""
        print(f"\r⏳ {msg}", end="", flush=True)
