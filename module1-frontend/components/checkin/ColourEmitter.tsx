'use client';

/**
 * The screen as a light source.
 *
 * During a colour-reflection challenge the display is the illuminant, so this
 * covers the viewport in the colour being measured. Filling the whole screen
 * is not cosmetic: the amount of light reaching the face is what the backend
 * measures, and a colour swatch in a corner of the page emits a small
 * fraction of what the full panel does.
 *
 * No web API can raise the panel's brightness -- no browser exposes one -- so
 * filling the viewport and keeping the screen awake is the whole of what the
 * page can do about light output. The rest is up to the student, which is why
 * the caption asks rather than assuming.
 *
 * Accessibility: the sequence changes at roughly two colours per second,
 * deliberately under the three-per-second large-area flash threshold in WCAG
 * 2.3.1, and the palette contains no black, so no step is a full-contrast
 * blackout. Both of those are properties of what the server sends; the client
 * refuses a challenge that would exceed the rate (see validateChallenge).
 */
export default function ColourEmitter({ colour, progress }: {
  colour: string;
  progress: { count: number; total: number };
}) {
  return (
    <div
      // A live region would announce every colour change, which is noise. The
      // status below carries the part worth hearing.
      aria-hidden="true"
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        backgroundColor: colour,
        display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
        // No transition: the backend assumes the colour is settled by the
        // time the frame is taken, and an animated fade would put a blend of
        // two colours on the screen for part of every step.
        transition: 'none',
      }}
    >
      <div style={{
        margin: '0 0 8vh', padding: '10px 18px', borderRadius: 999,
        background: 'rgba(0,0,0,0.55)', color: '#fff',
        font: '500 15px/1.35 system-ui, sans-serif', textAlign: 'center',
        maxWidth: '80vw',
      }}>
        Hold still and look at the screen
        <span style={{ display: 'block', opacity: 0.75, fontSize: 13 }}>
          {progress.count} of {progress.total}
        </span>
      </div>
    </div>
  );
}
