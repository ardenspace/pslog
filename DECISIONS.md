## 목적
이 문서는 “논의 끝에 확정된 결정”을 기록한다.
- 구현/기획 변경 시 반드시 여기에 남긴다.
- PRD의 문장보다 이 문서의 결정이 우선한다(충돌 시 DECISIONS 우선).

## 결정 로그

### 2026-07-02 — 플러그인 훅은 POSIX sh, 외부 런타임 의존 금지
- 결정: pslog-workflow 플러그인의 훅 스크립트는 POSIX sh로 작성한다 (node 등 런타임 요구 금지). SessionStart matcher는 `startup|resume|clear|compact` 전부 — 컴팩션/clear 후에도 컨텍스트 재주입.
- 이유: 마켓플레이스 배포물은 설치자 환경을 가정할 수 없고, 컴팩션 후 라우팅 안내 유실은 실제 사용성 문제.
- 영향: 훅 로직이 sh로 표현 안 될 만큼 커지면 런타임 추가 대신 설계를 재고한다.

### 2026-07-02 — 프로젝트 코딩 rules는 CLAUDE.md 상주가 아닌 스킬로
- 결정: frontend/backend 코딩 규칙은 `.claude/skills/<name>/SKILL.md` 스킬로 관리 — 해당 영역 코드를 만질 때만 온디맨드 로드.
- 이유: CLAUDE.md 상주 참조는 매 세션 고정 토큰 비용. 기존 루스 `.md`는 하네스가 아예 안 읽는 죽은 파일이었음.

### 2026-07-02 — 테스트에 고정 달력 날짜 금지 (log_events 파티션)
- 결정: log_events 관련 테스트의 `received_at`은 `utcnow()` 기준 상대 시각만 사용한다.
- 이유: 파티션이 migration 실행일 +30일만 pre-create — 고정 날짜는 시한폭탄 (5월 고정 날짜 테스트가 7월에 파티션 없음으로 사망).

### 2026-02-04 — 학습 우선순위
- 결정: 이 프로젝트의 1차 성공 기준은 “설계 패턴 학습”이다. 실사용은 보너스이며, 다만 MVP는 팀원이 실제로 써볼 수 있는 최소 흐름을 포함한다.
- 이유: 시작 동기(학습)를 훼손하지 않으면서도 실제 피드백 루프를 만들기 위함.
- 영향:
  - 완성도보다 수직 슬라이스/구조(서비스 레이어, 권한, 이벤트, 공유링크)를 우선한다.

### 2026-02-04 — MVP 메인 화면은 Kanban
- 결정: MVP의 메인 화면은 Kanban(Board)이며 Week는 보조 탭이다.
- 이유: 팀 진행 공유(상태 기반)가 우선이고, 개인 업무는 “내 태스크만” 필터로 같이 본다.
- 영향:
  - Kanban에서 최소 편집(생성/상태 변경)이 가능해야 한다.
  - Week는 MVP에서 조회 중심으로 단순화 가능.

### 2026-02-04 — 외부 공유는 Viewer(read-only) 고정
- 결정: 공유 링크로 접근한 외부 사용자는 무조건 Viewer(읽기 전용)이다.
- 이유: 로그인 없이도 안전하게 현황 공유를 가능하게 하기 위함.
- 영향:
  - 공유 페이지에는 편집 UI가 존재하지 않거나 비활성화되어야 한다.

### 2026-02-04 — 상태 모델은 4단계
- 결정: Task status는 todo/doing/done/blocked 4단계를 사용한다.
- 이유: 팀 진행 공유에서 “막힘(blocked)”은 핵심 신호다.
- 영향:
  - 보드는 4컬럼 고정(추후 확장 가능하나 MVP는 고정)

### 2026-02-04 — ShareLink 만료 정책: 30일 고정 + 수동 철회
- 결정: 공유 링크는 생성 시 기본 30일 후 만료되며, Owner는 언제든 수동 철회 가능하다.
- 이유: 보안(무기한 링크 방지)과 단순함(옵션 없음)의 균형.
- 영향:
  - expires_at은 생성 시 자동으로 30일 후로 설정.
  - is_active = false로 설정하여 철회.

### 2026-02-04 — 권한 체계는 Workspace + Project 이중 구조
- 결정: Workspace 레벨과 Project 레벨 모두에서 권한(role)을 부여할 수 있다.
- 이유: 프로젝트별로 세분화된 권한 제어가 필요.
- 영향:
  - ProjectMember 테이블에 role 필드 추가 필요.
  - 권한 체크 로직이 두 레벨을 모두 확인해야 함.

### 2026-02-04 — 권한 상속 규칙: Project 우선
- 결정: Project 레벨 멤버십이 있으면 그 role을 사용하고, 없으면 Workspace 레벨 role을 상속한다.
- 이유: 특정 프로젝트에 대해 Workspace와 다른 권한을 부여할 수 있어야 함.
- 영향:
  - `get_effective_role(user, project)` 로직 구현 필요.

### 2026-02-04 — ShareLink에 created_by 필드 추가
- 결정: 공유 링크 생성자를 추적하기 위해 created_by 필드를 추가한다.
- 이유: 감사/추적 목적.
- 영향:
  - ShareLink 모델에 created_by (FK → User) 추가 필요.

