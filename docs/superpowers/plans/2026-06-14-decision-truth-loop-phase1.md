# 결정-진실 루프 Phase 1 (컨벤션) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 구현 중 내려진 결정이 휘발성 handoff → 영속 DECISIONS.md 로 흘러가는 컨벤션(의식)을 `app-chak`(pslog 첫 실사용처)에 세운다. pslog 코드는 건드리지 않는다.

**Architecture:** 순수 문서/컨벤션 변경. handoff 에 `### 결정` H3 서브섹션(active 일자 섹션 안, `### 다음`/`### 블로커`와 동일 레벨)을 신설해 결정을 포착하고, PR 열 때 DECISIONS.md 로 승격하는 의식을 skill + CLAUDE.md 에 박는다. app-chak 에 없는 DECISIONS.md 를 부트스트랩한다.

**Tech Stack:** Markdown only. 변경은 모두 `~/Documents/ardensdevspace/app-chak` repo (별도 git repo). 검증은 grep + 리뷰.

**상위 설계서:** `pslog/docs/superpowers/specs/2026-06-14-decision-truth-loop-design.md` §3, §4.

**중요 — 두 repo:**
- Task 1~5 는 **app-chak** repo (`~/Documents/ardensdevspace/app-chak`)에서, 단일 브랜치 `feat/decision-truth-loop-convention` 위에.
- 이 plan/spec 문서는 **pslog** repo 에 있음 — Phase 1 에서 pslog 코드 변경 없음.

---

## File Structure (app-chak)

| 파일 | 변경 | 책임 |
|---|---|---|
| `DECISIONS.md` | **신규 생성** | settled 결정의 유일한 입구(index) |
| `handoffs/README.md` | 수정 | handoff 포맷 계약에 `### 결정` 추가 (pslog 가 읽을 포맷) |
| `.claude/skills/handoff-protocol/SKILL.md` | 수정 | 결정 포착 + PR 승격 의식 명문화 |
| `CLAUDE.md` | 수정 | "pslog 연동 규칙" 작업 흐름에 의식 삽입 |

설계 원칙: 세 문서 각자 한 역할 — PLAN(트래커, 얇게) / handoff(staging, 휘발) / DECISIONS(입구, 영속).

---

## Task 0: 브랜치 생성 (app-chak)

**Files:** 없음 (git 작업)

- [ ] **Step 1: app-chak 작업 디렉토리로 이동 + 클린 상태 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/app-chak && git status --short && git branch --show-current
```
Expected: 현재 브랜치 표시. 작업 트리에 무관한 대량 변경이 없는지 확인(있으면 사용자에게 보고 후 중단).

- [ ] **Step 2: 컨벤션 브랜치 생성**

Run:
```bash
cd ~/Documents/ardensdevspace/app-chak && git checkout -b feat/decision-truth-loop-convention
```
Expected: `Switched to a new branch 'feat/decision-truth-loop-convention'`

---

## Task 1: DECISIONS.md 부트스트랩 (app-chak)

app-chak 엔 DECISIONS.md 가 없다. pslog 의 DECISIONS.md 포맷(`목적` + `결정 로그`, 항목별 `결정/이유/영향`)을 따라 새로 만든다. 머리말에 승격 규칙을 박고, 첫 항목으로 "루프 도입" 자체를 기록(dogfood).

**Files:**
- Create: `~/Documents/ardensdevspace/app-chak/DECISIONS.md`

- [ ] **Step 1: DECISIONS.md 생성 (아래 내용 그대로)**

```markdown
# DECISIONS

## 목적
이 문서는 "논의·구현 끝에 확정된 결정"을 기록하는 **유일한 입구(index)**다.
- PRD/DESIGN 의 문장보다 이 문서의 결정이 우선한다(충돌 시 DECISIONS 우선).
- **구현 중 기획과 달라진 결정도 반드시 여기로 승격한다.** 작업 중에는 handoff `### 결정`에
  포착하고, PR 열 때 이 파일로 한 줄 이상 옮긴다(승격 의식: `.claude/skills/handoff-protocol`).
- 결정이 크고 맥락·대안을 한 페이지 쓸 가치가 있으면 `docs/adr/NNN.md`를 만들고 여기엔
  `[ADR-NNN] 제목 → 링크` 한 줄만 남긴다. 후임은 이 파일 하나만 위→아래로 읽으면 결정 전체를 본다.

