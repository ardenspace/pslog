# pslog-refactor 스킬 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `pslog-workflow` 플러그인에 동작 보존 리팩토링 전용 스킬 `pslog-refactor`를 추가하고, 세 스킬의 라우팅을 상호 배타적으로 정렬한다.

**Architecture:** 신규 스킬은 "코드 진단 → 무게 선언 → 범위 확정 → 동작보존 계약"의 앞 절반만 담당하는 얇은 SKILL.md + references 2개. 무게게이트·구현·끝검증은 기존 `pslog-workflow`로 핸드오프해 재사용한다. 산출물은 전부 마크다운(코드 아님)이라 각 task의 "검증"은 frontmatter 유효성·링크 해소·라우팅 상호배타 검토다.

**Tech Stack:** Claude Code 플러그인 스킬 (마크다운 SKILL.md + YAML frontmatter, `references/*.md`). `plugin.json`에 skills 배열 없음 → `skills/*/SKILL.md` 자동 발견.

## Global Constraints

- 외부 스킬 의존 0 — gstack·superpowers에 의존 금지 (자급자족 원칙, `memory/pslog-workflow-self-contained.md`).
- 신규 능력은 플러그인 안에 `skills/pslog-refactor/SKILL.md` 추가로 넣는다 (plugin.json 수정으로 skills 등록하지 않음 — 자동 발견).
- 스킬 문체: 기존 `pslog-workflow`/`pslog-planning` SKILL.md와 동일 — 한국어, 간결, 흐름 번호목록 + 「멈춤 규칙」 표 + references 포인터. 모든 멈춤 게이트는 "승인 없이 다음 단계 진입 금지".
- 범위: **동작 보존 작업만**. 행동을 바꾸는 버그픽스/기능변경은 `pslog-workflow`로 라우팅.
- frontmatter는 `name`, `description` 두 필드. `name`은 디렉터리명과 동일(`pslog-refactor`).

---

### Task 1: pslog-refactor SKILL.md (진단 앞 절반 + 핸드오프)

**Files:**
- Create: `plugins/pslog-workflow/skills/pslog-refactor/SKILL.md`

**Interfaces:**
- Consumes: 없음 (진입 스킬).
- Produces: `references/diagnose.md`, `references/preservation-contract.md`로의 포인터 (Task 2·3에서 생성). `pslog-workflow`로의 핸드오프(heavy면 PLAN.md `(deep)` task, light면 handoff 문서).

- [ ] **Step 1: SKILL.md 작성**

아래 내용 그대로 작성:

```markdown
---
name: pslog-refactor
description: pslog로 관리되는 프로젝트에서 행동은 그대로 두고 구조만 손보는 동작 보존 리팩토링 워크플로. "이거 정리하자/리팩토링/중복 제거/구조 개선/이 파일 지저분한데" 류로 들어올 때 사용. 코드 진단→무게(light/heavy)→범위 확정→동작보존 계약까지 정리한 뒤 pslog-workflow 로 코드화한다. (행동을 *바꾸는* 버그픽스/기능변경은 pslog-workflow, 새 기능 기획은 pslog-planning.)
---

# pslog-refactor

기존 코드를 **행동은 그대로, 구조만** 바꾸는 흐름. 코드 진단에서 출발해 무게에 맞는 준비를 한 뒤 `pslog-workflow` 엔진으로 코드화한다.
**핵심 원칙: ① 동작 보존이 1순위 — 끝에서 "행동 동치"를 증명한다. ② 각 단계 전이마다 멈추고 사람 승인을 받는다.**
(이건 진입 스킬의 *앞 절반* — 진단·계약. 무게게이트→brief│spec/plan→구현→끝검증은 `pslog-workflow` 스킬이 그대로 받는다. 중복 안 만든다.)

## 범위 (먼저 판별)

- **여기**: 중복 제거, 모듈 분리, 네이밍/구조 정리, 데드코드 제거, 의존성 업뎃 — **행동 불변**.
- **여기 아님**: 버그픽스·기능변경(행동을 *바꾸는* 게 목적) → `pslog-workflow`. 새 기능 기획 → `pslog-planning`.
- 사용자 발화가 행동 변경인지 애매하면(예: "이 코드 좀 어떻게 해봐") **관통하지 말고 행동을 바꾸는지 먼저 물어** 라우팅을 가른다.

## 흐름 (각 → 에서 멈춰 승인)

1. **무게 선언** — `pslog-workflow` 의 `references/weight-gate.md` 3-트리거로 light/heavy 판정 → **사용자 확인.**
2. **코드 진단** — `references/diagnose.md` 의 렌즈(DRY·모듈화·타입안전 = CLAUDE.md 원칙 + 코드 스멜·결합·복잡도 핫스팟·데드코드)로 "어디가/왜 아픈가 + 영향 범위"를 뽑는다. 넓은 진단은 서브에이전트 코드리뷰 재사용.
3. **범위 확정** — 무엇을 건드리고 **무엇은 안 건드리나(경계)** 를 명시 → **사용자 승인.** (리팩토링은 범위가 새는 게 최대 리스크.)
4. **동작보존 계약** — `references/preservation-contract.md`:
   - light → before 기존 테스트 green 확인(어떤 테스트가 이 행동을 덮나).
   - heavy → 현재 행동을 핀고정하는 characterization 테스트를 **먼저** 작성.
   → **계약(무엇으로 동치 증명할지) 사용자 승인.**
5. **핸드오프** — `pslog-workflow` 로 코드화:
   - heavy → PLAN.md `## 태스크` 에 `(deep)` task 한 줄 등록(제안 후 확인) + handoff 문서에 진단/계약 박음.
   - light → PLAN.md 건너뛰고 handoff 문서에 진단/계약 박고 바로 브랜치.
   이후 무게게이트→brief│spec/plan→구현→**끝검증(코드리뷰 + 「행동 동치」: 4번 테스트 그대로 green)** 은 `pslog-workflow` 가 수행.

