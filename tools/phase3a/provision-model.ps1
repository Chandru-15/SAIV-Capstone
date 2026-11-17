# Local-development provisioning only. No downloads, threshold changes or Docker restarts.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $RepoRoot
if ((& git branch --show-current).Trim() -ne 'feature/frontend-improvements') {
    throw 'Provisioning is restricted to feature/frontend-improvements.'
}
$CandidateDir = Join-Path $RepoRoot '.local/phase3a/model-candidate'
$ModelDir = Join-Path $RepoRoot '.local/phase3a/models'
$ArchivePath = Join-Path $CandidateDir 'buffalo_sc.zip'
$CandidatePath = Join-Path $CandidateDir 'w600k_mbf.onnx'
$TargetPath = Join-Path $ModelDir 'w600k_mbf.onnx'
$ArchiveSha = '57d31b56b6ffa911c8a73cfc1707c73cab76efe7f13b675a05223bf42de47c72'
$ModelSha = '9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f'
foreach ($Check in @(@($ArchivePath,$ArchiveSha),@($CandidatePath,$ModelSha))) {
    if (!(Test-Path -LiteralPath $Check[0] -PathType Leaf)) { throw 'Verified local candidate is missing. No alternative will be downloaded.' }
    if ((Get-FileHash -LiteralPath $Check[0] -Algorithm SHA256).Hash -ne $Check[1]) { throw 'Candidate checksum mismatch; provisioning refused.' }
}
New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null
if (Test-Path -LiteralPath $TargetPath) {
    if ((Get-FileHash -LiteralPath $TargetPath -Algorithm SHA256).Hash -ne $ModelSha) {
        throw 'A different target model already exists; it will not be overwritten.'
    }
    Write-Host 'Expected development model already present; preserved.'
} else {
    Copy-Item -LiteralPath $CandidatePath -Destination $TargetPath
}
if ((Get-FileHash -LiteralPath $TargetPath -Algorithm SHA256).Hash -ne $ModelSha) { throw 'Provisioned model integrity check failed.' }
$Provenance = [ordered]@{
    scope = 'User-authorized isolated development/testing only; team/production approval pending'
    release = 'https://github.com/deepinsight/insightface/releases/tag/model-zoo'
    archive_url = 'https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_sc.zip'
    archive_sha256 = $ArchiveSha
    model_sha256 = $ModelSha
    container_path = '/app/models/w600k_mbf.onnx'
}
[IO.File]::WriteAllText((Join-Path $ModelDir 'provenance.json'), ($Provenance | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
Write-Host 'Provisioned verified development-only model through the existing read-only bind mount.'
Write-Host 'No services restarted. Run verify-model.ps1 and real fixture checks before claiming readiness.'
