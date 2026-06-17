# pslog-workflow — 구현 단계 + 결정 승격 설계서

작성일: 2026-06-17
상태: 초안 (brainstorming 합의 완료, 사용자 리뷰 대기)
관련 설계서:
- `2026-06-17-pslog-workflow-design.md` (부모 — 무게 게이트 / brief·spec·plan / 추적 레이어. 본 설계는 그 **5·6번 "구현·마무리" 단계를 구체화**)
- `2026-06-14-decision-truth-loop-design.md` (Drift 모델 / OPEN→RESOLVED→IGNORED / DECISIONS 승격 — 본 설계가 승격 타이밍을 명확히 함)
- `2026-05-01-phase-6-discord-notifications-design.md` (notification dispatcher — 본 설계의 미승격 결정 알림이 재사용)

---

## 1. 배경 / 문제

부모 설계서는 `task → brief|spec→plan → 코드` 흐름의 **준비 문서(brief/spec/plan)** 까지는 촘촘히 정의했지만,
**plan 이후 "구현"(SKILL.md 5번)과 "마무리"(6번)는 한 줄로 얇게 남겼다.** heavy task 는 정의상 할 게 많은데
(계약 변경·설계 분기·교차 의존), plan 승인 후 코드까지가 사실상 "에이전트가 알아서 쭉 짠다"의 블랙박스다.

두 가지를 채운다:
1. **구현 규율** — plan 의 Step 들을 어떤 입자도로 진행하고, 어디서 무엇으로 검증하는가.
2. **결정 승격 타이밍** — 구현 중 생기는 결정을 *언제* 기록하고 *언제* DECISIONS.md 로 굳히는가.
   특히 PR 이 멀거나(오래 가는 브랜치) 모호할 때(dev vs main) 생기는 **공유 사각지대**를 어떻게 메우나.

---

## 2. 목표 / 비목표

### 2.1 목표
- 구현 단계를 **"step별 값싼 확인 + 끝에 숲 검증"** 2층으로 정의해, 누적붕괴는 막고 "나무만 보는" 함정은 피한다.
- 결정의 **기록(capture)** 과 **승격(promote)** 을 분리하고, 승격 트리거를 모호함 없이 정의한다.
- **공유는 handoff(실시간) / 정전화는 DECISIONS.md(land 시)** 라는 두 채널 역할을 명시한다.
- 오래 묵는 미승격 결정을 **pslog 가 감지·알림**(Drift 재사용)해 공유 사각지대를 메운다.

### 2.2 비목표 (YAGNI)
- step 별 단위 테스트 *작성* 강제 — 끝에 e2e/의미있는 테스트로 충분(§3).
- step 별 코드 리뷰 — 리뷰는 숲(전체 diff)을 봐야 가치가 나옴(§3).
- 승격을 PR 보다 일찍 당기기 — 안 굳은 결정을 정전화하면 거짓 기록이 됨(§4).
- pslog 가 DECISIONS.md 를 직접 수정(write-back) — read-only 유지. 감지·알림만.

---

## 3. 구현 단계 — 검증 2층 모델

### 3.1 "숲 vs 나무" 원칙
plan 은 완성될 퍼즐, 각 Step 은 *미완성 퍼즐 조각*이다. 조각 단위로 무거운 의식(단위 테스트 작성·코드 리뷰)을
걸면 **조각만 보고 전체 응집을 못 보는** 함정에 빠지고, 나중에 리팩터될 내부에 대한 테스트는 버려지는 작업이 된다.
반대로 **아무 확인도 안 하면** step 3 의 깨짐을 step 7 후 e2e 에서야 발견 → 누적된 코드 위에서 디버깅(건초더미).

→ 두 층으로 분리한다:

| 시점 | 하는 것 | 성격 |
|---|---|---|
| **각 Step 끝** | typecheck / build / import 통과만 (값싼 tripwire) | "땅 안 꺼졌나" — 테스트 *작성* 아님, 판단 아님 |
| **구현 전체 끝** | 코드 리뷰 + 수정 → e2e(또는 DoD 검증 명령) | "퍼즐 완성됐나" — 숲을 봄 |

- step 별엔 **테스트를 짜는 게 아니라 안 깨졌나 보는** 싸구려 확인만. (typecheck 류는 수 초)
- 리뷰·e2e 는 **전체 diff 를 놓고 한 번** — 응집/일관성은 퍼즐이 다 맞춰진 뒤에만 보인다.
  마침 `/code-review`·`/review` 류가 *브랜치 diff 전체*를 보게 설계됨(조각 아닌 숲).