## 멈춤 규칙 (강제)

| 전이 | 승인받을 것 |
|---|---|
| 시작 → 진단 | 무게(light/heavy) 선언 |
| 진단 → 범위확정 | "뭘 고치고 뭘 안 건드리나" 경계 |
| 범위 → 핸드오프 | 동작보존 계약(어떤 테스트로 동치 증명) |
| 이후 | `pslog-workflow` 멈춤 규칙 그대로 |

승인 없이 다음 단계로 진입 금지.

## 메커니즘

진단은 에이전트가 1차로 "아픈 곳 + 영향 범위" 초안을 내고, **고칠 범위·경계 판단은 사용자가**. 동작 보존은 *테스트가 곧 계약* — 리팩토링 전후로 같은 테스트가 green이어야 "구조만 바꿨다"가 증명된다. 무게게이트·구현·끝검증은 `pslog-workflow` 와 한 몸.

자세한 진단 렌즈·동작보존 계약은 `references/` 참고(필요할 때만 읽는다).
```

- [ ] **Step 2: frontmatter·링크 검증**

확인:
- frontmatter에 `name: pslog-refactor`, `description:` 존재. `name`이 디렉터리명과 일치.
- 본문이 가리키는 `references/diagnose.md`, `references/preservation-contract.md` 는 Task 2·3에서 생성 예정(현재 미존재 정상).
- `pslog-workflow` 의 `references/weight-gate.md` 경로가 실재하는지: `plugins/pslog-workflow/skills/pslog-workflow/references/weight-gate.md` 존재 확인.

- [ ] **Step 3: 커밋**

```bash
git add plugins/pslog-workflow/skills/pslog-refactor/SKILL.md
git commit -m "feat(refactor): pslog-refactor SKILL.md — 동작 보존 리팩토링 진입 스킬"
```

---

### Task 2: references/diagnose.md (진단 렌즈)

**Files:**
- Create: `plugins/pslog-workflow/skills/pslog-refactor/references/diagnose.md`

**Interfaces:**
- Consumes: SKILL.md 2단계가 이 파일을 가리킴.
- Produces: 진단 결과 형식(아픈 곳 + 영향 범위 + 무게 신호) — 3단계 범위 확정과 5단계 핸드오프가 소비.

- [ ] **Step 1: diagnose.md 작성**

아래 내용 그대로 작성:

```markdown
# 진단 렌즈

리팩토링 대상 코드를 읽어 "어디가/왜 아픈가 + 어디까지 번지나"를 뽑는다. **고칠지/범위는 사용자가 결정** — 진단은 후보와 근거만 낸다.

## 렌즈 (CLAUDE.md 원칙이 곧 진단 축)

