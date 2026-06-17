#!/usr/bin/env node
// pslog-workflow SessionStart hook.
// pslog 관리 프로젝트(repo 루트에 PLAN.md 존재)에서만 트리거를 context에 주입한다.
// 아니면 아무것도 출력하지 않음(다른 프로젝트에서 무해). 파일은 절대 수정하지 않는다(read-only).

const fs = require("fs");
const path = require("path");

const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const planPath = path.join(projectDir, "PLAN.md");

if (fs.existsSync(planPath)) {
  const output = {
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext:
        "이 프로젝트는 pslog-workflow 플러그인으로 관리된다. 두 스킬을 쓴다: " +
        "(1) 새 feature 아이디어/기획에 들어가면 → pslog-planning 스킬(/pslog-workflow:pslog-planning) — " +
        "5렌즈 기획 → 실행계획.md → PLAN.md 분해. " +
        "(2) PLAN.md의 task 하나를 잡아 코드로 옮기면 → pslog-workflow 스킬(/pslog-workflow:pslog-workflow) — " +
        "무게 게이트(brief vs spec→plan) → 코드 → 검증. 각 단계 사람 승인 흐름을 따른다.",
    },
  };
  process.stdout.write(JSON.stringify(output));
}

process.exit(0);