### 3.2 검증 강도와 무게 게이트
- **heavy** task(계약 변경·설계 분기·교차 의존)는 *남이 의존하는 코드*라 끝의 e2e/리뷰가 본전 뽑는 자리 — 필수.
- **light** task 는 brief 의 DoD 검증(예: `pnpm typecheck`) 한 줄로 충분 — 끝의 무거운 e2e 불요.
- step별 tripwire 는 둘 다 공통(어차피 싸다).

### 3.3 SKILL.md 5·6번 개정문 (반영 대상)
> **5. 구현** — plan 의 Step 을 하나씩 따라 코드. **각 Step 끝 build/typecheck 로 안 깨졌나만 확인**(테스트 작성·리뷰 아님).
> 전체 끝나면 → **코드 리뷰 + 수정 → e2e(또는 DoD 검증 명령) 통과**. 진행하며 handoff 오늘 날짜 섹션 갱신.
> 구현 중 plan 과 달라진 결정은 **그때그때 handoff `### 결정`에 기록**(코드 리뷰에서 나온 결정도 여기로).
> **6. 마무리** — git push **직전 반드시 handoff commit**. task 끝나면 PLAN.md `[task-NNN]` 체크.
> **PR(공유 브랜치 첫 land) 때 spec·handoff 의 굳은 결정을 DECISIONS.md 로 승격**(§4, decision-truth-loop 와 물림).

---

## 4. 결정 — 기록 / 승격 분리

### 4.1 두 동작을 나눈다
| 동작 | 뜻 | 시점 |
|---|---|---|
| **기록 (capture)** | 결정을 그 자리에 적어둠 | 결정이 *생기는 순간* (계속) |
| **승격 (promote)** | DECISIONS.md 에 정전(canonical)으로 올림 | **공유 브랜치 첫 land(PR) 때 한 번** |

### 4.2 기록 — 결정이 생기는 두 자리
1. **spec 작성 시** — heavy task `spec.md` 의 "④ 대안 & 결정 ★" (설계-시점 결정).
2. **구현 중** — plan 대로 짜다 달라진 선택을 handoff `### 결정`에 그때그때.
   - ← §3 의 **코드 리뷰 단계에서 나온 결정**도 여기로 흡수(리뷰가 구현과 PR 사이에 있으므로 자연스럽다).

### 4.3 두 채널의 역할 — handoff=실시간 / DECISIONS=정전
| | handoff `### 결정` | DECISIONS.md |
|---|---|---|
| 상태 | **in-flight** (OPEN, 뒤집힐 수 있음) | **settled** (RESOLVED, 굳음) |
| 공유 | **push 할 때마다 = 계속** | land 때 한 번 |
| 노출 | 커밋된 handoff + **pslog 가 ingest → 대시보드/Discord** | 프로젝트 영구 기억 |

**핵심: 실시간 공유 채널은 DECISIONS.md 가 아니라 handoff 다.** pslog 의 본업이 handoff 를 읽어 팀에 노출하는 것
이고(부모 §1), SKILL 6번이 "push 직전 handoff commit" 이므로 — **구현 중 결정을 handoff 에 적고 push 하는 순간
이미 팀에 공유된다.** DECISIONS.md 는 *굳은 것만* 보관하는 아카이브이지 실시간 공유 채널이 아니다.

### 4.4 승격 트리거 — "공유 브랜치 첫 land"
"main 에 PR" 이 아니라 **"이 작업이 *공유 통합 브랜치*에 처음 land 되는 순간"** 으로 정의한다(dev/main 모호함 해소):
- 팀이 dev 에서 통합 → **dev 로의 PR 이 승격 트리거.**
- 이후 dev→main PR 때는 이미 DECISIONS.md 에 있으니 **중복 승격 안 함**(멱등).
- 즉 "정전화 = 공유 히스토리에 처음 합쳐지는 순간" 한 규칙.

### 4.5 왜 PR 보다 일찍 당기지 않나
spec 에서 A 안으로 정했어도 구현 중 A 가 안 돼 B 로 갈아탈 수 있다. spec/플로우 중간에 DECISIONS.md 로 박으면
**뒤집혔을 때 정전 기록이 거짓말**이 된다. land = 결정이 굳은 시점이라 decision-truth-loop 의 `OPEN→RESOLVED`
와 정확히 맞물린다.

---

## 5. 공유 사각지대 대처 — 오래 묵는 미승격 결정 감지