| 렌즈 | 심문 | 신호 |
|---|---|---|
| DRY | 같은 로직 2번+ 반복? | 복붙된 블록, 평행 분기 |
| 모듈화 | 한 파일/함수가 여러 책임? | 비대 파일, 긴 함수, 낮은 응집 |
| 타입 안전 | `any`/암묵 any, 느슨한 계약? | 타입 누락, 런타임 캐스팅 |
| 결합도 | 바꾸면 멀리까지 깨지나? | 양방향 의존, 전역/싱글톤 누수 |
| 복잡도 핫스팟 | 분기·중첩 폭발? | 깊은 if/루프, 순환복잡도 높은 함수 |
| 데드코드 | 안 쓰이는 export/분기? | 미참조 심볼, 도달 불가 분기 |

## 영향 범위 (= 무게 신호)

진단은 항상 **"이 변경이 어디까지 번지나"** 를 같이 낸다 — 이게 `weight-gate.md` 3-트리거로 직결:
- 남이 의존하는 계약(공유 타입/모듈/API/스키마)을 건드리면 → heavy 후보.
- 한 파일 안에서 닫히면 → light 후보.

## 넓은 진단 — 서브에이전트 재사용

대상이 여러 파일/모듈이면 코드리뷰 서브에이전트로 한 번에 스캔해 후보를 모은 뒤 사용자에게 제시한다(자동 수정 금지 — 후보만).

## 진단 결과 형식 (범위 확정 입력)

```
## 진단 — <대상>
- 아픈 곳: <렌즈>로 본 문제 1~N (파일:라인)
- 영향 범위: 닫힘 / <공유 계약명>까지 번짐
- 무게 신호: light │ heavy (트리거 ①②③ 중 무엇)
- 안 건드릴 것(제안 경계): …
```
```

- [ ] **Step 2: 검증**

확인:
- SKILL.md 2단계가 가리키는 `references/diagnose.md` 가 이제 존재.
- "진단 결과 형식"의 필드(아픈 곳/영향 범위/무게 신호/경계)가 SKILL.md 3단계(범위 확정)·1단계(무게)와 어긋나지 않음.

- [ ] **Step 3: 커밋**

```bash
git add plugins/pslog-workflow/skills/pslog-refactor/references/diagnose.md
git commit -m "feat(refactor): diagnose.md — 진단 렌즈 + 영향 범위 → 무게 신호"
```

---

### Task 3: references/preservation-contract.md (동작 보존 계약)

**Files:**
- Create: `plugins/pslog-workflow/skills/pslog-refactor/references/preservation-contract.md`

**Interfaces:**
- Consumes: SKILL.md 4단계가 이 파일을 가리킴.
- Produces: 동작보존 계약 형식 — `pslog-workflow` 끝검증의 "행동 동치" 레이어가 소비(같은 테스트 green).

- [ ] **Step 1: preservation-contract.md 작성**

아래 내용 그대로 작성:

```markdown
# 동작 보존 계약

리팩토링 = 행동 불변. **리팩토링 전후로 같은 테스트가 green** 이어야 "구조만 바꿨다"가 증명된다. 무게에 따라 안전망 강도가 다르다.

## light — 기존 테스트가 안전망

1. 이 행동을 덮는 **기존 테스트를 식별** — 무엇이 green이면 안전한가.
2. before: 그 테스트 + build/typecheck green 확인 (시작점 고정).
3. 리팩토링.
4. after: **같은 테스트 + build/typecheck green** = 동치. 안 깨지면 통과.

> 대상 영역에 테스트가 없으면 light라도 핵심 경로 1~2개는 characterization으로 깐다(아래).

## heavy — characterization 먼저

1. 진단 단계에서 **현재 행동을 핀고정하는 테스트를 먼저 작성** — "지금 이렇게 동작한다"를 캡처(옳은지 아닌지 판단하지 않고 *현 상태*를 박는다).
2. 그 테스트가 리팩토링 *전* 코드에서 green인지 확인(핀이 맞는지).
3. 리팩토링.
4. **동일 테스트 green = 동치 증명.** 이 테스트가 `pslog-workflow` 끝검증의 「행동 동치」 레이어.

## 계약 형식 (핸드오프에 박음)