### 2026-02-04 — ShareLink.scope는 PROJECT_READ만 사용
- 결정: MVP에서 ShareLink의 scope는 project_read만 사용한다.
- 이유: 개별 태스크 공유(task_read)는 MVP 범위 외.
- 영향:
  - task_read enum은 제거하거나 미사용으로 둠.

### 2026-02-04 — Comment 모델 유지 (MVP API/UI 미구현)
- 결정: Comment 모델은 DB 스키마에 유지하되, MVP에서는 API/UI를 구현하지 않는다.
- 이유: DB 스키마 안정성 유지, MVP+1 대비.
- 영향:
  - 마이그레이션 불필요, API만 MVP+1에서 추가.

### 2026-02-04 — Task.reporter_id 유지
- 결정: 태스크 생성자(reporter)를 기록하는 reporter_id 필드를 유지한다.
- 이유: 누가 태스크를 만들었는지 추적 가능, 향후 필터/통계에 유용.
- 영향:
  - 태스크 생성 시 reporter_id = current_user로 자동 설정.

### 2026-02-04 — 공유 링크 생성 권한: Owner만
- 결정: 공유 링크 생성/철회는 Owner만 가능하다.
- 이유: MVP 단순화. 복잡한 권한 위임 체계는 불필요.
- 영향:
  - API에서 owner 권한 체크 필수.
  - UI에서 owner가 아니면 공유 버튼 숨김.

### 2026-02-04 — Week 뷰 범위: due_date + "마감일 미지정" 그룹
- 결정: Week 뷰는 due_date 기준으로 표시하되, 마감일 없는 태스크는 별도 "미지정" 섹션에 표시한다.
- 이유: 마감일 없는 태스크도 가시성 유지.
- 영향:
  - API는 due_date 필터 + due_date IS NULL 두 가지 쿼리 필요하거나, 클라이언트에서 분리.

### 2026-02-04 — Blocked 상태: 단순 상태
- 결정: Blocked는 다른 상태(todo/doing/done)와 동일하게 자유롭게 변경 가능하다.
- 이유: MVP 단순화. 해제 사유 입력 등의 추가 규칙 없음.
- 영향:
  - 상태 변경 시 특별한 validation 없음.

### 2026-02-04 — Workspace 자동 생성 정책
- 결정: 회원가입 시 "{name}의 워크스페이스"를 자동 생성한다.
- 이유: MVP 단순화. 사용자가 바로 프로젝트를 만들 수 있게 함.
- 영향:
  - auth_service.register()에서 Workspace + WorkspaceMember(owner) 자동 생성.
  - 사용자는 여러 Workspace에 소속 가능 (초대 통해).
  - UI에 Workspace 드롭다운 유지 (1개면 자동 선택).

### 2026-02-04 — MVP 사용 시나리오: 한 팀이 여러 프로젝트
- 결정: MVP 타겟 시나리오는 "한 팀(2~3명)이 여러 프로젝트를 함께 관리"이다.
- 이유: 실제 사용 목표와 일치.
- 영향:
  - Workspace = 팀 단위, Project = 작업 단위.
  - 팀원 초대는 Workspace 레벨에서 한번만 하면 모든 Project 접근 가능.

### 2026-02-05 — ShareLink.expires_at은 NOT NULL
- 결정: expires_at 필드는 NOT NULL이며, 생성 시 항상 30일 후로 설정된다.
- 이유: 무기한 링크 방지. "30일 고정 만료" 결정과 일관성.
- 영향:
  - DB 스키마에서 expires_at NOT NULL.
  - 생성 API에서 자동으로 now() + 30일 설정.

### 2026-02-05 — "내 태스크만" 필터: 서버 처리
- 결정: "내 태스크만" 필터는 서버에서 처리한다.
- 이유: 확장성. 태스크 수가 많아지면 클라이언트 필터는 비효율.
- 영향:
  - GET /projects/{id}/tasks에 mine_only 쿼리 파라미터.
  - 서버에서 assignee_id = current_user.id 조건 적용.

### 2026-02-05 — 태스크 삭제: MVP 포함 (Owner만)
- 결정: 태스크 삭제 기능을 MVP에 포함한다. Owner만 삭제 가능.
- 이유: 삭제 없이 운영은 비현실적.
- 영향:
  - DELETE /tasks/{id} API 구현.
  - Editor는 삭제 불가, Owner만 가능.
  - UI에서 Owner가 아니면 삭제 버튼 숨김.

### 2026-02-05 — 프로젝트 생성자는 자동으로 Owner
- 결정: 프로젝트 생성 시 생성자가 해당 프로젝트의 Owner가 된다.
- 이유: 생성자가 관리 권한을 갖는 것이 자연스러움.
- 영향:
  - project_service.create()에서 ProjectMember(role=owner) 자동 생성.

### 2026-02-05 — 멤버 초대/관리: MVP 포함
- 결정: Owner가 Workspace에 멤버를 초대하는 기능을 MVP에 포함한다.
- 이유: 팀 협업이 핵심 시나리오.
- 영향:
  - POST /workspaces/{id}/members API 구현 (Owner만).
  - 초대 시 role 지정 가능 (owner/editor/viewer).
  - 초대 UI 구현 필요.