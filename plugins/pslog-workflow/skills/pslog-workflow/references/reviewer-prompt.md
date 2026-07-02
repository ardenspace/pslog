# 독립 리뷰어 서브에이전트 — 끝검증 프롬프트 템플릿

끝검증의 코드 리뷰는 **구현한 에이전트가 직접 하지 않는다** — self-review 는 구조적으로
자기 코드에 관대하다. 세션 히스토리가 없는 신선한 서브에이전트에게 아래만 전달해 리뷰시킨다.

## 준비 (dispatch 전)

1. diff 를 **파일로** 떨군다 (프롬프트에 인라인 금지 — 리뷰어가 필요한 만큼 읽게):
   ```sh
   git diff main...HEAD --stat > /tmp/<task>-diff.txt
   git diff main...HEAD -U10 >> /tmp/<task>-diff.txt
   ```
2. 전달할 경로 목록: diff 파일 + 준비 문서(brief 또는 spec/plan) + handoff.
3. 렌즈 확정: refactor 면 **동작보존 계약**(4단계에서 승인받은 것), workflow 면
   **brief DoD / spec 의 수용 기준**.

## 프롬프트 골격

```
당신은 <repo 절대경로>의 독립 코드 리뷰어입니다. 이 변경을 작성하지 않았고
세션 히스토리도 없습니다 — 신선한 눈으로 보세요.

## 리뷰 대상
- diff 파일: /tmp/<task>-diff.txt (main...<브랜치>, stat + U10)
- 준비 문서: <brief|spec/plan 경로> / handoff: <경로>
- 필요하면 저장소의 실제 파일을 직접 읽어 diff 컨텍스트를 보충하세요.

## 렌즈
<동작보존 계약: 관찰 가능한 런타임 행동 불변 — 허용되는 유일한 차이까지 명시>
<또는 brief DoD / 수용 기준을 항목별로>

각 변경별로 적대적으로 검증하세요. 의심 지점은 사용처 grep, 시나리오 추적,
mutation test(코드를 일부러 망가뜨려 테스트가 잡는지)로 확인해도 좋습니다.
단, 검증 후 워킹트리를 반드시 원상복구하세요 (git checkout -- / stash — 커밋 금지).

## 검증 명령 (직접 실행 가능)
<brief 의 검증 명령 그대로>

## 산출물
- 판정: PASS / PASS-with-nits / FAIL
- 발견사항: 심각도(blocker/major/nit)별 — 파일:라인 + 왜 계약이 깨지는지 구체적 시나리오
- 계약/DoD 항목별 추적 결과 요약
```

## 재검증 루프

- **FAIL/major** → 수정 → **좁은 re-review**: 수정분 diff 만 새로 떨궈, 이전 지적 요약과
  함께 재dispatch (전체 재리뷰 아님). PASS 까지 반복.
- **nit** → 고칠지 사용자와 판단(범위 밖이면 handoff `### 결정` 에 기록만). 고쳤으면
  역시 좁은 re-review 로 닫는다.
- 리뷰어의 지적으로 바뀐 결정은 handoff `### 결정` 에 그때그때 기록.

## 실전 근거

Track R ⑩ 에서 리뷰어가 mutation test 로 진짜 동치 구멍(서버 값 외부 변경 시 리셋
트리거 누락)을, lint-gate 에서 stale draft 부활(A→B→A) 케이스를 잡았다 — 둘 다
구현 세션 안에서는 안 보이던 것.
