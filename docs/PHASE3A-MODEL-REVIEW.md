# Phase 3A recognition candidate — isolated development enabled

Update, 5 November 2026: the user explicitly authorized this exact verified candidate
for isolated development/testing. It is now provisioned read-only and actual fixture
enrollment/matching passes; see `PHASE3A-FACE-READINESS.md`. Final team/production
approval remains pending. No thresholds, algorithm, alignment, hash seed or projection
were changed. The candidate inspection below records the earlier quarantine checks.

## Provenance and integrity

- Publisher/repository: `deepinsight/insightface`.
- Official release: https://github.com/deepinsight/insightface/releases/tag/model-zoo
- API metadata: https://api.github.com/repos/deepinsight/insightface/releases/tags/model-zoo
- Exact asset URL: https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_sc.zip
- ZIP size: **14,969,382 bytes**.
- GitHub API-published ZIP SHA-256, independently matched to the actual download:
  `57d31b56b6ffa911c8a73cfc1707c73cab76efe7f13b675a05223bf42de47c72`.
- ZIP entries: `det_500m.onnx` (2,524,817 bytes) and `w600k_mbf.onnx`
  (13,616,099 bytes). Only the recognition entry was extracted, using a fixed target
  filename rather than extracting arbitrary archive paths. The detection model is
  not used; the merged service retains its MediaPipe detection/alignment pipeline.
- SHA-256 of the extracted **ONNX file**, computed after verified ZIP extraction:
  `9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f`.

These are different hashes for different artifacts. The extracted hash is linked
to a publisher-digest-verified archive; it is not presented as an independent
published ONNX digest. The release states mirrored legacy files are identical
unless otherwise noted. That statement does not prove the team's prior deployment
used this exact file. The older `v0.7` asset metadata was also checked and lacked a
digest; the newer release supplied the missing verifiable integrity record.

The archive, file and original API asset record are ignored local artifacts under
`.local/phase3a/model-candidate`. After the subsequent development-only authorization,
the identical verified ONNX file was copied to `.local/phase3a/models`, mounted
read-only by the running application. Neither model artifact is in Git.

## Architecture and compatibility inspection

Inspection ran in a disposable face-image container with no server ports published
and a read-only candidate mount. `onnx==1.17.0` was installed **only in that
disposable container**, not in the running service or application requirements.
The graph passed `onnx.checker.check_model` with no external tensor files.

| Property | Observed result |
|---|---|
| Producer / graph | `pytorch` / `torch-jit-export` |
| ONNX format | IR 6, standard opset 11 |
| Input | `input.1`, float32, dynamic batch x 3 x 112 x 112 |
| Output | `516`, float32, 1 x 512 |
| Graph operators | 49 Conv, 34 PRelu, 12 Add, 1 Flatten, 1 Gemm, 1 BatchNormalization |
| Grouped convolutions | 16 |
| Runtime | ONNX Runtime 1.20.1, CPU |
| Synthetic RGB input | Existing `(pixel - 127.5) / 127.5`, NCHW layout accepted |
| Output checks | 512 finite values, nonzero norm, L2 normalization succeeds |
| Hash contract | Existing 256-bit projection shape produces 64 hexadecimal characters |

The graph structure is consistent with the upstream MobileFaceNet recognition
artifact expected by the merged implementation. Tensor and preprocessing compatibility
passed. A single synthetic inference took approximately 0.0067 seconds on this
machine; this is not a recognition benchmark, attendance latency guarantee or phone
performance result.

## Pending owner decisions and tests

1. Module 3 teammate confirms this exact artifact/version for final team/production
   use; the user's authorization currently covers isolated development/testing only.
2. Confirm training/preprocessing assumptions and permitted non-commercial course
   usage under the upstream model terms: https://github.com/deepinsight/insightface
3. If prior templates matter, compare the original model checksum, preprocessing,
   alignment and SimHash seed/projection. Tensor compatibility alone cannot establish
   template compatibility. No existing template was replaced or re-enrolled here.
4. Development provisioning, hash/load verification and real fixture recognition
   now pass. Successful real-camera blink/head-turn capture remains manual.
5. Resolve any actual backend/face contract failure without weakening pose, matching,
   consent, liveness, device or risk controls.

**Current result:** provenance/checksum, technical compatibility and actual fixture
enrollment/recognition verified in development. Final owner approval, broader accuracy,
prior-template compatibility and physical attendance acceptance remain pending.
