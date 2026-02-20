import matplotlib
matplotlib.use('Agg')
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import json
import os
import numpy as np
from datetime import datetime

def plot_performance():
    log_file = "trade_history.jsonl"
    if not os.path.exists(log_file):
        print(f"Error: {log_file} not found.")
        return

    data = []
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        data.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    if not data:
        print("No data found in log file.")
        return

    df = pd.DataFrame(data)
    
    required_cols = ['strategy', 'timestamp', 'pnl', 'action']
    for col in required_cols:
        if col not in df.columns:
            print(f"Error: Missing column '{col}'")
            return

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')

    # === [R] Shadow Bot 데이터 생성 (Simulation) ===
    # trade_history.jsonl에는 원본만 있으므로, 여기서 [R] 데이터를 시뮬레이션으로 생성해서 추가함
    # [FIX] Stateful Tracking: TID별 진입 가격을 기억해야 Payout 계산 가능
    
    trade_info = {} # {tid: {price: float, size: float}}
    anti_records = []
    
    for _, row in df.iterrows():
        # 원본 데이터 파싱
        orig_action = row['action']
        tid = row.get('tid')
        
        # OPEN일 때 가격 정보 저장
        if orig_action == 'OPEN':
            trade_info[tid] = {
                'price': float(row.get('price', 0.5)),
                'size': float(row.get('size_usdc', 0))
            }
        
        # [R] 전략 이름
        anti_strategy = f"[R] {row['strategy']}"
        
        # 기본값
        anti_pnl = 0.0
        anti_price = 0.0 # for record
        anti_action = orig_action
        
        # TID 정보 조회
        info = trade_info.get(tid, {'price': 0.5, 'size': 0.0})
        orig_entry_price = info['price']
        orig_entry_size = info['size']
        
        # [Logic] 진입 가격 (Slippage 2%)
        anti_entry_price = 1.0 - orig_entry_price + 0.02
        if anti_entry_price >= 1.0: anti_entry_price = 0.99
        anti_price = anti_entry_price 
        
        if orig_action == 'WIN':
            # 원본 승리 -> 반대 패배 (배팅액 전액 손실)
            anti_pnl = -orig_entry_size
            anti_action = 'LOSS'
            
        elif orig_action == 'LOSS':
            # 원본 패배 -> 반대 승리
            # Payout = (Size / AntiPrice)
            # Fee = 2%
            if anti_entry_price > 0:
                shares = orig_entry_size / anti_entry_price
                payout = shares * 1.0
                fee = payout * 0.02
                net_payout = payout - fee
                anti_pnl = net_payout - orig_entry_size
            else:
                anti_pnl = 0
            anti_action = 'WIN'
            
        elif orig_action == 'OPEN':
            anti_pnl = 0.0
            
        # 레코드 생성
        anti_record = row.copy()
        anti_record['strategy'] = anti_strategy
        anti_record['pnl'] = anti_pnl
        anti_record['price'] = anti_entry_price
        anti_record['action'] = anti_action
        anti_records.append(anti_record)
        
    # 데이터 병합
    if anti_records:
        anti_df = pd.DataFrame(anti_records)
        df = pd.concat([df, anti_df], ignore_index=True)
        
    strategies = df['strategy'].unique()
    
    # === 성과 분석 (Metrics Calculation) ===
    metrics = []
    base_strategies = set()
    
    for strategy in strategies:
        if strategy.startswith("[R] "):
            base_strategies.add(strategy[4:])
        else:
            base_strategies.add(strategy)
            
        strat_df = df[df['strategy'] == strategy].copy()
        
        # PnL 누적 (시간순)
        strat_df['cumulative_pnl'] = strat_df['pnl'].cumsum()
        
        total_pnl = strat_df['pnl'].sum()
        
        # Win Rate (WIN/LOSS 액션만 카운트)
        closed_trades = strat_df[strat_df['action'].isin(['WIN', 'LOSS'])]
        wins = len(closed_trades[closed_trades['action'] == 'WIN'])
        total_closed = len(closed_trades)
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
        
        # Max Drawdown (MDD)
        # 누적 PnL에서 고점 대비 하락폭 계산
        # 초기 자금(0)부터 시작 가정
        cum_pnl = strat_df['cumulative_pnl']
        running_max = cum_pnl.cummax()
        drawdown = cum_pnl - running_max
        mdd = drawdown.min() if not drawdown.empty else 0.0
        
        metrics.append({
            'Strategy': strategy,
            'Total PnL': total_pnl,
            'Win Rate': win_rate,
            'Trades': total_closed,
            'MDD': mdd,
            'Final Equity': cum_pnl.iloc[-1] if not cum_pnl.empty else 0
        })

    metrics_df = pd.DataFrame(metrics).sort_values('Total PnL', ascending=False)
    
    # === 시각화 (High Quality) ===
    plt.style.use('dark_background')
    
    # 색상 맵핑 (Base Strategy -> Color)
    unique_base_strats = sorted(list(base_strategies))
    palette = sns.color_palette("husl", len(unique_base_strats))
    color_map = {name: color for name, color in zip(unique_base_strats, palette)}
    
    fig = plt.figure(figsize=(20, 12)) # 고해상도용 큰 사이즈
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1])
    
    # 1. 상단: 수익 곡선 차트
    ax1 = fig.add_subplot(gs[0])
    
    # 굵기 및 스타일 설정
    for strategy in strategies:
        strat_df = df[df['strategy'] == strategy].sort_values('timestamp')
        strat_df['cumulative_pnl'] = strat_df['pnl'].cumsum()
        
        is_reverse = strategy.startswith("[R] ")
        base_name = strategy[4:] if is_reverse else strategy
        
        color = color_map.get(base_name, 'white')
        linestyle = '--' if is_reverse else '-'
        alpha = 0.8 if is_reverse else 1.0
        linewidth = 1.5 if is_reverse else 2.5 # 원본을 더 굵게
        
        label = f"{strategy}"
        if is_reverse:
            label = None # 범례 간소화를 위해 [R]은 생략하거나 별도 처리? 일단 표시.
            label = f"{strategy}"

        ax1.plot(strat_df['timestamp'], strat_df['cumulative_pnl'], 
                 label=label, color=color, linestyle=linestyle,
                 linewidth=linewidth, alpha=alpha)

    ax1.set_title("Polymarket Bot Performance: Original vs [R]everse", fontsize=20, fontweight='bold', color='white', pad=20)
    ax1.set_ylabel("Cumulative PnL (USDC)", fontsize=14)
    # 범례는 너무 많으니 밖으로 빼거나 Top performering 만 표시? 
    # 일단 우측 외부에 배치
    ax1.legend(bbox_to_anchor=(1.01, 1), loc='upper left', fontsize=10, frameon=True, facecolor='#222222')
    ax1.grid(True, linestyle=':', alpha=0.2)
    
    # 2. 하단: 분석 테이블
    ax2 = fig.add_subplot(gs[1])
    ax2.axis('off')
    
    # 테이블 데이터 (Top Sorted)
    cell_text = []
    # 색상 적용을 위해 Row Colors 준비
    row_colors = []
    
    for _, row in metrics_df.iterrows():
        st_name = row['Strategy']
        is_reverse = st_name.startswith("[R] ")
        base_name = st_name[4:] if is_reverse else st_name
        base_color = color_map.get(base_name)
        
        # RGBA -> Hex
        hex_color = matplotlib.colors.to_hex(base_color)
        # 어둡게 만들어서 배경으로 쓰기엔 복잡하니 그냥 텍스트 색상? 아니면 인덱스 컬러?
        # 심플하게 배경은 유지.
        
        cell_text.append([
            row['Strategy'],
            f"${row['Total PnL']:+.2f}",
            f"{row['Win Rate']:.1f}%",
            f"{row['Trades']}",
            f"${row['MDD']:+.2f}"
        ])
    
    col_labels = ['Strategy', 'Total PnL', 'Win Rate', 'Trades', 'Max Drawdown']
    
    table = ax2.table(cellText=cell_text, colLabels=col_labels, loc='center', cellLoc='center',
                      colColours=['#333333']*5)
    
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.3)
    
    # 헤더 스타일
    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_text_props(weight='bold', color='white')
            cell.set_facecolor('#444444')
        else:
            # 전략 이름(0열)에 색상 힌트 줄 수 있으면 좋음
            # 일단 기본 스타일
            cell.set_text_props(color='white')
            cell.set_facecolor('#1e1e1e')
            cell.set_edgecolor('#333333')

    plt.tight_layout()
    
    output_file = "performance_chart.png"
    plt.savefig(output_file, dpi=300, facecolor='#121212', bbox_inches='tight')
    print(f"✅ Enhanced Chart Saved: {os.path.abspath(output_file)}")
    
    if os.name == 'nt':
        os.startfile(output_file)

if __name__ == "__main__":
    plot_performance()
