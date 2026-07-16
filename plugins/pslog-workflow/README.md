# pslog-workflow

pslog로 관리되는 프로젝트의 기획~구현 워크플로 플러그인. 세 스킬과 SessionStart 훅으로 구성된다.

## 설치

```
/plugin marketplace add ardenspace/pslog
/plugin install pslog-workflow@pslog
```

## 스킬 3개 — 라우팅

| 스킬 | 언제 | 하는 일 |
|---|---|---|
| `pslog-planning` | 새 feature 아이디어/기획 | 5렌즈(문제·차별/범위/설계/adversarial/테스트) Q&A → `실행계획.md` → PLAN.md task 분해 |
| `pslog-workflow` | PLAN.md task를 코드로 / 행동을 *바꾸는* 버그픽스·기능변경 | 무게 게이트(light→brief / heavy→spec→plan) → 구현(step마다 build/typecheck + 기존 회귀 스위트) → 끝검증 |
| `pslog-refactor` | 행동은 그대로, 구조만 정리 | 진단 → 범위 확정 → 동작보존 계약 → pslog-workflow로 코드화 |

공통 원칙: **각 단계 전이마다 멈추고 사람 승인을 받는다.** 자동 관통 금지.

`pslog-workflow`의 끝검증(v0.3.x): **세션 히스토리 없는 독립 리뷰어 서브에이전트**(diff 파일 + 준비 문서 + 계약/DoD 렌즈만 제공, `references/reviewer-prompt.md`) 리뷰 + 수정 → e2e(heavy) 또는 brief DoD → 통과한 DoD·테스트 케이스 중 자동화 가능한 것을 **회귀 테스트로 스위트에 커밋**. 이 회귀 스위트가 다음 task들의 구현 step 게이트가 된다.

수동 호출: `/pslog-workflow:pslog-planning` · `/pslog-workflow:pslog-workflow` · `/pslog-workflow:pslog-refactor`

## SessionStart 훅

repo 루트에 `PLAN.md`가 있으면(= pslog 관리 프로젝트) 세션 시작 시 위 라우팅 안내를 컨텍스트에 주입한다.
PLAN.md 없는 프로젝트에서는 아무것도 하지 않는다. POSIX sh만 사용 — 별도 런타임 의존 없음.
`startup|resume|clear|compact` 전부에서 발화해 컴팩션 후에도 안내가 유지된다.

## 전제

- repo 루트 `PLAN.md`의 `## 태스크` 아래 `- [ ] [task-NNN] 제목 — @username — \`영향파일\`` 포맷 task.
- 사용자별 username은 각자 `~/.claude/CLAUDE.md`에 기록 (예: "내 pslog username: arden").
- handoff는 `handoffs/{브랜치}.md` — 포맷은 `skills/pslog-workflow/references/handoff-format.md`.

## 버전

변경 이력은 repo 커밋 히스토리 참조. 스킬 텍스트/훅이 바뀌면 minor 버전을 올린다.
