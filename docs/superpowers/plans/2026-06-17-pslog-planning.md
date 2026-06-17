# pslog-planning 스킬 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pslog-workflow 플러그인에 `pslog-planning` 스킬을 번들로 추가해, feature 아이디어 → 5렌즈 기획 → `실행계획.md` → `PLAN.md` task 분해까지를 단일·자급자족 의식으로 수행한다.

**Architecture:** 산출물은 코드가 아니라 **스킬 마크다운**(`SKILL.md` + `references/` 3종) + 등록 메타(plugin.json·hook). `pslog-workflow` 스킬과 같은 플러그인 안에서 `skills/*/SKILL.md` 자동 발견으로 등록된다. 외부 스킬(gstack/superpowers) 의존 0.

**Tech Stack:** Claude Code 플러그인 스킬(마크다운 + frontmatter), node hook 스크립트.

**설계서:** `docs/superpowers/specs/2026-06-17-pslog-planning-design.md` (이 계획의 진실의 원천).

## Global Constraints

이 계획의 모든 task 에 암묵 적용. 설계서 §9 미결은 아래 **v1 기본값**으로 잠금(설계서 권장값):

- **자급자족** — 외부 스킬(gstack/superpowers) 참조·호출·의존 금지. 영감만. (메모리 `pslog-workflow-self-contained`)
- **검증 방식** — 산출물이 마크다운이라 자동 테스트 없음. 각 task 의 검증 = **구조 점검(필수 섹션 존재) + 설계서 대조**. 최종 task 에서 실제 스킬 dogfood.
- **C1 무게 티어 판정** = **사람 선언**(가벼운 쪽). 큰 feature 면 사용자가 "풀 기획" 선언, 아니면 핵심 렌즈(1·2·3)만.
- **C2 실행계획 위치** = `docs/` 직하, 파일명은 사람이 정함(feature 식별). 작성 후 `PLAN.md` 최상단에 `상세 실행계획: [링크]` 한 줄.
- **C3 렌즈 가이드** = `references/lenses.md` 로 분리(SKILL 본문은 얇게, context 절약).
- **C4 PLAN 기록** = **제안 후 확인**(자동 수정 금지). 기존 `weight-gate.md` 규약과 동일.
- **C5 lock-in 키** = 실행계획 §5 결정에 `E1, E2…` id 부여 → PLAN task 본문에 `(E#)` 백링크 → 구현 시 handoff `### 결정`/DECISIONS.md 가 같은 `E#` 로 참조.
- 파일 경로는 항상 `plugins/pslog-workflow/...` (프로젝트 루트 기준).
- 한국어 본문, 기존 `pslog-workflow/SKILL.md`·`references/*` 의 간결·표 위주 톤 유지.

---

### Task 1: pslog-planning SKILL.md (스킬 척추)

**Files:**
- Create: `plugins/pslog-workflow/skills/pslog-planning/SKILL.md`

**Interfaces:**
- Produces: 스킬 본문. `references/lenses.md`, `references/execution-plan-template.md`, `references/decompose.md` 를 가리킴(Task 2~4 가 채움).

- [ ] **Step 1: SKILL.md 작성**

frontmatter + 본문을 아래 구조로 작성. 내용은 설계서 §4(흐름)·§4.2(메커니즘)·§4.3(게이트) 그대로:

