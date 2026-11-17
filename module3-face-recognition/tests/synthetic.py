"""Physically-derived face crops for the colour-reflection liveness tests.

No public anti-spoofing dataset can validate this challenge directly. NUAA,
CASIA-FASD, Replay-Attack, OULU-NPU and SiW are all *passive* captures under
ambient or fixed studio light: none of them contains a face lit by a sequence
of colours a server chose seconds earlier, because that sequence does not
exist until the challenge is issued. Scoring the algorithm against them would
measure something other than what it does.

So the four cases are built here from the same reflectance model the detector
assumes, which makes the tests a statement about the model rather than about
one recording:

    reflected = albedo * (ambient + gain * (N . L) * illuminant)

    REAL        N varies over an ellipsoid-with-a-nose, so (N . L) runs from
                ~1 at the nose and forehead to ~0 at the edge of the face.
    PRINT       N is constant: a sheet of paper faces the screen uniformly,
                so every patch gets the same light.
    SCREEN      a large constant self-emission plus a small specular lobe off
                the glass; the emission dilutes the response and the lobe
                concentrates what is left.
    RECORDING   lit by colours unrelated to the ones we asked for.

What this does NOT establish is real-world accuracy: skin varies, phone
cameras vary, and rooms vary. ``benchmark/evaluate_colour_liveness.py`` exists
for that, and takes real frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np

SKIN_ALBEDO = np.float64([0.44, 0.31, 0.26])
PRINT_ALBEDO = np.float64([0.40, 0.30, 0.27])

# Ambient room light, linear RGB. Slightly warm, as indoor light is.
AMBIENT = np.float64([0.10, 0.095, 0.085])


def linear_to_srgb(values: np.ndarray) -> np.ndarray:
    arr = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0)
    return np.where(arr <= 0.0031308, arr * 12.92,
                    1.055 * arr ** (1 / 2.4) - 0.055)


def _encode(linear: np.ndarray, rng: np.random.Generator,
            noise: float = 0.004) -> np.ndarray:
    """Linear light -> an 8-bit sRGB crop, with a little sensor noise."""
    srgb = linear_to_srgb(linear)
    if noise:
        srgb = srgb + rng.normal(0.0, noise, srgb.shape)
    return np.clip(srgb * 255.0, 0, 255).astype(np.uint8)


def _hex_to_linear(value: str) -> np.ndarray:
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    srgb = np.float64([int(text[i:i + 2], 16) for i in (0, 2, 4)]) / 255.0
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


@dataclass
class Geometry:
    """``cosine`` is (N . L) per pixel; ``mask`` is where the face is."""
    cosine: np.ndarray
    mask: np.ndarray
    texture: np.ndarray


def face_geometry(size: int = 240, seed: int = 7) -> Geometry:
    """An ellipsoid head with a nose ridge and brow, lit from the screen.

    Crude as a face, but it has the one property that matters: the surface
    normal turns away from the screen as you move outward, and the nose
    stands proud of it. That is what makes the response uneven.
    """
    rng = np.random.default_rng(seed)
    axis = np.linspace(-1.0, 1.0, size)
    x, y = np.meshgrid(axis, axis)

    # Ellipsoid: a face is taller than it is wide.
    radial = (x / 0.82) ** 2 + (y / 1.0) ** 2
    mask = radial <= 1.0
    height = np.sqrt(np.clip(1.0 - radial, 0.0, None))

    # A nose ridge down the middle of the lower half, and a brow across the
    # upper third. Both add local relief, which is what a flat print lacks.
    nose = 0.33 * np.exp(-((x / 0.085) ** 2) - ((y - 0.22) / 0.34) ** 2)
    brow = 0.10 * np.exp(-((y + 0.34) / 0.10) ** 2) * np.exp(-((x / 0.60) ** 2))
    height = height + nose + brow

    # Normals from the height field; L points at the screen, so (N . L) = N_z.
    gy, gx = np.gradient(height)
    norm = np.sqrt(gx ** 2 + gy ** 2 + 1.0)
    cosine = np.clip(1.0 / norm, 0.0, 1.0) * mask

    # Skin texture: eyes and mouth are darker, plus fine variation.
    texture = np.ones((size, size, 1), dtype=np.float64)
    for cx, cy, rx, ry, depth in ((-0.34, -0.17, 0.15, 0.08, 0.55),
                                  (0.34, -0.17, 0.15, 0.08, 0.55),
                                  (0.0, 0.56, 0.26, 0.09, 0.40)):
        blob = np.exp(-(((x - cx) / rx) ** 2) - (((y - cy) / ry) ** 2))
        texture[..., 0] -= depth * blob
    texture[..., 0] += rng.normal(0.0, 0.02, (size, size))
    texture = np.clip(texture, 0.12, 1.2)
    return Geometry(cosine=cosine, mask=mask, texture=texture)


def _background(size: int, mask: np.ndarray) -> np.ndarray:
    """A wall behind the subject. It responds to the screen as well, which is
    exactly why the detector insets the face box before measuring."""
    return (~mask)[..., None].astype(np.float64)


def render_real(colours: Sequence[str], size: int = 240, gain: float = 0.95,
                seed: int = 7, auto_exposure: bool = False) -> List[np.ndarray]:
    """A live 3D face, lit by the sequence we asked for."""
    geo = face_geometry(size, seed)
    rng = np.random.default_rng(seed + 1)
    albedo = SKIN_ALBEDO * geo.texture
    frames = []
    for colour in colours:
        light = AMBIENT + gain * _hex_to_linear(colour) * geo.cosine[..., None]
        linear = albedo * light
        # The wall behind is lit too, flatly.
        linear = linear + _background(size, geo.mask) * 0.22 * (
            AMBIENT + 0.5 * gain * _hex_to_linear(colour))
        frames.append(_encode(_maybe_expose(linear, auto_exposure), rng))
    return frames


def render_print(colours: Sequence[str], size: int = 240, gain: float = 0.95,
                 seed: int = 7, auto_exposure: bool = False) -> List[np.ndarray]:
    """A matte photograph held up to the camera.

    Same face, same light, one difference: the surface is flat, so (N . L) is
    the same everywhere and the whole sheet responds together.
    """
    geo = face_geometry(size, seed)
    rng = np.random.default_rng(seed + 2)
    # The printed image still LOOKS shaded -- the shading is in the ink.
    printed = PRINT_ALBEDO * geo.texture * (0.35 + 0.65 * geo.cosine[..., None])
    frames = []
    for colour in colours:
        light = AMBIENT + gain * _hex_to_linear(colour)   # no cosine term
        linear = printed * light
        frames.append(_encode(_maybe_expose(linear, auto_exposure), rng))
    return frames


def render_screen(colours: Sequence[str], size: int = 240, gain: float = 0.95,
                  seed: int = 7, emission: float = 0.62,
                  auto_exposure: bool = False) -> List[np.ndarray]:
    """A phone or tablet replaying a video of the face.

    The device emits its own light, which does not care what colour we are
    showing; our light comes back only as a specular highlight off the glass.
    """
    geo = face_geometry(size, seed)
    rng = np.random.default_rng(seed + 3)
    axis = np.linspace(-1.0, 1.0, size)
    x, y = np.meshgrid(axis, axis)
    # A tight off-centre highlight: flat glass reflects the screen as an
    # image of the screen, not as a diffuse wash.
    lobe = np.exp(-(((x - 0.26) / 0.16) ** 2) - (((y + 0.30) / 0.16) ** 2))

    emitted = emission * SKIN_ALBEDO * geo.texture * (0.4 + 0.6 * geo.cosine[..., None])
    frames = []
    for colour in colours:
        linear = emitted + 0.9 * gain * lobe[..., None] * _hex_to_linear(colour)
        frames.append(_encode(_maybe_expose(linear, auto_exposure), rng))
    return frames


def render_recording(colours: Sequence[str], size: int = 240, gain: float = 0.95,
                     seed: int = 7, auto_exposure: bool = False) -> List[np.ndarray]:
    """A real 3D face, filmed earlier under colours nobody asked for.

    The strongest of the four attacks: everything about it is a genuine face
    responding to genuine coloured light. It fails only because the light is
    not the light this challenge requested.
    """
    rng = np.random.default_rng(seed + 4)
    wrong = ["#%02x%02x%02x" % tuple(rng.integers(40, 256, 3)) for _ in colours]
    return render_real(wrong, size=size, gain=gain, seed=seed,
                       auto_exposure=auto_exposure)


def _maybe_expose(linear: np.ndarray, enabled: bool,
                  target: float = 0.18) -> np.ndarray:
    """Simulate camera auto-exposure: rescale so mean luminance is constant.

    This is the hostile case for any brightness-based method, and the reason
    the detector correlates chromaticity. A real face must still pass with
    this on.
    """
    if not enabled:
        return linear
    luma = float((linear @ np.float64([0.2126, 0.7152, 0.0722])).mean())
    if luma < 1e-9:
        return linear
    return linear * (target / luma)


RENDERERS = {
    "real": render_real,
    "print": render_print,
    "screen": render_screen,
    "recording": render_recording,
}
