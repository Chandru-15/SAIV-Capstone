[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedSha256
)
$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$ModelPath = Join-Path $RepoRoot '.local/phase3a/models/w600k_mbf.onnx'
if (!(Test-Path -LiteralPath $ModelPath -PathType Leaf)) { throw 'Approved model has not been provisioned.' }
$Actual = (Get-FileHash -LiteralPath $ModelPath -Algorithm SHA256).Hash
if ($Actual -ne $ExpectedSha256) { throw 'Model checksum mismatch. Do not start model-dependent testing.' }
Write-Host 'Model SHA-256 matches the supplied trusted checksum. This does not establish provenance by itself.'
Set-Location -LiteralPath $RepoRoot
$Probe = @'
import numpy as np
from app.main import _get_embedder
s, name = _get_embedder()
inputs, outputs = s.get_inputs(), s.get_outputs()
print('Inputs:', [(x.name, x.shape, x.type) for x in inputs])
print('Outputs:', [(x.name, x.shape, x.type) for x in outputs])
assert len(inputs) == 1 and inputs[0].type == 'tensor(float)'
assert inputs[0].shape[1:] == [3, 112, 112]
embedding = s.run(None, {name: np.zeros((1, 3, 112, 112), dtype=np.float32)})[0]
assert embedding.size == 512 and np.isfinite(embedding).all()
print('PASS expected tensor shape and finite synthetic inference; recognition quality remains untested')
'@
$Probe | & docker compose --env-file .env.phase3a -p saiv-phase3a -f compose.phase3a.yml exec -T face-recognition python -
if ($LASTEXITCODE -ne 0) { throw 'ONNX model loading failed.' }
