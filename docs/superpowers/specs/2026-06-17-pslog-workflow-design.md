# pslog-workflow — 태스크 준비도 루프 설계서

작성일: 2026-06-17
상태: 초안 (brainstorming 합의 완료, 사용자 리뷰 대기)
관련 설계서:
- `2026-06-14-decision-truth-loop-design.md` (Drift 모델 / OPEN→RESOLVED→IGNORED 라이프사이클 / DECISIONS 승격 루프 — 본 설계가 재사용·연장)
- `2026-04-26-ai-task-automation-design.md` (PLAN/handoff sync, handoff 파서)
- `2026-05-01-phase-6-discord-notifications-design.md` (notification dispatcher)

---

## 1. 배경 / 문제

pslog는 추적 대상 repo의 `PLAN.md`(체크박스 task = source of truth) + `handoffs/{branch}.md` +
git push webhook 을 ingest 해 태스크 대시보드·드리프트·Discord 알림을 만드는 **read-only 플랫폼**이다.
`app-chak` 이 첫 입주처이며, pslog 는 **어느 프로젝트나** 이렇게 관리할 수 있게 설계됐다(프로젝트별
`plan_path`/`handoff_dir`/`decisions_path` 컬럼이 그 증거).

지금까지의 진화:
1. PLAN 의 역할(role)로 task 를 나눔 → 2. task↔PLAN 정합(드리프트) → 3. handoff↔PLAN 모순 감지.

**관찰된 다음 문제 — 할당받은 task 하나하나를 "탄탄하게" 실현할 발판이 없다.**
현재 대상 repo 의 루프는 "PLAN 읽음 → task 잡음 → 브랜치 + handoff 만듦 → 바로 코드"다.
task 와 코드 사이에 **무엇을·왜·언제 끝났다고 할지(설계·계획)를 정리하는 단계가 없다.**

- app-chak 은 spec/plan 을 쓰긴 하지만 **feature 단위**다(`docs/superpowers/specs|plans`). PLAN.md 의
  개별 `task-NNN` 에는 spec 도 plan 도 없고, task 레벨 추적은 체크박스 + handoff 뿐이다.
- 그 spec/plan 은 외부 범용 스킬(superpowers) 포맷이라 **pslog 의 컨벤션(task-NNN 백링크,
  handoff `### 결정`, DECISIONS 루프, 역할 게이트)을 모른다.** task 와도 안 묶여 추적이 불가능하다.

즉 **"task 단위 준비(준비도)"라는 레이어가 비어 있다.** 이걸 채우되, app-chak 전용이 아니라
**pslog 가 모든 프로젝트에 제공하는 플랫폼 기능**으로 만든다.

---

## 2. 목표 / 비목표

### 2.1 목표
- 대상 repo 의 task→코드 흐름에 **"무게에 맞는 준비 문서(brief 또는 spec→plan)"** 단계를 끼운다.
- **각 단계 전이(기획 → tasks 나눔 → spec → plan → 코드)마다 사람의 판단·승인 게이트를 둔다** —
  에이전트가 단계를 자동으로 관통하지 않는다(§5.3).
- 그 준비 컨벤션을 **프로젝트 무관한 범용 스킬(`pslog-workflow`)로 정의·배포**한다.
  (pslog 가 계약을 소유, 각 프로젝트는 설치 + 트리거 한 줄.)
- pslog 가 **task 별 준비도(필수 산출물 유무)를 generic 하게 감지·시각화**한다(드리프트 재사용).

### 2.2 비목표 (YAGNI)
- 모든 task 에 풀 spec+plan 강제 — 무게 게이트로 대부분은 brief 한 장(§5).
- pslog 가 repo 를 직접 수정(write-back) — **read-only 미러 유지.** 감지·알림만.
- pslog 가 대상 repo 의 Claude 세션 안에서 코드를 직접 작성 — 실제 저작·코딩은 대상 repo 의
  에이전트가 `pslog-workflow` 스킬로 수행한다.

---

## 3. 핵심 모델 — 플랫폼 3레이어