원칙: **"한 줄은 항상, 한 페이지는 가끔."**

## 결정 로그

### 2026-06-14 — 결정-진실 루프 도입
- 결정: 구현 중 결정을 handoff `### 결정`에 포착 → PR 열 때 DECISIONS.md로 승격하는 의식을 채택한다.
  pslog가 미승격/상태모순을 드리프트로 감지한다(Phase 2).
- 이유: PLAN.md(얇은 트래커)와 handoff(휘발성 로그)가 서로 다른 이야기를 하고, 구현 중 결정이
  handoff에 갇혀 죽어 후임이 놓치는 문제를 닫기 위함.
- 영향:
  - handoff 포맷에 `### 결정` H3 서브섹션 추가(`handoffs/README.md`).
  - PR 열기 전 승격이 작업 흐름의 일부가 됨(`handoff-protocol` skill, `CLAUDE.md`).
  - 설계서: `pslog/docs/superpowers/specs/2026-06-14-decision-truth-loop-design.md`.
```

- [ ] **Step 2: 생성 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/app-chak && test -f DECISIONS.md && grep -q "유일한 입구" DECISIONS.md && grep -q "결정-진실 루프 도입" DECISIONS.md && echo OK
```
Expected: `OK`

- [ ] **Step 3: 커밋**

```bash
cd ~/Documents/ardensdevspace/app-chak && git add DECISIONS.md && git commit -m "docs: DECISIONS.md 부트스트랩 — 결정의 유일한 입구 + 승격 규칙"
```

---

## Task 2: handoffs/README.md 에 `### 결정` 포맷 추가 (app-chak)

handoff 포맷 계약(pslog 가 읽음)에 `### 결정` 서브섹션을 추가한다. 파일 형식 예시와 파싱 규칙 양쪽에 반영.

**Files:**
- Modify: `~/Documents/ardensdevspace/app-chak/handoffs/README.md`

- [ ] **Step 1: 파일 형식 예시에 `### 결정` 추가**

`handoffs/README.md` 의 "## 파일 형식" 코드블록에서, `### 마지막 커밋` 바로 위에 `### 결정` 블록을 끼운다.

찾을 텍스트 (코드블록 내부):
```markdown
  - [ ] 약관 동의 체크박스
  - [ ] 에러 메시지 i18n

### 마지막 커밋
```
바꿀 텍스트:
```markdown
  - [ ] 약관 동의 체크박스
  - [ ] 에러 메시지 i18n

### 결정
- [task-007] 약관 동의를 별도 모달 대신 인라인 체크박스로 — 화면 전환 비용↓·접근성↑ → DECISIONS
  # ↑ 구현 중 기획과 달라진 결정. PR 열 때 DECISIONS.md로 승격하고 끝에 `→ DECISIONS` 마킹.
  # 결정이 없으면 이 섹션 자체를 비워둔다(대부분의 task).

### 마지막 커밋
```

- [ ] **Step 2: "## 파싱 규칙 (pslog 측)" 에 `### 결정` 항목 추가**

찾을 텍스트:
```markdown
- `### 마지막 커밋`, `### 다음`, `### 블로커` 자유 영역도 raw로 보존
```
바꿀 텍스트:
```markdown
- `### 마지막 커밋`, `### 다음`, `### 블로커` 자유 영역도 raw로 보존
- `### 결정` 서브섹션의 `- [task-NNN] ... — 왜 → DECISIONS|ADR-NNN` 항목은 **승격 추적용**.
  pslog(Phase 2)가 이 항목을 파싱해, `→ DECISIONS`/`→ ADR` 마커가 없거나 같은 브랜치 커밋에
  DECISIONS.md 변경이 없으면 "결정 미승격" 드리프트로 감지한다. (Phase 1 에선 사람이 지키는 규칙)
```

- [ ] **Step 3: "## 작업 규칙" 에 승격 한 줄 추가**

찾을 텍스트:
```markdown
- handoff 미갱신 push는 pslog Discord에 ⚠️ 알림.
```
바꿀 텍스트:
```markdown
- handoff 미갱신 push는 pslog Discord에 ⚠️ 알림.
- **PR 열기 전**, `### 결정` 항목을 `DECISIONS.md`로 승격(날짜+한 줄, 무거우면 `docs/adr/` + 링크)하고
  handoff 항목 끝에 `→ DECISIONS` 마킹. 자세한 절차는 `.claude/skills/handoff-protocol`.
