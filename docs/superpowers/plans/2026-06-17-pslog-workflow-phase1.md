# pslog-workflow Phase 1 (플러그인 + 스킬) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Task 3·4(SKILL/레퍼런스 저작)는 실행 시 **superpowers:writing-skills** 를 함께 쓰면 좋다.

**Goal:** pslog 가 정의하는 "task→spec/brief→plan→코드 + 단계별 사람 승인" 방법론을 `pslog-workflow` 라는 Claude Code 플러그인(스킬 1개)으로 만들어, pslog repo 가 마켓플레이스로 배포하고 로컬 설치까지 검증한다.

**Architecture:** pslog repo 루트에 마켓플레이스 매니페스트(`.claude-plugin/marketplace.json`)를 두고, `plugins/pslog-workflow/` 에 플러그인(매니페스트 + 스킬)을 둔다. 스킬 본문(SKILL.md)은 흐름·승인 게이트만 담아 얇게 유지하고, 무게 게이트/템플릿/handoff 포맷 같은 무거운 계약은 `references/` 로 분리(스킬은 필요할 때만 레퍼런스를 읽는다). 코드는 없다 — 산출물은 마크다운·JSON.

**Tech Stack:** Claude Code 플러그인/스킬(마크다운 + YAML frontmatter), 마켓플레이스 JSON. 검증은 `claude plugin validate` + `/plugin` 설치 명령.

설계 근거: `docs/superpowers/specs/2026-06-17-pslog-workflow-design.md` (§4 배포·전달, §5 무게 게이트·승인 게이트, §6 템플릿).

---

## File Structure

```
D:\pslog\
├── .claude-plugin\
│   └── marketplace.json                         (NEW) pslog repo = 마켓플레이스
└── plugins\
    └── pslog-workflow\
        ├── .claude-plugin\
        │   └── plugin.json                       (NEW) 플러그인 매니페스트
        ├── hooks\
        │   └── hooks.json                        (NEW) SessionStart hook 등록
        ├── bin\
        │   └── inject-pslog-context.js           (NEW) PLAN.md 있으면 트리거 주입 (node, Windows 호환)
        └── skills\
            └── pslog-workflow\
                ├── SKILL.md                       (NEW) 메인 흐름 + 승인 게이트 (얇게)
                └── references\
                    ├── weight-gate.md             (NEW) 3-트리거 + (deep) 마커 + 백필
                    ├── templates.md               (NEW) brief/spec/plan 템플릿 + 위치
                    └── handoff-format.md          (NEW) handoff strict 포맷 (app-chak 에서 흡수)
```

- `marketplace.json` 의 `source` 는 마켓플레이스 루트(=repo 루트) 기준 상대경로 `"./plugins/pslog-workflow"`.
- 스킬 디렉터리명(`pslog-workflow`) 이 곧 호출명 → `/pslog-workflow` (플러그인 네임스페이스: `/pslog-workflow:pslog-workflow`).
- SKILL.md 는 500줄 이하 유지, 무거운 계약은 references 로.

---

## Task 1: 플러그인 매니페스트 + 디렉터리

**Files:**
- Create: `plugins/pslog-workflow/.claude-plugin/plugin.json`

- [ ] **Step 1: plugin.json 생성**

```json
{
  "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
  "name": "pslog-workflow",
  "displayName": "pslog Workflow",
  "version": "0.1.0",
  "description": "pslog로 관리되는 프로젝트에서 할당된 task를 무게에 맞는 준비 문서(brief 또는 spec→plan)로 탄탄하게 실현하고, 각 단계마다 사람 승인을 받는 워크플로 스킬.",
  "author": { "name": "ardenspace" },
  "homepage": "https://github.com/ardenspace/pslog",
  "repository": "https://github.com/ardenspace/pslog",
  "license": "MIT",
  "keywords": ["pslog", "workflow", "spec", "plan", "task"]
}
```

- [ ] **Step 2: JSON 유효성 확인**

Run: `node -e "JSON.parse(require('fs').readFileSync('plugins/pslog-workflow/.claude-plugin/plugin.json','utf8')); console.log('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add plugins/pslog-workflow/.claude-plugin/plugin.json
git commit -m "feat(plugin): pslog-workflow 플러그인 매니페스트"
```

