// The live canvas: what the design looks like, at the platform's real pixel size, scaled up.
//
// ONE RESOLVER OF RECORD
// ----------------------
// Every box drawn here comes from the BACKEND (`/api/validate` -> `boxes`), not from
// arithmetic repeated in TypeScript. A client that reimplemented the percentage resolution
// would drift from the build, and a preview that disagrees with the built face is worse than
// no preview: it teaches a layout that is not the one being shipped.
//
// Local arithmetic is used for exactly one thing: the frames of an in-flight drag, so the
// pointer feels attached to the element. The moment the pointer is released the design
// changes, validation re-runs, and the server's numbers take over again. So a drift can only
// ever last as long as your finger is down, and it corrects visibly.
//
// WHAT THIS IS NOT
// ----------------
// It is a LAYOUT preview, and it is labelled as one. It does not render Pebble's actual
// fonts, so text is drawn as a measured box plus a web-font approximation at the right size.
// Ground truth is the emulator render on the Preview tab. The two are deliberately different
// affordances rather than one pretending to be the other.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Design, Element, Length, PlatformName, ResolvedBox, ScreenInfo } from './types'

type Handle = 'nw' | 'ne' | 'sw' | 'se' | 'move'

interface Props {
  design: Design
  boxes: ResolvedBox[]
  screen: ScreenInfo | null
  platform: PlatformName
  selected: number
  imageUrl: (id: string) => string
  onSelect: (i: number) => void
  onCommit: (i: number, patch: Partial<Element>) => void
  zoom: number
  /** Show the colours the WATCH shows rather than the ones the design specified.
   *
   * Default on. Pebble's screens are reflective e-paper and desaturate hard, so a preview in
   * specified colours looks markedly unlike the render for a design that is otherwise
   * pixel-correct. The Preview tab already defaults to the on-wrist appearance; this makes the
   * canvas agree with it. The corrected values come from the server, in the resolved box,
   * because the 64-entry table lives there and a second copy here would drift. */
  onWatchColours: boolean
  onToggleColours: (next: boolean) => void
}

/** Pixel delta -> the same unit the element already used.
 *
 * An element positioned in percent stays in percent and one in pixels stays in pixels.
 * Silently converting a percentage to an absolute pixel on first drag would be the worst
 * possible outcome: the design would look identical and would have quietly lost the
 * cross-platform adaptation that is the whole point of this rail.
 */
function applyDelta(value: Length | null | undefined, deltaPx: number, extent: number,
                    fallback: number): Length {
  if (typeof value === 'string' && value.endsWith('%')) {
    const pct = parseFloat(value) + (deltaPx / extent) * 100
    return `${Math.round(pct * 10) / 10}%`
  }
  if (value === 'center') {
    // "center" has no offset to add to. Dragging away from centre means the element is no
    // longer centred, so it becomes a percentage of where it now is rather than silently
    // staying centred or jumping to a pixel.
    const pct = ((fallback + deltaPx) / extent) * 100
    return `${Math.round(pct * 10) / 10}%`
  }
  return Math.round((typeof value === 'number' ? value : fallback) + deltaPx)
}