| 레이어 | 내용 | 범용성 보장 |
|---|---|---|
| **① 포맷/의식** | pslog 가 정의하는 무게 게이트 + brief/spec/plan 템플릿 + task→준비→코드 의식 | 프로젝트 무관한 계약(contract) |
| **② 배포** | 그 의식을 수행하는 **범용 스킬 `pslog-workflow`** | 프로젝트별 config 로 경로·포맷 파라미터화 |
| **③ 추적** | task 별 준비도 감지 → 대시보드·Discord | pslog 가 이미 repo 를 읽으니 generic |

이는 decision-truth-loop 의 **Phase1(컨벤션)+Phase2(pslog 감지) 패턴을 그대로 연장**한 것이다.

---

## 4. 전달 메커니즘 — `pslog-workflow` 스킬 (B: 통합)

### 4.1 왜 스킬인가 (context 예산)
- **CLAUDE.md / AGENTS.md 는 항상 context 에 상주**한다(매 세션·모든 프로젝트). 길수록 매번 토큰 낭비.
- **스킬은 lazy** — `name`+`description` 한 줄만 상주, 본문(수백 줄 절차·템플릿)은 **호출될 때만** 로드.
- 따라서 "task 잡고 코드 짤 때만 필요한 절차·템플릿"은 **스킬에** 넣고, CLAUDE.md 엔 **트리거 한 줄**만 둔다.

### 4.2 통합 범위 (B 결정)
신규 brief/spec/plan 의식만 더하는 게 아니라, **기존 pslog 연동 규칙(handoff 헤더 strict 포맷,
task 선택 흐름, PLAN 작성 규칙 등 — 현재 app-chak CLAUDE.md 28~62줄에 인라인)까지 `pslog-workflow`
스킬로 흡수**한다.

결과:
- 어느 프로젝트든 CLAUDE.md 는 **"pslog 쓴다 + 트리거 한 줄" + 스킬 설치 한 번**으로 끝.
- app-chak CLAUDE.md 의 그 35줄도 **오히려 줄어듦**(계약이 스킬로 이동).
- 계약이 **1벌**로 관리되어 프로젝트 간 복붙(DRY 위반) 제거 → "어느 프로젝트나" 확장성 확보.

### 4.3 CLAUDE.md 트리거 (얇게)
프로젝트 CLAUDE.md 에 남는 건 대략:
> pslog 로 관리하는 프로젝트다. "내 할 일/다음 작업" 요청 또는 코드 작성 진입 시 → `pslog-workflow` 스킬을 따른다.

(스킬 `description` 자체로 자동 디스커버리도 되지만, pslog 는 "강제"가 핵심이라 트리거 줄을 명시해 호출 누락을 막는다.)

### 4.4 배포 — Claude Code 플러그인 (확정)
`pslog-workflow` 는 **Claude Code 플러그인**으로 배포한다(마켓플레이스 등록).
- 프로젝트는 마켓플레이스에서 설치 → **업데이트가 전파**된다(수동 복사처럼 복사본이 드리프트하지 않음).
- 누구나 설치 가능 = pslog 의 "어느 프로젝트나" 정체성과 일치. 당분간 사용자 본인만 써도 플러그인으로 시작한다.
- 구조: `pslog-workflow/.claude-plugin/plugin.json` + `skills/`(SKILL.md) + 마켓플레이스 매니페스트(`marketplace.json`).
- 수동 복사(A)/API 서빙(C)은 기각 — 전파 안 됨 / 커스텀 메커니즘.

---

## 5. 무게 게이트 — 티어형

task 마다 무게를 보고 준비 강도를 정한다. **기본값은 light.**

### 5.1 3-트리거 룰 (셋 중 하나라도 yes → heavy)

| 트리거 | 뜻 | PLAN.md 에서 읽히는 신호 |
|---|---|---|
| **① 계약 변경** | 남이 의존하는 걸 건드림 | DB 스키마/migration, API 요청·응답 shape, auth, 공유 타입/모듈 (영향파일 backtick) |
| **② 설계 분기** | 구현 방법에 진짜 대안 2개+ → "어느 쪽?" 결정 필요 | (사람 판단 — 어차피 DECISIONS 로 남길 결정) |
| **③ 교차 의존** | 다른 lane/역할이 이 결과물을 기다림 | "병렬 가능" lane, handoff→designer, frontend 가 이 API 대기 |

