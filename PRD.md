## 1. 문서 목적
이 문서는 pslog의 MVP를 구현/확장할 때 방향성을 잃지 않기 위한 “진실의 원천(Source of Truth)”이다.
- 구현자는 이 문서를 먼저 읽고, 모호한 부분은 DECISIONS.md 또는 Open Questions를 확인한다.
- MVP 범위는 SCOPE.md가 최종 기준이다.

> **2026-07 업데이트** — 이 문서는 MVP(2026-02) 시점의 기준 문서이며, MVP는 완료됐다.
> 이후 제품은 아래 한 문장 정의를 넘어 확장됐고, **MVP 이후 기능의 source of truth는
> `docs/superpowers/specs/` 의 설계서 시리즈와 DECISIONS.md** 다.
> 확장 스코프 요약은 SCOPE.md “현재 스코프”, 구현 현황은 README “구현 상태” 참조.

## 2. 제품 한 문장 정의

**MVP (2026-02):** pslog는 팀(워크스페이스)에서 태스크를 등록하고, Kanban 보드(메인)와 Week 뷰(보조)로 진행을 공유하며, 필요 시 외부에 Viewer 권한으로 읽기 전용 공유 링크를 제공하는 협업 태스크 툴이다.

**현재 (2026-07):** 위에 더해, 추적 대상 repo의 `PLAN.md`(체크박스 task = source of truth)·`handoffs/{branch}.md`·git push webhook·앱 로그를 ingest 해 태스크 대시보드, 계획↔실제 드리프트 감지, 에러 로그 그룹핑, Discord 알림을 제공하는 **개발팀 협업 툴**이다.

## 3. 목표/우선순위
### 3.1 1차 목표(학습)
- Backend: Router → Service 분리, 권한 체크, 이벤트 로그, 공유 링크(토큰), 표준 응답/에러 패턴
- Frontend: 인증 상태 분리(Zustand), 서버 상태 관리(리스트/변경), 역할 기반 UI 제어(read-only)

### 3.2 2차 목표(실사용)
- 팀원들이 “개인 할 일 등록”과 “팀 진행 공유”를 최소 1주간 실제로 사용해보게 한다.

## 4. 성공 지표(완료 정의)
### 4.1 계량 지표(1주)
- 팀원 2~3명 로그인
- 사용자당 태스크 5개 이상 생성
- 상태 변경 이벤트 10회 이상 발생(doing/done 포함)
- 공유 링크로 보드 열람 3회 이상

### 4.2 품질 지표
- Viewer(내부/외부)에서 편집 동작이 불가능하고 UI도 read-only로 일관되게 보임
- 권한 에러가 “조용히 실패”하지 않고 명확한 에러/가드로 처리됨
- 태스크 상태 변경 시 TaskEvent가 누락 없이 기록됨

## 5. 사용자/권한 모델
### 5.1 사용자 유형
- 내부 사용자(로그인): 워크스페이스 멤버
- 외부 사용자(비로그인): 공유 링크 접속자(항상 read-only)

### 5.2 역할(Role)
- Owner: 워크스페이스/프로젝트 관리, 멤버 관리, 공유 링크 생성/철회, 태스크 편집
- Editor: 태스크 생성/수정/상태 변경, (정책에 따라) 공유 링크 생성 가능할 수 있음
- Viewer: 읽기 전용(태스크/보드/주간 조회만)

### 5.3 권한 규칙(최소)
- Viewer: 어떤 경우에도 데이터 변경 불가
- 공유 링크 사용자: 항상 Viewer와 동일
- 편집 가능 조건은 “역할 + 리소스 범위(워크스페이스/프로젝트)”에 의해 결정

## 6. 문제/가치(사용자 관점)
- 팀 진행이 흩어져 있으면 “지금 뭐가 막혔는지/누가 뭘 하는지” 파악이 느리다.
- 개인은 “내 할 일”을 한 곳에서 관리하고 싶지만, 팀은 “전체 흐름(상태)”을 먼저 본다.
- 외부 공유는 계정을 만들기 전에 빠르게 ‘현황’을 보여줄 수 있어야 한다.

## 7. 핵심 사용자 시나리오(End-to-End)
### 7.1 내부(팀원) 메인 시나리오
1) 로그인
2) 프로젝트 선택
3) 태스크 생성(기본 assignee=나, 기본 status=todo)
4) 진행하면서 status 변경(todo→doing→done, 필요 시 blocked)
5) 보드에서 팀 진행 확인(컬럼별로 병목/대기 파악)
6) “내 태스크만” 토글로 개인 집중 모드

