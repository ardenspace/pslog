## 목적
이 문서는 “MVP에서 한다/안 한다”를 단호하게 고정한다.
- AI/구현자가 과도하게 기능을 확장하는 것을 방지한다.
- 새로운 기능을 추가하려면 반드시 이 문서를 업데이트한다.

## 현재 스코프 (2026-07 갱신)

아래 MVP 절(2026-02)은 시점 기록으로 보존한다. MVP는 완료됐고, 이후 다음 확장이
설계서를 거쳐 In Scope로 승격·구현 완료됐다. 각 확장의 상세 범위는
`docs/superpowers/specs/` 의 해당 설계서가 기준이다:

- **드래그앤드롭 상태 변경** — MVP 제외 항목이었으나 이후 구현 (dnd-kit)
- **Discord 요약 리포트 + 알림 dispatcher** — `2026-05-01-phase-6-discord-notifications-design.md`
- **git 연동** — PLAN.md/handoff ingest, push webhook, git settings(레포/PAT) — `2026-04-26-ai-task-automation-design.md`
- **로그 수집 파이프라인** — ingest 토큰, rate limit, 에러 핑거프린트 그룹핑(Errors 탭), 헬스 체크 — `2026-04-26-error-log-design.md` 시리즈
- **드리프트 감지** — PLAN/handoff/DECISIONS 정합성 위반 감지(Drifts 탭) — `2026-06-14-decision-truth-loop-design.md`

### 여전히 하지 않음 (2026-07 기준 Out of Scope)
- 댓글 UI/멘션/알림(이메일/푸시) — Comment 모델만 존재, API/UI 없음 (DECISIONS 2026-02-04)
- 파일 첨부
- 고급 권한(리소스별 세분화된 액션, 정책 엔진)

---

## MVP에 포함(In Scope) — 2026-02 시점 기록
### 인증/접근
- 회원가입/로그인/로그아웃/내 정보(me)

### 핵심 도메인
- 프로젝트 단위로 태스크 생성/조회/상태 변경
- Kanban 보드(메인): 4컬럼(todo/doing/done/blocked)
- “내 태스크만” 토글(개인 업무 동시 지원)
- (보조) Week 탭: 주간 기준 조회(최소)

### 공유
- 공유 링크 생성(권한자만)
- 공유 링크로 Kanban read-only 열람(로그인 불필요)

### 권한/정책(최소)
- Owner/Editor/Viewer 구분
- Viewer(내부/외부)는 절대 변경 불가
- 편집 UI는 권한에 따라 노출/비활성 제어

### 이벤트 로그
- task.created, task.status_changed 저장

## MVP에서 제외(Out of Scope) — 2026-02 시점 기록
(“하지 않음”이 확정된 항목이었으나, 일부는 이후 승격됨 — 위 “현재 스코프” 참조)
- 드래그앤드롭으로 상태 변경(초기엔 드롭다운/버튼)
- 댓글/멘션/알림(이메일/푸시)
- 파일 첨부
- 고급 권한(리소스별 세분화된 액션, 복잡한 정책 엔진)
- 다중 워크스페이스 고급 UX(초기 단순화)
- Discord 리포트(원하면 MVP+1로)

## MVP+1 후보(Next) — 2026-02 시점 기록
- ~~Drag & Drop~~ → 구현 완료
- ~~Discord 요약 리포트(주간 done/blocked)~~ → 구현 완료
- 간단 코멘트(태스크당 1줄 로그) → 미구현 (Comment 모델만 존재)