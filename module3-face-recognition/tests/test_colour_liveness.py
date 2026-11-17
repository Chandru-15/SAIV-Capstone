"""Colour-reflection liveness, tested against the reflectance model.

These tests assert the physics the detector claims to read, not a table of
recorded outputs: every frame set is rendered from the equations in
tests/synthetic.py, so a test failing means the detector stopped reading what
it says it reads.

What they do NOT establish is field accuracy on real phones. See
TestCalibration for the margins that are actually pinned, and
benchmark/evaluate_colour_liveness.py for real captures.
"""

import itertools
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import colour_liveness as cl          # noqa: E402
from tests import synthetic as syn            # noqa: E402

# Six saturated colours spanning a wide luminance range, which is what makes
# the pattern test work: the shading has to slide between ambient-dominated
# and screen-dominated for a 3D surface to look different from a flat one.
SEQUENCE = ["#ff0000", "#00ffff", "#00ff00", "#ff00ff", "#0000ff", "#ffff00"]


class TestALiveFacePasses:
    def test_a_lit_three_dimensional_face_passes(self):
        result = cl.analyse(syn.render_real(SEQUENCE), SEQUENCE)
        assert result.passed is True, result.reason
        assert result.response_correlation > cl.CORRELATION_MIN
        assert result.pattern_variation > cl.PATTERN_VARIATION_MIN
        assert result.specular_concentration < cl.SPECULAR_CONCENTRATION_MAX

    def test_the_face_tracks_the_colour_we_asked_for(self):
        """Per channel, not just on average: showing red must brighten red."""
        result = cl.analyse(syn.render_real(SEQUENCE), SEQUENCE)
        for channel, correlation in result.per_channel_correlation.items():
            assert correlation is not None and correlation > 0.5, channel

    def test_it_survives_camera_auto_exposure(self):
        """The hostile case for any brightness-based method.

        Auto-exposure rescales every frame to a constant mean, erasing the
        brightness response entirely. Chromaticity and the normalised pattern
        both survive it by construction, and this is the test that says so.
        """
        frames = syn.render_real(SEQUENCE, auto_exposure=True)
        result = cl.analyse(frames, SEQUENCE)
        assert result.passed is True, result.reason
        # Brightness response is largely gone...
        assert result.response_amplitude < 0.2
        # ...and the verdict did not depend on it.
        assert result.response_correlation > 0.9


class TestAPrintedPhotograph:
    def test_a_flat_print_is_refused(self):
        result = cl.analyse(syn.render_print(SEQUENCE), SEQUENCE)
        assert result.passed is False
        assert "flat" in (result.reason or "").lower()

    def test_it_is_refused_despite_a_perfect_colour_response(self):
        """The reason this challenge needs more than one measurement.

        Paper is matte, so a print tracks the colour sequence *better* than
        skin does -- correlation near 1.0. Colour response alone would admit
        every printed photograph.
        """
        printed = cl.analyse(syn.render_print(SEQUENCE), SEQUENCE)
        live = cl.analyse(syn.render_real(SEQUENCE), SEQUENCE)
        assert printed.response_correlation >= live.response_correlation
        assert printed.passed is False and live.passed is True

    def test_shading_printed_into_the_ink_does_not_help_it(self):
        """The trap that defeats the obvious implementation.

        The rendered print carries the depicted face's shading in its ink, so
        its response is as UNEVEN as a real face's. It is refused because the
        unevenness does not change across the sequence, which is what being
        flat actually means.
        """
        result = cl.analyse(syn.render_print(SEQUENCE), SEQUENCE)
        assert result.pattern_variation < cl.PATTERN_VARIATION_MIN
        assert result.passed is False


