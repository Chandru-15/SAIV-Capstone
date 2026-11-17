"""Active colour-reflection liveness: did this face respond to OUR light?

The existing blink and head-turn challenges ask the subject to *move*. A
sufficiently good video replay satisfies both, because a replayed face blinks
and turns exactly like a real one. This challenge asks a different question,
one a replay cannot answer: the client screen emits a sequence of saturated
colours chosen by the server moments earlier, and the face must be seen to be
lit by them.

The physics, which is the whole reason this discriminates:

    A real face is a dim, matte, THREE-DIMENSIONAL surface. Light from the
    screen lands on it and scatters back roughly in proportion to how much
    each patch of skin faces the screen (Lambert's cosine law). The nose
    bridge, forehead and cheekbones catch more of it than the temples, the
    eye sockets or the underside of the chin. So the response is strong AND
    it varies across the face in a way that follows the face's shape.

    A printed photograph is matte but FLAT. Every part of it faces the screen
    equally, so it brightens and shifts hue uniformly. The response is strong
    and almost perfectly even.

    A phone or tablet replaying a video is a BRIGHT EMITTER behind glass. Its
    own emission swamps the little light it receives from us, so the colour
    response is weak; what little comes back is a specular reflection off
    flat glass, which arrives as a concentrated hotspot rather than a
    face-shaped gradient.

    A pre-recorded video, however it is shown, cannot respond at all. The
    sequence is chosen per challenge and is unknown when the recording was
    made.

Three measurements follow directly from those four paragraphs:

    response_correlation   does the face's CHROMATICITY track the sequence we
                           asked for? Chromaticity, not brightness, because
                           camera auto-exposure fights per-frame brightness
                           and would eat the signal; it cannot undo a hue
                           shift. Defeats replays and recordings.

    pattern_variation      does the pattern of light ACROSS the face change
                           as the illuminant changes? Divide each frame by
                           its own mean, so exposure cancels, and a flat
                           surface gives the identical pattern in every
                           frame: its albedo map over the mean of its albedo
                           map, with the illuminant cancelling out of both.
                           A face does not, because as the colour's luminance
                           rises and falls the shading slides between
                           ambient-dominated and screen-dominated. Flat
                           surfaces hold still; real ones breathe. Defeats
                           printed photographs -- including the obvious trap,
                           a print whose own INK reproduces the shading of
                           the face it depicts.

    specular_concentration did the whole response arrive in one small patch?
                           High for glass, low for skin. Defeats screens.

What this module does NOT do is decide anything about identity, and it never
sees an image that leaves the process: it is handed already-cropped face
regions, it returns numbers, and it holds no state.

The thresholds at the top are calibrated against the synthetic renderer in
tests/synthetic.py, which builds each of the four cases above from the same
reflectance equations described here. They are deliberately conservative and
are a starting point for real-device calibration, not a finished verdict --
see ``tests/test_colour_liveness.py`` for what is actually pinned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# The face box is trimmed by this fraction on each side before measuring.
# MediaPipe's box includes hair, ears and a margin of whatever is behind the
# subject; background pixels respond to the screen too (a wall right behind a
# held-up photograph responds beautifully) and would be read as face response.
FACE_INSET = 0.12

# Cells per side over the inset face region. 6x6 = 36 cells: fine enough that
# the nose and forehead land in different cells from the temples, coarse
# enough that each cell still averages hundreds of pixels and so is not
# dominated by sensor noise.
GRID = 6

# Below this the grid is meaningless -- a 30px face gives 5px cells, which is
# noise. Measured on the inset region, not the original frame.
MIN_REGION_PIXELS = 48

# Fewer frames than this and the correlation is not worth computing: with 3
# points a coincidence is likely.
MIN_FRAMES_FOR_VERDICT = 4

# Fewer than this many DISTINCT illuminants and there is nothing to correlate
# against, however many frames arrived.
MIN_DISTINCT_COLOURS = 3

# The sequence's own luminance dynamic range: brightest colour over dimmest.
#
# This is a requirement on the CALLER, and the pattern test is worthless
# without it. The shading only slides between ambient-dominated and
# screen-dominated if the screen's contribution actually changes by a large
# FACTOR across the sequence -- a difference is not enough, it has to be a
# ratio, because what varies is g.c/A. Six washed-out pastels span most of the
# 0..1 luminance range by difference and still only manage a ratio of ~6, at
# which a live face measures 0.015 and lands under the threshold: refused for
# being alive. Pure primaries reach ~12.9 because blue is a twelfth of the
# luminance of yellow.
#
# So a sequence under this is reported as undecided and named as such, rather
# than being scored into a verdict it cannot support. That is louder than
# silently returning "spoof" for a badly chosen palette.
MIN_LUMINANCE_RATIO = 8.0

# The response floor, as a coefficient of variation of face luminance across
# the sequence. Under this, the screen was too dim, too far away or washed out
# by ambient light, and the honest answer is "could not tell" -- NOT "spoof".
# Returning a refusal here would fail every student in a sunlit room.
AMPLITUDE_FLOOR = 0.015

# Decision thresholds, set from the parameter sweep in
# tests/test_colour_liveness.py::TestCalibration, which renders every case
# across screen gain, ambient level, auto-exposure, face size and seed and
# asserts these still sit inside the observed gaps:
#
#   pattern_variation      print <= 0.009   |   real >= 0.025    (~3x margin)
#   specular_concentration real  <= 0.005   |   screen >= 0.138  (~27x margin)
#
# They are calibrated against a reflectance model, not against phones. Real
# devices need a measurement pass before these are trustworthy in the field;
# the benchmark script exists for that.
CORRELATION_MIN = 0.55
PATTERN_VARIATION_MIN = 0.016
SPECULAR_CONCENTRATION_MAX = 0.05

_LUMA = np.float64([0.2126, 0.7152, 0.0722])


# -- colour helpers -----------------------------------------------------------

def parse_hex_colour(value: str) -> Tuple[float, float, float]:
    """``"#ff00aa"`` -> linear-light RGB in 0..1.

    Linear, not sRGB: reflected light adds linearly and the whole method is a
    statement about light. Correlating gamma-encoded values would bend an
    honest straight line.
    """
    text = (value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"Not a hex colour: {value!r}")
    try:
        srgb = np.float64([int(text[i:i + 2], 16) for i in (0, 2, 4)]) / 255.0
    except ValueError as exc:
        raise ValueError(f"Not a hex colour: {value!r}") from exc
    return tuple(srgb_to_linear(srgb))


def srgb_to_linear(values: np.ndarray) -> np.ndarray:
    """Undo the sRGB transfer function. Input and output in 0..1."""
    arr = np.asarray(values, dtype=np.float64)
    return np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)


def _chromaticity(rgb: np.ndarray) -> np.ndarray:
    """Each channel's share of the total. Scale-free, so a per-frame exposure
    change cancels out and only the hue shift survives."""
    arr = np.asarray(rgb, dtype=np.float64)
    total = arr.sum(axis=-1, keepdims=True)
    return np.divide(arr, total, out=np.full_like(arr, 1.0 / 3.0),
                     where=total > 1e-9)


def _pearson(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    """Correlation, or None when either side is constant.

    None rather than 0.0: a constant expectation gives no basis for a
    judgement, which is a different statement from "they disagree".
    """
    xc = np.asarray(x, dtype=np.float64) - np.mean(x)
    yc = np.asarray(y, dtype=np.float64) - np.mean(y)
    nx, ny = float(np.linalg.norm(xc)), float(np.linalg.norm(yc))
    if nx < 1e-9 or ny < 1e-9:
        return None
    return float(np.dot(xc, yc) / (nx * ny))


# -- region sampling ----------------------------------------------------------

def _inset(region: np.ndarray) -> np.ndarray:
    height, width = region.shape[:2]
    dy, dx = int(height * FACE_INSET), int(width * FACE_INSET)
    trimmed = region[dy:height - dy, dx:width - dx]
    return trimmed if trimmed.size else region


def _cell_means(region: np.ndarray, grid: int = GRID) -> np.ndarray:
    """Mean linear RGB per grid cell. Returns (grid*grid, 3)."""
    linear = srgb_to_linear(np.asarray(region, dtype=np.float64) / 255.0)
    height, width = linear.shape[:2]
    rows = np.linspace(0, height, grid + 1).astype(int)
    cols = np.linspace(0, width, grid + 1).astype(int)
    out = np.zeros((grid * grid, 3), dtype=np.float64)
    for r in range(grid):
        for c in range(grid):
            block = linear[rows[r]:rows[r + 1], cols[c]:cols[c + 1]]
            if block.size:
                out[r * grid + c] = block.reshape(-1, 3).mean(axis=0)
    return out


# -- result -------------------------------------------------------------------

@dataclass
class ColourReflectionResult:
    """``passed is None`` means "no verdict", never "failed".

    The caller must keep that distinction: a dim screen, a distant face or a
    sunlit room all land here, and treating them as spoofs would reject
    honest students for their lighting. Same rule the risk engine applies to
    an absent attestation verdict and an unknown client address.
    """

    passed: Optional[bool]
    score: float
    reason: Optional[str] = None
    response_correlation: Optional[float] = None
    response_amplitude: Optional[float] = None
    pattern_variation: Optional[float] = None
    specular_concentration: Optional[float] = None
    frames_measured: int = 0
    distinct_colours: int = 0
    per_channel_correlation: Dict[str, Optional[float]] = field(default_factory=dict)

    def as_details(self) -> Dict[str, object]:
        def rounded(value: Optional[float]) -> Optional[float]:
            return None if value is None else round(float(value), 4)

        return {
            "colour_challenge_passed": self.passed,
            "colour_response_score": rounded(self.score),
            "colour_reason": self.reason,
            "response_correlation": rounded(self.response_correlation),
            "response_amplitude": rounded(self.response_amplitude),
            "pattern_variation": rounded(self.pattern_variation),
            "specular_concentration": rounded(self.specular_concentration),
            "frames_measured": self.frames_measured,
            "distinct_colours": self.distinct_colours,
            "per_channel_correlation": {
                channel: rounded(value)
                for channel, value in self.per_channel_correlation.items()
            },
            "thresholds": {
                "response_correlation_min": CORRELATION_MIN,
                "pattern_variation_min": PATTERN_VARIATION_MIN,
                "specular_concentration_max": SPECULAR_CONCENTRATION_MAX,
                "response_amplitude_floor": AMPLITUDE_FLOOR,
                "luminance_ratio_min": MIN_LUMINANCE_RATIO,
            },
        }


def _undecided(reason: str, **extra) -> ColourReflectionResult:
    return ColourReflectionResult(passed=None, score=0.0, reason=reason, **extra)


# -- the analysis -------------------------------------------------------------

def analyse(
    face_regions: Sequence[Optional[np.ndarray]],
    expected_colours: Sequence[str],
) -> ColourReflectionResult:
    """Compare what the face did against the sequence we asked the screen for.

    ``face_regions[i]`` is the cropped face from frame ``i`` as RGB uint8, or
    None if no face was found in it; ``expected_colours[i]`` is the hex colour
    the screen was asked to be showing when frame ``i`` was taken. The two
    must be the same length -- the pairing IS the evidence, and a caller that
    cannot state which colour each frame belongs to has nothing to verify.
    """
    if len(face_regions) != len(expected_colours):
        raise ValueError(
            f"{len(face_regions)} frames but {len(expected_colours)} expected "
            "colours; each frame must say which colour it was taken under")

    usable: List[np.ndarray] = []
    illuminants: List[Tuple[float, float, float]] = []
    too_small = 0
    for region, colour in zip(face_regions, expected_colours):
        if region is None or region.size == 0:
            continue
        inset = _inset(region)
        if min(inset.shape[:2]) < MIN_REGION_PIXELS:
            too_small += 1
            continue
        usable.append(inset)
        illuminants.append(parse_hex_colour(colour))

    if too_small and not usable:
        return _undecided(
            "The face filled too little of the frame to measure the light on "
            "it. Hold the device closer and try again.",
            frames_measured=0)
    if len(usable) < MIN_FRAMES_FOR_VERDICT:
        return _undecided(
            "Too few frames showed a face to judge the colour response.",
            frames_measured=len(usable))

    expected = np.asarray(illuminants, dtype=np.float64)
    distinct = len({tuple(np.round(row, 6)) for row in expected})
    if distinct < MIN_DISTINCT_COLOURS:
        return _undecided(
            "The frames did not span enough different colours to judge a "
            "response.", frames_measured=len(usable), distinct_colours=distinct)

    expected_luma = expected @ _LUMA
    luminance_ratio = float(expected_luma.max() / max(float(expected_luma.min()), 1e-9))
    if luminance_ratio < MIN_LUMINANCE_RATIO:
        return _undecided(
            "The requested colour sequence does not span a wide enough range "
            "of brightness to tell a face from a flat picture.",
            frames_measured=len(usable), distinct_colours=distinct)

    cells = np.stack([_cell_means(region) for region in usable])   # (F, C, 3)
    face_mean = cells.mean(axis=1)                                 # (F, 3)
    luminance = cells @ _LUMA                                      # (F, C)
    face_luma = luminance.mean(axis=1)                             # (F,)

    # Did it respond at all? A coefficient of variation, so it is independent
    # of skin tone and of overall exposure.
    mean_luma = float(face_luma.mean())
    amplitude = float(face_luma.std() / mean_luma) if mean_luma > 1e-9 else 0.0

    # Chromaticity correlation, per channel and averaged. Auto-exposure can
    # flatten brightness but cannot repaint a hue.
    observed_chroma = _chromaticity(face_mean)
    expected_chroma = _chromaticity(expected)
    per_channel: Dict[str, Optional[float]] = {}
    usable_channels: List[float] = []
    for index, channel in enumerate("rgb"):
        value = _pearson(expected_chroma[:, index], observed_chroma[:, index])
        per_channel[channel] = value
        if value is not None:
            usable_channels.append(value)
    # Negative is anti-correlation, which is not a weak response but a wrong
    # one; clamping to 0 keeps it below any threshold without pretending the
    # sign carries information we act on.
    correlation = max(0.0, float(np.mean(usable_channels))) if usable_channels else None

    # Does the pattern across the face CHANGE as the illuminant changes?
    #
    # Dividing each frame by its own mean removes every per-frame global gain,
    # which is exactly what auto-exposure applies, so what is left is the
    # shape of the light over the face and nothing about its level. For a
    # flat surface that shape is the same in every frame:
    #
    #     a(x).(A + g.c_i) / mean_x[a(x).(A + g.c_i)]  =  a(x) / mean_x[a(x)]
    #
    # the illuminant cancels top and bottom, leaving the albedo map. This is
    # what makes it immune to the trap that defeated a simpler measure: a
    # printed photograph carries the face's shading in its ink, so its
    # response is just as uneven as a real face's -- but it is uneven in the
    # SAME way in every frame. A real face is not, because (N.L) weights the
    # screen's contribution per pixel while ambient does not, so the shading
    # slides as the colour's luminance rises and falls.
    normalised = luminance / np.maximum(face_luma[:, None], 1e-9)   # (F, C)
    pattern_variation = float(normalised.std(axis=0).mean())

    # Where the response physically landed, in absolute terms. Skin scatters
    # it over the whole face; flat glass returns an image of the screen, which
    # is a small bright patch. Measured on the raw swing rather than anything
    # normalised, because "one cell holds most of the total response" is the
    # claim being made.
    cell_swing = luminance.std(axis=0)                              # (C,)
    swing_total = float(cell_swing.sum())
    if swing_total > 1e-12:
        share = cell_swing / swing_total
        uniform = 1.0 / len(cell_swing)
        specular = float((share.max() - uniform) / (1.0 - uniform))
    else:
        specular = 0.0
    cell_mean = luminance.mean(axis=0)
    peak_modulation = float(np.max(np.where(cell_mean > 1e-9,
                                            cell_swing / np.maximum(cell_mean, 1e-9), 0.0)))

    stats = dict(
        response_correlation=correlation,
        response_amplitude=amplitude,
        pattern_variation=pattern_variation,
        specular_concentration=specular,
        frames_measured=len(usable),
        distinct_colours=distinct,
        per_channel_correlation=per_channel,
    )

    score = _score(correlation or 0.0, pattern_variation, specular)

    # Checked before the amplitude floor, and deliberately. A tablet's own
    # emission swamps our light, so the face as a whole barely responds --
    # which looks like "too dim to tell". But the glass still throws back a
    # bright highlight, so SOME cell responded strongly while the face did
    # not. That combination is evidence, not darkness, and must not be
    # allowed to retire as an inconclusive result. The peak guard keeps
    # sensor noise in a genuinely dark frame from reaching this branch.
    if specular > SPECULAR_CONCENTRATION_MAX and peak_modulation >= AMPLITUDE_FLOOR:
        return ColourReflectionResult(
            passed=False, score=score,
            reason="The screen's light came back from one small bright spot "
                   "rather than from a face. A phone or tablet held up to "
                   "the camera looks like this.", **stats)
    if amplitude < AMPLITUDE_FLOOR:
        return _undecided(
            "The screen's light did not reach the face strongly enough to "
            "measure. Raise your screen brightness, move somewhere less "
            "bright, and hold the device closer.", **stats)
    if correlation is None:
        return _undecided(
            "The requested colours did not differ enough to measure a "
            "response.", **stats)

    if correlation < CORRELATION_MIN:
        return ColourReflectionResult(
            passed=False, score=score,
            reason="The face did not change colour with the screen. A "
                   "photograph, a recording or another screen would look "
                   "like this.", **stats)
    if pattern_variation < PATTERN_VARIATION_MIN:
        return ColourReflectionResult(
            passed=False, score=score,
            reason="The whole image brightened evenly instead of shading "
                   "across a face. A flat printed photograph looks like "
                   "this.", **stats)

    return ColourReflectionResult(passed=True, score=score, **stats)


def _score(correlation: float, pattern_variation: float, specular: float) -> float:
    """A continuous 0..1 reading of the same three numbers the verdict uses.

    The verdict is the gate; this only lets a borderline-but-passing response
    land lower than a textbook one in the aggregate liveness score. Each term
    is normalised so that exactly meeting its threshold scores 0.5.
    """
    def ramp(value: float, threshold: float) -> float:
        if threshold <= 0:
            return 1.0
        return float(np.clip(0.5 * value / threshold, 0.0, 1.0))

    def inverse_ramp(value: float, limit: float) -> float:
        if limit >= 1.0:
            return 1.0
        return float(np.clip(1.0 - 0.5 * value / limit, 0.0, 1.0))

    return float(np.clip(
        0.50 * ramp(correlation, CORRELATION_MIN)
        + 0.30 * ramp(pattern_variation, PATTERN_VARIATION_MIN)
        + 0.20 * inverse_ramp(specular, SPECULAR_CONCENTRATION_MAX),
        0.0, 1.0))