```markdown
---
name: pslog-planning
description: pslog로 관리되는 프로젝트에서 새 feature 아이디어를 기술적으로 탄탄한 기획으로 만드는 워크플로. 사용자가 "새 기능/이거 만들자/기획하자" 류로 들어올 때 사용. 5렌즈(문제·차별/범위/설계/adversarial/테스트)로 실행계획.md를 쓰고 PLAN.md task로 분해한다. task 하나를 코드로 옮기는 건 pslog-workflow 스킬.
---

# pslog-planning

feature 아이디어를 **기술적으로 탄탄한 실행계획 + PLAN.md task 들**로 만드는 흐름.
**핵심 원칙: 각 렌즈에서 사람과 Q&A 하고, 큰 멈춤 2개(실행계획 승인 / 분해 승인)에서 멈춰 승인받는다.**
(이건 pslog-workflow 의 *위쪽* 절반 — PLAN.md 를 *만든다*. task 하나를 코드로 옮기는 아래쪽은 `pslog-workflow` 스킬.)

## 흐름

1. **무게 선언** — 큰 feature 면 풀 기획(렌즈 1~5), 작은 개선이면 핵심 렌즈(1·2·3)만. **사용자에게 확인.**
2. **렌즈 1~5** — 각 렌즈마다: 에이전트가 1차 초안 + 질문 세트 → **사용자가 답** → 답을 lock-in 결정으로 박음 → 다음 렌즈.
   렌즈별 질문 가이드 + 실패 클래스 체크리스트는 `references/lenses.md`.
3. **실행계획 합성** — 렌즈 결과를 `docs/<feature>-실행계획.md` 로 (`references/execution-plan-template.md` 템플릿).
   → ★ **사용자 승인** (실행계획 전체).
4. **분해** — 실행계획 → `PLAN.md` 골디락스 task (`references/decompose.md` 규칙).
   → ★ **사용자 승인** (task 쪼갬·lane·assignee). PLAN.md 수정은 **제안 후 확인**.
5. **핸드오프** — 이제 각 task 는 `pslog-workflow` 스킬로 코드화.

## 멈춤 규칙 (강제)

| 전이 | 승인받을 것 |
|---|---|
| 시작 → 렌즈 | 무게(풀/핵심) 선언 |
| 렌즈 안 | (연속 Q&A — 사용자가 답) |
| 렌즈 전부 → 실행계획 | 실행계획.md 전체 승인 |
| 실행계획 → PLAN 분해 | PLAN task 분해 승인 (제안 후 확인) |

승인 없이 다음 단계 진입 금지.

## 메커니즘 — 하이브리드

에이전트가 렌즈별 1차 심문으로 인지부하를 줄이되, **판단·반박은 사용자가**. 질문이 많을수록 기획이 촘촘해지고 후속 pslog-workflow 가 쉬워진다. 모순 탐지는 조용한 배경(중심은 "기술적으로 탄탄").

자세한 렌즈·템플릿·분해 규칙은 `references/` 참고(필요할 때만 읽는다).
```

- [ ] **Step 2: 구조 검증**

확인: (a) frontmatter `name: pslog-planning` + `description` 존재, (b) "흐름" 5단계, (c) "멈춤 규칙" 표에 큰 게이트 2개 포함, (d) `references/` 3파일 모두 언급, (e) pslog-workflow 와의 경계("위쪽/아래쪽") 명시. 설계서 §4 와 대조해 누락 없는지.

- [ ] **Step 3: Commit**

```bash
git add plugins/pslog-workflow/skills/pslog-planning/SKILL.md
git commit -m "feat(skill): pslog-planning 스킬 척추 (흐름+게이트+메커니즘)"
```

---

### Task 2: references/lenses.md (5렌즈 + 실패 클래스 5버킷)

**Files:**
- Create: `plugins/pslog-workflow/skills/pslog-planning/references/lenses.md`

**Interfaces:**
- Consumes: SKILL.md 흐름 2단계가 이 파일을 가리킴.
- Produces: 렌즈별 질문 가이드 + 기술 깊이 체크리스트.

- [ ] **Step 1: lenses.md 작성**

설계서 §4.1(5렌즈) + §5(실패 클래스) 를 옮긴다. 구조:

```markdown
# 렌즈 + 실패 클래스 체크리스트

## 5렌즈 (순서대로, 각 렌즈 = 질문 → 사용자 답 → lock-in)

### 렌즈 1 — 문제·차별 (CEO/Product)
질문: 왜 필요? 누구? 지금 뭐가 불편? 무엇이 다른가? 충분히 큰 문제인가?

### 렌즈 2 — 범위 (YAGNI)
질문: V1에 **만든다** / **안 만든다**? 이연(V1.5/V2)? 한 줄로 자를 수 있나?

### 렌즈 3 — 설계·결정 (Eng)
질문: 핵심 아키텍처? 기존 인프라 재사용? 아래 **실패 클래스 5버킷**을 반드시 심문해 lock-in 결정으로.

### 렌즈 4 — adversarial
질문: 이걸 어떻게 깨뜨리지? 각 lock-in 결정이 틀렸다면? 빠진 엣지/경합?

### 렌즈 5 — 테스트
질문: 각 핵심 동작을 어떻게 검증? T1~Tn 으로. 무엇이 "됐다"의 기준?

## 실패 클래스 5버킷 (렌즈 3·4 의 바닥 — 최소 이건 다 심문)

| 버킷 | 심문 |
|---|---|
| 동시성/async | race, task GC, cancel cleanup, 세마포 |
| 데이터 | N+1, 인덱스, migration, dedup/reconcile, 캐시 일관성 |
| 계약/경계 | API shape, 권한 누설, 멱등, 에러 표면, 프로토콜 완결 |
| 리소스 | rate limit/budget 격리, backpressure, 캐시 |
| 실패 모드 | timeout, circuit, graceful shutdown, fail-fast, disconnect |

**바닥 + 열린 심문**: 5버킷은 반드시 + "이 설계만의 특수 리스크는?"(도메인 특수)도 묻는다.

## 무게에 따라
- 풀 기획: 렌즈 1~5 전부.
- 핵심만: 렌즈 1·2·3 (adversarial/테스트 생략 가능).
```

- [ ] **Step 2: 구조 검증**

확인: 5렌즈 모두 + 5버킷 표 + "바닥+열린 심문" 문구 + 무게 분기. 설계서 §4.1/§5 와 버킷·렌즈 누락 없는지 대조.

- [ ] **Step 3: Commit**

```bash
git add plugins/pslog-workflow/skills/pslog-planning/references/lenses.md
git commit -m "feat(skill): pslog-planning lenses.md (5렌즈 + 실패 클래스 5버킷)"
```

---

### Task 3: references/execution-plan-template.md (실행계획 필수 섹션)

**Files:**
- Create: `plugins/pslog-workflow/skills/pslog-planning/references/execution-plan-template.md`

**Interfaces:**
- Consumes: SKILL.md 흐름 3단계가 이 템플릿으로 실행계획 합성.
- Produces: `실행계획.md` 8 필수 섹션 + lock-in id 규약.

- [ ] **Step 1: 템플릿 작성**

설계서 §6.1 을 옮긴다:

```markdown
# 실행계획.md 템플릿

위치: `docs/<feature>-실행계획.md` (파일명은 사람이 정함). 작성 후 PLAN.md 최상단에 `상세 실행계획: [링크]`.

## 필수 섹션
1. 한 줄 요약
2. 풀려는 문제        — 누구 / 왜
3. 무엇이 다른가      — 차별점 (렌즈1)
4. 범위 (만든다/안 만든다)  — YAGNI (렌즈2)
5. 핵심 설계 결정 ★   — 각 결정에 `E1, E2…` id + 이유 (렌즈3·4). 이게 상류 lock-in.
6. 리스크/영향        — 실패 클래스 5버킷 심문 결과 + migration/롤백
7. 테스트 케이스       — T1~Tn (렌즈5)
8. 구현 순서 / 병렬 lane

## lock-in id 규약 (C5)
- §5 결정마다 `E#` 부여. PLAN task 가 `(E#)` 로 백링크.
- 구현 시 handoff `### 결정`/DECISIONS.md 가 같은 `E#` 참조 → 기획~코드 결정 사슬.

