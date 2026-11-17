# Only synthetic data, internal network, RAM-only PostgreSQL, no published ports.
[CmdletBinding()]
param([switch]$SkipBuild, [switch]$RecoveryOnly)
$ErrorActionPreference = 'Stop'
$featureRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$testImage = 'saiv-password-reset-tests:bb28642'
$testNetwork = 'saiv-password-reset-' + [Guid]::NewGuid().ToString('N').Substring(0,12)
$testPostgres = "$testNetwork-pg"
$scope = 'forgot-password-disposable-tests'
$created = $false
function Checked { & docker @args; if ($LASTEXITCODE -ne 0) { throw 'Disposable password-reset validation failed.' } }
try {
    if (-not $SkipBuild) { Checked build -t $testImage (Join-Path $featureRoot 'module2-backend') }
    Checked network create --internal --label "saiv.scope=$scope" $testNetwork
    $created = $true
    Checked run -d --name $testPostgres --label "saiv.scope=$scope" --network $testNetwork --network-alias reset-test-postgres --tmpfs /var/lib/postgresql/data -e POSTGRES_USER=otp_test -e POSTGRES_PASSWORD=synthetic-only -e POSTGRES_DB=saiv_password_reset_disposable postgres:15-alpine
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        & docker exec $testPostgres pg_isready -U otp_test -d saiv_password_reset_disposable *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Disposable PostgreSQL did not become ready.' }
    $testUrl = 'postgresql+psycopg2://otp_test:synthetic-only@reset-test-postgres:5432/saiv_password_reset_disposable'
    $testPaths = if ($RecoveryOnly) { "['tests/flow/test_password_reset_flow.py','tests/flow/test_password_recovery_security_flow.py','tests/unit/test_password_recovery_safety.py']" } else { "['tests']" }
    $python = "import app,pytest,sys;from app.database import engine;assert app.__file__.startswith('/workspace/module2-backend/app/');assert engine.url.host=='reset-test-postgres' and engine.url.database=='saiv_password_reset_disposable';sys.exit(pytest.main($testPaths+['-p','no:cacheprovider','-r','fE','--tb=short']))"
    Checked run --rm --network $testNetwork --read-only --tmpfs /tmp -e PYTHONDONTWRITEBYTECODE=1 -e SECRET_KEY=synthetic-password-reset-tests-only -e SESSION_AUTO_CLOSE_SECONDS=0 -e SMTP_HOST= -e FACE_SERVICE_URL=http://127.0.0.1:9 -e "DATABASE_URL=$testUrl" -e "TEST_DATABASE_URL=$testUrl" --mount "type=bind,source=$featureRoot,target=/workspace,readonly" --workdir /workspace/module2-backend --entrypoint python $testImage -c $python
} finally {
    $existing = & docker ps -aq --filter "name=^$testPostgres$"
    if ($existing) {
        $labels = & docker inspect --format '{{json .Config.Labels}}' $testPostgres
        if (($labels | ConvertFrom-Json).'saiv.scope' -ne $scope) { throw 'Disposable resource ownership mismatch.' }
        & docker rm -f $testPostgres | Out-Null
    }
    if ($created) {
        $labels = & docker network inspect --format '{{json .Labels}}' $testNetwork
        if (($labels | ConvertFrom-Json).'saiv.scope' -ne $scope) { throw 'Disposable network ownership mismatch.' }
        & docker network rm $testNetwork | Out-Null
    }
}
