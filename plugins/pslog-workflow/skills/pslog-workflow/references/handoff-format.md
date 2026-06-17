# handoff 포맷 (pslog 파서 strict — 어긋나면 MalformedHandoffError)

- 파일: `handoffs/{현재브랜치}.md` (브랜치명의 `/` → `-`).
- 첫 줄: `# Handoff: <branch> — @<username>`
  - `Handoff:` 키워드 필수. 구분자는 em-dash `—` (U+2014), 일반 hyphen 아님.
  - branch는 공백 없는 1단어(`feat/task-011-writing-catalog`), username은 영숫자/언더스코어/하이픈.
- 일자 섹션: `## YYYY-MM-DD` (최소 1개, 최신이 active).
- task 체크박스: 마스터 task ID는 들여쓰기 0(`- [ ] task-NNN`, pslog DB 동기화),
  개인 서브작업은 들여쓰기 2(raw, 동기화 안 됨).
- 자유 노트(선택): `### 마지막 커밋` / `### 다음` / `### 블로커`.
- 결정 포착(선택): `### 결정` — 구현이 기획과 달라지면 1~2줄. PR 때 DECISIONS.md로 승격하고 `→ DECISIONS` 마킹.
- git push 직전 반드시 이 파일 commit (미갱신 push → pslog Discord ⚠️).
