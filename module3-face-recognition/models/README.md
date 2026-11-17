# Face embedding model

`w600k_mbf.onnx` - ArcFace-style MobileFaceNet (512-d embeddings, 112x112 RGB input),
trained on WebFace600K. It is the recognition model from InsightFace's `buffalo_sc` pack:
https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip

- Size ~13 MB, runs on CPU with ONNX Runtime (~60 ms per face in testing).
- Override the location with the `FACE_EMBED_MODEL_PATH` environment variable.
- Licensing: InsightFace states its pretrained models are for non-commercial research use.
  Fine for coursework; check their license before using it in a commercial product.
- Make sure `*.onnx` is not in `.gitignore`, or teammates will clone without the model
  (the service then answers 503 "Face embedding model is not available").