class TestAScreenHeldUpToTheCamera:
    def test_a_replaying_tablet_is_refused(self):
        result = cl.analyse(syn.render_screen(SEQUENCE), SEQUENCE)
        assert result.passed is False
        assert result.specular_concentration > cl.SPECULAR_CONCENTRATION_MAX

    def test_a_weak_response_from_one_patch_is_refused_not_excused(self):
        """A tablet's own emission swamps our light, so the face as a whole
        barely responds -- which could be read as "too dim to tell" and
        retired as inconclusive. It must not be: the glass still throws back
        a highlight, and a strong response from one patch while the face
        shows none is evidence of glass, not of darkness.
        """
        result = cl.analyse(syn.render_screen(SEQUENCE, auto_exposure=True), SEQUENCE)
        assert result.response_amplitude < cl.AMPLITUDE_FLOOR
        assert result.passed is False, "must not retire as inconclusive"

    @pytest.mark.parametrize("emission", [0.35, 0.62, 0.95])
    def test_refused_however_bright_the_tablet_is(self, emission):
        result = cl.analyse(syn.render_screen(SEQUENCE, emission=emission), SEQUENCE)
        assert result.passed is False


class TestARecordingMadeEarlier:
    def test_a_real_face_lit_by_the_wrong_colours_is_refused(self):
        """The strongest attack of the four: a genuine 3D face, genuinely
        responding to genuine coloured light. It fails on the one thing it
        cannot have -- the light being ours.
        """
        result = cl.analyse(syn.render_recording(SEQUENCE), SEQUENCE)
        assert result.passed is False
        assert result.response_correlation < cl.CORRELATION_MIN

    def test_the_ORDER_is_part_of_the_secret(self):
        """Guessing the palette is not enough; the order has to be right.

        These frames are a live face lit by exactly the six colours this
        challenge uses -- just in a different order. Scored against the order
        they were really lit by they pass, which proves nothing is wrong with
        the images. Scored against the order the server asked for, they fail.
        An attacker who knows the palette still has 6! orders to guess, once,
        inside a 60-second single-use window.
        """
        actual = list(reversed(SEQUENCE))
        frames = syn.render_real(actual)
        assert cl.analyse(frames, actual).passed is True
        assert cl.analyse(frames, SEQUENCE).passed is False


class TestNoVerdictIsNotAFailedVerdict:
    """``passed is None`` has to stay distinct from ``passed is False``.

    A dim screen, a distant face and a sunlit room all land here. Reading any
    of them as a spoof would refuse honest students over their lighting --
    the same mistake as scoring an absent attestation verdict as a failed one.
    """

    def test_a_dim_screen_gives_no_verdict(self):
        result = cl.analyse(syn.render_real(SEQUENCE, gain=0.004), SEQUENCE)
        assert result.passed is None
        assert "bright" in (result.reason or "").lower()

    def test_too_few_frames_gives_no_verdict(self):
        short = SEQUENCE[:3]
        result = cl.analyse(syn.render_real(short), short)
        assert result.passed is None
        assert result.frames_measured == 3

    def test_frames_without_a_face_are_skipped_not_counted(self):
        frames = syn.render_real(SEQUENCE)
        frames[2] = None
        result = cl.analyse(frames, SEQUENCE)
        assert result.frames_measured == 5
        assert result.passed is True, result.reason

    def test_a_face_too_small_to_measure_gives_no_verdict(self):
        tiny = [f[:20, :20] for f in syn.render_real(SEQUENCE)]
        result = cl.analyse(tiny, SEQUENCE)
        assert result.passed is None
        assert "closer" in (result.reason or "").lower()

    def test_a_sequence_too_dull_to_judge_says_so(self):
        """A caller fault, reported as undecided rather than as a spoof.

        Six desaturated pastels look well spread -- they cover most of the
        brightness range by difference -- but only reach a luminance RATIO of
        about 6, and the pattern test needs a ratio. A live face scores 0.015
        under them, just under the threshold, so scoring this into a verdict
        would refuse real students for the server's choice of palette.
        """
        pastels = ["#f695cb", "#a24e96", "#f3f9f8", "#ac6ba2", "#b465f7", "#9f738c"]
        result = cl.analyse(syn.render_real(pastels), pastels)
        assert result.passed is None
        assert "brightness" in (result.reason or "").lower()

    def test_a_sequence_of_only_bright_colours_says_so(self):
        bright = ["#ffff00", "#00ffff", "#ffffff", "#88ff88", "#ffff88", "#aaffff"]
        result = cl.analyse(syn.render_real(bright), bright)
        assert result.passed is None

    def test_the_shipped_style_of_sequence_clears_the_requirement(self):
        """Saturated primaries and secondaries reach ~12.9, comfortably over
        the 8.0 floor -- which is why module 2's palette is built from them."""
        luma = np.array([cl.parse_hex_colour(c) for c in SEQUENCE]) @ cl._LUMA
        assert luma.max() / luma.min() > cl.MIN_LUMINANCE_RATIO

    def test_one_colour_repeated_gives_no_verdict(self):
        flat = ["#ff0000"] * 6
        result = cl.analyse(syn.render_real(flat), flat)
        assert result.passed is None
        assert result.distinct_colours == 1