### 7.2 외부(공유 링크) 시나리오
1) Owner(또는 정책상 허용된 Editor)가 공유 링크 생성
2) 링크 전달
3) 외부 사용자는 로그인 없이 프로젝트 보드를 read-only로 열람

## 8. 정보구조(IA) / 라우팅(개념)
- /login, /register
- /app (로그인 필요)
  - 상단: workspace/project 선택
  - 탭: Board(기본) / Week
- /share/:token (공개, read-only)

## 9. 화면 명세
### 9.1 Dashboard > Board(Kanban) [MVP 메인]
#### 구성
- 상단
  - Project 선택(초기 단순 드롭다운 가능)
  - 토글: “내 태스크만”
  - (선택) 필터: assignee(전체/특정)
  - 버튼: “새 태스크”
  - 버튼: “공유 링크”(권한자만)
- 본문: 4 컬럼
  - To do / Doing / Done / Blocked
- 카드 최소 표시
  - title(필수)
  - assignee(표시/필터의 기준)
  - due_date(있으면)
  - updated_at(선택)

#### 상호작용(MVP)
- 카드 클릭 → 상세 패널/모달(제목/설명/담당/기한/상태)
- 상태 변경은 드래그앤드롭이 아니라 “드롭다운/버튼”으로 처리
- Viewer는 모든 편집 UI 비활성화 + “읽기 전용” 배지

#### 빈 화면/에러
- 태스크 없음: “첫 태스크를 만들어보세요”
- 권한 없음: “읽기 전용입니다” 안내

### 9.2 Dashboard > Week(보조)
- 동일 태스크를 week_start 기준(월요일 시작)으로 묶어 리스트/간단한 주간 영역에 표시
- “내 태스크만” 토글은 Board와 동일한 의미로 동작
- MVP에서는 Week에서 편집까지 필수는 아니며, 최소는 조회

### 9.3 Share View (/share/:token) [Public, read-only]
- 프로젝트명/마지막 업데이트
- Kanban 보드 read-only 표시
- (선택) “팀 멤버라면 로그인” 링크 제공

## 10. 기능 명세
### 10.1 Task
#### 필드(최소)
- id
- project_id
- title (필수)
- description (옵션)
- status: todo|doing|done|blocked (필수)
- assignee_id (권장, 기본=생성자)
- due_date (옵션, 날짜)
- created_at, updated_at

#### 규칙
- 기본 assignee: 생성자
- due_date 의미: 마감일(Week 뷰에서 주간 포함 여부 판단에 사용)

### 10.2 Board(조회/필터)
- 프로젝트 단위로 태스크 목록 조회
- 목록은 status로 그룹핑되어 렌더링
- 필터
  - 내 태스크만(assignee=current_user)
  - assignee 지정(선택)

### 10.3 ShareLink(읽기 전용 공유)
- 토큰 기반으로 프로젝트 보드를 공개
- 공개 범위: 프로젝트 보드 read-only
- 만료 정책은 DECISIONS.md에서 결정(기본은 MVP에서 “없음” 권장)

### 10.4 TaskEvent(활동 로그)
- 기록 대상(최소)
  - task.created
  - task.status_changed(from,to)
- MVP에서 UI로 노출하지 않아도 저장은 필수(학습 포인트)

## 11. API 설계(개념, 최소)
(구체 endpoint 목록은 BACKLOG 또는 구현 문서에서 확정)
- Auth: register/login/me
- Projects: list/create
- Tasks:
  - list (project_id 기준, optional filters)
  - create
  - patch/update status/assignee/due_date
- Share:
  - create share link
  - read-only fetch by token

표준 응답/에러 포맷은 프로젝트 규칙(예: {data, message} / {detail, code})을 따른다.

## 12. Open Questions(결정 완료)

모든 Open Questions가 해결되었습니다. 결정 내용은 DECISIONS.md를 참조하세요.

- ~~ShareLink 만료 정책~~ → 30일 고정 + 수동 철회
- ~~Editor 공유 권한~~ → Owner만 생성/철회 가능
- ~~Week 뷰 범위~~ → due_date 기준 + "마감일 미지정" 그룹
- ~~Blocked 해제 규칙~~ → 단순 상태 (자유 변경)