export default function Canvas(props: Props) {
  const { design, boxes, screen, platform, selected, imageUrl, onSelect, onCommit, zoom,
          onWatchColours, onToggleColours } = props
  const wrapRef = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<null | {
    index: number; handle: Handle; startX: number; startY: number
    dx: number; dy: number; box: ResolvedBox
  }>(null)

  const byIndex = useMemo(() => {
    const m = new Map<number, ResolvedBox>()
    for (const b of boxes) m.set(b.index, b)
    return m
  }, [boxes])

  const onPointerDown = useCallback((e: React.PointerEvent, index: number, handle: Handle) => {
    const box = byIndex.get(index)
    if (!box) return
    e.stopPropagation()
    e.preventDefault()
    ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
    onSelect(index)
    setDrag({ index, handle, startX: e.clientX, startY: e.clientY, dx: 0, dy: 0, box })
  }, [byIndex, onSelect])

  useEffect(() => {
    if (!drag) return
    const move = (e: PointerEvent) => {
      setDrag((d) => d && ({ ...d, dx: (e.clientX - d.startX) / zoom,
                                   dy: (e.clientY - d.startY) / zoom }))
    }
    const up = () => {
      setDrag((d) => {
        if (!d || !screen) return null
        const el = design.elements[d.index]
        if (!el) return null
        const { dx, dy, handle, box } = d
        // Below a pixel of movement this was a click, not a drag. Committing a sub-pixel
        // delta would rewrite a clean "center" into "49.9%" every time someone selected
        // something.
        if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return null

        const patch: Partial<Element> = {}
        if (el.type === 'polygon') {
          // A polygon has no x/y to offset, so a move translates every vertex. Patching x/y
          // would have been silently ignored by the backend: the drag would animate and the
          // shape would snap back on release, which reads as a broken editor.
          if (handle !== 'move' || !el.points) return null
          patch.points = el.points.map(([px, py]) => [
            applyDelta(px, dx, screen.w, 0),
            applyDelta(py, dy, screen.h, 0),
          ])
        } else if (handle === 'move') {
          patch.x = applyDelta(el.x, dx, screen.w, box.x)
          patch.y = applyDelta(el.y, dy, screen.h, box.y)
        } else {
          // Corner resize. The west and north handles move the origin as well as the size,
          // so the opposite corner stays put, which is what a user expects from a handle.
          const west = handle === 'nw' || handle === 'sw'
          const north = handle === 'nw' || handle === 'ne'
          const dw = west ? -dx : dx
          const dh = north ? -dy : dy
          // 4px floor: a zero or negative box is a build-time cropping error and an
          // invisible element is indistinguishable from a deleted one.
          patch.w = applyDelta(el.w, Math.max(4 - box.w, dw), screen.w, box.w)
          patch.h = applyDelta(el.h, Math.max(4 - box.h, dh), screen.h, box.h)
          if (west) patch.x = applyDelta(el.x, dx, screen.w, box.x)
          if (north) patch.y = applyDelta(el.y, dy, screen.h, box.y)
        }
        onCommit(d.index, patch)
        return null
      })
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
    }
  }, [drag, zoom, design.elements, screen, onCommit])

  if (!screen) {
    return <div className="wb-sub">Validating the design to get its real pixel layout…</div>
  }

  /** The box to DRAW: the server's, plus an in-flight drag offset. */
  const live = (index: number): ResolvedBox | undefined => {
    const box = byIndex.get(index)
    if (!box) return undefined
    if (!drag || drag.index !== index) return box
    const { dx, dy, handle } = drag
    if (handle === 'move') return { ...box, x: box.x + dx, y: box.y + dy }
    const west = handle === 'nw' || handle === 'sw'
    const north = handle === 'nw' || handle === 'ne'
    const w = Math.max(4, box.w + (west ? -dx : dx))
    const h = Math.max(4, box.h + (north ? -dy : dy))
    return { ...box, w, h, x: west ? box.x + (box.w - w) : box.x,
             y: north ? box.y + (box.h - h) : box.y }
  }

  const css = { width: screen.w * zoom, height: screen.h * zoom }

  return (
    <div className="wb-canvas-wrap" ref={wrapRef}>
      <div
        className={'wb-canvas' + (screen.round ? ' round' : '')}
        style={{ ...css, background: design.background }}
        onPointerDown={() => onSelect(-1)}
      >
        {design.elements.map((el, i) => {
          const box = live(i)
          if (!box || el.visible === false) return null
          const style: React.CSSProperties = {
            left: box.x * zoom, top: box.y * zoom,
            width: box.w * zoom, height: box.h * zoom,
          }
          return (
            <div
              key={i}
              className={'wb-el' + (i === selected ? ' sel' : '')}
              style={style}
              onPointerDown={(e) => onPointerDown(e, i, 'move')}
            >
              <ElementVisual el={el} box={box} zoom={zoom} imageUrl={imageUrl}
                             onWatch={onWatchColours} />
            </div>
          )
        })}

        {/* Selection chrome lives in its own layer ABOVE the design, not on the selected
            element.
            It used to be inside the element div, with `.wb-el.sel { z-index: 2 }` lifting that
            div so the outline and handles were not buried. The side effect was that selecting
            an element also raised its CONTENT above every later element, silently inverting
            the paint order the sidebar promises ("drawn top to bottom, so later elements paint
            over earlier"). Selecting the cream disc under a dial hid the dial's ticks and
            hands completely, which reads as a broken renderer rather than as a z-order bug.
            The emulator render is unaffected, so nothing but the editor ever showed it.
            Drawn from the same `live()` box as the element, so it tracks an in-flight drag. */}
        {(() => {
          const el = design.elements[selected]
          const box = live(selected)
          if (!el || !box || el.visible === false) return null
          return (
            <div
              className="wb-sel"
              style={{
                left: box.x * zoom, top: box.y * zoom,
                width: box.w * zoom, height: box.h * zoom,
              }}
            >
              {/* No corner handles on a polygon: resizing one means scaling every vertex
                  about a corner, which is a different gesture from resizing a box and would
                  quietly destroy a hand-tuned wedge. Dragging to move still works. */}
              {el.type !== 'polygon' && (['nw', 'ne', 'sw', 'se'] as Handle[]).map((h) => (
                <span
                  key={h}
                  className={`wb-handle ${h}`}
                  onPointerDown={(e) => onPointerDown(e, selected, h)}
                />
              ))}
            </div>
          )
        })()}

        {/* Timeline Quick View slides up from the bottom to show an upcoming pin and covers
            51px, including a 2px border, on every rectangular platform. Round watches have no
            such overlay, so `quick_view` is 0 on chalk and gabbro and this does not render.

            Drawn as a band rather than described in prose, because "your date is under the
            overlay" is a thing you see instantly and read slowly.

            LABELLED, because the band alone does not explain itself. It carried only a `title`
            tooltip and the first question anyone asked about the canvas was what the hatched
            area was for. A hover tooltip is an explanation for someone who already suspects
            there is something to hover. */}
        {screen.quick_view > 0 && (
          <div className="wb-quickview"
               style={{ top: (screen.h - screen.quick_view) * zoom,
                        height: screen.quick_view * zoom }}
               title={`Timeline Quick View slides up from the bottom to show an upcoming pin, `
                      + `covering this ${screen.quick_view}px band. Anything here is hidden `
                      + `while a pin is showing. Round platforms have no such overlay.`}>
            <span className="wb-quickview-tag">
              Quick View covers this {screen.quick_view}px when a pin shows
            </span>
          </div>
        )}
      </div>
      <div className="wb-canvas-meta">
        <code>{platform}</code> {screen.w}x{screen.h}
        {screen.round ? ' round' : ''} {screen.colors === 2 ? ' B/W' : ' 64 colours'}
        <span className="wb-sub"> · layout preview, not a render</span>
        {screen.colors > 2 && (
          <label className="wb-canvas-toggle"
                 title="Pebble's screens are reflective e-paper and desaturate hard: #FF0000
                        reaches the eye as #E35462. On shows what a wrist sees, off shows the
                        values the design specifies.">
            <input type="checkbox" checked={onWatchColours}
                   onChange={(e) => onToggleColours(e.target.checked)} />
            on-watch colour
          </label>
        )}
      </div>
    </div>
  )
}

