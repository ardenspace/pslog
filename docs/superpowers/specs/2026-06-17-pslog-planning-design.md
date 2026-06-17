# pslog-planning — feature 기획 스킬 설계서

작성일: 2026-06-17
상태: 초안 (brainstorming 합의 완료, 사용자 리뷰 대기)
관련 설계서:
- `2026-06-17-pslog-workflow-design.md` (부모 — 무게 게이트 / task→코드 / 추적. 본 설계는 그 **위쪽(PLAN.md를 만드는) 레이어**)
- `2026-06-17-pslog-workflow-implementation-stage-design.md` (구현 단계 + DECISIONS 승격 — 본 설계의 lock-in 결정이 그 루프로 흘러감)
- `2026-04-26-ai-task-automation-design.md` (PLAN.md 포맷·골디락스 룰 — 분해 산출물의 계약)

---

## 1. 배경 / 문제

pslog-workflow 는 **PLAN.md 의 task 하나를 코드로 옮기는 아래쪽 절반**을 정의했다. 하지만 그 **PLAN.md(task 들)가 무엇을 기준으로 나오는지 = 기획 레이어는 비어 있다.**

현재 PLAN.md 작성 근거(`ai-task-automation` §6.1·§11.1)는 **형식·입자도 규약뿐**이다: "스프린트 시작 시 사람이 수동 작성", 골디락스 0.5~3일, `[task-NNN]`, `@user`, backtick 경로. *무엇을* task 로 쓸지(어떤 제품 요구/기획에서)는 "사람이 알아서"가 전제다.

app-chak 의 실제 흐름을 역설계하면 그 다리가 보인다:
```
gstack 파이프라인(~/.gstack/, repo 밖) → 실행계획.md(docs/, 사람이 정착·정리) → PLAN.md(사람이 18 task 로 분해)
```
즉 feature 기획은 **외부 도구(gstack)로 했고, 실행계획→PLAN 분해는 사람이 손으로** 했다. 이 두 과정이 **반복 가능한 의식으로 정의돼 있지 않다.**

**본 설계 = 그 위쪽 절반을 pslog-workflow 플러그인 *자체* 스킬(`pslog-planning`)로 채운다.**

---

## 2. 목표 / 비목표

### 2.1 목표
- 아이디어 → 기획 → 설계 → `실행계획.md` → `PLAN.md` task 분해까지를 **단일 스킬의 의식**으로 정의.
- **기술적으로 탄탄한** 기획을 강제 (app-chak E1~E14 수준의 구체적 lock-in 결정).
- 분해 산출물이 부모 PLAN.md 계약(골디락스·`[task-NNN]`·lane·백링크)과 정합 → pslog-workflow 로 매끄럽게 핸드오프.

### 2.2 비목표 (YAGNI)
- **외부 스킬 의존 — 절대 금지.** gstack·superpowers 는 *영감*일 뿐(forcing-function 패턴 참고). pslog-workflow 플러그인은 **자급자족**: 능력은 번들 스킬로 추가한다(`skills/*/SKILL.md` 자동 발견). → 메모리 `pslog-workflow-self-contained` 참조.
- gstack 의 멀티 에이전트 리뷰 *기계* 복제 — 대신 forcing function 으로 같은 *출력 모양*을 낸다(§5).
- task별 `spec.md`/`plan.md` 생성 — 그건 pslog-workflow 의 task 고도 일. 본 스킬은 feature 고도.

---

## 3. 아키텍처 — 2-스킬, 두 고도

pslog-workflow 플러그인은 **스킬 2개**를 번들한다 (`plugin.json` 에 skills 배열 없음 → `skills/` 자동 발견):

```
plugins/pslog-workflow/skills/
  pslog-planning/  SKILL.md     ← 신규. feature 고도. PLAN.md 를 *만드는* 위쪽 절반.
  pslog-workflow/  SKILL.md     ← 기존. task 고도. PLAN.md 를 *소비하는* 아래쪽 절반.
```

| 스킬 | 입력 | 출력 | "spec/plan"의 뜻 |
|---|---|---|---|
| **pslog-planning** | feature 아이디어 1개 | `실행계획.md` + `PLAN.md`(task 들) | feature *전체* 설계 + 스프린트 task 목록 |
| **pslog-workflow** | PLAN.md 의 task 1개 | `task-NNN/{spec,plan,brief}.md` → 코드 | 그 task *하나*의 설계·실행 |

- 두 스킬은 **PLAN.md 에서 핸드오프.** 겹치지 않음.
- **용어 함정 주의**: feature `실행계획`(≈ feature spec) ≠ task별 `spec.md`. `PLAN.md`(스프린트 task 목록) ≠ task별 `plan.md`. 같은 단어, 다른 고도.
- lazy 로딩: 각 스킬은 `name`+`description` 한 줄만 상주, 본문은 호출 때만 → 기획 스킬을 더해도 workflow 스킬이 안 비대해짐(부모 §4.1 논리).

