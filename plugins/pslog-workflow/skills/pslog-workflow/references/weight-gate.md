# 무게 게이트

task마다 무게를 보고 준비 강도를 정한다. **기본값은 light.**

## 3-트리거 룰 (셋 중 하나라도 yes → heavy)

| 트리거 | 뜻 | PLAN.md에서 읽히는 신호 |
|---|---|---|
| ① 계약 변경 | 남이 의존하는 걸 건드림 | DB 스키마/migration, API 요청·응답 shape, auth, 공유 타입/모듈 (영향파일 backtick) |
| ② 설계 분기 | 구현 방법에 진짜 대안 2개+ → "어느 쪽?" 결정 필요 | 사람 판단 — 어차피 DECISIONS로 남길 결정 |
| ③ 교차 의존 | 다른 lane/역할이 이 결과물을 기다림 | "병렬 가능" lane, handoff→designer, frontend가 이 API 대기 |

- light(0개) → brief 한 장. heavy(1+개) → spec → plan.

## (deep) 마커 — PLAN.md 선언

heavy task는 PLAN.md task 줄에 `(deep)` 마커. 마커 없음 = light.

```
- [ ] [task-007] (deep) 결제 웹훅 재시도 — @sejong — `services/webhook.py`, `alembic/...`
- [ ] [task-012] 버튼 색 토큰 교체 — @jessica — `theme.ts`
```

- 진실의 원천 = PLAN.md. 마커 1개라 "PLAN 얇게" 안 깸.
- 원칙: 스프린트 PLAN 작성 시 미리 표시.
- 백필: 안 달려 있으면 task 잡을 때 게이트가 평가 → heavy면 마커를 PLAN.md에 다는 것을
  **사용자에게 제안 후 확인**받아 추가(자동 수정 금지).