---

## Task 2: 마켓플레이스 매니페스트

**Files:**
- Create: `.claude-plugin/marketplace.json`

- [ ] **Step 1: marketplace.json 생성**

```json
{
  "$schema": "https://json.schemastore.org/claude-code-marketplace.json",
  "name": "pslog",
  "description": "pslog 플랫폼이 배포하는 워크플로 스킬 마켓플레이스",
  "version": "0.1.0",
  "owner": { "name": "ardenspace" },
  "plugins": [
    {
      "name": "pslog-workflow",
      "source": "./plugins/pslog-workflow",
      "description": "task→spec/brief→plan→코드 + 단계별 사람 승인 워크플로",
      "category": "productivity",
      "strict": true
    }
  ]
}
```

- [ ] **Step 2: 마켓플레이스 검증**

Run: `claude plugin validate .`
Expected: 검증 통과(에러 0). marketplace.json JSON 문법 / 중복 plugin 명 / source 경로 traversal / 버전 불일치 체크.
(만약 `claude plugin validate` 가 없는 버전이면 Step 3 의 `/plugin marketplace add ./` 로 대체 검증.)

- [ ] **Step 3: Commit**

```bash
git add .claude-plugin/marketplace.json
git commit -m "feat(plugin): pslog 마켓플레이스 매니페스트 + pslog-workflow 등록"
```

---

## Task 3: SKILL.md — 메인 흐름 + 승인 게이트 (얇게)

**Files:**
- Create: `plugins/pslog-workflow/skills/pslog-workflow/SKILL.md`

- [ ] **Step 1: SKILL.md 작성 (아래 내용 그대로)**

```markdown
---
name: pslog-workflow
description: pslog로 관리되는 프로젝트에서 할당된 task를 탄탄하게 실현하는 워크플로. 사용자가 "내 할 일/다음 작업/뭐 하지" 류로 묻거나, task를 잡고 코드 작성에 들어갈 때 사용. PLAN.md/handoff를 읽어 task를 고르고, 무게 게이트로 brief vs spec→plan을 정하고, 각 단계마다 사람 승인을 받는다.
---

# pslog-workflow

pslog로 관리되는 repo에서 **할당받은 task 하나를 무게에 맞는 준비 문서로 정리한 뒤 코드로 옮기는** 흐름.
**핵심 원칙: 각 단계 전이마다 멈추고 사람의 승인을 받는다. 절대 자동으로 관통하지 않는다.**

## 흐름 (각 → 에서 멈춰 승인)

1. **task 선택** — `PLAN.md` 의 `## 태스크` 에서 `@<username>` + 미완료(`[ ]`) 필터 →
   lane 의존성("병렬 가능") 고려해 1~2개 제안 → **사용자 확인**.
   (username 모르면 먼저 물어봄. 각자 `~/.claude/CLAUDE.md` 에 박아두면 자동.)
2. **브랜치 + handoff** — 동의 시 `feat/task-NNN-<짧은-슬러그>` 생성 →
   `handoffs/{브랜치}.md` 확인/생성 (포맷은 `references/handoff-format.md`).
3. **무게 판정** — `references/weight-gate.md` 의 3-트리거로 light/heavy 판정 →
   결과와 `(deep)` 마커 부여를 **사용자에게 확인**(heavy면 PLAN.md task 줄에 `(deep)` 백필 제안).
4. **준비 문서 작성** — `references/templates.md` 의 템플릿으로:
   - light → `docs/tasks/task-NNN/brief.md` 작성 → **brief(특히 DoD) 사용자 확인** → 코드.
   - heavy → `docs/tasks/task-NNN/spec.md` 작성 → **사용자 승인** → `plan.md` 작성 →
     **사용자 승인** → 코드.
5. **구현** — 승인된 plan/brief 의 Step·DoD 따라 코드. 진행하며 handoff 오늘 날짜 섹션 갱신.
6. **마무리** — git push **직전 반드시 handoff commit**. task 끝나면 PLAN.md `[task-NNN]` 체크.
   구현 중 기획과 달라진 결정은 handoff `### 결정` 에 적고 PR 때 DECISIONS.md 로 승격.

