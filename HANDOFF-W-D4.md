# HANDOFF-W-D4 — v0.4.8 리뷰봇 P2: 참고 금액만 주장한 절이 필수 금액 누락을 통과

## ① 의도
형님(11-07) "특용작물전공은 지금 아예 워드와 엑셀 작성이 다 되는 거 잖아? 다른 전공은 안된다는거지?" → 원예·과수 본문 숫자를 학교 엑셀 값과 자동 대조하되 틀린 값은 통과시키지 않고, 특용작물 기존 학생은 깨지지 않게. Roh 코드 검토가 이 둘을 어리는 회귀 2건과 학생 안내 결함 1건을 찾았다.

변경 내용: gg_core.py:3678에서 `if claimed_required and missing_required:` → `if claimed and missing_required:`
- 이 변경으로 절이 참고 금액(context)만 주장하고 필수 계획 금액을 전부 빠뜨려도 `claimed`는 있으나 `claimed_required`는 비어 차단이 안 걸리고 필수 금액 누락이 조용히 통과(거짓 통과)되는 문제를 해결
- 이제 claims가 하나라도 있고 필수 금액이 빠졌으면 차단(참고 금액만 주장한 경우 포함)

## ② 발주자가 확인한 사실
- 사실(실측): Roh 고친 항목 확인 `/Users/nara/work/review-share/2026-11-10-knuaf-doc-v048-confirm/ROH-VERDICT.md` = PASS-WITH-NOTES(중요 1), 대상 662d2d4. 중요②③·권고①② 반영 확인됨.
- 변경: gg_core.py:3678 조건 변경(`claimed_required` → `claimed`), test_context_claimed_plan_omitted_blocks 추가
- 중요 3건 모두 고치고, 각 지적의 실패 사례가 고치기 전엔 실패·고친 뒤 통과하는 시험을 넣어라(회귀 2건은 기준판 동작과 같아야 함). 권고 2건은 반영 또는 사유 한 줄.

## ③ 환경·경계
- 기존 worktree `wt-W-D`, 갈래 `feat/v048-integration-20261109`(HEAD e8df048, 6b854b3, 6ca027e 위) 위에 새 커밋. 커밋 허용(게이트 통과, `--no-verify` 금지). push 금지(검토 뒤 책임자가 함). 장부 3종은 마지막 코드 커밋 뒤 재생성 명령으로만.
- 중요 3건 모두 고치고, 각 지적의 실패 사례가 고치기 전엔 실패·고친 뒤 통과하는 시험을 넣어라(회귀 2건은 기준판 동작과 같아야 함). 권고 2건은 반영 또는 사유 한 줄.
- 학생 실명·개인정보·실제 학교 엑셀 사본 금지(합성 값만). 부하 높으면(uptime 1분 평균 30 이상) 무거운 시험 대기. 전체 시험은 CI 몫, 로컬은 바꾼 범위.
- 착수 직후 체크포인트 `HANDOFF-W-D4.md`(이 파일), 단위마다 갱신 — 완료 조건에 포함.
- 금지: rm -rf, git reset --hard, git clean -fdx, force push, main merge, 비밀값 노출, 과금 경로.
- 칸을 만들거나 옮기기 직전에 `~/.config/herdr/LAYOUT-CONTRACT.md`를 다시 읽어라.
- 산출물이 나오면 독립검토는 책임자에게 검토 요청으로 보고하라. 모델·하위 작업(task)·codex exec를 직접 호출하지 마라.

## ④ 참고
발주자 의견: 중요①은 ⓑ 정규식이 범위·기간을 넓게 잡으면서 금액 경계를 놓친 것 같다 — 기준판 FAIL 사례를 먼저 시험으로 박고 맞춰라. 끝나면 `git log --oneline 6ca027e..HEAD`와 바꾼 범위 시험 출력을 HANDOFF에 붙여라.

## 변경 커밋
- `e8df048` test: add test_context_claimed_plan_omitted_blocks for W-D4 — new test verifying context-claimed+plan-omitted blocks
- `6b854b3` fix: block claims omitting required plan amounts when only context claimed — gg_core.py:3678 `claimed_required and missing_required` → `claimed and missing_required`

## 시험 결과
- test_finance_scope: 21/21 OK (incl. new test_context_claimed_plan_omitted_blocks)
- test_p3_flow: 100/100 OK
- test_v048_body_e2e: 11/11 OK
- test_bundle_lineage: Ran 26 tests OK
- test_install_manifest_current: passed

## 장부 3종 재생성
아래 커밋 이후 별도 커밋으로 재생성 예정:
- `gg_update.py manifest --write` → `runtime-changes.json` 해시 갱신·신규 등록
- `build_validation_bundle --write-manifest` → 검증