class TestTheContract:
    def test_frames_must_say_which_colour_they_belong_to(self):
        """A caller that cannot pair frames with colours has no evidence.

        Silently zipping to the shorter list would score frames against the
        wrong illuminant, which is both wrong and quietly wrong.
        """
        with pytest.raises(ValueError, match="which colour"):
            cl.analyse(syn.render_real(SEQUENCE), SEQUENCE[:4])

    @pytest.mark.parametrize("value,expected_channel", [
        ("#ff0000", 0), ("#00ff00", 1), ("#0000ff", 2)])
    def test_hex_parses_to_linear_light(self, value, expected_channel):
        parsed = cl.parse_hex_colour(value)
        assert parsed[expected_channel] == pytest.approx(1.0)
        assert sum(parsed) == pytest.approx(1.0)

    def test_short_hex_is_accepted(self):
        assert cl.parse_hex_colour("#f00") == cl.parse_hex_colour("#ff0000")

    @pytest.mark.parametrize("value", ["", "red", "#12345", "#gggggg", None])
    def test_a_non_colour_is_refused(self, value):
        with pytest.raises(ValueError):
            cl.parse_hex_colour(value)

    def test_linearisation_is_not_a_no_op(self):
        """Mid-grey sRGB is about 21% of the light of white, not 50%. The
        whole method is a statement about light, so this has to be right."""
        assert cl.srgb_to_linear(np.float64([0.5])) == pytest.approx([0.2140], abs=1e-3)

    def test_details_round_trip_to_plain_json_types(self):
        import json
        details = cl.analyse(syn.render_real(SEQUENCE), SEQUENCE).as_details()
        assert json.loads(json.dumps(details))["colour_challenge_passed"] is True


class TestCalibration:
    """The margins the shipped thresholds actually rest on.

    Every case is rendered across screen gain, auto-exposure, face size and
    seed, and the thresholds must sit strictly inside the gap between the
    classes. If a change narrows a gap, this fails here rather than in the
    field.
    """

    GRID = list(itertools.product((False, True), (0.45, 0.95, 1.6), (7, 23, 41), (160, 240)))

    def _sweep(self, renderer):
        out = []
        for auto_exposure, gain, seed, size in self.GRID:
            out.append(cl.analyse(
                renderer(SEQUENCE, auto_exposure=auto_exposure, gain=gain,
                         seed=seed, size=size), SEQUENCE))
        return out

    def test_every_live_face_in_the_sweep_passes(self):
        results = self._sweep(syn.render_real)
        failures = [r.reason for r in results if r.passed is not True]
        assert not failures, failures

    def test_every_spoof_in_the_sweep_is_refused(self):
        for renderer in (syn.render_print, syn.render_screen, syn.render_recording):
            for result in self._sweep(renderer):
                assert result.passed is not True, (renderer.__name__, result)

    def test_the_pattern_threshold_sits_inside_the_gap(self):
        prints = max(r.pattern_variation for r in self._sweep(syn.render_print))
        live = min(r.pattern_variation for r in self._sweep(syn.render_real))
        assert prints < cl.PATTERN_VARIATION_MIN < live
        assert live / prints > 2.0, f"margin collapsed: {prints:.4f} .. {live:.4f}"

    def test_the_specular_threshold_sits_inside_the_gap(self):
        live = max(r.specular_concentration for r in self._sweep(syn.render_real))
        screens = min(r.specular_concentration for r in self._sweep(syn.render_screen))
        assert live < cl.SPECULAR_CONCENTRATION_MAX < screens
        assert screens / live > 5.0, f"margin collapsed: {live:.4f} .. {screens:.4f}"