## 멈춤 규칙 (강제)

| 전이 | 승인받을 것 |
|---|---|
| 기획 → tasks 나눔 | 어떤 task 잡을지 (1~2개 제안 후 확인) |
| task → 무게 판정 | light/heavy + `(deep)` 마커 부여 |
| spec → plan (heavy) | spec 초안 검토·승인 |
| plan → 코드 | plan 검토·승인 |
| brief → 코드 (light) | brief의 DoD 확인 |

승인 없이 다음 단계로 진입 금지. (이 멈춤이 이 방법론의 핵심 안전장치다.)

## 무게에 따라 만드는 것

- **light**(트리거 0개) → `brief.md` 한 장.
- **heavy**(트리거 1+개) → `spec.md` → `plan.md`. (brief 없음 — spec이 대체.)

자세한 판정 기준·마커·템플릿·handoff 포맷은 `references/` 참고(필요할 때만 읽는다).
```

- [ ] **Step 2: 줄 수 확인 (500줄 이하)**

Run: `node -e "console.log(require('fs').readFileSync('plugins/pslog-workflow/skills/pslog-workflow/SKILL.md','utf8').split('\n').length)"`
Expected: 100 미만(얇게 유지 확인).

- [ ] **Step 3: Commit**

```bash
git add plugins/pslog-workflow/skills/pslog-workflow/SKILL.md
git commit -m "feat(skill): pslog-workflow 메인 흐름 + 승인 게이트"
```

---

## Task 4: references — 무게 게이트 / 템플릿 / handoff 포맷

**Files:**
- Create: `plugins/pslog-workflow/skills/pslog-workflow/references/weight-gate.md`
- Create: `plugins/pslog-workflow/skills/pslog-workflow/references/templates.md`
- Create: `plugins/pslog-workflow/skills/pslog-workflow/references/handoff-format.md`

- [ ] **Step 1: weight-gate.md 작성**

```markdown
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

\`\`\`
- [ ] [task-007] (deep) 결제 웹훅 재시도 — @sejong — \`services/webhook.py\`, \`alembic/...\`
- [ ] [task-012] 버튼 색 토큰 교체 — @jessica — \`theme.ts\`
\`\`\`

- 진실의 원천 = PLAN.md. 마커 1개라 "PLAN 얇게" 안 깸.
- 원칙: 스프린트 PLAN 작성 시 미리 표시.
- 백필: 안 달려 있으면 task 잡을 때 게이트가 평가 → heavy면 마커를 PLAN.md에 다는 것을
  **사용자에게 제안 후 확인**받아 추가(자동 수정 금지).
```

- [ ] **Step 2: templates.md 작성**

```markdown
# 준비 문서 템플릿 + 위치

## 위치 — task별 폴더 (고정 파일명)

\`\`\`
docs/tasks/task-007/spec.md   docs/tasks/task-007/plan.md   ← (deep)
docs/tasks/task-012/brief.md                                ← light
\`\`\`

루트(`docs/tasks`)는 프로젝트 설정으로 다를 수 있음. feature 단위 큰 문서는 별개.

## ① light → brief.md (한 장, 항상)

\`\`\`
# task-012 brief — 버튼 색 토큰 교체
- 왜: design-system 토큰 통일 (PLAN task-012)
- 무엇: Button 계열 hardcoded color → theme 토큰. (out: 레이아웃 안 건드림)
- 완료조건(DoD): ☐ 모든 Button 토큰 사용  ☐ 다크모드 회귀 없음
- 영향파일: \`theme.ts\`, \`Button.tsx\`
- 검증: pnpm typecheck
\`\`\`

## ② heavy → spec.md (설계, 먼저)

\`\`\`
# task-007 spec — 결제 웹훅 재시도   확정일: YYYY-MM-DD
1. 배경/문제      — 왜 필요, 지금 뭐가 깨지나
2. 목표 / 비목표  — YAGNI 명시
3. 설계(안)       — 핵심 접근 (백엔드면 데이터모델·계약 / UI면 화면·플로우)
4. 대안 & 결정    — 트리거② 가 heavy 이유 → 대안 비교 후 결정. PR 때 DECISIONS.md로 승격
5. 영향/리스크    — 트리거① 계약 변경 범위, migration, 롤백
6. 의존/사인오프  — 트리거③ 누가 이 결과물 기다리나 + 역할 게이트
\`\`\`

## ③ heavy → plan.md (실행, spec 다음)

\`\`\`
# task-007 plan   (spec: ./spec.md)
- 아키텍처 한 줄 + 파일 구조(신규/수정)
- Step 분해:  - [ ] Step 1 …  - [ ] Step 2 …
- 각 Step 검증 명령 + 롤백
\`\`\`

DoD는 항상 1급 필드. 모든 문서 제목에 task-NNN 백링크.
```

