"""Rebuild CASIA-FASD clips from a flat frame dump, for evaluate_colour_liveness.

The frame releases of CASIA-FASD ship one flat folder of JPEGs named like

    28_HR_2.avi_125_fake.jpg
    |  |      |   |
    |  |      |   label
    |  |      frame number inside the original video
    |  capture quality (HR = the high-resolution camera)
    subject

which is the wrong shape for this benchmark twice over: the measurements are
temporal, so frames have to be grouped back into the video they came from and
put in shooting order, and a flat folder would instead interleave several
hundred unrelated captures into one "clip".

    python benchmark/prepare_casia_fasd.py /data/casiafasd /data/casia-clips
    python benchmark/evaluate_colour_liveness.py /data/casia-clips --trials 5

Nothing is modified in the source folder. Depth maps are skipped: the challenge
measures colour, and a synthesised depth map has none.
"""

import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

NAME = re.compile(r"^(?P<clip>.+)\.avi_(?P<frame>\d+)_(?P<label>real|fake)\.jpg$")
MIN_FRAMES = 4          # colour_liveness.MIN_FRAMES_FOR_VERDICT


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    raw, out = Path(sys.argv[1]), Path(sys.argv[2])
    if not raw.is_dir():
        print(f"Not a folder: {raw}")
        return 2

    clips = defaultdict(list)
    unparsed = 0
    for path in raw.rglob("*.jpg"):
        if "depth" in {part.lower() for part in path.parts}:
            continue
        match = NAME.match(path.name)
        if not match:
            unparsed += 1
            continue
        split = "test" if any("test" in p for p in path.parts) else "train"
        label = "real" if match["label"] == "real" else "attack"
        clips[(label, split, match["clip"])].append((int(match["frame"]), path))

    if not clips:
        print(f"No CASIA-FASD frame names found under {raw}. Expected files "
              f"like 1_2.avi_75_real.jpg; {unparsed} file(s) did not match.")
        return 1

    if out.exists():
        shutil.rmtree(out)
    kept = short = 0
    for (label, split, clip), frames in sorted(clips.items()):
        if len(frames) < MIN_FRAMES:
            short += 1
            continue
        folder = out / f"{label}_{split}_{clip}"
        folder.mkdir(parents=True)
        # Zero-padded, so the benchmark's name sort is shooting order.
        for index, (_, path) in enumerate(sorted(frames)):
            shutil.copyfile(path, folder / f"{index:03d}.jpg")
        kept += 1

    print(f"{kept} clips written to {out}")
    if short:
        print(f"{short} clip(s) skipped: fewer than {MIN_FRAMES} frames")
    if unparsed:
        print(f"{unparsed} file(s) skipped: name did not parse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