전체 사슬:
```
아이디어 ─[pslog-planning]→ 실행계획.md + PLAN.md ─[pslog-workflow]→ task별 코드
                                  └ lock-in 결정(E#) ───────────────→ DECISIONS.md 승격
```

---

## 4. pslog-planning 흐름 (5렌즈 + 분해)

```
아이디어 "X 기능 만들자"
  → 렌즈 1~5 (각 렌즈 = 에이전트 질문 → 사람 답 → lock-in)
  → 실행계획.md 합성
  ★ 게이트 1: 실행계획 전체 승인
  → PLAN.md 골디락스 분해
  ★ 게이트 2: 분해 승인
  → pslog-workflow 핸드오프
```

### 4.1 5렌즈 (app-chak 실행계획에서 역산한 검증된 결)
| # | 렌즈 | 묻는 것 | 실행계획 대응 |
|---|---|---|---|
| 1 | **문제·차별 (CEO/Product)** | 왜? 누구? 뭐가 다른가? 충분히 큰가? | §1~3 |
| 2 | **범위 (YAGNI)** | 만든다 / 안 만든다 — V1 칼질 | §4 |
| 3 | **설계·결정 (Eng)** | 아키텍처 + lock-in (계약/edge/race/cancel) | §5-A·B |
| 4 | **adversarial** | "이걸 어떻게 깨뜨리지? 이 결정이 틀렸다면?" | E11~E14 가 여기서 나옴 |
| 5 | **테스트** | 어떻게 검증? T1~Tn | 테스트계획 |
| → | **분해** | 실행계획 → 골디락스 task (lane, (E#) 백링크) | §11·12 |

(디자인 렌즈는 별도로 강제하지 않음 — UI feature 면 설계 렌즈 안에서 다룸. 필요 시 프로젝트가 6번째 렌즈로 추가 가능.)

### 4.2 메커니즘 — 하이브리드 (사람이 운전대, 에이전트가 1차 심문)
각 렌즈에서:
1. 에이전트가 **1차 초안 + 그 렌즈의 질문 세트**를 뽑는다.
2. **사람이 답한다** — 이게 밀도의 연료. *질문이 많을수록 기획이 촘촘해지고, 후속 workflow 가 쉬워진다.*
3. 답을 **lock-in 결정**으로 박는다.
4. 다음 렌즈.

- 사람 관여는 렌즈마다 연속적(Q&A). pslog 철학 "사람 게이트가 핵심 안전장치"와 정합.
- **모순 탐지는 조용한 배경**으로만 — lock-in 이 쌓이며 기록은 남고, 대놓고 충돌하면 한 번 짚는 정도. 별도 phase·중심 아님. **중심은 "기술적으로 탄탄"이다.**

### 4.3 멈춤 게이트
| 전이 | 승인받을 것 |
|---|---|
| 렌즈 안 | (연속 Q&A — 사람이 답) |
| 렌즈 전부 → 실행계획 | **실행계획.md 전체 승인** |
| 실행계획 → 분해 | (분해 진행) |
| 분해 → 핸드오프 | **PLAN.md 분해 승인** (task 쪼갬·lane·assignee) |

---

## 5. 기술 깊이 엔진 — 실패 클래스 체크리스트 (바닥) + 열린 심문 (특수)

단일 스킬이 E1~E14 수준 lock-in 을 뽑는 법: 에이전트는 그 지식을 *이미* 갖고 있으니, **체계적 적용을 강제**한다. eng(3)·adversarial(4) 렌즈가 아래를 훑는다:

| 버킷 | 심문 | app-chak 대응 |
|---|---|---|
| **동시성/async** | race, task GC, cancel cleanup, 세마포 | E2·E3 |
| **데이터** | N+1, 인덱스, migration, dedup/reconcile, 캐시 일관성 | E8·E10·E14 |
| **계약/경계** | API shape, 권한 누설, 멱등, 에러 표면, 프로토콜 완결 | E4·E6·E7·E12 |
| **리소스** | rate limit/budget 격리, backpressure, 캐시 | E9·E11 |
| **실패 모드** | timeout, circuit, graceful shutdown, fail-fast, disconnect | E5·E13 |

- **바닥(floor)**: 최소한 이 5버킷은 반드시 심문 → 흔한 클래스 누락 방지.
- **열린 심문**: 그 위에 "이 설계만의 특수 리스크는?"(예: app-chak Solar SSE 세마포 — 일반 체크리스트에 없는 도메인 특수).
- = 빠짐없음(체크리스트) + 특수성(열린 심문). 떠오르는 기술 리스크가 다 이 안에 들어가는 것으로 검증됨.

---

## 6. 산출물

### 6.1 실행계획.md 템플릿 (필수 섹션 — app-chak 모양)
위치: `docs/` (프로젝트 설정으로 override 가능). 파일명은 사람이 정함(feature 식별).
```
1. 한 줄 요약
2. 풀려는 문제        — 누구/왜
3. 무엇이 다른가      — 차별점 (렌즈1)
4. 범위 (만든다/안 만든다)  — YAGNI (렌즈2)
5. 핵심 설계 결정 ★   — Product 결정 + Implementation lock-in (E#), 각 이유 (렌즈3·4)
6. 리스크/영향        — 실패 클래스 심문 결과
7. 테스트 케이스 (T#) — (렌즈5)
8. 구현 순서 / 병렬 lane
```
§5 의 lock-in(E#)이 **상류 결정** — PLAN task 가 (E#)로 백링크, 구현 시 DECISIONS.md 로 승격(구현단계 설계서와 물림).

### 6.2 분해 규칙 (실행계획 → PLAN.md)
- 골디락스 **0.5~3일/task**, `[task-NNN]`(프로젝트 unique), `@assignee`, backtick 영향파일.
- **(E#) 백링크** — task 본문에서 실행계획 lock-in 참조 (app-chak `task-001 … (E11)` 식).
- **lane** — 병렬 가능 그룹 (실행계획 §8 의존성에서 도출).
- 결과를 `PLAN.md` 에 기록(부모 `ai-task-automation` §6.1 포맷 그대로).

---

## 7. 무게 티어 (기획도 무게에 비례)
task 레벨 light/heavy 와 같은 결:
- **큰 feature** → 전 렌즈 풀가동(질문 多) + 풀 실행계획.
- **작은 feature/개선** → 핵심 렌즈만(문제·범위·설계) + 얇은 실행계획. adversarial/테스트 렌즈는 생략 가능.
- 판정 기준은 계획 단계에서 확정(§9).

---

## 8. 구현 순서
1. **Phase A — `pslog-planning` SKILL.md 저작** (pslog 코드 0)
   - 흐름(5렌즈+분해) + 메커니즘(하이브리드 Q&A) + 게이트.
   - `references/lenses.md`(렌즈별 질문 가이드 + 실패 클래스 체크리스트), `references/execution-plan-template.md`, `references/decompose.md`.
2. **Phase B — 트리거/hook 갱신**
   - SessionStart hook / 트리거가 planning(새 feature 시작) vs workflow(task 진입)를 구분 안내.
   - plugin.json description 갱신(2-스킬 반영).

(pslog 추적 코드 변경 없음 — 본 스킬은 대상 repo 에서 도는 의식. read-only 원칙 무관.)

---

## 9. 계획 단계에서 확정할 디테일 (미결)
1. **무게 티어 판정 기준**(§7): feature 크기를 무엇으로 가르나(영향 파일 수? lane 수? 사람 선언?). v1 은 사람 선언(가벼운 쪽) 권장.
2. **실행계획 파일명/위치 규약**(§6.1): `docs/` 직하 vs `docs/features/`. PLAN.md 상단 링크 형식.
3. **렌즈별 질문 가이드 깊이**(§5): 체크리스트를 SKILL 본문 inline vs `references/lenses.md` 분리(분리 권장 — context 절약).
4. **분해 시 PLAN.md 자동 기록 vs 제안 후 확인**(§6.2): 부모 §5.3 사람 게이트와 연결 — "제안 후 확인" 권장.
5. **lock-in(E#) ↔ DECISIONS.md 승격 키 매칭**: 구현단계 설계서의 승격 규칙과 ID 정합.

---

## 10. 합의 로그 (brainstorming 2026-06-17)
- pslog-workflow 플러그인 **자급자족** — 외부 스킬 의존 금지, 능력은 번들 스킬로. ✅
- **2-스킬 구성**: pslog-planning(feature 고도, PLAN 만듦) + pslog-workflow(task 고도, PLAN 소비), PLAN.md 핸드오프. ✅
- 흐름: 아이디어 → **5렌즈**(문제·차별 / 범위 / 설계·결정 / adversarial / 테스트) → 실행계획.md → **분해** → PLAN.md. ✅
- 메커니즘: **하이브리드** — 에이전트 렌즈별 1차 질문, 사람이 답(밀도 연료), lock-in. 모순 탐지는 **조용한 배경**(중심 아님). ✅
- 중심 = **기술적으로 탄탄** — eng·adversarial 렌즈가 **실패 클래스 5버킷(바닥) + 열린 심문(특수)** 으로 깊이 강제. ✅
- 게이트: 렌즈 안 연속 Q&A + **큰 멈춤 2개**(실행계획 승인 / 분해 승인). ✅
- 무게 티어: 큰 feature=풀 렌즈, 작은 거=핵심 렌즈만. ✅
- lock-in(E#) → PLAN (E#)백링크 → DECISIONS.md 승격 사슬(구현단계 설계서와 물림). ✅
