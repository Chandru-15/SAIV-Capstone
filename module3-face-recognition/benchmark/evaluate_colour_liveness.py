"""Measure the colour-reflection challenge against real captures.

Two things need measuring, and only one of them can use a public dataset.

TRUE NEGATIVES -- any public dataset works, and this is the point most people
miss. NUAA, CASIA-FASD, Replay-Attack, OULU-NPU, SiW and CelebA-Spoof are all
passive captures under ambient or studio light. None contains a face lit by a
sequence a server chose seconds earlier, because that sequence does not exist
until the challenge is issued. So every clip in all of them -- the genuine
ones included -- IS the replay attack as far as this challenge is concerned,
and every one of them must be refused. A dataset of 10,000 real faces that
this challenge accepts would be a 10,000-case false-accept rate.

    python benchmark/evaluate_colour_liveness.py /data/casia-fasd/clips

TRUE POSITIVES -- no public dataset can supply these. They have to be captured
through the app, which emits the sequence, and saved with the colour each
frame was taken under. Run the check-in flow with SAIV_LIVENESS_DUMP set, then
point this at the folder; a `colours.txt` of one hex colour per line, ordered
like the frames, switches it to scoring the capture against its real sequence.

    python benchmark/evaluate_colour_liveness.py /captures/mine --lit

Expected output in the two modes:

    without --lit   passes: 0        anything else is a false accept
    with --lit      passes: all      anything else is a false reject

Folder layout, either mode -- one sub-folder per capture, FRAMES SORTED BY NAME
and in shooting order, which is what a zero-padded dump gives you. Order is not
cosmetic: every measurement here is temporal, and a shuffled folder is a
different experiment.

    clips/
      attack_01/  000.jpg 001.jpg 002.jpg ...
      attack_02/  000.jpg ...

Each clip is scored against several independent sequences (--trials), because
one sequence per clip measures one draw, not the challenge: the server picks
the sequence, so the attacker's real success rate is the share of sequences its
footage happens to satisfy. Face detection runs once per clip and is reused
across trials, so trials are nearly free.

Images are read, measured and dropped; nothing is written and no frame leaves
the process.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import colour_liveness as cl          # noqa: E402
from app.main import _crop_face, detect_face  # noqa: E402

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_SIDE = 1280

# The same palette and the same construction module 2 issues from -- see
# module2-backend/app/services/liveness_challenge.py. Copied rather than
# imported because this script runs inside module 3, which cannot import module
# 2; `test_colour_liveness.py` pins the values that matter, and the ratio
# assertion below catches any drift at the point it would distort a result.
#
# Mirroring the real generator is not a detail. Six colours drawn uniformly
# from this palette often fail colour_liveness.MIN_LUMINANCE_RATIO, and module
# 3 then returns no verdict at all -- so a uniform draw quietly spends a large
# share of the dataset on sequences the server would never issue, and reports
# them as "undecided" rather than as the refusals they should have been.
PALETTE = ("#ff0000", "#00ff00", "#0000ff", "#00ffff", "#ff00ff", "#ffff00",
           "#ffffff")
DARKEST = "#0000ff"
BRIGHT_ENOUGH = ("#00ff00", "#00ffff", "#ffff00", "#ffffff")
FRAMES_PER_STEP = 2


def fresh_sequence(rng, steps: int) -> List[str]:
    """One sequence, built as the server builds it.

    Seeded with the dimmest colour plus one bright one so the luminance ratio
    clears module 3's floor structurally, shuffled, then adjacent repeats are
    broken up -- a repeated step is one during which nothing changes. Uses the
    benchmark's seeded RNG rather than a CSPRNG: this is a measurement that has
    to be reproducible, and nothing here is issued to a client.
    """
    sequence = [DARKEST, BRIGHT_ENOUGH[rng.integers(len(BRIGHT_ENOUGH))]]
    sequence += [PALETTE[i] for i in rng.integers(0, len(PALETTE),
                                                  max(0, steps - 2))]
    order = rng.permutation(len(sequence))
    sequence = [sequence[i] for i in order]
    for i in range(1, len(sequence)):
        if sequence[i] != sequence[i - 1]:
            continue
        neighbours = {sequence[i - 1]}
        if i + 1 < len(sequence):
            neighbours.add(sequence[i + 1])
        options = [c for c in PALETTE if c not in neighbours]
        if options:
            sequence[i] = options[rng.integers(len(options))]
    return sequence


def colours_for_frames(rng, count: int, frames_per_step: int) -> List[str]:
    """A fresh sequence expanded to one colour per frame, as the server pairs it.

    Each colour is held for `frames_per_step` frames, exactly as
    `LivenessChallenge.colours_per_frame` expands it, so a clip is scored
    against the shape the deployed challenge actually has.

    The step count is floored and the clip's leftover tail frames are left
    unscored, rather than truncating the expanded sequence to the frame count.
    Truncating looks equivalent and is not: it can cut off the step carrying
    the dark seed colour, which collapses the luminance ratio and turns what
    should have been a refusal into "no verdict". The full dataset run found
    that on four-frame clips before this was a floor.
    """
    steps = count // frames_per_step
    if steps < cl.MIN_DISTINCT_COLOURS:
        return []                     # too short to carry a valid challenge
    colours = [c for c in fresh_sequence(rng, steps)
               for _ in range(frames_per_step)]
    ratio = _luminance_ratio(colours)
    if ratio < cl.MIN_LUMINANCE_RATIO:
        raise AssertionError(
            f"Built a sequence with luminance ratio {ratio:.2f}, below module "
            f"3's {cl.MIN_LUMINANCE_RATIO}; the result would be 'no verdict', "
            f"not a refusal: {colours}")
    return colours


def _luminance_ratio(colours: Sequence[str]) -> float:
    luma = np.asarray([cl.parse_hex_colour(c) for c in colours],
                      dtype=np.float64) @ cl._LUMA
    return float(luma.max() / max(float(luma.min()), 1e-9))


def load(path: Path) -> Optional[np.ndarray]:
    try:
        image = Image.open(path)
        image.load()
        image = image.convert("RGB")
    except Exception as exc:
        print(f"  ! {path.name}: {type(exc).__name__}: {exc}")
        return None
    if max(image.size) > MAX_SIDE:
        scale = MAX_SIDE / max(image.size)
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    return np.array(image)


def face_regions(frames: Sequence[np.ndarray]) -> List[Optional[np.ndarray]]:
    regions: List[Optional[np.ndarray]] = []
    for frame in frames:
        detection = detect_face(frame)
        regions.append(_crop_face(frame, detection["bounding_box"])
                       if detection["detected"] else None)
    return regions


def recorded_colours(folder: Path, count: int) -> Optional[List[str]]:
    """The capture's own sequence, for --lit. One hex colour per frame."""
    path = folder / "colours.txt"
    if not path.exists():
        print(f"  ! {folder.name}: --lit needs colours.txt (one hex per frame)")
        return None
    colours = [line.strip() for line in path.read_text().splitlines()
               if line.strip()]
    if len(colours) != count:
        print(f"  ! {folder.name}: colours.txt has {len(colours)} lines for "
              f"{count} frames")
        return None
    return colours


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, help="folder of capture sub-folders")
    parser.add_argument("--lit", action="store_true",
                        help="captures were taken through the app; score each "
                             "against its own colours.txt")
    parser.add_argument("--trials", type=int, default=5,
                        help="fresh sequences per clip (negative mode only); "
                             "the server picks the sequence, so this is the "
                             "attacker's success rate over draws")
    parser.add_argument("--frames-per-step", type=int, default=FRAMES_PER_STEP,
                        help="frames held per colour, as module 2 issues it")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--quiet", action="store_true",
                        help="per-clip lines only when something passed")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Not a folder: {args.root}")
        return 2

    folders = sorted(p for p in args.root.iterdir() if p.is_dir())
    if not folders:
        folders = [args.root]        # a single flat folder of frames
    rng = np.random.default_rng(args.seed)
    trials = 1 if args.lit else max(1, args.trials)

    verdicts: Counter = Counter()
    reasons: Counter = Counter()
    accepted_once: List[str] = []
    measured: List[cl.ColourReflectionResult] = []

    for folder in folders:
        paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTS)
        if len(paths) < cl.MIN_FRAMES_FOR_VERDICT:
            print(f"  - {folder.name}: {len(paths)} frames, need "
                  f"{cl.MIN_FRAMES_FOR_VERDICT}")
            verdicts["skipped"] += trials
            continue
        frames = [f for f in (load(p) for p in paths) if f is not None]
        # Detect once; the face does not move because we changed the sequence
        # we score it against.
        regions = face_regions(frames)

        clip: Counter = Counter()
        for _ in range(trials):
            colours = (recorded_colours(folder, len(frames)) if args.lit
                       else colours_for_frames(rng, len(frames),
                                               args.frames_per_step))
            if not colours:
                verdicts["skipped"] += 1
                continue
            # Score only the frames a whole number of steps covers; the colour
            # of a frame is its position, so a trailing partial step has no
            # colour to be scored against.
            result = cl.analyse(regions[:len(colours)], colours)
            measured.append(result)
            label = {True: "passed", False: "refused", None: "undecided"}[result.passed]
            verdicts[label] += 1
            clip[label] += 1
            if result.reason:
                reasons[result.reason.split(".")[0]] += 1

        if clip["passed"]:
            accepted_once.append(f"{folder.name} ({clip['passed']}/{trials})")
        if not args.quiet or clip["passed"]:
            worst = max(clip, key=lambda k: (k == "passed", k == "undecided",
                                             clip[k])) if clip else "skipped"
            print(f"  {worst:<9} {folder.name:<30} "
                  f"pass={clip['passed']}/{trials}")

    print()
    print("=" * 72)
    total = sum(verdicts[k] for k in ("passed", "refused", "undecided"))
    print(f"clips {len(folders)}   trials/clip {trials}   scored sequences {total}")
    for label in ("passed", "refused", "undecided", "skipped"):
        print(f"{label:<10} {verdicts[label]:>6}")
    if reasons:
        print("\nreasons:")
        for reason, count in reasons.most_common():
            print(f"  {count:>6}  {reason}")

    if total and measured:
        print("\nmeasured (min / median / max):")
        for name in ("response_correlation", "pattern_variation",
                     "specular_concentration", "response_amplitude"):
            values = [getattr(r, name) for r in measured
                      if getattr(r, name) is not None]
            if values:
                print(f"  {name:<24} {min(values):.4f} / "
                      f"{float(np.median(values)):.4f} / {max(values):.4f}")

    print()
    if args.lit:
        bad = verdicts["refused"] + verdicts["undecided"]
        print(f"FALSE REJECTS: {bad} of {total}" if bad else
              f"All {total} genuine captures accepted.")
        return 1 if bad else 0
    if verdicts["passed"]:
        print(f"FALSE ACCEPTS: {verdicts['passed']} of {total} scored "
              f"sequences, over {len(accepted_once)} clip(s):")
        for line in accepted_once[:40]:
            print(f"  {line}")
        return 1
    print(f"All {total} scored sequences refused, as they must be: none of "
          f"these captures was lit by the sequence it was scored against.")
    return 0


def _fmt(value: Optional[float]) -> str:
    return "  n/a " if value is None else f"{value:6.4f}"


if __name__ == "__main__":
    raise SystemExit(main())
