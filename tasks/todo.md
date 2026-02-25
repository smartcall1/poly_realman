# Whale Copy Bot 켈리 베팅(Kelly Criterion) 적용

## 1. 개요
승률이 좋은 고래라도 이미 가격이 너무 오른 상태에서 따라사면 (EV 마이너스) 장기적으로 손실이 됩니다.
이를 방어하기 위해 수학적 켈리 공식을 통한 능동적 배팅금(Size) 조절 시스템을 적용합니다.

## 2. 작업 목표
- EV가 플러스일 때: Half Kelly (최대 15% 자본) 배팅
- EV가 마이너스일 때: Scout Bet (최소 1% 자본, 한도 $20) 배팅

## 3. 작업 목록
- [x] `whale_copy_bot.py` 에서 Kelly 비중 도출 수식 구현 (`p - (1-p)/b`)
- [x] `docs/architecture_and_projection.md` 에 기댓값(EV) 최적화로 인한 V3.0 프로젝션 갱신
- [ ] 실행 및 라이브 검증 테스트

---
## Review
(작업 완료 후 작성)