- [ ] **Step 3: handoff-format.md 작성**

```markdown
# handoff 포맷 (pslog 파서 strict — 어긋나면 MalformedHandoffError)

- 파일: `handoffs/{현재브랜치}.md` (브랜치명의 `/` → `-`).
- 첫 줄: `# Handoff: <branch> — @<username>`
  - `Handoff:` 키워드 필수. 구분자는 em-dash `—` (U+2014), 일반 hyphen 아님.
  - branch는 공백 없는 1단어(`feat/task-011-writing-catalog`), username은 영숫자/언더스코어/하이픈.
- 일자 섹션: `## YYYY-MM-DD` (최소 1개, 최신이 active).
- task 체크박스: 마스터 task ID는 들여쓰기 0(`- [ ] task-NNN`, pslog DB 동기화),
  개인 서브작업은 들여쓰기 2(raw, 동기화 안 됨).
- 자유 노트(선택): `### 마지막 커밋` / `### 다음` / `### 블로커`.
- 결정 포착(선택): `### 결정` — 구현이 기획과 달라지면 1~2줄. PR 때 DECISIONS.md로 승격하고 `→ DECISIONS` 마킹.
- git push 직전 반드시 이 파일 commit (미갱신 push → pslog Discord ⚠️).
```

- [ ] **Step 4: 세 파일 JSON/마크다운 존재 확인**

Run: `ls plugins/pslog-workflow/skills/pslog-workflow/references/`
Expected: `handoff-format.md  templates.md  weight-gate.md`

- [ ] **Step 5: Commit**

```bash
git add plugins/pslog-workflow/skills/pslog-workflow/references/
git commit -m "feat(skill): pslog-workflow 레퍼런스(무게 게이트/템플릿/handoff)"
```

---

## Task 5: 로컬 설치 + 스킬 로드 검증

**Files:** (없음 — 설치/검증만)

- [ ] **Step 1: 마켓플레이스 검증 재확인**

Run: `claude plugin validate .`
Expected: 통과. (없는 버전이면 다음 스텝으로.)

- [ ] **Step 2: 로컬 마켓플레이스 추가**

세션에서: `/plugin marketplace add ./`
Expected: `pslog` 마켓플레이스가 추가됨(로컬 디렉터리에서 직접 로드, 캐시 아님).

- [ ] **Step 3: 플러그인 설치**

세션에서: `/plugin install pslog-workflow@pslog`
Expected: 설치 성공 메시지.

- [ ] **Step 4: 스킬 로드 확인**

세션에서: `/skills` (또는 `/plugin list`)
Expected: 목록에 `pslog-workflow` 스킬 보임. `/pslog-workflow` 로 호출 가능.

- [ ] **Step 5: description 자동 디스커버리 sanity**

세션에서 새 대화로 "내 할 일 뭐지?" 류 입력 → `pslog-workflow` 스킬이 후보로 떠오르는지 확인.
Expected: 스킬이 트리거됨(또는 후보 제시). 안 되면 SKILL.md `description` 의 트리거 문구 보강.

---

## Task 6: (결정) dogfood — 의식이 실제로 도는지

> 기본값 **(가)**. (나)/(다) 원하면 이 Task를 교체.

**(가) 샘플 task 수동 워크스루 — 권장(기본)**
- [ ] **Step 1:** 가상의 task 하나(예: `task-999 (deep)` 와 일반 `task-998`)로 스킬을 끝까지 따라가 보며
  각 멈춤(승인 게이트)이 실제로 막는지, brief/spec/plan 산출물이 `docs/tasks/` 규칙대로 나오는지 확인.
  (실제 코드 구현은 건너뛰고 흐름·문서·게이트만 검증 — 임시 산출물은 커밋하지 않음.)
- [ ] **Step 2:** 어긋난 부분을 SKILL.md/references 에 반영 후 Task 3·4 의 commit 갱신.

**(나) app-chak 적용** — app-chak CLAUDE.md 를 트리거 한 줄로 축소 + 플러그인 설치 + PLAN.md `(deep)` 도입 후 실제 task 1개로 end-to-end.
**(다) pslog self-dogfood** — pslog 가 `PLAN.md` 채택 + 플러그인 설치 후 자기 task 로 흐름 검증(decision-truth-loop 가 지적한 self-dogfood 결여도 해소). 범위 큼 — 별도 작업 권장.

---

## Task 7: SessionStart hook — 자동 강제 (설계 §4.5)

**Files:**
- Create: `plugins/pslog-workflow/hooks/hooks.json`
- Create: `plugins/pslog-workflow/bin/inject-pslog-context.js`

설치만 하면 pslog 프로젝트(repo 루트에 `PLAN.md` 존재)에서 세션 시작 시 트리거가 자동 주입됨 → CLAUDE.md 수동 편집 불필요. 비-pslog 프로젝트선 무출력(무해). read-only(파일 수정 없음).

- [ ] **Step 1: hooks.json 생성** — `SessionStart`(matcher `startup|resume`) → `node ${CLAUDE_PLUGIN_ROOT}/bin/inject-pslog-context.js`. Windows 함정 회피 위해 `.js` 직접 실행이 아니라 `command:"node"` + `args:[경로]`.
- [ ] **Step 2: inject-pslog-context.js 생성** — `CLAUDE_PROJECT_DIR`(없으면 cwd) 기준 `PLAN.md` 존재 시 `hookSpecificOutput.additionalContext` JSON 출력, 없으면 무출력. stdin 파싱 없이 동기 동작.
- [ ] **Step 3: 유닛 테스트** — `CLAUDE_PROJECT_DIR=<PLAN.md 있는 임시폴더> node 스크립트` → 주입 JSON / 없는 폴더 → 무출력. 둘 다 확인.
  Expected: Case1 valid injection JSON, Case2 empty.
- [ ] **Step 4: `claude plugin validate .`** → ✔ passed.
- [ ] **Step 5: Commit**
  ```bash
  git add plugins/pslog-workflow/hooks/ plugins/pslog-workflow/bin/
  git commit -m "feat(plugin): SessionStart hook — pslog 프로젝트 자동 트리거 주입"
  ```
- [ ] **Step 6: 사용자 검증** — `/reload-plugins` → `/hooks` 에 SessionStart 등록 확인. (PLAN.md 있는 프로젝트서 실제 주입은 그곳에서 확인.)

---

## Self-Review (작성자 점검)

- **Spec 커버리지**: §4.2 통합(handoff 흡수)=Task 4 handoff-format / §4.3 트리거=SKILL 흐름1 + Task(나) /
  §4.4 플러그인 배포=Task 1·2·5 / §5.1 3-트리거=weight-gate / §5.2 `(deep)`=weight-gate / §5.3 승인 게이트=SKILL 멈춤 규칙 /
  §6 템플릿·위치=templates.md. 모두 매핑됨.
- **Placeholder**: 구조 파일(plugin.json/marketplace.json) 내용 완전. 템플릿의 `YYYY-MM-DD`/`task-NNN` 은
  사용자가 채우는 의도된 자리(템플릿이므로 정상).
- **일관성**: 호출명 `pslog-workflow`, 마켓플레이스명 `pslog`, 설치 타깃 `pslog-workflow@pslog` — Task 1/2/5 전체 일치.
  스킬 본문은 무게 판정을 references/weight-gate.md 로, 템플릿을 references/templates.md 로 일관 위임.

## 미결 → Phase 2 로 (본 Phase 범위 밖)
- pslog 코드(추적): `Project.tasks_dir`, 준비도 판정, `Drift(TASK_NOT_PREPARED)`, 대시보드·Discord — 별도 plan.