- **light** (트리거 0개) → `brief` 한 장.
- **heavy** (트리거 1+개) → `spec` → `plan`.

### 5.2 무게 선언 — PLAN.md `(deep)` 마커
- heavy task 는 PLAN.md task 줄에 `(deep)` 마커를 단다. 마커 없음 = light.
  ```
  - [ ] [task-007] (deep) 결제 웹훅 재시도 — @sejong — `services/webhook.py`, `alembic/...`
  - [ ] [task-012] 버튼 색 토큰 교체 — @jessica — `theme.ts`
  ```
- **진실의 원천 = PLAN.md.** 마커 1개라 "PLAN 얇게 유지" 원칙 안 깸. `(deep)` 는 사람이 직관적으로 이해 가능(`!` 등 기호 대신).
- **누가/언제**:
  - 원칙: **스프린트 PLAN 작성 시 미리** 표시(스키마 건드림 등은 그때 이미 앎).
  - 백필: 안 달려 있어도 **task 잡을 때 `pslog-workflow` 게이트가 평가** → heavy 면 마커를 PLAN.md 에
    달아주고 사용자에게 알림. (에이전트가 대상 repo 안에서 PLAN.md 수정하는 것이므로 pslog read-only 원칙과 무관.)
- pslog 는 `(deep)` 유무로 **"이 task 에 어떤 산출물이 있어야 하는지"**를 안다(§7 준비도 판정의 입력).

### 5.3 사람 승인 게이트 (단계마다 멈춤)
이 흐름은 **에이전트가 자동으로 관통하지 않는다.** 각 단계 전이에서 멈추고 사람의 판단·승인을 받는다:

| 전이 | 게이트 |
|---|---|
| 기획(PLAN/feature) → **tasks 나눔** | 어떤 task 를 잡을지 1~2개 제안 후 사용자 확인 (기존 흐름 유지) |
| task 잡음 → **무게 판정** | 게이트 결과(light/heavy)와 `(deep)` 마커 부여를 사용자에게 확인 |
| (heavy) **spec 작성 → plan** | spec 초안을 사람이 검토·승인해야 plan 으로 진행 |
| **plan 작성 → 코드** | plan 을 사람이 검토·승인해야 구현 시작 |
| (light) **brief 작성 → 코드** | brief(특히 DoD)를 사람이 확인 후 구현 |

- `pslog-workflow` 스킬이 각 멈춤을 **강제**한다(승인 없이 다음 단계 진입 금지).
- superpowers 의 brainstorming/writing-plans 가 스스로 멈춰 승인받는 것과 같은 결 — 이 방법론의 핵심 안전장치.
- pslog 추적(§7)은 "산출물이 있나"는 보지만 "사람이 승인했나"까지는 보지 않는다(휘발성 대화 영역).
  강제는 스킬이, 가시화는 산출물 존재로 분담.

---

## 6. 문서 템플릿 3종 + 위치

### 6.1 위치 규칙 — task 별 폴더
```
docs/tasks/task-007/
  ├─ spec.md      ← (deep) 일 때
  └─ plan.md      ← (deep) 일 때
docs/tasks/task-012/
  └─ brief.md     ← light 일 때
```
- **고정 파일명**(`brief.md`/`spec.md`/`plan.md`) → pslog 준비도 판정이 **"파일 있나?" 한 방**. 날짜·슬러그 파싱 불필요.
- 사람도 "task-007 폴더 열면 다 있음" — 가장 직관적. task 단위로 brief→spec→plan 응집.
- feature 단위 큰 문서(기존 `docs/specs` 등)는 그대로 둔다 — 본 설계는 **task 레이어**로 별개.
- 루트(`docs/tasks`)는 프로젝트별 설정(§7.1) 으로 override 가능 → generic.

