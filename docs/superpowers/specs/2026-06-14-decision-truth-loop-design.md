# 결정-진실 루프 설계서 (Decision-Truth Loop)

작성일: 2026-06-14
상태: 초안 (brainstorming 합의 완료, 사용자 리뷰 대기)
관련 설계서:
- `2026-04-26-ai-task-automation-design.md` (PLAN/handoff sync 기반)
- `2026-05-01-phase-6-discord-notifications-design.md` (Discord 알림 dispatcher)
- `2026-04-26-error-log-design.md` 외 error-log 시리즈 (OPEN/RESOLVED/IGNORED 상태 패턴 — 본 설계가 재사용)

---

## 1. 배경 / 문제

pslog(이하 새 이름 **pslog**)는 추적 대상 repo의 `PLAN.md`(체크박스 task = source of truth) +
`handoffs/{branch}.md`(브랜치별 작업 로그) + git push webhook 을 ingest 해
태스크 대시보드와 Discord 알림을 만든다. `app-chak` 이 이 시스템의 첫 실사용처다.

**관찰된 문제** — 구현을 하다 보면 기획(PLAN/PRD/spec)과 달라지는 결정이 생긴다.
그 결정은 작업 중 handoff 에는 흔적이 남지만, `PLAN.md` 는 따라가지 못한다.
즉 PLAN 과 handoff 가 **서로 다른 이야기를 한다**. 게다가:

1. **결정이 handoff(휘발성)에서 태어나 handoff에서 죽는다.** handoff 는 브랜치 단위,
   append-only, 세션 로그 성격이라 휘발적이다. 구현 중 내려진 결정을 영속 문서로
   **승격시키는 의식이 없다.** 후임이 `PLAN.md`(+ PRD/DECISIONS)만 읽고 handoff 는
   안 읽으면 "as-built 진실"을 놓친다.

2. **pslog 가 드리프트를 잡을 데이터를 가지고도 안 본다.** `_apply_handoff` 는 handoff 의
   체크박스(`parsed_tasks`)와 free_notes(`blockers`/`next`)를 파싱해 DB 에 저장까지 하지만,
   `parsed_tasks` 는 대시보드 "개수 뱃지"로만 쓰이고 `free_notes` 는 저장 후 어디서도 읽히지 않는다.
   "PLAN 엔 task-007 DONE 인데 handoff 엔 미완/블로커" 같은 모순을 자동으로 잡을 수 있는데
   안 잡는다. 현재 유일한 PLAN↔handoff 연결은 "이번 push 에 handoff 파일이 바뀌었나?"
   존재 여부 체크(`handoff_missing`)뿐 — **내용 일관성은 보지 않는다.**

### 1.1 본 설계가 다루지 않는 인접 관찰 (별도 백로그)

진단 중 발견했으나 본 설계 범위 밖:
- pslog 가 자기 자신을 dogfood 하지 않음(PLAN.md 없이 BACKLOG.md 사용, handoff 단일 main.md).
  → 선택적 개선, 우선순위 낮음.
- 진실의 원천 다수화(PRD / DECISIONS / docs/plans / handoffs / PLAN) + 우선순위 규칙 부족.
  → 본 설계가 "DECISIONS.md 단일 입구" 원칙으로 부분 완화.
- `PLAN.md` 의 "영향 파일" 필드가 파싱되지만 `_apply_plan` 에서 버려짐(dead metadata).

---

## 2. 목표 / 비목표

### 2.1 목표
- 구현 중 발생한 결정이 **휘발성 handoff → 영속 DECISIONS.md** 로 흘러가는 경로(의식)를 만든다.
- 그 의식이 **지켜지지 않았을 때 pslog 가 자동으로 감지·가시화**한다(드리프트 알림).
- handoff 와 PLAN 의 **상태 모순**을 pslog 가 감지한다(이미 저장 중인 데이터 활용).

### 2.2 비목표 (YAGNI)
- ADR 의무화 — DECISIONS.md 한 줄이 기본, ADR 은 무거운 결정만 옵션.
- pslog 가 repo 를 직접 수정(write-back) — pslog 는 읽기 전용 미러로 유지. 감지·알림만.
- stale handoff 시점 비교(아래 4.3 의 signal C) — v1 제외, 백로그.
- handoff 블로커 자유텍스트 ↔ task 매칭 — v1 의 signal B 는 **구조적 체크박스 상태만** 사용.

---

## 3. 핵심 모델 — 세 문서, 각자 한 역할

| 문서 | 역할 | 성격 |
|---|---|---|
| `PLAN.md` | 태스크 트래커 (체크박스 + assignee) | 얇게 유지. 두껍게 만들지 않음 |
| `handoffs/{branch}.md` | 작업 중 낙서 + 결정 staging | 휘발성, 브랜치 단위 |
| `DECISIONS.md` | settled 결정의 **유일한 입구(index)** | 영속, 후임이 읽는 한 곳 |

