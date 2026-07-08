# 준비 문서 템플릿 + 위치

## 위치 — task별 폴더 (고정 파일명)

```
docs/tasks/task-007/spec.md   docs/tasks/task-007/plan.md   ← (deep)
docs/tasks/task-012/brief.md                                ← light
```

루트(`docs/tasks`)는 프로젝트 설정으로 다를 수 있음. feature 단위 큰 문서는 별개.

## ① light → brief.md (한 장, 항상)

```
# task-012 brief — 버튼 색 토큰 교체
- 왜: design-system 토큰 통일 (PLAN task-012)
- 무엇: Button 계열 hardcoded color → theme 토큰. (out: 레이아웃 안 건드림)
- 완료조건(DoD): ☐ 모든 Button 토큰 사용  ☐ 다크모드 회귀 없음
- 영향파일: `theme.ts`, `Button.tsx`
- 검증: pnpm typecheck
- 남길 테스트: 없음 — 순수 토큰 교체, typecheck 가 회귀 커버 (자동화 가능분 있으면 T# 로 명시)
```

## ② heavy → spec.md (설계, 먼저)

> heavy일 때 brief.md는 작성하지 않음 — spec.md의 배경/목표가 brief를 대체.

```
# task-007 spec — 결제 웹훅 재시도   확정일: YYYY-MM-DD
1. 배경/문제      — 왜 필요, 지금 뭐가 깨지나
2. 목표 / 비목표  — YAGNI 명시
3. 설계(안)       — 핵심 접근 (백엔드면 데이터모델·계약 / UI면 화면·플로우)
4. 대안 & 결정 ★  — 트리거② 가 heavy 이유 → 대안 비교 후 결정. PR 때 DECISIONS.md로 승격
5. 영향/리스크    — 트리거① 계약 변경 범위, migration, 롤백
6. 의존/사인오프  — 트리거③ 누가 이 결과물 기다리나 + 역할 게이트
```

## ③ heavy → plan.md (실행, spec 다음)

```
# task-007 plan   (spec: ./spec.md)
- 아키텍처 한 줄 + 파일 구조(신규/수정)
- Step 분해:  - [ ] Step 1 …  - [ ] Step N: 회귀 테스트 (T1~Tn 중 자동화 가능분 — 없으면 이유 명시)
- 각 Step 검증 명령 + 롤백
```

완료조건(DoD)은 모든 문서에서 1급 필드 — brief엔 `완료조건(DoD)` 항목으로, spec엔 목표(§2)의
완료 기준으로, plan엔 각 Step 검증으로 반드시 드러난다. 모든 문서 제목에 task-NNN 백링크.
`남길 테스트`/회귀 Step 도 1급 — brief 의 `남길 테스트` 필드, plan 의 마지막 Step 으로 항상 존재한다.