## 무게에 따라
- 핵심만: 1~5 + 8 (6 리스크·7 테스트는 얇게/생략 가능).
```

- [ ] **Step 2: 구조 검증**

확인: 8 섹션 전부 + `E#` id 규약 + PLAN 링크 규약(C2) + lock-in↔DECISIONS(C5). 설계서 §6.1 과 대조.

- [ ] **Step 3: Commit**

```bash
git add plugins/pslog-workflow/skills/pslog-planning/references/execution-plan-template.md
git commit -m "feat(skill): pslog-planning execution-plan-template.md (8 섹션 + E# 규약)"
```

---

### Task 4: references/decompose.md (실행계획 → PLAN 분해 규칙)

**Files:**
- Create: `plugins/pslog-workflow/skills/pslog-planning/references/decompose.md`

**Interfaces:**
- Consumes: SKILL.md 흐름 4단계가 이 규칙으로 분해.
- Produces: PLAN.md 골디락스 task 분해 계약. 부모 `pslog-workflow` 가 이 PLAN.md 를 소비.

- [ ] **Step 1: decompose.md 작성**

설계서 §6.2 + 부모 `ai-task-automation` §6.1 PLAN 포맷을 옮긴다:

```markdown
# 분해 규칙 — 실행계획 → PLAN.md

## 골디락스
- 1 task = **0.5~3일** 분량. 너무 작으면(시간 단위) 합치고, 크면(주 단위) 쪼갠다.

## task 줄 포맷 (PLAN.md `## 태스크` 아래)
`- [ ] [task-NNN] <제목> (E#) — @assignee — \`영향파일\``
- `[task-NNN]` 프로젝트 내 unique.
- `(E#)` — 실행계획 §5 lock-in 백링크 (해당 task 가 그 결정 구현 시).
- `@assignee` — 역할 담당.
- backtick 영향파일.
- heavy task 면 `(deep)` 마커도 (부모 무게 게이트 — `weight-gate.md`).

## lane
- 병렬 가능 그룹을 PLAN 노트에 "병렬 가능: lane A (task-001/002) …" 로. 실행계획 §8 의존성에서 도출.