```

- [ ] **Step 4: 변경 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/app-chak && grep -q "### 결정" handoffs/README.md && grep -q "결정 미승격" handoffs/README.md && grep -q "PR 열기 전" handoffs/README.md && echo OK
```
Expected: `OK`

- [ ] **Step 5: 커밋**

```bash
cd ~/Documents/ardensdevspace/app-chak && git add handoffs/README.md && git commit -m "docs(handoff): 포맷 계약에 ### 결정 서브섹션 + 승격 규칙 추가"
```

---

## Task 3: handoff-protocol SKILL.md 에 승격 의식 추가 (app-chak)

수신/발신 체크리스트 중심의 기존 skill 에 "결정 승격" 절차를 추가한다.

**Files:**
- Modify: `~/Documents/ardensdevspace/app-chak/.claude/skills/handoff-protocol/SKILL.md`

- [ ] **Step 1: frontmatter description 에 트리거 보강**

찾을 텍스트:
```markdown
description: Use when receiving deliverables from another team role (개발자2 or 디자이너), preparing deliverables for others, or checking handoff SLA compliance. Triggers on role transitions, Phase boundaries, or integration points.
```
바꿀 텍스트:
```markdown
description: Use when receiving deliverables from another team role (개발자2 or 디자이너), preparing deliverables for others, checking handoff SLA compliance, or opening a PR (decision promotion). Triggers on role transitions, Phase boundaries, integration points, or PR open.
```

- [ ] **Step 2: "## 머지 순서 (고정)" 섹션 앞에 결정 승격 섹션 삽입**

찾을 텍스트:
```markdown
## 머지 순서 (고정)

개발자2 변경 반영 -> 개발자1 통합 -> 릴리즈
```
바꿀 텍스트:
```markdown
## 결정 승격 (Decision Promotion)

구현 중 기획(PLAN/PRD/DESIGN)과 달라진 결정은 휘발성 handoff에 갇혀 죽지 않도록 영속 문서로 승격한다.

### 포착 (작업 중)
- 기획과 달라지는 결정이 생기면 그 자리에서 handoff active 일자 섹션의 `### 결정`에 1~2줄 적는다.
  형식: `- [task-NNN] 무엇을 어떻게 바꿈 — 왜`
- 흐름 끊지 말고 짧게. 결정이 없으면 섹션 자체를 비워둔다.

### 승격 (PR 열 때 — 필수)
PR 열기 = "이 브랜치 작업 끝" 신호. PR 열기 직전 반드시:
1. handoff `### 결정`의 각 항목을 `DECISIONS.md` "결정 로그"에 추가한다.
   - 형식: `### YYYY-MM-DD — 제목` + `결정 / 이유 / 영향` (pslog DECISIONS.md 항목들 참고).
   - 무거운 결정(아키텍처급, 대안 비교 필요)이면 `docs/adr/NNN.md` 한 장 만들고 DECISIONS.md엔
     `[ADR-NNN] 제목 → 링크` 한 줄만.
2. handoff의 해당 `### 결정` 항목 끝에 `→ DECISIONS` (또는 `→ ADR-NNN`) 마킹.
3. DECISIONS.md 변경을 같은 브랜치에 커밋(= PR에 포함).

미승격 시: pslog(Phase 2)가 "결정 미승격" 드리프트로 감지해 Discord ⚠️ + 대시보드에 표시한다.
리뷰어는 PR 리뷰 시 `### 결정` ↔ DECISIONS.md 승격 여부를 더블체크한다.

## 머지 순서 (고정)

개발자2 변경 반영 -> 개발자1 통합 -> 릴리즈
```

- [ ] **Step 3: 변경 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/app-chak && grep -q "결정 승격 (Decision Promotion)" .claude/skills/handoff-protocol/SKILL.md && grep -q "승격 (PR 열 때 — 필수)" .claude/skills/handoff-protocol/SKILL.md && echo OK
```
Expected: `OK`

- [ ] **Step 4: 커밋**

