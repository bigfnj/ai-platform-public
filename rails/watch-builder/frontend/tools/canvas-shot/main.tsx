// Renders the real Canvas for every preset, with no backend, so it can be screenshotted.
//
// WHY THIS IS COMMITTED. Everything else that verifies this rail goes through the Pebble SDK:
// generate a project, build it, install it on an emulator, screenshot, compare. That never
// executes one line of `Canvas.tsx`, because the editor's canvas is a separate renderer that
// draws from resolved boxes rather than from a build. The result was two canvas defects in a
// row that a full test suite, conformance, smoke and eight verified emulator renders all
// passed over, and that the owner found by looking at the screen. Three rounds of "fixed" for
// one bug, because there was no way to look.
//
// So: `tools/canvas-shot/shoot.ps1` builds this, serves it, screenshots it in headless
// Chrome, and puts the result beside the emulator renders. Compare the two and the divergences
// are visible in one glance instead of one at a time.
//
// The fixtures are captured output from `GET /api/presets` and `POST /api/validate` against a
// running rail, NOT hand-written. Boxes especially: writing them by hand would repeat the
// original sin, which was the canvas inventing geometry the server had already resolved.
// Refresh them with `shoot.ps1 -Refresh`.
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import Canvas from '../../src/Canvas'
import type { Design, PlatformName, ResolvedBox, ScreenInfo } from '../../src/types'
import fixtures from './fixtures.json'
import '../../src/theme.css'

interface Fixture {
  design: Design
  boxes: ResolvedBox[]
  screen: ScreenInfo
  platform: PlatformName
}

const data = fixtures as unknown as Record<string, Fixture>

/** Which element to select per preset.
 *
 * Selection is not cosmetic here. The z-order defect only appeared while something was
 * selected: `.wb-el.sel` carried a z-index that lifted the selected element's CONTENT above
 * every later element, so selecting the disc under a dial hid the dial's ticks and hands
 * entirely. Index 0 is that disc in most presets, so selecting it is the case that used to
 * fail. `?sel=none` renders everything unselected for the clean comparison.
 */
const params = new URLSearchParams(location.search)
const mode = params.get('sel') ?? '0'
const zoom = Number(params.get('zoom') ?? '2')
// `?only=<id>` renders a single canvas with no caption and no padding, so it can be montaged
// directly against that preset's emulator screenshot. Judging the two side by side is the
// only way to tell a canvas bug from a difference that is supposed to be there.
const only = params.get('only')
const bare = params.get('bare') === '1'
// `?clean=1` hides the editor's own affordances: the dashed per-element outlines, the
// selection chrome and the Quick View hatch. Those are deliberately NOT part of the design,
// so they have to go before the canvas can be pixel-diffed against an emulator screenshot.
// Eyeballing the two side by side is not good enough; it is what let a z-order bug through
// twice. A style override here rather than a prop, because the product should not grow a
// "hide the affordances" mode just so a harness can measure it.
const clean = params.get('clean') === '1'

function Panel({ id }: { id: string }) {
  const f = data[id]
  if (!f) return null
  const sel = mode === 'none' ? -1 : Number(mode)
  const el = f.design.elements[sel]
  return (
    <div style={bare ? undefined : { marginRight: 18, marginBottom: 14 }}>
      {!bare && (
        <div style={{ color: '#9fb0d0', font: '11px ui-monospace, monospace', marginBottom: 4 }}>
          {id} · {f.platform} · sel={sel < 0 ? 'none' : `${sel} ${el?.type ?? '?'}`}
        </div>
      )}
      <Canvas
        design={f.design}
        boxes={f.boxes}
        screen={f.screen}
        platform={f.platform}
        selected={sel}
        imageUrl={() => ''}
        onSelect={() => {}}
        onCommit={() => {}}
        zoom={zoom}
        onWatchColours={params.get('specified') !== '1'}
        onToggleColours={() => {}}
      />
    </div>
  )
}

function Shot() {
  const ids = only ? [only] : Object.keys(data)
  return (
    <div className="watch-builder"
         style={{
           padding: bare ? 0 : 14,
           background: '#0d1424',
           display: 'flex',
           flexWrap: 'wrap',
           width: bare ? 'fit-content' : undefined,
         }}>
      {ids.map((id) => <Panel key={id} id={id} />)}
    </div>
  )
}

if (clean) {
  const style = document.createElement('style')
  style.textContent = `
    .watch-builder .wb-el { outline: none !important; }
    .watch-builder .wb-el:hover { outline: none !important; }
    .watch-builder .wb-sel { display: none !important; }
    .watch-builder .wb-quickview { display: none !important; }
    .watch-builder .wb-canvas-meta { display: none !important; }
  `
  document.head.appendChild(style)
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><Shot /></StrictMode>
)
