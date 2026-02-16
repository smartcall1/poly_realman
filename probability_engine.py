"""
Binary Option 확률 엔진 (Probability Engine)

수학적 기반:
    Modified Black-Scholes for Cash-or-Nothing Binary Options

    P(S_T > K) = N(d2)

    d2 = [ln(S/K) + (μ - 0.5σ²)T] / (σ√T)

    S = 현재 스팟 가격 (Binance)
    K = 스트라이크 가격 (Polymarket 마켓의 목표 가격)
    σ = 실현 변동성 (연율화, binance_feed에서)
    T = 만기까지 시간 (연 단위)
    μ = 드리프트 (단기 모멘텀 보정)

Fat-tail 보정:
    크립토 5분 수익률 분포는 normal이 아니라 leptokurtic (꼬리가 두꺼움).
    이를 보정하기 위해:
    1. Parkinson 변동성을 블렌딩 (binance_feed에서 처리)
    2. Volatility Scaling Factor 적용 (기본 1.2x)
    3. Edge threshold를 높게 설정하여 모델 오류 마진 확보
"""

import math


def _norm_cdf(x: float) -> float:
    """
    표준 정규 분포 CDF (Cumulative Distribution Function).

    scipy 없이 math.erfc로 구현.
    N(x) = 0.5 × erfc(-x / √2)

    정확도: 소수점 15자리까지 정확 (IEEE 754)
    """
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def calculate_binary_probability(
    spot: float,
    strike: float,
    volatility: float,
    time_to_expiry_sec: float,
    drift: float = 0.0,
    risk_free_rate: float = 0.0,
    vol_scale: float = 1.2,
) -> float:
    """
    Binary Option "YES" 확률 계산.

    P(S_T > K) = N(d2)

    Args:
        spot: 현재 스팟 가격 ($S$)
        strike: 스트라이크 가격 ($K$)
        volatility: 연율화 변동성 ($σ$)
        time_to_expiry_sec: 만기까지 남은 시간 (초)
        drift: 단기 드리프트 ($μ$), 기본 0
        risk_free_rate: 무위험 이자율 ($r$), 초단기이므로 0
        vol_scale: Fat-tail 보정 스케일링 팩터 (기본 1.2)

    Returns:
        0.0 ~ 1.0 사이의 확률 (YES가 될 확률)

    경계 조건:
        - T ≤ 0: 이미 만기 → S > K이면 1.0, 아니면 0.0
        - σ ≤ 0: 변동성 0 → S > K이면 1.0, 아니면 0.0
        - S ≤ 0 or K ≤ 0: 비정상 → 0.5 반환
    """
    # 비정상 입력 방어
    if spot <= 0 or strike <= 0:
        return 0.5

    # 변경: 시간을 연 단위로 변환 (1년 = 365.25 × 24 × 3600 = 31,557,600초)
    T = time_to_expiry_sec / 31_557_600.0

    # 만기 도달 또는 초과
    if T <= 0:
        return 1.0 if spot > strike else 0.0

    # 변동성 보정 (fat-tail scaling)
    sigma = volatility * vol_scale

    # σ가 극히 작으면 (가격이 거의 안 움직임)
    if sigma <= 1e-10:
        return 1.0 if spot > strike else 0.0

    # d2 계산
    # μ = drift (시장 추세), r = risk_free_rate
    # 여기서 drift를 μ로 직접 사용 (GBM의 실제 드리프트)
    mu = drift if drift != 0 else risk_free_rate

    sqrt_T = math.sqrt(T)
    d2 = (math.log(spot / strike) + (mu - 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)

    prob = _norm_cdf(d2)

    # 클리핑: 절대 0이나 1이 되지 않도록 (모델 오류 마진)
    prob = max(0.001, min(0.999, prob))

    return prob


def calculate_edge(
    fair_prob: float,
    market_price: float,
    fee_rate: float = 0.02,
) -> float:
    """
    수수료 반영 실질 엣지 계산.

    엣지 = 기대 수익 - 비용

    EV = fair_prob × (1 - market_price) - (1 - fair_prob) × market_price - fee
       = fair_prob - market_price - fee_cost

    좀 더 정확히:
    승리 시 순이익 = (1 - market_price) × (1 - fee_rate)
    패배 시 손실 = market_price
    EV = fair_prob × 순이익 - (1 - fair_prob) × 손실

    Args:
        fair_prob: 모델 계산 확률
        market_price: 시장 가격 (매수 가격)
        fee_rate: 거래 수수료율

    Returns:
        실질 엣지 (양수면 +EV, 음수면 -EV)
    """
    if market_price <= 0 or market_price >= 1:
        return -1.0

    # 승리 시 순 페이오프 (수수료 차감)
    win_payout = (1.0 - market_price) * (1.0 - fee_rate)

    # 패배 시 손실 (매수 금액 전액)
    loss = market_price

    # 기대값
    ev = fair_prob * win_payout - (1.0 - fair_prob) * loss

    return ev


def calculate_implied_probability(market_price: float, fee_rate: float = 0.02) -> float:
    """
    시장 가격에서 내재 확률 역산 (수수료 포함).

    시장 가격이 '이미' 수수료를 반영했다고 가정하면:
    implied_prob ≈ market_price / (1 - fee_rate × (1 - market_price))

    간단히: 시장 가격 그 자체가 내재 확률의 근사치.
    """
    if market_price <= 0 or market_price >= 1:
        return market_price
    return market_price


def get_probability_confidence(
    n_candles: int,
    vol: float,
    time_to_expiry_sec: float,
) -> float:
    """
    확률 계산의 신뢰도 (0.0 ~ 1.0).

    신뢰도에 영향을 주는 요소:
    1. 캔들 데이터 수: 많을수록 변동성 추정이 정확
    2. 만기까지 시간: 너무 짧으면 모델 오류 큼
    3. 변동성 수준: 극단적이면 모델 불안정

    Returns:
        0.0 (신뢰 불가) ~ 1.0 (높은 신뢰)
    """
    conf = 1.0

    # 데이터 수 페널티
    if n_candles < 5:
        conf *= 0.2
    elif n_candles < 10:
        conf *= 0.5
    elif n_candles < 20:
        conf *= 0.8

    # 만기 시간 페널티 (30초 미만이면 신뢰↓)
    if time_to_expiry_sec < 30:
        conf *= 0.3
    elif time_to_expiry_sec < 60:
        conf *= 0.6

    # 극단적 변동성 페널티
    if vol > 5.0:  # 연율화 500% 이상
        conf *= 0.5
    elif vol < 0.05:  # 연율화 5% 미만 (의심스러운 데이터)
        conf *= 0.4

    return max(0.0, min(1.0, conf))
def adjust_prob_by_expert_signals(
    base_prob: float,
    signals: dict
) -> tuple:
    """
    Antigravity Pure Alpha: 전문가 직관 기반 상남자 확률 보정.
    """
    adj_prob = base_prob
    reasons = []

    trend = signals.get('trend', 'neutral')
    strength = signals.get('strength', 0.0)
    state = signals.get('state', 'normal')
    
    # 1. 추세 추종 (CORE ALPHA - 상남자의 직관)
    # 추세가 조금이라도 보이면 파격적으로 밀어줌 (최대 25~30%)
    if trend == 'bull':
        boost = strength * 0.25
        adj_prob += boost
        reasons.append(f"PURE BULL (+{boost:.1%})")
    elif trend == 'bear':
        boost = strength * 0.25
        adj_prob -= boost
        reasons.append(f"PURE BEAR (-{boost:.1%})")

    # 2. RSI 모멘텀 (추가 가속)
    if state == 'strong_trend_up':
        adj_prob += 0.08
        reasons.append("Trend Momentum UP (+8%)")
    elif state == 'strong_trend_down':
        adj_prob -= 0.08
        reasons.append("Trend Momentum DOWN (-8%)")

    # 3. 과열 필터 (상남자 모드에선 살짝만 조절 - 역발상 방지)
    if state == 'overbought' and adj_prob > 0.85:
        adj_prob -= 0.03
        reasons.append("Cooling Down (-3%)")
    elif state == 'oversold' and adj_prob < 0.15:
        adj_prob += 0.03
        reasons.append("Heating Up (+3%)")

    # 클리핑 (거의 0 or 1에 도달하게 허용)
    adj_prob = max(0.001, min(0.999, adj_prob))
    
    alpha_log = " | ".join(reasons) if reasons else "Neutral"
    return adj_prob, alpha_log