**루프 한 줄 요약:**
구현 중 일탈 → handoff `### 결정` 에 포착 → **PR 열 때** DECISIONS.md 로 승격 →
머지 시 pslog 가 대조 → 안 맞으면 드리프트 OPEN → 고치면 자동 RESOLVED.

### 3.1 DECISIONS.md 입구 규칙 (Q2 합의)
- **필수**: 모든 결정은 DECISIONS.md 에 최소 한 줄(기존 `결정 / 이유 / 영향` 포맷). 후임은 이 파일 하나로 결정 전체를 본다.
- **옵션**: 결정이 크고(아키텍처급) 맥락·대안을 한 페이지 쓸 가치가 있으면 `docs/adr/NNN.md` 신설 +
  DECISIONS.md 에 `[ADR-NNN] 제목 → 링크` 한 줄. ADR 을 따로 "발견"할 필요 없음 — 입구가 항상 DECISIONS.md.
- 원칙: **"한 줄은 항상, 한 페이지는 가끔."** 4-5인 팀이 실제 유지 가능한 선.
- ADR 미도입은 비가역 결정 아님 — 필요해지는 순간 파일 1개 + 링크 1줄로 추가, 마이그레이션 0.

---

## 4. Phase 1 — 컨벤션 (pslog 코드 0)

pslog 코드 없이 즉시 가치. 계약(handoff 포맷)은 pslog 가 정의하고 `app-chak` 이 첫 적용.

### 4.1 handoff 포맷 확장 — `### 결정` 서브섹션
- **H3 서브섹션**으로 active 일자 섹션(`## YYYY-MM-DD`) 안에 둔다 — 기존 `### 다음`/`### 블로커`와 동일 레벨.
  (H2 `### 결정` 은 파서의 일자 섹션 정규식 `## YYYY-MM-DD` 와 충돌하므로 쓰지 않는다.)
- 항목 형식(파서 친화): `- [task-NNN] <무엇을 어떻게 바꿈> — <왜>`
- 승격 완료 시 표시: 항목 끝에 `→ DECISIONS` (또는 `→ ADR-NNN`) 마커.
- **결정이 없으면 서브섹션 자체가 없음** → 대부분의 "기획대로" task 는 오버헤드 0.

### 4.2 Phase 1 산출물 (파일 단위)
1. **pslog 측 계약 문서**: 본 스펙 + handoff `### 결정` 포맷을 pslog 문서에 명문화(파서 스펙과 co-locate).
2. **app-chak `handoffs/README.md` 템플릿**: `### 결정` 섹션 추가(손수 치다 누락 방지).
3. **app-chak `.claude/skills/handoff-protocol/SKILL.md`**: 의식 추가 —
   "PR 열기 전, handoff `### 결정` 항목을 DECISIONS.md 로 승격(날짜+한 줄, 무거우면 docs/adr/ + 링크) 후 `→ DECISIONS` 마킹."
4. **app-chak `CLAUDE.md` "pslog 연동 규칙"**: 위 의식을 작업 흐름에 박음(PR 단계).
5. **app-chak `DECISIONS.md` 신규 생성**: app-chak 엔 현재 DECISIONS.md 가 **없다**(pslog 엔 있음).
   Phase 1 에서 부트스트랩 — `목적` + 머리말("구현 중 결정도 여기로 승격. 무거운 건 ADR 링크") + 첫 결정 항목(루프 도입 자체).

### 4.3 의식 (Q3 합의 — 단계형)
1. **포착(작업 중)**: 구현이 PLAN/기획과 달라지면 handoff `### 결정` 에 1-2줄. 흐름 안 끊김.
2. **승격(PR 열 때)**: PR 열기 = "이 브랜치 작업 끝" 신호. 이 시점에 `### 결정` 항목을 DECISIONS.md 로 옮기고 `→ DECISIONS` 마킹.
3. **누가**: 작업하는 Claude 에이전트(skill + CLAUDE.md 로 강제). PR 리뷰어가 더블체크(app-chak 에 이미 리뷰 게이트 존재).
4. **write-through 대안은 기각**: 결정 즉시 DECISIONS.md 직접 기록은 안 머지된/버려진 브랜치 결정이 settled 문서를 오염시킴(staging 없음).

---

## 5. Phase 2 — pslog 드리프트 감지 (코드)

### 5.1 파서 변경
- `handoff_parser_service`: `### 결정` 섹션 파싱 → `ParsedHandoff.decisions: list[ParsedDecision]`
  (필드: `external_id`, `text`, `promoted: bool` — `→ DECISIONS`/`→ ADR` 마커 유무).
- pslog 의 watched 파일에 `DECISIONS.md` 추가 — 승격 확인용. `Project` 에 `decisions_path`(기본 `DECISIONS.md`) 컬럼.

