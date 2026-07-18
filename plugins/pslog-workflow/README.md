# pslog-workflow

[pslog](../../README.md)로 관리되는 프로젝트의 **기획 → 구현 → 리팩토링 워크플로 플러그인**.
스킬 3개(`pslog-planning` / `pslog-workflow` / `pslog-refactor`)와 SessionStart 훅으로 구성된다.

**핵심 설계 원칙 — 에이전트는 절대 자동으로 관통하지 않는다.**
기획에서 구현까지 모든 단계 전이에 사람 승인 게이트를 둔다. 반복 실행은 에이전트가, 판단과 승인은 사람이 맡는다.

<br />
## 설치

```
/plugin marketplace add ardenspace/pslog
/plugin install pslog-workflow@pslog
```

<br />
## 전제

- repo 루트 `PLAN.md`의 `## 태스크` 아래 다음 포맷의 task가 있다 (pslog가 파싱해 대시보드 보드로 가져가는 포맷과 동일):
  ```
  - [ ] [task-001] 로그인 UI 리뉴얼 — @alice — `frontend/Login.tsx`
  ```
  heavy task는 제목 뒤에 `(deep)` 마커를 붙인다.
- 사용자별 username은 각자 `~/.claude/CLAUDE.md`에 기록한다 (예: "내 pslog username: arden") — "내 할 일 알려줘"에서 task 필터 기준이 된다.
- 작업 기록은 `handoffs/{브랜치}.md` — 포맷은 [`skills/pslog-workflow/references/handoff-format.md`](skills/pslog-workflow/references/handoff-format.md).

<br />
## 구성 — 스킬 3개 라우팅

| 스킬 | 언제 | 하는 일 |
|---|---|---|
| `pslog-planning` | 새 feature 아이디어/기획 | 5렌즈 Q&A → `실행계획.md` → PLAN.md task 분해 |
| `pslog-workflow` | PLAN.md task를 코드로 / 행동을 *바꾸는* 버그픽스·기능변경 | 무게 게이트 → 준비 문서 → 구현 → 끝검증 |
| `pslog-refactor` | 행동은 그대로, 구조만 정리 | 진단 → 범위 확정 → 동작보존 계약 → pslog-workflow로 코드화 |

수동 호출: `/pslog-workflow:pslog-planning` · `/pslog-workflow:pslog-workflow` · `/pslog-workflow:pslog-refactor`

<br />
### SessionStart 훅

repo 루트에 `PLAN.md`가 있으면(= pslog 관리 프로젝트) 세션 시작 시 위 라우팅 안내를 컨텍스트에 주입한다 — 팀원이 CLAUDE.md에 연동 규칙을 복붙할 필요가 없다.
PLAN.md 없는 프로젝트에서는 아무것도 하지 않는다(조용히 exit 0). POSIX sh만 사용 — 별도 런타임 의존 없음.
`startup|resume|clear|compact` 전부에서 발화해 컴팩션 후에도 안내가 유지된다.

<br />
## pslog-planning — 아이디어 → 실행계획 → PLAN.md

feature 아이디어를 5개의 렌즈에 순서대로 통과시켜 기술적으로 탄탄한 기획으로 만든다.
각 렌즈에서 에이전트가 1차 초안 + 질문 세트를 내고, 사람이 답하면 lock-in 결정으로 박은 뒤 다음 렌즈로 넘어간다.

1. **문제·차별** — 왜 필요한가, 누구를 위한가
2. **범위** — V1에 뭘 넣고 뭘 뺄 것인가
3. **설계** — 어떻게 만들 것인가
4. **adversarial** — 어떻게 깨뜨릴 수 있는가
5. **테스트** — 무엇이 "됐다"의 기준인가

흐름: 무게 선언(큰 feature = 렌즈 1~5 / 작은 개선 = 핵심 렌즈 1·2·3) → 렌즈 Q&A → `docs/<feature>-실행계획.md` 합성 → ★ **실행계획 승인** → PLAN.md 골디락스 task 분해 → ★ **분해 승인** (task 쪼갬·lane·assignee).
이후 각 task는 `pslog-workflow`로 코드화한다.

상세: [`lenses.md`](skills/pslog-planning/references/lenses.md) · [`execution-plan-template.md`](skills/pslog-planning/references/execution-plan-template.md) · [`decompose.md`](skills/pslog-planning/references/decompose.md)

<br />
## pslog-workflow — task → 코드

할당받은 task 하나를 무게에 맞는 준비 문서로 정리한 뒤 코드로 옮긴다.

**흐름**: task 선택(PLAN.md에서 `@username` + 미완료 필터, 1~2개 제안) → 브랜치 `feat/task-NNN-<슬러그>` + handoff 생성 → 무게 판정 → 준비 문서 → 구현 → 끝검증 → 마무리(PLAN.md 체크 + DECISIONS.md 승격).

**무게 게이트** — 3-트리거로 light/heavy를 판정한다:

1. **계약 변경** — DB 스키마/migration, API shape, 공유 타입 등 다른 사람이 의존하는 것을 건드리는가
2. **설계 분기** — 실질적인 구현 대안이 2개 이상 있어 "어느 쪽?"을 결정해야 하는가
3. **교차 의존** — 다른 lane/역할이 이 task의 결과물을 기다리는가

트리거 0개 = **light** → `docs/tasks/task-NNN/brief.md` 한 장(DoD 확인 후 코드).
트리거 1+개 = **heavy** → `spec.md` 승인 → `plan.md` 승인 → 코드. PLAN.md task 줄에 `(deep)` 마커.

