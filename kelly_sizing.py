"""
분수 켈리 기준 포지션 사이징 (Fractional Kelly Criterion)

핵심 공식:
    f* = (b × p - q) / b

    b = 배당률 (odds received) = (1 / market_price) - 1
    p = 승리 확률 (모델 계산)
    q = 패배 확률 = 1 - p

    실제 베팅 = f* × KELLY_FRACTION × bankroll

Why Fractional Kelly?
    Full Kelly는 이론적으로 장기 복리 수익을 최대화하지만,
    모델이 완벽하지 않은 현실에서는 너무 공격적이다.
    모델 오류(확률 추정 오차)가 있으면 Full Kelly는 파산으로 직행.
    따라서 Kelly의 25% (Quarter Kelly)를 사용:
    - 기대 성장률은 Full Kelly의 ~75%
    - 변동성(drawdown)은 Full Kelly의 ~50%로 감소
    - 모델 오류에 대한 상당한 마진 확보

안전장치:
    1. 음수 켈리 → 0 (절대 베팅 안 함)
    2. MAX_BET_FRACTION 한도 (뱅크롤의 10%)
    3. MIN_BET_USDC 미만이면 스킵
    4. Drawdown 보호: 뱅크롤이 초기의 50% 아래면 봇 정지
"""


def kelly_bet_size(
    bankroll: float,
    win_prob: float,
    market_price: float,
    fee_rate: float = 0.02,
    kelly_fraction: float = 0.25,
    max_bet_fraction: float = 0.10,
    min_bet_usdc: float = 1.0,
) -> float:
    """
    분수 켈리 기준으로 최적 베팅 금액 계산.

    Args:
        bankroll: 현재 총 뱅크롤 (USDC)
        win_prob: 모델 계산 승리 확률 (0~1)
        market_price: 시장 매수 가격 (0~1)
        fee_rate: 거래 수수료율
        kelly_fraction: 켈리 비율 (0.25 = Quarter Kelly)
        max_bet_fraction: 최대 베팅 비율 (뱅크롤 대비)
        min_bet_usdc: 최소 베팅 금액

    Returns:
        베팅 금액 (USDC). 0이면 베팅하지 않음.

    수학:
        Binary option에서의 Kelly:
        - 승리 시 순 페이오프: (1 - price) × (1 - fee) / price
        - 이것이 'b' (odds)
        - f* = (b × p - q) / b
    """
    # 입력 검증
    if bankroll <= 0 or win_prob <= 0 or win_prob >= 1:
        return 0.0
    if market_price <= 0 or market_price >= 1:
        return 0.0

    # 배당률 계산 (수수료 반영)
    # 승리 시 1 shares를 받는데, 매수 가격 market_price를 지불
    # 순 이익 = (1 - market_price) × (1 - fee_rate)
    # odds = 순 이익 / 베팅금 = [(1 - market_price) × (1 - fee)] / market_price
    net_win = (1.0 - market_price) * (1.0 - fee_rate)
    b = net_win / market_price  # odds received

    if b <= 0:
        return 0.0

    p = win_prob
    q = 1.0 - p

    # Kelly fraction
    f_star = (b * p - q) / b

    # 음수 Kelly → 베팅하지 않음 (-EV)
    if f_star <= 0:
        return 0.0

    # 분수 켈리 적용
    f_actual = f_star * kelly_fraction

    # 최대 한도 적용
    f_actual = min(f_actual, max_bet_fraction)

    # 금액 계산
    bet_amount = bankroll * f_actual

    # 최소 금액 체크
    if bet_amount < min_bet_usdc:
        return 0.0

    # 뱅크롤 초과 방지 (이론적으로 불가능하지만 안전장치)
    bet_amount = min(bet_amount, bankroll * 0.95)

    return round(bet_amount, 2)


def kelly_info(
    win_prob: float,
    market_price: float,
    fee_rate: float = 0.02,
) -> dict:
    """
    Kelly 계산의 상세 정보 반환 (디버깅/로깅용).

    Returns:
        {
            'odds': 배당률,
            'full_kelly': Full Kelly 비율,
            'edge': 기대 엣지,
            'expected_growth': 기대 로그 성장률,
        }
    """
    if market_price <= 0 or market_price >= 1 or win_prob <= 0:
        return {'odds': 0, 'full_kelly': 0, 'edge': 0, 'expected_growth': 0}

    net_win = (1.0 - market_price) * (1.0 - fee_rate)
    b = net_win / market_price
    p = win_prob
    q = 1.0 - p

    if b <= 0:
        return {'odds': 0, 'full_kelly': 0, 'edge': 0, 'expected_growth': 0}

    f_star = (b * p - q) / b

    # 기대 엣지
    edge = p * net_win - q * market_price

    # 기대 로그 성장률 (Kelly를 따를 때)
    import math
    if f_star > 0:
        growth = p * math.log(1 + b * f_star) + q * math.log(1 - f_star)
    else:
        growth = 0.0

    return {
        'odds': round(b, 4),
        'full_kelly': round(f_star, 4),
        'edge': round(edge, 4),
        'expected_growth': round(growth, 6),
    }