```bash
cd ~/Documents/ardensdevspace/app-chak && git add .claude/skills/handoff-protocol/SKILL.md && git commit -m "feat(skill): handoff-protocol에 결정 승격 의식 추가"
```

---

## Task 4: CLAUDE.md "pslog 연동 규칙" 에 의식 삽입 (app-chak)

작업 흐름 문서에 승격을 박는다. 기존 "### handoff 파일 갱신 (필수)" 와 "### PLAN.md" 사이/주변.

**Files:**
- Modify: `~/Documents/ardensdevspace/app-chak/CLAUDE.md`

- [ ] **Step 1: "### handoff 파일 갱신 (필수)" 블록에 결정 포착·승격 단계 추가**

찾을 텍스트:
```markdown
3. **git push 직전 반드시** 해당 파일 commit.
4. 마스터 PLAN의 task ID(`task-XXX`)는 들여쓰기 0 체크박스, 개인 서브 작업은 들여쓰기 2 체크박스.
```
바꿀 텍스트:
```markdown
3. **git push 직전 반드시** 해당 파일 commit.
4. 마스터 PLAN의 task ID(`task-XXX`)는 들여쓰기 0 체크박스, 개인 서브 작업은 들여쓰기 2 체크박스.
5. 구현 중 기획과 달라진 결정은 active 일자 섹션의 `### 결정`에 `- [task-NNN] 무엇 바꿈 — 왜`로 포착.

### 결정 승격 (PR 열 때 — 필수)
PR 열기 직전, handoff `### 결정` 항목을 `DECISIONS.md`로 승격한다(날짜+`결정/이유/영향`, 무거우면 `docs/adr/` + 링크 한 줄). 승격한 handoff 항목 끝엔 `→ DECISIONS` 마킹. 절차 상세는 `.claude/skills/handoff-protocol` (결정 승격). 미승격은 pslog가 드리프트로 잡는다.
```

- [ ] **Step 2: "### PLAN.md" 또는 "### 강제" 블록 근처에 DECISIONS.md 역할 한 줄 추가**

찾을 텍스트:
```markdown
### 강제
- handoff 미갱신 push → pslog Discord ⚠️ 알림
- PR 머지 거부 lint hook은 추후 도입
```
바꿀 텍스트:
```markdown
### 결정의 집
- `DECISIONS.md` = settled 결정의 유일한 입구. 후임/3자는 이 파일 하나로 결정 전체를 본다.
- PLAN.md는 얇은 트래커로 유지(결정 본문을 PLAN에 적지 않는다). handoff는 둘 사이 staging.

### 강제
- handoff 미갱신 push → pslog Discord ⚠️ 알림
- `### 결정` 미승격(DECISIONS.md 누락) push → pslog 드리프트 알림 (Phase 2)
- PR 머지 거부 lint hook은 추후 도입
```

- [ ] **Step 3: 변경 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/app-chak && grep -q "결정 승격 (PR 열 때 — 필수)" CLAUDE.md && grep -q "### 결정의 집" CLAUDE.md && echo OK
```
Expected: `OK`

- [ ] **Step 4: 커밋**

```bash
cd ~/Documents/ardensdevspace/app-chak && git add CLAUDE.md && git commit -m "docs: pslog 연동 규칙에 결정 승격 의식 + DECISIONS.md 역할 명시"
```

---

## Task 5: 의식 self-test — 가짜 결정 1건으로 라운드트립 (app-chak)

컨벤션이 실제로 굴러가는지, 새 규칙을 따라 결정 1건을 포착→승격해본다(연습). 실제 코드 결정이 아니라 컨벤션 검증용 더미이므로, 검증 후 되돌린다.

**Files:**
- 임시 수정(되돌림): `handoffs/` 내 아무 활성 파일 또는 신규 임시 파일

- [ ] **Step 1: 임시 handoff 에 `### 결정` 1건 작성 → DECISIONS.md 로 승격 → 마킹**

연습: 새 임시 파일 `handoffs/feat-decision-truth-loop-convention.md` 를 만들고(이 브랜치의 handoff), `### 결정`에 더미 항목 1줄을 적은 뒤, README/skill 의 승격 절차대로 DECISIONS.md 에 한 줄 추가하고 handoff 항목에 `→ DECISIONS` 마킹까지 해본다. 절차가 막힘없이 되는지(문서가 자기모순 없는지) 확인이 목적.