### 5.2 새 모델 `Drift`
기존 `error_group` 패턴 그대로 차용.

| 필드 | 설명 |
|---|---|
| `id` | PK |
| `project_id` | FK |
| `type` | `DECISION_NOT_PROMOTED`(A) / `STATUS_CONTRADICTION`(B) |
| `external_id` | 관련 task (nullable — A 는 브랜치 단위일 수 있음) |
| `branch` | 관련 브랜치 |
| `status` | `OPEN` / `RESOLVED` / `IGNORED` |
| `detail` | 사람용 설명 + 고칠 힌트 |
| `opened_at`, `resolved_at` | 라이프사이클 타임스탬프 |

멱등: 같은 (project, type, external_id/branch) 드리프트가 이미 OPEN 이면 중복 생성하지 않음.

### 5.3 감지 (sync_service 평가 단계 추가)
`_process_inner` 가 `_apply_plan`/`_apply_handoff` 후 드리프트 평가를 호출.

- **A — DECISION_NOT_PROMOTED**: 브랜치 handoff 에 `decisions` 항목(특히 `promoted=False`)이 있는데
  그 브랜치 커밋에 `DECISIONS.md` 변경이 없음 → `Drift(A)` OPEN.
  DECISIONS.md 가 올라오면(다음 sync 에서 조건 해소) 자동 RESOLVED.
- **B — STATUS_CONTRADICTION**: handoff `parsed_tasks` 체크박스 상태와 `Task.status` 를
  `external_id` 로 조인해 모순 감지(예: handoff 미체크 / PLAN DONE) → `Drift(B)` OPEN.
  일치되면 자동 RESOLVED.

### 5.4 라이프사이클 (Q5 합의 — 상태형)
- `OPEN` → 다음 sync 에서 조건 사라지면 **자동 RESOLVED**.
- 의도된 불일치는 **수동 IGNORED**(예: 일부러 둔 상태 차이).
- 무상태(push 마다 Discord 핑) 대안은 기각 — 시끄럽고 "아직 안 고친 것" 뷰가 없음.

### 5.5 노출
- **API**: 드리프트 목록 조회 + status PATCH(IGNORED 토글). `log_errors` endpoint 패턴 재사용.
- **프론트**: 대시보드에 "열린 드리프트" 패널(실장님이 "지금 안 맞는 거 N건" 한눈에). `log_errors` 화면 패턴 재사용.
- **Discord**: `notification_dispatcher` 경유 push 요약에 드리프트 줄 추가 + 고칠 힌트.
  예: "⚠️ task-007: handoff 에 결정 있는데 DECISIONS.md 미승격 → 승격하세요" /
  "⚠️ PLAN task-007 DONE 인데 handoff 미완 → 둘 중 하나 맞추세요".

---

## 6. 구현 순서

1. **Phase 1**(컨벤션) 먼저 — 코드 0, 즉시 `app-chak` 에 적용해 #1 해결. Phase 2 의 명세 역할도 함.
2. **Phase 2**(pslog 코드) — Phase 1 이 정한 계약(`### 결정` 포맷, DECISIONS.md 입구)에 맞춰 구현.

---

## 7. 계획 단계에서 확정할 디테일 (본 스펙에서 미결)

1. **A 감지 시점**: push 이벤트만으로 충분한지 vs GitHub PR 이벤트까지 hook 해야 하는지.
   (승격이 "PR 열 때"라 시점 정합 필요. push 누적으로 브랜치 단위 DECISIONS.md 변경 유무를 보는 heuristic 가능성 검토.)
2. **"DECISIONS.md 승격됨" 판정 강도**: 단순 파일 변경 감지 vs task 백링크(`[task-NNN]`) 매칭까지.
   v1 은 가벼운 쪽(파일 변경 + 같은 브랜치)으로 시작 권장.
3. **B 의 조인 정확도**: handoff `parsed_tasks` 와 PLAN task 의 `external_id` 매칭 시
   handoff 서브 체크박스(들여쓰기 2)와 마스터 체크박스(들여쓰기 0) 구분 처리.
4. **Drift detail/힌트 카피** 최종 문구 + i18n 여부.

---

## 8. 합의 로그 (brainstorming 2026-06-14)

- Q1: 기획 1스펙 통합 + 구현 Phase 1 → Phase 2 순. ✅
- Q2: 결정의 집 = DECISIONS.md 단일 입구 + 무거운 것만 옵션 ADR(백링크). ✅
- Q3: 단계형 의식, 포착(handoff `### 결정`) → **PR 열 때** 승격. ✅
- Q4: v1 드리프트 = A(결정 미승격) + B(상태 모순). C(stale) 백로그. D(handoff 누락) 기구현. ✅
- Q5: 상태형 드리프트(OPEN→자동 RESOLVED/수동 IGNORED) + 대시보드 + Discord 힌트. ✅
