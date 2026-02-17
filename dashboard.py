import json
import os
import time
from datetime import datetime
from collections import defaultdict

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

def format_currency(value):
    color = "\033[92m" if value > 0 else "\033[91m" if value < 0 else ""
    reset = "\033[0m"
    return f"{color}${value:,.2f}{reset}"

def run_dashboard():
    history_file = 'trade_history.jsonl'
    
    while True:
        stats = defaultdict(lambda: {
            'pnl': 0.0, 
            'trades': 0, 
            'wins': 0, 
            'losses': 0, 
            'last_trade': '-', 
            'roi': 0.0,
            'total_bet': 0.0
        })
        
        try:
            if os.path.exists(history_file):
                with open(history_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            data = json.loads(line)
                            # 실제 키는 'strategy'이며, 과거 로그는 없을 수 있음
                            name = data.get('strategy', 'Legacy (Pre-v3)')
                            
                            pnl = data.get('pnl', 0.0)
                            action = data.get('action', '')
                            
                            s = stats[name]
                            
                            # 정산된 거래(WIN/LOSS/EXPIRED)에 대해서만 PnL 및 승수 집계
                            if action in ['WIN', 'LOSS', 'EXPIRED']:
                                s['pnl'] += pnl
                                s['trades'] += 1
                                if pnl > 0: s['wins'] += 1
                                elif pnl < 0: s['losses'] += 1
                            
                            # 주문(OPEN) 시에 베팅 규모 집계
                            if action == 'OPEN':
                                s['total_bet'] += data.get('size_usdc', 0.0)
                            
                            s['last_trade'] = data.get('timestamp', '-')[:19]
                        except:
                            continue
            
            clear_console()
            print("="*85)
            print(f" 🚀 [POLYMARKET HATEBOT v3.0] UNIFIED PERFORMANCE DASHBOARD ({datetime.now().strftime('%H:%M:%S')})")
            print("="*85)
            print(f"{'PERSONA':<15} | {'PnL':<12} | {'Win%':<8} | {'Trades':<8} | {'Total Bet':<12} | {'Last Action'}")
            print("-"*85)
            
            # PnL 순으로 정렬
            sorted_stats = sorted(stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
            
            total_global_pnl = 0
            for name, s in sorted_stats:
                win_rate = (s['wins'] / s['trades'] * 100) if s['trades'] > 0 else 0
                total_global_pnl += s['pnl']
                
                print(f"{name:<15} | {format_currency(s['pnl']):<21} | {win_rate:>6.1f}% | {s['trades']:>8} | ${s['total_bet']:>10.1f} | {s['last_trade']}")
            
            print("-"*85)
            print(f"{'TOTAL PROFIT':<15} | {format_currency(total_global_pnl):<21}")
            print("="*85)
            print("\n [Tip] 이 화면은 5초마다 자동 갱신됩니다. (Ctrl+C로 종료)")
            
        except Exception as e:
            print(f"대시보드 갱신 오류: {e}")
            
        time.sleep(5)

if __name__ == "__main__":
    # ANSI 이스케이프 코드 활성화 (Windows용)
    if os.name == 'nt':
        os.system('')
    run_dashboard()
