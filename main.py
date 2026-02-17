"""
+EV 바이너리 옵션 봇 — 메인 진입점

루프:
1. Polymarket에서 활성 UPDOWN 마켓 탐색
2. EVStrategy.run_ev_step() 호출 (Fair Value → Edge → Kelly → 진입)
3. 만기 도달 포지션 자동 정산
4. 반복

핵심: 절대 조기 청산하지 않는다. 만기까지 보유. Hold-to-Maturity.
"""

import time
import json
from config import config


def main():
    print("=== POLYMARKET HATEBOT v2.1 ===")
    print("Core: Pure Alpha Sniper (YES/NO Mode)")
    print()
    print(f"  모드: {'📋 PAPER TRADING (가상)' if config.PAPER_TRADING else '💰 LIVE TRADING (실전)'}")
    print(f"  뱅크롤: ${config.INITIAL_BANKROLL:.2f}")
    print(f"  최소 엣지: {config.MIN_EDGE:.0%}")
    print(f"  켈리 비율: {config.KELLY_FRACTION:.0%}")
    print(f"  최대 베팅: 뱅크롤의 {config.MAX_BET_FRACTION:.0%}")
    print(f"  드로다운 한도: {config.DRAWDOWN_HALT_PCT:.0%}")
    print()

    from client_wrapper import PolymarketClient
    from ev_strategy import EVStrategy

    # 클라이언트 초기화
    try:
        client = PolymarketClient()
    except Exception as e:
        if config.PAPER_TRADING:
            print("[주의] API 클라이언트 초기화 실패 (Paper 모드로 계속)")
            client = None
        else:
            print(f"[에러] 클라이언트 초기화 실패: {e}")
            return

    strategy = EVStrategy(client)

    print("  🚀 봇 시작! Binance 데이터 수집 중...\n")

    # 초기 캔들 데이터 로딩 (첫 루프 전 변동성 계산용)
    print("  ⏳ 초기 변동성 데이터 수집 중...", end="", flush=True)
    for coin in ['BTC', 'ETH', 'SOL', 'XRP']:
        strategy.binance.fetch_candles(coin, limit=60)
        time.sleep(0.5)  # API 부하 방지
    print(" 완료!")

    try:
        active_tokens = []
        last_search = 0

        while True:
            try:
                now = time.time()

                # === 시장 탐색 ===
                if not active_tokens or (now - last_search) > config.MARKET_SCAN_INTERVAL:
                    markets = client.find_active_markets() if client else []
                    active_tokens = []
                    for m in markets:
                        tids = m.get('clobTokenIds', [])
                        if isinstance(tids, str):
                            tids = json.loads(tids)
                        if tids:
                            # YES 토큰 (보통 인덱스 0)
                            active_tokens.append({
                                'tid': tids[0],
                                'side': 'YES',
                                'question': m.get('question', '?'),
                                'slug': m.get('slug', ''),
                                'end_time': m.get('end_time', 0),
                            })
                            # NO 토큰 (보통 인덱스 1) - 하락 베팅용
                            if len(tids) > 1:
                                active_tokens.append({
                                    'tid': tids[1],
                                    'side': 'NO',
                                    'question': m.get('question', '?'),
                                    'slug': m.get('slug', ''),
                                    'end_time': m.get('end_time', 0),
                                })
                    last_search = now

                    if not active_tokens:
                        strategy.show_status("시장 탐색 중...")
                        time.sleep(10)
                        continue

                # === 각 마켓 데이터 수집 ===
                market_data = []
                for item in active_tokens:
                    order_book = client.get_order_book(item['tid']) if client else None
                    if order_book:
                        market_data.append({
                            'tid': item['tid'],
                            'side': item['side'],
                            'question': item['question'],
                            'order_book': order_book,
                            'end_time': item.get('end_time', 0),
                        })

                # === +EV 전략 실행 ===
                if market_data:
                    strategy.run_ev_step(market_data)
                else:
                    # 데이터 수집 실패 → 즉시 재탐색
                    active_tokens = []
                    last_search = 0
                    strategy.show_status("데이터 수집 실패 — 시장 재탐색 중...")

            except Exception as e:
                if config.DEBUG_MODE:
                    print(f"\n[Error] {e}")
                    import traceback
                    traceback.print_exc()

            time.sleep(config.MAIN_LOOP_INTERVAL)

    except KeyboardInterrupt:
        print("\n=== HATEBOT STOPPED (User Interrupted) ===")
    except Exception as e:
        print(f"\n=== HATEBOT CRASHED ===")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 종료 시 통계 출력
        if 'strategy' in locals():
            print(f"  Bets: {strategy.stats['total_bets']} | W:{strategy.stats['wins']} / L:{strategy.stats['losses']}")
            print(f"  PnL: ${strategy.stats['total_pnl']:+.2f} | Bankroll: ${strategy.bankroll:.2f}")



if __name__ == "__main__":
    main()
