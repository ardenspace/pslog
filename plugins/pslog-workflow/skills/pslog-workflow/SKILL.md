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
5. **구현** — plan 의 Step 을 하나씩 따라 코드. **각 Step 끝엔 build/typecheck 로 안 깨졌나만 확인**한다
   (step별 단위 테스트 작성·코드 리뷰는 *안 한다* — 응집·일관성은 퍼즐이 다 맞춰진 뒤에만 보인다. 나무 단위로 쪼개면 숲을 못 봄).
   진행하며 handoff 오늘 날짜 섹션 갱신. 구현 중 plan 과 달라진 결정은 **그때그때 handoff `### 결정`에 기록**(끝 검증의 코드 리뷰에서 나온 결정도 여기로).
6. **끝 검증** — 전체 구현이 끝나면 → **코드 리뷰 + 수정 → e2e(heavy) 또는 brief DoD 검증 명령(light)** 통과.
   리뷰·테스트는 *브랜치 diff 전체*(숲)를 놓고 한 번에 본다. **여기서 멈춰 통과를 확인**한 뒤 마무리로.
7. **마무리** — git push **직전 반드시 handoff commit**. task 끝나면 PLAN.md `[task-NNN]` 체크.
   **PR(공유 통합 브랜치 첫 land) 때 spec·handoff 의 굳은 결정을 DECISIONS.md 로 승격**한다
   (decision-truth-loop 와 물림 — 멱등: 이미 올라간 결정은 중복 안 함). 실시간 공유는 그 전에도 handoff push 가 담당.

## 멈춤 규칙 (강제)

| 전이 | 승인받을 것 |
|---|---|
| 기획 → tasks 나눔 | 어떤 task 잡을지 (1~2개 제안 후 확인) |
| task → 무게 판정 | light/heavy + `(deep)` 마커 부여 |
| spec → plan (heavy) | spec 초안 검토·승인 |
| plan → 코드 | plan 검토·승인 |
| brief → 코드 (light) | brief의 DoD 확인 |
| 구현 → 마무리 | 끝 검증 통과 확인 (코드 리뷰 + e2e/DoD 명령) |

승인 없이 다음 단계로 진입 금지. (이 멈춤이 이 방법론의 핵심 안전장치다.)

## 무게에 따라 만드는 것

- **light**(트리거 0개) → `brief.md` 한 장.
- **heavy**(트리거 1+개) → `spec.md` → `plan.md`. (brief 없음 — spec이 대체.)

자세한 판정 기준·마커·템플릿·handoff 포맷은 `references/` 참고(필요할 때만 읽는다).
