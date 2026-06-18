# pslog-refactor 스킬 설계

- 날짜: 2026-06-18
- 상태: 설계 승인됨 (구현 계획 대기)
- 관련: [[pslog-planning]], [[pslog-workflow]], `memory/pslog-workflow-self-contained.md`

## 한 줄 요약

`pslog-workflow` 플러그인에 **세 번째 스킬 `pslog-refactor`** 를 추가한다. 코드 진단에서 출발해 무게에 맞는 준비 문서를 만든 뒤, **기존 `pslog-workflow` 실행 엔진으로 핸드오프**하는 *동작 보존 리팩토링 전용* 워크플로다.

## 동기

`pslog-planning`(아이디어→PLAN.md)과 `pslog-workflow`(PLAN.md task→코드)는 둘 다 **새 행동을 추가**하는 흐름에서 출발한다. 그런데 리팩토링/구조 정리는:

1. **진입이 다르다** — "뭘 왜 고칠지"를 핸드오프받는 게 아니라 *기존 코드를 읽어서 진단*한다.
2. **동작 보존이 1순위** — 끝 검증이 feature DoD가 아니라 "행동 동치(characterization)" 증명이어야 한다.

이 둘이 기존 두 스킬과 어긋나므로 별도 진입 스킬이 필요하다. 단, 무게 게이트·구현·끝검증 엔진은 `pslog-workflow`에 이미 있으므로 **재사용하고 중복하지 않는다.**

## 핵심 결정 (확정)

| # | 결정 | 선택 | 근거 |
|---|---|---|---|
| 1 | 구조 | 얇은 진단 스킬 + workflow 핸드오프 | 게이트·구현·검증 엔진 중복 금지(DRY). 3-스킬(planning/refactor/workflow)이 모두 workflow로 수렴. |
| 2 | 범위 | **동작 보존 작업만** | 버그픽스는 *행동을 바꾸는* 게 목적 → 동치 증명 계약과 충돌 → `pslog-workflow`로. 리팩토링·데드코드 제거·의존성 업뎃처럼 행동 불변인 것만 이 스킬. |
| 3 | 핸드오프 | 무게에 따라 분기 | light는 PLAN.md 건너뛰고 바로 브랜치+handoff. heavy(계약변경·교차의존)만 PLAN.md `(deep)` task 등록 후 공유·추적. |
| 4 | 동작 보존 안전망 | 무게 연동 | light: before/after 기존 테스트 green + build/typecheck. heavy: 진단 단계에서 characterization 테스트로 현재 행동 핀고정 → 리팩토링 → 동일 테스트 green = 동치 증명. |
| 5 | 자동 발동(라우팅) | description 트리거 + 행동변경 디스앰비규에이터 | 세 스킬 트리거를 상호 배타적으로 정렬. 애매하면 되묻기. |

## 흐름

```
pslog-refactor (앞 절반 — 진단)
  1. 무게 선언      — 사용자 확인 (light/heavy)
  2. 코드 진단      — DRY·모듈화·타입안전(=CLAUDE.md 원칙)을 렌즈로
                     "어디가/왜 아픈가" + 영향 범위. 서브에이전트 리뷰 재사용.
  3. 범위 확정      — 무엇을 건드리고 무엇은 안 건드리나(경계) → 사용자 승인
  4. 동작보존 계약  — light: before 테스트 green 확인
                     heavy: characterization 테스트로 현재 행동 핀고정
        │ 핸드오프 (heavy면 PLAN.md (deep) task + handoff, light면 handoff에 진단/계약 박고 브랜치)
        ▼
pslog-workflow (기존 엔진 — 중복 없음)
  5. 무게게이트 → brief │ spec→plan
  6. 구현
  7. 끝검증         — 코드리뷰 + 「행동 동치」 레이어(같은 테스트 green)
```

## 멈춤 게이트 (강제)

| 전이 | 승인받을 것 |
|---|---|
| 시작 → 진단 | 무게(light/heavy) 선언 |
| 진단 → 범위확정 | "뭘 고치고 뭘 안 건드리나" 경계 |
| 범위 → 핸드오프 | 동작보존 계약(어떤 테스트로 동치 증명) |
| 이후 | `pslog-workflow` 게이트 그대로 |

## 라우팅 / 자동 발동

스킬은 매 메시지마다 `description` 매칭으로 발동한다 — **코드가 아니라 description 문구가 라우터.** 세 스킬 트리거를 상호 배타적으로 둔다:

| 사용자 발화 | 발동 | 판별 키 |
|---|---|---|
| "이 중복 없애자", "이 모듈 쪼개자", "이 파일 지저분한데 정리" | **refactor** | 행동 불변 |
| "이거 버그야 고쳐줘", "이렇게 동작하게 바꿔" | **workflow** | 행동 변함 |
| "이런 기능 만들자", "기획하자" | **planning** | 새 행동 추가 |
| "내 다음 task 뭐지" | **workflow** | PLAN.md 소비 |

- refactor `description` 제안: `"이거 정리하자/리팩토링/중복 제거/구조 개선/지저분한데" 류로, 행동은 그대로 두고 구조만 손볼 때. 코드 진단→무게→brief│spec/plan으로 정리 후 pslog-workflow로 코드화. (행동을 바꾸는 버그픽스/기능변경은 pslog-workflow.)`
- `pslog-workflow` description에 "버그픽스=행동변경도 여기" 한 줄 보강. planning은 그대로.
- **애매하면 되묻기**: "이 코드 좀 어떻게 해봐"처럼 행동 변경 여부가 불명확하면 관통하지 않고 *행동을 바꾸는지* 먼저 확인한다(안전장치).
- 발동 직후 흐름: 자동 발동 → "행동 보존 리팩토링 같아 보이니 pslog-refactor로, light로 보이는데 맞나요?" 제안 → 사람 승인. (멈춤 게이트에 내장.)

## 자급자족 준수

- 외부 스킬 의존 0. `plugins/pslog-workflow/skills/pslog-refactor/SKILL.md` + `references/` 추가 → `plugin.json` skills 배열 없이 자동 발견.
- **2-스킬 → 3-스킬 구성**으로 확장. planning·refactor 둘 다 workflow로 수렴.
- 메모리 `pslog-workflow-self-contained.md`의 "확정 2-스킬" 노트를 이 결정으로 갱신한다.

## references 예상 구성

- `references/diagnose.md` — 진단 렌즈(DRY/모듈화/타입안전 + 코드 스멜·결합·복잡도 핫스팟·데드코드), 서브에이전트 리뷰 재사용법
- `references/preservation-contract.md` — light/heavy별 동작 보존 안전망, characterization 테스트 작성 가이드
- (handoff·weight-gate·templates는 `pslog-workflow`의 기존 references 재사용)

## 명시적으로 범위 밖 (YAGNI)

- 버그픽스/기능변경 (→ `pslog-workflow`)
- 새 무게 게이트·구현·끝검증 엔진 (→ 기존 재사용)
- 큰 다단계 리팩토링 기획 (→ 필요하면 `pslog-planning`의 핵심 렌즈)