/** One element's appearance. Approximate by design; the emulator render is ground truth. */
function ElementVisual({ el, box, zoom, imageUrl, onWatch }: {
  el: Element; box: ResolvedBox; zoom: number; imageUrl: (id: string) => string
  onWatch: boolean
}) {
  // `box.on_watch` is the element's colour after the display's own desaturation, computed
  // server-side. Absent on 2-colour platforms, where there is nothing to desaturate.
  const elementColour = (onWatch ? box.on_watch : undefined) ?? el.color ?? '#FFFFFF'

  if (el.type === 'image') {
    if (!el.image_id) {
      return <div className="wb-el-empty">no image chosen</div>
    }
    // object-fit mirrors the backend's resampling modes so the framing you see is the
    // framing that gets shipped. `contain` is the default there and here.
    const fit = el.fit === 'stretch' ? 'fill' : el.fit === 'cover' ? 'cover' : 'contain'
    return <img className="wb-el-img" src={imageUrl(el.image_id)} alt=""
                style={{ objectFit: fit as React.CSSProperties['objectFit'] }} />
  }

  if (el.type === 'rect') {
    return <div style={{
      width: '100%', height: '100%',
      background: el.filled === false ? 'transparent' : elementColour,
      border: el.filled === false ? `${Math.max(1, (el.stroke || 1) * zoom)}px solid ${elementColour}` : undefined,
      borderRadius: (el.radius || 0) * zoom,
    }} />
  }

  if (el.type === 'circle' || el.type === 'bluetooth') {
    return <div style={{
      width: '100%', height: '100%', borderRadius: '50%',
      background: el.filled === false ? 'transparent' : elementColour,
      border: el.filled === false ? `${Math.max(1, (el.stroke || 1) * zoom)}px solid ${elementColour}` : undefined,
    }} />
  }

  if (el.type === 'polygon') {
    const pts = box.points
    if (!pts || pts.length < 3) return <div className="wb-el-empty">no points</div>
    // Vertices arrive from the server in SCREEN pixels and are shifted into this element's
    // box here, so the same numbers codegen emits are the ones drawn. The SVG is sized to the
    // box with overflow visible, because a wedge's outer points are deliberately off-screen
    // and clipping them here would show a shape the watch does not draw.
    const d = pts.map(([x, y]) => `${(x - box.x) * zoom},${(y - box.y) * zoom}`).join(' ')
    return (
      <svg width={box.w * zoom} height={box.h * zoom} style={{ overflow: 'visible' }}>
        <polygon
          points={d}
          fill={el.filled === false ? 'none' : elementColour}
          stroke={el.filled === false ? elementColour : 'none'}
          strokeWidth={el.filled === false ? Math.max(1, (el.stroke || 1) * zoom) : undefined}
        />
      </svg>
    )
  }

  if (el.type === 'arc') {
    const [a0, a1] = box.angles || [0, 360]
    const th = Math.max(1, (box.thickness ?? el.thickness ?? 6) * zoom)
    const w = box.w * zoom
    const h = box.h * zoom
    // FitCircle on the watch, so the ring is circular inside a non-square box. Matched here
    // by taking the smaller half-extent as the radius and centring it, rather than letting
    // an SVG ellipse stretch and show a sweep the watch will not draw.
    const r = Math.min(w, h) / 2 - th / 2
    const cx = w / 2
    const cy = h / 2
    const sweep = ((a1 - a0) % 360 + 360) % 360 || 360
    // Two half-arcs when the sweep is 180 degrees or more: a single SVG arc command cannot
    // express one, because the large-arc flag is ambiguous at exactly half a turn.
    const at = (deg: number) => {
      const rad = ((deg - 90) * Math.PI) / 180
      return `${cx + r * Math.cos(rad)},${cy + r * Math.sin(rad)}`
    }
    const mid = a0 + sweep / 2
    const d = sweep >= 359.9
      ? `M ${at(a0)} A ${r} ${r} 0 1 1 ${at(a0 + 180)} A ${r} ${r} 0 1 1 ${at(a0)}`
      : `M ${at(a0)} A ${r} ${r} 0 0 1 ${at(mid)} A ${r} ${r} 0 0 1 ${at(a1)}`
    return (
      <svg width={w} height={h} style={{ overflow: 'visible' }}>
        <path d={d} fill="none" stroke={elementColour} strokeWidth={th} />
      </svg>
    )
  }

  if (el.type === 'battery-ring') {
    // Drawn as a full ring at a nominal charge. The real one is a radial fill whose sweep is
    // the live battery level, which the editor has no way to know.
    return <div style={{
      width: '100%', height: '100%', borderRadius: '50%',
      border: `${Math.max(1, (el.thickness || 6) * zoom)}px solid ${elementColour}`,
      opacity: 0.85,
    }} />
  }

  if (el.type === 'battery-bar') {
    return <div style={{ width: '70%', height: '100%', background: elementColour }} />
  }

  if (el.type === 'line') {
    return <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <div style={{
        position: 'absolute', left: 0, top: '50%',
        width: '100%', height: Math.max(1, (el.stroke || 1) * zoom), background: elementColour,
      }} />
    </div>
  }

  if (el.type === 'analog') {
    // Twelve ticks and two hands, matching what codegen emits, and NO outer circle.
    //
    // The previous version drew a circle border plus a single hand. The border was the
    // problem: `analog` draws no outer circle on the watch, which is precisely why a preset
    // can put a filled disc underneath it in one colour and the marks on top in another. So
    // the editor showed a ring that no build produces, on the most prominent element of every
    // preset. One hand instead of two was the smaller half of the same error.
    //
    // Geometry copied from the codegen branch: ticks from r - max(4, r/8) to r - 2, hour hand
    // at 55% of the radius and minute at 85%, weights 14% and 10%. Hour heavier than minute,
    // because weight is how the eye separates them when they are close together.
    const w = box.w * zoom
    const h = box.h * zoom
    const r = Math.min(w, h) / 2
    const cx = w / 2
    const cy = h / 2
    const hand = (onWatch ? box.on_watch_hand : undefined) ?? el.hand_color ?? elementColour
    // The actual time, because the point of a preview is to match what gets built.
    //
    // This was a fixed 10:09 at first, the classic watch-display pose: both hands clearly
    // separated, and a shape that does not shift under you while nudging a wedge. Matching
    // the build wins. Comparing the canvas against an emulator render is how every defect in
    // here has been found, and two hands at a different angle is the difference that makes a
    // correct preview read as broken.
    //
    // Minute precision, so this re-renders when the design changes rather than every second.
    // The hour hand creeps with the minutes, as codegen's does: an hour hand that jumps on
    // the hour is the clearest tell that a face was generated rather than designed.
    const now = new Date()
    const hourDeg = (now.getHours() % 12) * 30 + now.getMinutes() * 0.5
    const minDeg = now.getMinutes() * 6
    const tickInner = r - Math.max(4 * zoom, r / 8)
    const tickOuter = r - 2 * zoom
    const at = (deg: number, radius: number) => {
      const a = ((deg - 90) * Math.PI) / 180
      return [cx + radius * Math.cos(a), cy + radius * Math.sin(a)]
    }
    const [hx, hy] = at(hourDeg, r * 0.55)
    const [mx, my] = at(minDeg, r * 0.85)
    return (
      <svg width={w} height={h} style={{ overflow: 'visible' }}>
        {Array.from({ length: 12 }, (_, i) => {
          const [x1, y1] = at(i * 30, tickInner)
          const [x2, y2] = at(i * 30, tickOuter)
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
                       stroke={elementColour} strokeWidth={Math.max(1, zoom)} />
        })}
        <line x1={cx} y1={cy} x2={hx} y2={hy} stroke={hand} strokeLinecap="round"
              strokeWidth={Math.max(1, (r * 0.14) | 1)} />
        <line x1={cx} y1={cy} x2={mx} y2={my} stroke={hand} strokeLinecap="round"
              strokeWidth={Math.max(1, (r * 0.10) | 1)} />
        <circle cx={cx} cy={cy} r={Math.max(2, r / 18)} fill={hand} />
      </svg>
    )
  }

  // Text-ish. The web font is not Pebble's, so the SIZE comes from the server's measured
  // line box and only the glyph shapes are an approximation.
  //
  // The STRING comes from the server too, for the same reason the boxes do. This used to
  // hardcode 'Sat 26 Sep' for every date element, so a face whose date is `%d/%m` drew ten
  // glyphs in the editor and five on the watch: it overflowed its plate, clipped mid-word to
  // "Sat 26 S" and ran into the week number beside it. The format string says what a date
  // will contain, and `sample_text` on the backend is the one place that expands it.
  //
  // The literals below are a fallback for a stale `boxes` payload only, which happens for one
  // frame after adding an element and before validation returns.
  const sample = box.sample ?? (
    el.type === 'time' ? '10:24'
      : el.type === 'date' ? 'Sat 26 Sep'
      : el.type === 'battery-text' ? '80%'
      : el.type === 'steps' ? '7431'
      : (el.text || ''))
  const mono = el.font === 'auto' && (el.type === 'time')
  return (
    <div className="wb-el-text" style={{
      color: elementColour,
      fontSize: box.h * zoom * 0.72,
      lineHeight: `${box.h * zoom}px`,
      textAlign: el.align || 'center',
      fontFamily: mono ? 'ui-monospace, monospace' : 'system-ui, sans-serif',
      fontWeight: 700,
    }}>{sample}</div>
  )
}
