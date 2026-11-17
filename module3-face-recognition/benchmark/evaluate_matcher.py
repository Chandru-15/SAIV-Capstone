"""
Measure how well the face matcher separates "same person" from "different people".

Usage (from module3-face-recognition/):
    python benchmark/evaluate_matcher.py path/to/faces
    # or inside Docker:
    docker compose run --rm -v "%cd%/faces:/faces" face-recognition python benchmark/evaluate_matcher.py /faces

Folder layout - one sub-folder per person, 2+ photos each, ideally taken on DIFFERENT
devices / lighting / days (that is where real failures come from):

    faces/
      alice/  laptop.jpg  phone_indoor.jpg  phone_outdoor.jpg
      bob/    a.jpg  b.jpg  c.jpg
      ...

It enrolls every photo through the real /face/enroll endpoint, then scores every
same-person pair (genuine) and every different-person pair (impostor) exactly as
/face/verify would, and prints FRR/FAR at the shipped 0.70 threshold plus a suggested
FACE_COSINE_THRESHOLD. Photos are processed in memory only; nothing is written to disk.
"""
import base64
import io
import itertools
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402

from app import main as m  # noqa: E402

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_SIDE = 1280  # roughly what a phone camera frame looks like after upload


def encode(path: Path) -> str:
    image = Image.open(path).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode()


def estimated_cosine(h1: str, h2: str) -> float:
    distance = bin(int(h1, 16) ^ int(h2, 16)).count("1")
    return float(np.cos(np.pi * distance / m.FACE_HASH_BITS))


def main(root: Path) -> None:
    client = TestClient(m.app)
    hashes = {}  # (person, filename) -> hash
    for person_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for photo in sorted(p for p in person_dir.iterdir() if p.suffix.lower() in EXTS):
            r = client.post("/face/enroll", json={"user_id": person_dir.name, "image": encode(photo), "camera_consent": True})
            if r.status_code == 201:
                hashes[(person_dir.name, photo.name)] = r.json()["face_template_hash"]
            else:
                print(f"  could not enroll {person_dir.name}/{photo.name}: {r.json().get('detail')}")

    people = sorted({p for p, _ in hashes})
    print(f"\nEnrolled {len(hashes)} photos of {len(people)} people.")
    genuine, impostor = [], []
    for (pa, fa), (pb, fb) in itertools.combinations(hashes, 2):
        pair = (hashes[(pa, fa)], hashes[(pb, fb)])
        (genuine if pa == pb else impostor).append((m.compare_face_hashes(*pair), estimated_cosine(*pair), f"{pa}/{fa} vs {pb}/{fb}"))
    if not genuine or not impostor:
        sys.exit("Need at least 2 people and 2 photos for someone to compute genuine and impostor pairs.")

    gs, gc = np.array([g[0] for g in genuine]), np.array([g[1] for g in genuine])
    is_, ic = np.array([i[0] for i in impostor]), np.array([i[1] for i in impostor])
    t = m.FACE_MATCH_THRESHOLD
    print(f"Same-person pairs: {len(gs)}   Different-person pairs: {len(is_)}\n")
    print(f"Score at threshold {t:.2f}:")
    print(f"  Real user rejected  (FRR): {(gs < t).mean():6.1%}  ({(gs < t).sum()}/{len(gs)})")
    print(f"  Stranger accepted   (FAR): {(is_ >= t).mean():6.1%}  ({(is_ >= t).sum()}/{len(is_)})\n")
    print(f"Estimated cosine - same person: min {gc.min():.3f}  median {np.median(gc):.3f} | different people: max {ic.max():.3f}  median {np.median(ic):.3f}")
    print(f"Current FACE_COSINE_THRESHOLD = {m.FACE_COSINE_THRESHOLD}")
    if gc.min() > ic.max():
        print(f"Clean gap -> suggested FACE_COSINE_THRESHOLD = {(gc.min() + ic.max()) / 2:.2f}  (midpoint of {ic.max():.3f} and {gc.min():.3f})")
    else:
        print("The two groups overlap: suggested threshold at equal error rate:")
        grid = np.linspace(0, 1, 201)
        best = min(grid, key=lambda x: abs((gc < x).mean() - (ic >= x).mean()))
        print(f"  FACE_COSINE_THRESHOLD ~ {best:.2f}  (FRR {(gc < best).mean():.1%}, FAR {(ic >= best).mean():.1%})")
    worst = sorted(genuine)[:5]
    print("\nHardest same-person pairs (most likely real-world failures):")
    for score, cos, name in worst:
        print(f"  score {score:.3f}  cos {cos:.3f}  {name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