### 6.2 ① light → `brief.md` (한 장, 항상 필수)
```
# task-012 brief — 버튼 색 토큰 교체
- 왜: design-system 토큰 통일 (PLAN task-012)
- 무엇: Button 계열 hardcoded color → theme 토큰. (out: 레이아웃 안 건드림)
- 완료조건(DoD): ☐ 모든 Button 토큰 사용  ☐ 다크모드 회귀 없음
- 영향파일: `theme.ts`, `Button.tsx`
- 검증: pnpm typecheck
```
작은 task 에도 "왜 / 무엇 / 끝 기준"의 척추를 준다. ~10줄.

### 6.3 ② heavy → `spec.md` (설계, 먼저)
```
# task-007 spec — 결제 웹훅 재시도   확정일: YYYY-MM-DD
1. 배경/문제      — 왜 필요, 지금 뭐가 깨지나
2. 목표 / 비목표  — YAGNI 명시
3. 설계(안)       — 핵심 접근 (백엔드면 데이터모델·계약 / UI면 화면·플로우)
4. 대안 & 결정 ★  — 트리거② 가 heavy 이유 → 대안 비교 후 결정.
                    이 결정은 PR 때 DECISIONS.md 로 승격(decision-truth-loop 와 연결).
5. 영향/리스크    — 트리거① 계약 변경 범위, migration, 롤백
6. 의존/사인오프  — 트리거③ 누가 이 결과물 기다리나 + 역할 게이트
```

### 6.4 ③ heavy → `plan.md` (실행, spec 다음)
```
# task-007 plan   (spec: ./spec.md)
- 아키텍처 한 줄 + 파일 구조(신규/수정)
- Step 분해:  - [ ] Step 1 …  - [ ] Step 2 …
- 각 Step 검증 명령 + 롤백
```
heavy task 는 brief 를 따로 두지 않는다 — spec 의 배경/목표가 brief 를 대체.

### 6.5 superpowers 와 다른 점 (= "pslog 만의" 이유)
1. **task-NNN 에 명시적으로 묶임** — pslog 추적의 키. (범용 spec 은 feature 단위라 task 와 안 묶임)
2. **DoD 가 1급 필드** — brief 에도 들어감. "탄탄하게"의 실체.
3. **spec 의 "대안 & 결정" → DECISIONS.md 승격**과 직결 — decision-truth-loop 와 한 몸으로 물림.
4. **무게 게이트가 어떤 문서를 쓸지 자동 결정** — 사람이 매번 고민 안 함.

---

## 7. 추적 레이어 (pslog 코드)

### 7.1 프로젝트 설정
- `Project` 에 `tasks_dir` 컬럼 추가(기본 `docs/tasks`). 기존 `plan_path`/`handoff_dir`/`decisions_path` 옆.
- pslog 는 `{tasks_dir}/task-NNN/` 를 읽어 산출물 유무를 본다.

### 7.2 준비도 판정 규칙
task 의 무게(PLAN.md `(deep)` 유무)에 따라 필수 산출물이 다르다:
- `(deep)` task → `spec.md` **그리고** `plan.md` 존재해야 OK.
- 일반 task → `brief.md` 존재하면 OK.
- **그 task 의 브랜치에 코드가 들어왔는데(push) 필수 산출물이 없음** → **준비도 미달**.
  ("코드 들어옴" 판정: 해당 브랜치 push 이벤트에 코드 파일 변경이 있는데 `docs/tasks/task-NNN/` 의 필수
  파일이 없거나 비어 있음. 정확한 시점·강도는 계획 단계에서 확정 — §8.)

### 7.3 Drift 재사용
새 모델을 만들지 않고 기존 `Drift` 에 **type `TASK_NOT_PREPARED` 하나 추가**.
- 라이프사이클: `OPEN` → 산출물 생기면 다음 sync 에서 **자동 RESOLVED** / 의도적이면 **수동 IGNORED**.
- 멱등: 같은 (project, type, external_id=task-NNN) 이 이미 OPEN 이면 중복 생성 안 함.
- 노출: 기존 드리프트 대시보드 패널 + `notification_dispatcher` Discord 힌트 재사용.
  예: "⚠️ task-007 (deep): 코드 들어왔는데 spec/plan 없음 → docs/tasks/task-007/ 에 작성하세요."