PR 이 멀어도 결정은 §4.3 대로 handoff push 로 *이미 흐른다*. 그래도 "한 브랜치에서 너무 오래 작업 → 중요한
결정이 handoff 에 묻혀 아무도 주목 안 함" 위험이 남는다. 이는 **승격을 앞당겨 풀 게 아니라 pslog 추적 레이어가
띄울 일** — 부모 §7 의 `TASK_NOT_PREPARED` 와 동일 패턴으로 **Drift 타입 하나 추가**.

### 5.1 신규 Drift `STALE_DECISION` (가칭)
- 트리거(개략): 어떤 브랜치의 handoff `### 결정`에 **OPEN(미승격) 결정이 오래/많이 머묾**.
- 라이프사이클: 해당 결정이 DECISIONS.md 로 승격되면 다음 sync 에서 **자동 RESOLVED** / 의도적이면 **수동 IGNORED**.
- 멱등: 같은 (project, type, external_id=branch 또는 결정 키) 이 이미 OPEN 이면 중복 생성 안 함.
- 노출: 기존 드리프트 대시보드 패널 + `notification_dispatcher` Discord 힌트 재사용.
  예: "⚠️ feat/task-007 에 미승격 결정 3개가 12일째 — 중간 공유나 PR 분할 검토."
- read-only 원칙 유지 — 감지·알림만, repo·DECISIONS.md 수정 없음.

### 5.2 감지 위치
부모 §7.4 와 동일하게 `sync_service._process_inner` 의 드리프트 평가 단계. handoff 파서가 이미 `### 결정`
섹션과 브랜치별 상태를 알고, push 이벤트 타임스탬프로 "머문 기간"을 잰다.

---

## 6. 구현 순서

1. **Phase A — 스킬 개정 (pslog 코드 0)**
   - `pslog-workflow` SKILL.md 5·6번을 §3.3 개정문으로 교체(검증 2층 + 결정 기록/승격 명시).
   - 필요 시 `references/` 에 "구현 단계 체크리스트"(tripwire / 끝-리뷰-e2e / 결정 기록 위치) 보강.
2. **Phase B — pslog 추적 코드 (`STALE_DECISION`)**
   - Drift 타입 `STALE_DECISION` 추가 + 판정 규칙을 `sync_service` 에 추가.
   - 대시보드 패널 + Discord 힌트(기존 인프라 재사용).
   - (부모 Phase 2 의 `TASK_NOT_PREPARED` 와 함께 가도 됨 — 같은 평가 단계.)

---

## 7. 계획 단계에서 확정할 디테일 (본 스펙 미결)

1. **"오래/많이" 임계치**(§5.1): N일 / OPEN 결정 N개 — 둘 중 무엇을, 기본값 얼마로. v1 은 가벼운 쪽(일수 기반 단일 기준) 권장.
2. **"미승격" 판정 방법**(§5): handoff `### 결정` 항목이 DECISIONS.md 에 들어갔는지 매칭하는 기준(텍스트/키/링크).
3. **"공유 브랜치" 식별**(§4.4): 프로젝트 설정으로 통합 브랜치명(`dev`/`main` 등)을 둘지, 아니면 "첫 PR 머지"를 generic 하게 볼지.
4. **step tripwire 명령**(§3.1): 프로젝트별 typecheck/build 명령을 어디서 읽나(plan 의 Step 검증 명령 재사용 권장).
5. **Drift detail/힌트 카피** 최종 문구 + i18n.

---

## 8. 합의 로그 (brainstorming 2026-06-17)

- 구현 입자도: plan 의 Step 단위로 진행하되 step별 단위테스트·리뷰는 **안 함**(숲 vs 나무). ✅
- 검증 2층: **각 Step 끝 = 값싼 tripwire(typecheck/build) / 전체 끝 = 코드 리뷰 + e2e**. ✅
- superpowers executing-plans 하드 의존 **기각** — 패턴만 self-contained 흡수("설치 한 번이면 끝" 정체성 유지). ✅
- 결정: **기록(handoff/spec, 계속) / 승격(DECISIONS.md, land 시) 분리**. ✅
- 실시간 공유 채널 = **handoff push**(pslog 가 ingest), DECISIONS.md = 굳은 것만 보관. ✅
- 승격 트리거 = **"공유 통합 브랜치 첫 land"**(dev/main 모호함 해소, 멱등). ✅
- PR 보다 일찍 승격 **안 함** — 안 굳은 결정 정전화 = 거짓 기록. ✅
- 공유 사각지대 = 승격 당기지 말고 **`STALE_DECISION` Drift 로 감지·Discord 알림**(read-only). ✅