```
## 동작보존 계약 — <대상>
- 무게: light │ heavy
- 동치 증명: <테스트 파일::케이스 목록> 이 before/after 동일 green
- characterization 신규(heavy): <작성한 테스트>
- 검증 명령: <예: bun test path / pytest path -v>
```

## 안 되는 것

- 동치 증명 테스트 없이 "눈으로 봤다" 로 통과 처리 금지.
- 리팩토링 도중 행동을 *바꾸는* 변경이 끼어들면 → 멈추고 분리(그건 `pslog-workflow` 일).
```

- [ ] **Step 2: 검증**

확인:
- SKILL.md 4단계가 가리키는 `references/preservation-contract.md` 존재.
- "계약 형식"이 SKILL.md 5단계 핸드오프(handoff에 계약 박음)·`pslog-workflow` 끝검증(같은 테스트 green)과 일관.
- 검증 명령 예시가 pslog 스택(bun/pytest)과 맞음.

- [ ] **Step 3: 커밋**

```bash
git add plugins/pslog-workflow/skills/pslog-refactor/references/preservation-contract.md
git commit -m "feat(refactor): preservation-contract.md — light/heavy 동작 보존 안전망"
```

---

### Task 4: 라우팅 정렬 — pslog-workflow description + plugin.json

**Files:**
- Modify: `plugins/pslog-workflow/skills/pslog-workflow/SKILL.md:3` (frontmatter description)
- Modify: `plugins/pslog-workflow/.claude-plugin/plugin.json:6` (description)

**Interfaces:**
- Consumes: Task 1의 refactor description(트리거 문구)과 상호 배타여야 함.
- Produces: 세 스킬 라우팅 경계 확정.

- [ ] **Step 1: pslog-workflow SKILL.md description에 버그픽스 디스앰비규에이터 추가**

현재 (line 3):
```
description: pslog로 관리되는 프로젝트에서 할당된 task를 탄탄하게 실현하는 워크플로. 사용자가 "내 할 일/다음 작업/뭐 하지" 류로 묻거나, task를 잡고 코드 작성에 들어갈 때 사용. PLAN.md/handoff를 읽어 task를 고르고, 무게 게이트로 brief vs spec→plan을 정하고, 각 단계마다 사람 승인을 받는다.
```

변경 후:
```
description: pslog로 관리되는 프로젝트에서 할당된 task를 탄탄하게 실현하는 워크플로. 사용자가 "내 할 일/다음 작업/뭐 하지" 류로 묻거나, task를 잡고 코드 작성에 들어갈 때, 또는 행동을 *바꾸는* 버그픽스/기능변경에 사용. PLAN.md/handoff를 읽어 task를 고르고, 무게 게이트로 brief vs spec→plan을 정하고, 각 단계마다 사람 승인을 받는다. (행동은 그대로 두고 구조만 바꾸는 리팩토링은 pslog-refactor.)
```

- [ ] **Step 2: plugin.json description에 refactor 추가**

현재 (line 6):
```
  "description": "pslog로 관리되는 프로젝트의 기획~구현 워크플로. pslog-planning(feature 아이디어 → 실행계획 → PLAN.md 분해) + pslog-workflow(PLAN task → 무게 게이트 → 코드), 각 단계 사람 승인.",
```

변경 후:
```
  "description": "pslog로 관리되는 프로젝트의 기획~구현 워크플로. pslog-planning(feature 아이디어 → 실행계획 → PLAN.md 분해) + pslog-refactor(코드 진단 → 동작 보존 리팩토링) + pslog-workflow(PLAN task → 무게 게이트 → 코드), 각 단계 사람 승인.",
