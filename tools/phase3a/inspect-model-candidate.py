"""Inspect the checksum-verified candidate in a disposable container only.

Does not provision /app/models or start an API. No photographs or user templates.
ONNX 1.17.0 is an inspection-only dependency, not an application requirement.
"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import onnx
import onnxruntime as ort

path = Path('/candidate/w600k_mbf.onnx')
assert sha256(path.read_bytes()).hexdigest() == '9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f'
model = onnx.load(str(path), load_external_data=False)
assert not any(t.data_location == onnx.TensorProto.EXTERNAL for t in model.graph.initializer)
onnx.checker.check_model(model)
ops = Counter(node.op_type for node in model.graph.node)
groups = []
for node in model.graph.node:
    if node.op_type == 'Conv':
        groups.extend(int(a.i) for a in node.attribute if a.name == 'group')
options = ort.SessionOptions()
options.log_severity_level = 3
session = ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
inputs, outputs = session.get_inputs(), session.get_outputs()
assert len(inputs) == 1 and inputs[0].type == 'tensor(float)'
assert inputs[0].shape[1:] == [3, 112, 112]
rng = np.random.RandomState(20240)
chip = rng.randint(0, 256, size=(112,112,3), dtype=np.uint8)
blob = ((chip.astype(np.float32) - 127.5) / 127.5).transpose(2,0,1)[None]
start = perf_counter()
embedding = np.asarray(session.run(None, {inputs[0].name:blob})[0][0], dtype=np.float32)
elapsed = perf_counter() - start
assert embedding.size == 512 and np.isfinite(embedding).all()
norm = float(np.linalg.norm(embedding))
assert norm > 0
normalized = embedding / norm
assert np.isclose(np.linalg.norm(normalized), 1, atol=1e-5)
projection = np.random.RandomState(20240).standard_normal((256,512)).astype(np.float32)
fingerprint = np.packbits((projection @ normalized) > 0).tobytes().hex()
assert len(fingerprint) == 64
result = {
    'model_sha256':sha256(path.read_bytes()).hexdigest(), 'bytes':path.stat().st_size,
    'producer':model.producer_name, 'graph_name':model.graph.name,
    'ir_version':model.ir_version, 'opsets':[(x.domain,x.version) for x in model.opset_import],
    'operators':dict(ops), 'grouped_conv_count':sum(g>1 for g in groups),
    'inputs':[(x.name,x.shape,x.type) for x in inputs],
    'outputs':[(x.name,x.shape,x.type) for x in outputs],
    'onnxruntime':ort.__version__, 'synthetic_inference_seconds':round(elapsed,4),
    'finite_512d_embedding':True, 'normalization_and_64hex_hash_contract':True,
    'application_integration':False,
    'limitations':['No real-face accuracy/liveness test','No prior-template compatibility proof','Module 3 approval pending'],
}
print(json.dumps(result, indent=2))
