# 팔도 밥상 트렌드 — 일일 자동 수집 스크립트
# 작업 스케줄러에서 이 파일을 실행한다. (README 6절 참고)
#
# 두 가지를 지켜야 스케줄 실행이 성공한다.
#   1) 경로를 직접 적지 않고 $PSScriptRoot(이 스크립트가 있는 폴더)를 쓴다.
#      경로에 한글이 들어 있으면 직접 적었을 때 깨질 수 있다.
#   2) python 이 아니라 .venv 안의 python.exe 를 부른다.
#      스케줄러는 가상환경이 켜지지 않은 상태로 실행하기 때문이다.

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$Py   = Join-Path $Root ".venv\Scripts\python.exe"

Set-Location $Root

if (-not (Test-Path $Py)) {
    Write-Host "가상환경을 찾을 수 없습니다: $Py"
    Write-Host "  -> python -m venv .venv 로 먼저 만드세요."
    exit 1
}

Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm')] 일일 수집 시작 ($Root)"

& $Py main.py fetch --limit 50
& $Py main.py fetch --method crawl --target clean --limit 30
& $Py main.py clean
& $Py main.py summarize --unsummarized --limit 20
& $Py main.py sentiment --limit 20
& $Py main.py analyze
& $Py main.py report --save --format md
& $Py main.py export --format excel

Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm')] 일일 수집 완료"