- [ ] **Step 2: 절차가 매끄러웠는지 자체 평가 + 문서 보정**

승격 중 "어디에 뭘 적어야 하는지" 헷갈리는 지점이 있으면 Task 2~4 문서를 그 자리에서 보정(grep 으로 재확인). 헷갈림 없으면 통과.

- [ ] **Step 3: 더미 흔적 정리**

DECISIONS.md 의 더미 항목 제거(루프 도입 항목은 유지). 임시 handoff 파일은 이 브랜치의 실제 handoff 로 전환하거나(아래 내용으로 대체) 삭제. 연습 흔적이 커밋에 남지 않게 한다.

- [ ] **Step 4: 이 브랜치의 진짜 handoff 작성 + 커밋**

`handoffs/feat-decision-truth-loop-convention.md` 를 이 브랜치의 실제 일지로 작성(헤더 `# Handoff: feat/decision-truth-loop-convention — @arden`, 오늘 일자 섹션에 Task 1~5 요약, `### 결정`엔 "이번 작업은 컨벤션 도입 자체라 코드 결정 없음" 명시).

```bash
cd ~/Documents/ardensdevspace/app-chak && git add handoffs/feat-decision-truth-loop-convention.md DECISIONS.md && git commit -m "docs(handoff): 컨벤션 도입 브랜치 handoff + self-test 정리"
```

---

## Task 6: 최종 검토 + PR 준비

- [ ] **Step 1: 전체 변경 diff 리뷰**

Run:
```bash
cd ~/Documents/ardensdevspace/app-chak && git log --oneline main..feat/decision-truth-loop-convention && echo "---" && git diff --stat main..feat/decision-truth-loop-convention
```
Expected: 커밋 5~6개(Task 1~5), 변경 파일 = DECISIONS.md, handoffs/README.md, .claude/skills/handoff-protocol/SKILL.md, CLAUDE.md, handoffs/feat-...md. **app/ 등 코드 변경이 0인지 확인**(Phase 1 은 문서만).

- [ ] **Step 2: 4파일 일관성 최종 확인**

Run:
```bash
cd ~/Documents/ardensdevspace/app-chak && for f in DECISIONS.md handoffs/README.md .claude/skills/handoff-protocol/SKILL.md CLAUDE.md; do echo "== $f =="; grep -c "결정" "$f"; done
```
Expected: 각 파일에서 "결정" 1회 이상 매치(네 파일 모두 루프를 언급).

- [ ] **Step 3: 사용자에게 PR 생성 여부 확인**

⚠️ PR 생성/푸시는 사용자 승인 후. 사용자에게 보고: "Phase 1 컨벤션 app-chak 브랜치 `feat/decision-truth-loop-convention` 완료(문서 4+1). push/PR 할까요?"

---

## 자기 검토 (이 plan 작성자용 메모)

- **spec 커버리지**: §3(세 문서 모델) → Task 1·4; §4.1(`### 결정` 포맷) → Task 2; §4.2(산출물 5개) → Task 1~4 (pslog 측 계약 = README 의 파싱 규칙 + 이 plan/spec); §4.3(의식) → Task 3·4. §5(Phase 2)는 별도 plan.
- **DECISIONS.md 부재**는 진단 중 발견 → Task 1 으로 흡수. spec §4.2 도 갱신함.
- **`### 결정` (H3)**: spec 의 `## 결정`(H2)이 파서 일자 섹션과 충돌 → H3 로 정정(spec 갱신 완료). 이 plan 전반에 H3 사용.
- **테스트 부재**: Phase 1 은 실행 코드가 없어 pytest 없음 — 검증은 grep + 라운드트립 self-test(Task 5)로 대체. Phase 2(파서/Drift)에서 TDD 적용.

## Phase 2 (다음 plan) 예고 — 본 plan 범위 아님

spec §5 + §7 미결 4건(PR vs push 이벤트 시점, 승격 판정 강도, B 조인 정확도, 카피)을 확정한 뒤
별도 plan 으로: handoff_parser `### 결정` 파싱 → `Drift` 모델(error_group 패턴) → sync_service 감지 A·B
→ API/프론트(log_errors 패턴) → Discord. pslog repo 에서 TDD.