### 7.4 감지 위치
`sync_service._process_inner` 의 드리프트 평가 단계(이미 A/B 평가하는 곳)에 준비도 평가를 추가.
handoff 파서가 이미 task 별 브랜치·상태를 알고, `git_repo_service` 로 `docs/tasks/` 존재를 조회 가능.

---

## 8. 구현 순서

1. **Phase 1 — 컨벤션 + 스킬 (pslog 코드 0 에 가까움)**
   - `pslog-workflow` 스킬 저작: 무게 게이트 + brief/spec/plan 템플릿 + 기존 handoff/task-선택 규칙 흡수(B).
   - pslog 측 계약 문서(본 스펙 + 템플릿)를 파서 스펙과 co-locate.
   - app-chak 적용: CLAUDE.md 를 트리거 한 줄로 축소 + 스킬 설치 + PLAN.md `(deep)` 마커 도입.
   - Phase 2 의 명세 역할도 겸함.
2. **Phase 2 — pslog 추적 코드**
   - `Project.tasks_dir` 마이그레이션.
   - 준비도 판정 + `Drift(TASK_NOT_PREPARED)` 평가를 `sync_service` 에 추가.
   - 대시보드 패널 + Discord 힌트(기존 인프라 재사용).

---

## 9. 계획 단계에서 확정할 디테일 (본 스펙 미결)

1. **"코드 들어옴" 판정 강도**(§7.2): push 의 코드파일 변경 유무만 볼지, 브랜치-task 매칭을 어디까지 신뢰할지.
   v1 은 가벼운 쪽(브랜치명 `feat/task-NNN-*` + 코드 변경 존재) 권장.
2. **brief/spec/plan "있음" 판정**: 파일 존재만으로 충분한지 vs 최소 섹션(DoD 등) 채움까지 볼지.
   v1 은 파일 존재 + 비어있지 않음(가벼운 쪽) 권장.
3. **게이트 백필 시 PLAN.md 자동 수정**의 사용자 승인 흐름(자동 vs 제안 후 확인).
   (§5.3 사람 승인 게이트와 연결 — 자동보다 "제안 후 확인" 권장.)
4. **Drift detail/힌트 카피** 최종 문구 + i18n.

---

## 10. 합의 로그 (brainstorming 2026-06-17)

- 역할: 본 기능은 app-chak 전용이 아니라 **pslog 플랫폼 기능**(3레이어). ✅
- pslog 는 read-only 유지 — 저작·코딩은 대상 repo 에이전트가 스킬로 수행. ✅
- 전달: **B(통합)** — 신규 spec/plan + 기존 loop 까지 `pslog-workflow` 스킬로. CLAUDE.md 는 트리거 한 줄. ✅
- 무게: 티어형(light=brief / heavy=spec→plan), 기본 light. ✅
- 게이트: **3-트리거**(계약 변경 / 설계 분기 / 교차 의존), 하나라도 yes → heavy. ✅
- 선언: PLAN.md `(deep)` 마커(진실의 원천) + task 잡을 때 게이트 백필. ✅
- 템플릿: brief/spec/plan 3종 + superpowers 차별점 4가지(task 결속·DoD 1급·DECISIONS 승격·게이트 자동). ✅
- 위치: **task 별 폴더** `docs/tasks/task-NNN/` 고정 파일명. ✅
- 추적: `Project.tasks_dir` + 준비도 규칙 + **Drift `TASK_NOT_PREPARED` 재사용**(새 모델 X). ✅
- 배포: **Claude Code 플러그인/마켓플레이스**(전파됨, 누구나 설치). 수동복사/API 기각. ✅
- 흐름: **각 단계(기획→tasks→spec/brief→plan→코드)마다 사람 승인 게이트** — 스킬이 멈춤 강제. ✅