```

- [ ] **Step 3: 라우팅 상호배타 검토 (이 plan의 핵심 검증)**

세 description의 트리거가 겹치지 않는지 한 자리에서 확인:

| 발화 | 기대 발동 | description 근거 |
|---|---|---|
| "이 중복 없애자/모듈 쪼개자/지저분한데 정리" | refactor | refactor: "행동은 그대로 두고 구조만" |
| "이거 버그야 고쳐줘/이렇게 동작하게 바꿔" | workflow | workflow: "행동을 바꾸는 버그픽스/기능변경" |
| "이런 기능 만들자/기획하자" | planning | planning: "새 feature 아이디어" |
| "내 다음 task 뭐지" | workflow | workflow: "내 할 일/다음 작업/뭐 하지" |

겹침/누락 있으면 문구 조정.

- [ ] **Step 4: 커밋**

```bash
git add plugins/pslog-workflow/skills/pslog-workflow/SKILL.md plugins/pslog-workflow/.claude-plugin/plugin.json
git commit -m "feat(refactor): 라우팅 정렬 — workflow=행동변경/refactor=구조만 디스앰비규에이터"
```

---

### Task 5: 메모리 갱신 — 2-스킬 → 3-스킬 구성

**Files:**
- Modify: `C:\Users\User\.claude\projects\D--pslog\memory\pslog-workflow-self-contained.md`
- Modify: `C:\Users\User\.claude\projects\D--pslog\memory\MEMORY.md`

**Interfaces:**
- Consumes: Task 1~4 확정 사실.
- Produces: 갱신된 프로젝트 메모리(다음 세션 컨텍스트).

- [ ] **Step 1: pslog-workflow-self-contained.md 의 "확정 2-스킬 구성" 블록을 3-스킬로 갱신**

`**확정된 2-스킬 구성 (2026-06-17 합의):**` 제목을 `**확정된 3-스킬 구성 (2026-06-17 2-스킬 합의 → 2026-06-18 pslog-refactor 추가):**` 로 바꾸고, 목록에 다음 항목을 `pslog-planning` 과 `pslog-workflow` 사이에 추가:

```
- `pslog-refactor` (2026-06-18 설계) — **동작 보존 리팩토링**. 코드 진단 → 무게 → 범위 확정 → 동작보존 계약(characterization) → `pslog-workflow` 로 핸드오프. 행동 불변만 다룸(버그픽스=행동변경은 workflow). 앞 절반(진단)만 담당, 엔진은 workflow 재사용. 설계: `docs/superpowers/specs/2026-06-18-pslog-refactor-skill-design.md`.
```

- [ ] **Step 2: MEMORY.md 인덱스 한 줄 갱신**

`pslog-workflow 자급자족 원칙` 줄의 hook을 `2-스킬` → `3-스킬(planning/refactor/workflow)` 로 갱신:

```
- [pslog-workflow 자급자족 원칙](pslog-workflow-self-contained.md) — 외부 스킬 의존 금지, 기능은 번들 스킬로; 확정된 pslog-planning + pslog-refactor + pslog-workflow 3-스킬 구성
```

- [ ] **Step 3: 검증**

확인:
- `self-contained.md` 의 3-스킬 목록이 planning/refactor/workflow 순서로 일관, refactor 설명이 SKILL.md(Task 1)와 모순 없음.
- MEMORY.md 인덱스 hook이 본문과 일치.

(메모리 파일은 git 추적 대상 아님 — 커밋 없음.)

---

## Self-Review

**1. Spec coverage** (`2026-06-18-pslog-refactor-skill-design.md` 대비):
- 결정1 구조(얇은 진단+핸드오프) → Task 1 SKILL.md 흐름 5단계. ✅
- 결정2 범위(동작 보존만) → Task 1 "범위" 섹션 + Task 4 라우팅. ✅
- 결정3 핸드오프(무게 분기) → Task 1 5단계(light=PLAN 건너뜀 / heavy=(deep) task). ✅
- 결정4 안전망(무게 연동) → Task 3 preservation-contract.md. ✅
- 결정5 라우팅(트리거+디스앰비규에이터) → Task 1 description + Task 4. ✅
- 자급자족(3-스킬, 자동발견) → Global Constraints + Task 5. ✅
- references 구성(diagnose/preservation-contract) → Task 2·3. ✅

**2. Placeholder scan:** 각 파일 전체 내용을 task 안에 그대로 실음 — TODO/TBD 없음. ✅

**3. Type/이름 consistency:**
- 스킬명 `pslog-refactor` (frontmatter name = 디렉터리명) 전 task 동일. ✅
- "동작보존 계약" 형식 필드(무게/동치 증명/characterization/검증 명령)가 Task 3 정의 ↔ Task 1 5단계·`pslog-workflow` 끝검증 일관. ✅
- "진단 결과 형식" 필드(아픈 곳/영향 범위/무게 신호/경계)가 Task 2 정의 ↔ Task 1 3단계 일관. ✅
- 라우팅 트리거가 Task 1 description ↔ Task 4 workflow/plugin.json description 상호 배타. ✅
```
