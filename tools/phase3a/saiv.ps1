# Windows PowerShell 5.1. Never removes volumes or accesses another project.
[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [ValidateSet('init','start','stop','build','rebuild','status','logs','origin')]
    [string]$Action = 'status',
    [string]$PublicOrigin = '',
    [string]$Service = ''
)
$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $RepoRoot
$ActualRoot = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or [IO.Path]::GetFullPath($ActualRoot) -ne $RepoRoot) { throw 'Unexpected repository directory.' }
if ((& git branch --show-current).Trim() -ne 'feature/frontend-improvements') { throw 'Use the designated feature/frontend-improvements checkout.' }
$EnvFile = Join-Path $RepoRoot '.env.phase3a'
$ComposeArgs = @('compose','--env-file',$EnvFile,'--project-name','saiv-phase3a','-f',(Join-Path $RepoRoot 'compose.phase3a.yml'))
function Invoke-Compose {
    param([string[]]$DockerArguments)
    & docker @ComposeArgs @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed (exit $LASTEXITCODE)." }
}
function New-LocalSecret {
    $Bytes = New-Object byte[] 32
    $Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $Generator.GetBytes($Bytes) } finally { $Generator.Dispose() }
    return ([BitConverter]::ToString($Bytes)).Replace('-','').ToLowerInvariant()
}
if ($Action -eq 'init') {
    New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot '.local/phase3a/models') | Out-Null
    if (Test-Path -LiteralPath $EnvFile) {
        Write-Host 'Existing .env.phase3a preserved. Credentials were not rotated.'
    } else {
        $Settings = @(
            'SAIV_DB_PASSWORD=' + (New-LocalSecret)
            'SAIV_SECRET_KEY=' + (New-LocalSecret)
            'SAIV_GRAFANA_PASSWORD=' + (New-LocalSecret)
            'SAIV_PUBLIC_ORIGIN=http://localhost:8080'
            'SAIV_PUBLIC_HOST=localhost'
            'SAIV_GATEWAY_HOSTS=localhost 127.0.0.1'
            'SAIV_FORWARDED_PROTO=http'
        )
        [IO.File]::WriteAllLines($EnvFile, $Settings, (New-Object Text.UTF8Encoding($false)))
        Write-Host 'Created ignored .env.phase3a with random local credentials.'
    }
    exit 0
}
if (!(Test-Path -LiteralPath $EnvFile)) { throw 'Run .\tools\phase3a\saiv.ps1 init first.' }
switch ($Action) {
    'origin' {
        $Parsed = $null
        if (![Uri]::TryCreate($PublicOrigin, [UriKind]::Absolute, [ref]$Parsed)) { throw 'Provide an absolute public origin.' }
        $Local = $Parsed.Scheme -eq 'http' -and $Parsed.Host -eq 'localhost' -and $Parsed.Port -eq 8080
        $PrivateTunnel = $Parsed.Scheme -eq 'https' -and $Parsed.Host.EndsWith('.devtunnels.ms') -and $Parsed.IsDefaultPort
        if ((!$Local -and !$PrivateTunnel) -or $Parsed.UserInfo -or $Parsed.Query -or $Parsed.Fragment -or $Parsed.AbsolutePath -ne '/') {
            throw 'Use http://localhost:8080 or the HTTPS origin copied from the private VS Code Dev Tunnel, without a path/query/fragment.'
        }
        $Origin = $Parsed.GetLeftPart([UriPartial]::Authority)
        $Lines = [IO.File]::ReadAllLines($EnvFile)
        $Lines = @($Lines | Where-Object { $_ -notmatch '^SAIV_(PUBLIC_ORIGIN|PUBLIC_HOST|GATEWAY_HOSTS|FORWARDED_PROTO)=' })
        $Lines += @("SAIV_PUBLIC_ORIGIN=$Origin", "SAIV_PUBLIC_HOST=$($Parsed.Host)", "SAIV_FORWARDED_PROTO=$($Parsed.Scheme)")
        $GatewayHosts = (@($Parsed.Host, 'localhost', '127.0.0.1') | Sort-Object -Unique) -join ' '
        $Lines += "SAIV_GATEWAY_HOSTS=$GatewayHosts"
        [IO.File]::WriteAllLines($EnvFile, $Lines, (New-Object Text.UTF8Encoding($false)))
        Write-Host 'Public origin updated. Run start to recreate only services whose configuration changed.'
    }
    'start' {
        Invoke-Compose -DockerArguments @('up','-d','--build')
        if (!(Test-Path -LiteralPath (Join-Path $RepoRoot '.local/phase3a/models/w600k_mbf.onnx'))) {
            Write-Warning 'Model absent: face process may run, but model readiness and attendance baseline are blocked.'
        }
        Write-Warning 'Gateway portal forwarding remains disabled pending the Module 4 URL-session change.'
    }
    'build' {
        if ($Service -and $Service -notmatch '^[a-z][a-z0-9-]*$') { throw 'Invalid service name.' }
        $BuildArgs = @('build')
        if ($Service) { $BuildArgs += $Service }
        Invoke-Compose -DockerArguments $BuildArgs
    }
    'rebuild' {
        if ($Service) {
            if ($Service -notmatch '^[a-z][a-z0-9-]*$') { throw 'Invalid service name.' }
            Invoke-Compose -DockerArguments @('up','-d','--no-deps','--build',$Service)
        } else {
            Invoke-Compose -DockerArguments @('up','-d','--build')
        }
        Write-Warning 'Inspect service health: model-dependent readiness and gateway portal forwarding may remain blocked.'
    }
    'stop' { Invoke-Compose -DockerArguments @('stop') }
    'status' { Invoke-Compose -DockerArguments @('ps','-a') }
    'logs' {
        $LogArgs = @('logs','--tail','150')
        if ($Service) {
            if ($Service -notmatch '^[a-z][a-z0-9-]*$') { throw 'Invalid service name.' }
            $LogArgs += $Service
        }
        Invoke-Compose -DockerArguments $LogArgs
    }
}