**구현 중 게이트** — plan의 Step마다 build/typecheck + **기존 회귀 스위트**(이전 task들이 남긴 테스트)를 돌려 과거 동작을 깨지 않았는지만 확인한다. 이번 task의 새 테스트·리뷰는 step 단위로 하지 않고 끝 검증으로 이연한다 — 응집·일관성은 퍼즐이 다 맞춰진 뒤에만 보인다.

**끝 검증** — 구현 에이전트의 self-review가 아니라, **세션 히스토리 없는 독립 리뷰어 서브에이전트**에게 브랜치 diff 전체 + 준비 문서 + 계약/DoD 렌즈만 주고 리뷰시킨다([`reviewer-prompt.md`](skills/pslog-workflow/references/reviewer-prompt.md)). 지적은 수정 → 좁은 re-review로 닫는다. 이후 e2e(heavy) 또는 brief DoD 검증(light) 통과 → **DoD·테스트 케이스 중 자동화 가능한 것을 회귀 테스트로 스위트에 커밋**한다. 이 스위트가 다음 task들의 구현 step 게이트가 되므로, task가 진행될수록 검증망이 촘촘해진다. 자동화 가능한 검증이 없으면 그 이유를 handoff에 1줄 기록해야 게이트를 지난다.

**마무리** — push 직전 handoff commit, PLAN.md `[task-NNN]` 체크. PR land 시점에 spec·handoff에서 굳은 결정을 `DECISIONS.md`로 승격한다 (pslog 드리프트 감지의 decision-truth-loop와 물리는 지점 — 멱등, 이미 올라간 결정은 중복하지 않는다).

상세: [`weight-gate.md`](skills/pslog-workflow/references/weight-gate.md) · [`templates.md`](skills/pslog-workflow/references/templates.md) · [`handoff-format.md`](skills/pslog-workflow/references/handoff-format.md)

<br />
## pslog-refactor — 동작 보존 리팩토링

"행동은 그대로, 구조만 바꾼다"를 테스트로 증명하는 흐름. 중복 제거·모듈 분리·네이밍 정리·데드코드 제거가 대상이고, 행동을 *바꾸는* 버그픽스/기능변경은 `pslog-workflow`, 새 기능 기획은 `pslog-planning`으로 라우팅한다 (애매하면 관통하지 않고 먼저 묻는다).

흐름: 무게 선언 → 코드 진단(DRY·모듈화·타입안전 + 코드 스멜·결합·복잡도 핫스팟) → **범위 확정** — 무엇을 건드리고 무엇은 안 건드리는지 경계 승인(리팩토링 최대 리스크는 범위가 새는 것) → **동작보존 계약** 승인 → `pslog-workflow` 엔진으로 코드화.

<br />
**동치 증명 기준**:

- **light** — 리팩토링 전 기존 관련 테스트가 green임을 확인해 두고, 후에 같은 테스트가 그대로 green이면 "구조만 바꿨다"가 증명된다.
- **heavy** — 기존 테스트가 없거나 부족하면 리팩토링 *전에* 현재 동작을 가치 판단 없이 그대로 캡처하는 **characterization 테스트**를 먼저 작성하고, 후에 동일하게 green이면 증명된다.

상세: [`diagnose.md`](skills/pslog-refactor/references/diagnose.md) · [`preservation-contract.md`](skills/pslog-refactor/references/preservation-contract.md)

<br />
## 승인 게이트 전체 맵

| 단계 전이 | 승인받을 것 |
|---|---|
| 시작 → 렌즈 (planning) | 무게 선언 (풀 기획 / 핵심 렌즈) |
| 렌즈 전부 → 실행계획 | 실행계획.md 전체 승인 |
| 실행계획 → PLAN 분해 | task 쪼갬·lane·assignee 승인 |
| task 선택 | 어떤 task 잡을지 (1~2개 제안 후 확인) |
| task → 무게 판정 | light/heavy + `(deep)` 마커 |
| brief → 코드 (light) | DoD 확인 |
| spec → plan (heavy) | spec 초안 승인 |
| plan → 코드 | plan 승인 |
| 구현 → 마무리 | 끝 검증 통과 (독립 리뷰 + e2e/DoD + 회귀 테스트 남김) |
| 리팩토링: 진단 → 범위 | "뭘 고치고 뭘 안 건드리나" 경계 승인 |
| 리팩토링: 범위 → 핸드오프 | 동작보존 계약 승인 |

승인 없이 다음 단계 진입 금지 — 이 멈춤이 이 방법론의 핵심 안전장치다.

<br />
## 파일 구조

```
pslog-workflow/
├── .claude-plugin/plugin.json
├── bin/inject-pslog-context.sh   # SessionStart 훅 스크립트 (POSIX sh)
├── hooks/hooks.json
└── skills/
    ├── pslog-planning/           # SKILL.md + references/ (lenses, 실행계획 템플릿, 분해 규칙)
    ├── pslog-workflow/           # SKILL.md + references/ (무게 게이트, 템플릿, handoff 포맷, 리뷰어 프롬프트)
    └── pslog-refactor/           # SKILL.md + references/ (진단 렌즈, 동작보존 계약)
```

<br />
## 버전

현재 v0.3.1. 변경 이력은 repo 커밋 히스토리 참조. 스킬 텍스트/훅이 바뀌면 minor 버전을 올린다.
