# 병합과 학생 전달 분리 확인 절차 (읽기 전용 초안)

릴리스 태그 대상은 검토·CI를 통과한 main 병합 커밋으로 고정한다. 각 단계에서 출력값을 기록하며, `auto`, `apply`, `adopt`, 쓰기 모드 manifest 생성은 실행하지 않는다.

1. main의 병합 커밋과 PR을 확인한다.

   ```sh
   gh pr view <PR번호> -R starceas/knuaf-doc --json state,mergedAt,mergeCommit,headRefOid
   ```

   `state=MERGED`이고 `mergeCommit.oid`가 검토·CI 대상 커밋과 같은지 확인한다. 단순히 PR이 병합됐다는 사실만으로 학생 전달 완료 처리하지 않는다.

2. 병합 커밋의 버전·설치 목록을 읽고 해시를 계산한다.

   ```sh
   git show <merge-sha>:skills/knuaf-doc/version.json
   git show <merge-sha>:skills/knuaf-doc/install-manifest.json
   git show <merge-sha>:skills/knuaf-doc/references/builtin-sources.json
   ```

   병합 SHA의 `version`과 설치 manifest의 파일·SHA-256을 기록한다. 선언된 파일이 병합 트리에서 모두 존재하고 각 바이트 해시가 맞는지 확인한다.

3. 같은 버전 태그와 GitHub 릴리스가 그 병합 커밋을 가리키는지 확인한다.

   ```sh
   gh release view v<버전> -R starceas/knuaf-doc --json tagName,targetCommitish,publishedAt,assets
   git ls-remote origin 'refs/tags/v<버전>' 'refs/tags/v<버전>^{}'
   ```

   태그가 `<merge-sha>`와 같고 릴리스 버전이 `version.json`과 같아야 한다. 불일치·누락은 `미확인` 또는 실패로 남긴다.

4. 학생 설치본은 조회만 한다.

   ```sh
   python <설치본>/scripts/gg_update.py check
   python - <<'PY'
   import hashlib, json
   from pathlib import Path
   root = Path('<설치본>')
   manifest = json.loads((root / 'install-manifest.json').read_text(encoding='utf-8'))
   files = manifest.get('files', {})
   bad = []
   for rel, expected in files.items():
       p = root / rel
       actual = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
       if actual != expected:
           bad.append(rel)
   print(json.dumps({'declared': len(files), 'mismatches': bad}, ensure_ascii=False))
   PY
   ```

   `check`는 최신 릴리스 상태 조회다. 출력의 버전과 설치 manifest의 선언 파일 해시를 분리해 확인한다. `check`의 `up_to_date`만으로 main 커밋 반영이나 필수 기능 도달을 주장하지 않는다.

5. 필요한 기능의 진입 경로를 설치본에서 읽기 전용으로 대조한다. 최소한 복구한 배포 파일이 설치 manifest에 있고, 실제 설치본 파일 해시가 manifest와 일치해야 한다. K1의 w5R 산출물은 해당 담당 결과와 별도로 대조한다.

완료 판정은 `검토된 merge SHA == 버전 기록의 트리 == 태그 대상`, 릴리스가 해당 태그를 가리킴, 설치본 버전·필수 파일 해시가 일치함을 모두 확인한 경우에만 한다. 이 문서는 향후 로컬 설치본 한 개의 검증 절차이며 전체 학생 PC 확인 증거를 대신하지 않는다.