## 기록 (C4)
- PLAN.md 수정은 **제안 후 사용자 확인** (자동 수정 금지). 기존 `weight-gate.md` 규약과 동일.
- 작성 후 PLAN.md 최상단에 실행계획 링크 + 역할 줄.
```

- [ ] **Step 2: 구조 검증**

확인: 골디락스 0.5~3일 + task 포맷(`(E#)` 백링크 포함) + lane + "제안 후 확인"(C4) + `(deep)` 연결. 설계서 §6.2 및 부모 PLAN 포맷과 대조.

- [ ] **Step 3: Commit**

```bash
git add plugins/pslog-workflow/skills/pslog-planning/references/decompose.md
git commit -m "feat(skill): pslog-planning decompose.md (골디락스 분해 + E# 백링크)"
```

---

### Task 5: 등록 — plugin.json description + hook 트리거 갱신

**Files:**
- Modify: `plugins/pslog-workflow/.claude-plugin/plugin.json` (description)
- Modify: `plugins/pslog-workflow/bin/inject-pslog-context.js` (additionalContext)

**Interfaces:**
- Consumes: Task 1 의 `pslog-planning` 스킬 존재.
- Produces: 두 스킬을 구분 안내하는 트리거.

- [ ] **Step 1: plugin.json description 갱신**

`description` 필드를 2-스킬 반영으로 교체:

```json
"description": "pslog로 관리되는 프로젝트의 기획~구현 워크플로. pslog-planning(feature 아이디어 → 실행계획 → PLAN.md 분해) + pslog-workflow(PLAN task → 무게 게이트 → 코드), 각 단계 사람 승인.",
```

- [ ] **Step 2: hook additionalContext 갱신**

`bin/inject-pslog-context.js` 의 `additionalContext` 문자열을 두 스킬 구분 안내로 교체(나머지 로직·구조 불변):

```javascript
      additionalContext:
        "이 프로젝트는 pslog-workflow 플러그인으로 관리된다. 두 스킬을 쓴다: " +
        "(1) 새 feature 아이디어/기획에 들어가면 → pslog-planning 스킬(/pslog-workflow:pslog-planning) — " +
        "5렌즈 기획 → 실행계획.md → PLAN.md 분해. " +
        "(2) PLAN.md의 task 하나를 잡아 코드로 옮기면 → pslog-workflow 스킬(/pslog-workflow:pslog-workflow) — " +
        "무게 게이트(brief vs spec→plan) → 코드 → 검증. 각 단계 사람 승인 흐름을 따른다.",
```

- [ ] **Step 3: 검증**

확인: (a) `node plugins/pslog-workflow/bin/inject-pslog-context.js` 가 PLAN.md 있는 디렉토리에서 두 스킬 모두 언급한 JSON 을 출력(없는 데선 무출력). (b) plugin.json 이 유효 JSON(`node -e "JSON.parse(require('fs').readFileSync('plugins/pslog-workflow/.claude-plugin/plugin.json'))"`).

Run: `cd plugins/pslog-workflow && node -e "process.env.CLAUDE_PROJECT_DIR=process.cwd()" ; node bin/inject-pslog-context.js`
Expected: PLAN.md 없으면 무출력. (실제 출력 테스트는 PLAN.md 있는 repo 에서.)

- [ ] **Step 4: Commit**

```bash
git add plugins/pslog-workflow/.claude-plugin/plugin.json plugins/pslog-workflow/bin/inject-pslog-context.js
git commit -m "feat(plugin): 2-스킬 등록 (planning/workflow 트리거 구분)"
```

---

### Task 6: 끝 검증 — dogfood (실제 스킬 동작)

**Files:** (없음 — 동작 검증)

- [ ] **Step 1: 토이 feature 로 dogfood**

작은 가상 feature(예: "프로필에 다크모드 토글 추가")로 `pslog-planning` 스킬을 호출해 흐름을 태운다. 확인:
- 무게 선언 게이트에서 멈추는가.
- 렌즈마다 질문 → 답 → lock-in 이 도는가 (핵심 렌즈만이라도).
- `실행계획.md` 가 8 섹션 + `E#` 로 합성되고 **승인 게이트**에서 멈추는가.
- 분해가 골디락스 task + `(E#)` 백링크로 나오고 **제안 후 확인**하는가.

- [ ] **Step 2: 전체 일관성 점검**

`SKILL.md` ↔ `references/*` ↔ 설계서 간 용어·게이트·렌즈 수 일치 재확인. 어긋남 있으면 해당 파일 수정 후 재커밋.

- [ ] **Step 3: 마무리 커밋(있으면)**

```bash
git add -A && git commit -m "fix(skill): pslog-planning dogfood 후 일관성 보정"
```

(보정 없으면 생략.)

---

## Self-Review

**Spec coverage (설계서 → task):**
- §3 2-스킬/자급자족 → Task 1(스킬 생성) + Task 5(등록) + Global Constraints.
- §4 흐름·메커니즘·게이트 → Task 1(SKILL.md).
- §4.1 5렌즈 / §5 실패 클래스 → Task 2(lenses.md).
- §6.1 실행계획 템플릿 → Task 3.
- §6.2 분해 규칙 → Task 4.
- §7 무게 티어 → Task 1·2·3 의 "무게에 따라" + C1.
- §8 구현 순서(SKILL+references / 등록) → Task 1~5.
- §9 미결 → Global Constraints C1~C5 로 잠금.
- 끝 검증 → Task 6 dogfood.
- 누락 없음.

**Placeholder scan:** "TBD/TODO/적절히" 없음. 각 파일 내용은 섹션·문구까지 구체화. ✅

**Type consistency:** 파일 경로·스킬 이름(`pslog-planning`)·렌즈 수(5)·버킷 수(5)·게이트 수(2)·`E#` 규약이 task 간 일관. ✅
