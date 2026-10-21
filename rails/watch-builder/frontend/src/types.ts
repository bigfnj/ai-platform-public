// The design document, mirrored from the backend's pydantic models.
//
// Deliberately hand-mirrored rather than generated. The backend is the authority and
// validates every document, so a drift here surfaces as a 422 with field-level problems
// rather than a wrong face — and a generator would add a build step to a rail whose whole
// frontend is a few files.

export const SCHEMA_VERSION = 1

export type PlatformName =
  | 'aplite' | 'basalt' | 'chalk' | 'diorite' | 'flint' | 'emery' | 'gabbro'

export interface PlatformInfo {
  name: PlatformName
  label: string
  width: number
  height: number
  round: boolean
  colors: number
  quick_view: number
  has_leco60: boolean
}

export type ElementType =
  | 'time' | 'date' | 'text'
  | 'rect' | 'circle' | 'line'
  | 'battery-bar' | 'battery-ring' | 'battery-text'
  | 'steps' | 'bluetooth' | 'analog' | 'image'
  | 'polygon' | 'arc'

/** An int (absolute px), a percentage string, or "center". Percentages are preferred. */
export type Length = number | string

export interface Element {
  type: ElementType
  x?: Length
  y?: Length
  w?: Length | null
  h?: Length | null
  color?: string
  align?: 'left' | 'center' | 'right'
  font?: string
  text?: string | null
  format?: string | null
  radius?: number
  filled?: boolean
  r?: Length | null
  x2?: Length | null
  y2?: Length | null
  stroke?: number
  thickness?: number
  hand_color?: string | null
  second_hand?: boolean
  /** An upload id. The build resamples it to this element's resolved box PER PLATFORM,
   *  because graphics_draw_bitmap_in_rect clips a rect smaller than the bitmap and tiles a
   *  larger one. So resizing is a resample, not a rect change. */
  image_id?: string | null
  fit?: 'contain' | 'cover' | 'stretch'
  /** Polygon vertices as [x, y] Length pairs. Drawn with GPath on the watch, and allowed to
   *  fall outside the screen: bleeding off the edge and letting the SDK clip is how an
   *  edge-anchored wedge is built, so the layout audit only objects to a polygon that misses
   *  the screen entirely. */
  points?: Length[][] | null
  /** Arc sweep in degrees clockwise from twelve o'clock. end < start wraps through twelve. */
  angle_start?: number
  angle_end?: number
  /** Editor-only: hidden elements are skipped by the canvas and still built. */
  visible?: boolean
}

/** One element's pixel box on one platform, as resolved by the BACKEND.
 *  The canvas draws from these so there is a single resolver of record. */
export interface ResolvedBox {
  index: number
  type: ElementType
  x: number
  y: number
  w: number
  h: number
  font?: string
  ink_w?: number
  /** Textual elements only: what the element will actually say, with a date or time element's
   *  `format` already expanded against a fixed reference moment. Server-side, because the
   *  canvas guessing at it is how a `%d/%m` date came to be previewed as "Sat 26 Sep". */
  sample?: string
  /** This element's colour as the WATCH shows it, after the reflective display's own
   *  desaturation. Colour platforms only; absent on a 2-colour screen, where there is nothing
   *  to desaturate. Server-side because the 64-entry correction table lives there. */
  on_watch?: string
  on_watch_hand?: string
  /** Polygon only: the resolved vertices in screen pixels, since a bounding box says
   *  nothing about a shape. These are the exact GPoints codegen emits. */
  points?: [number, number][]
  /** Arc only: [start, end] degrees, and the ring thickness in pixels. */
  angles?: [number, number]
  thickness?: number
}

export interface ScreenInfo {
  w: number
  h: number
  round: boolean
  colors: number
  quick_view: number
}

export interface UploadedImage {
  id: string
  name: string
  width: number
  height: number
  bytes: number
  uploaded: number
}

export interface ImageResourceRow {
  element: number
  platform: string
  resource: string
  pixels: [number, number]
  bytes: number
  colors: number
}

export interface Design {
  schema: number
  name: string
  background: string
  platforms: PlatformName[]
  elements: Element[]
  note?: string | null
}

export interface Finding {
  severity: 'error' | 'warning'
  code: string
  platform: string
  message: string
  elements: number[]
}

export interface LayoutReport {
  errors: number
  warnings: number
  findings: Finding[]
  semantics?: string[]
  clock_elements?: number
  colour_shift?: { specified: string; on_watch: string; shift: number }[]
  boxes?: Record<string, ResolvedBox[]>
  screens?: Record<string, ScreenInfo>
}

export interface ModelChip {
  slot: string
  label: string
  model: string
  role: string
  state: 'missing' | 'cold' | 'warming' | 'loaded'
}

export interface Capabilities {
  broker: 'ok' | 'unreachable'
  models: ModelChip[]
  build_available: boolean
  sdk_version: string | null
  platforms: PlatformInfo[]
  ai: {
    designer_model: string
    features: string[]
    variation_default: number
    variation_max: number
  }
}

/** One platform's render.
 *
 * `on_watch` is `pebble screenshot`'s DEFAULT output, which already carries the sunlight
 * correction: a GColorRed fill screenshots as #E35462, measured. So it is the on-wrist
 * appearance as it comes out of the SDK, not something to correct further.
 *
 * `raw` is `--no-correction`: the literal palette colours the design specified. Optional,
 * because a failed second capture is a lost comparison rather than a broken render.
 */
export interface Shot { on_watch: string; raw?: string }

export interface BuildResponse {
  ok: boolean
  platforms: string[]
  duration_s: number
  layout: LayoutReport
  error?: string
  log_tail?: string
  pbw_base64?: string
  pbw_name?: string
  pbw_bytes?: number
  images?: ImageResourceRow[]
  image_warnings?: string[]
  render?: {
    duration_s: number
    errors: Record<string, string>
    shots: Record<string, Shot>
  }
}

export interface Variant {
  ok: boolean
  axis: string
  design?: Design
  error?: string
}

/** What an AI action will cost, judged for this viewer (ai.estimate). `backend` is who the
 * broker would answer them from; on `claude` the GPU is used only if a call falls back. */
export interface Estimate {
  model_ref: string
  backend: 'claude' | 'ollama'
  calls: number
  approx_seconds: number
  holds_gpu: boolean
  note: string
}

/** queued and running are the states a page keeps polling; the other three are final. */
export type JobState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface JobProgress {
  done: number
  total: number
  unit: 'design' | 'variant'
}

/** One AI job as GET /api/ai/jobs/{job_id} answers it (jobs.py _view). `partial` holds the
 * variants finished so far; `result` is set only once the job is done. `stopped` is set when
 * the rail stopped starting calls on its own: the job reached its ceiling (`ceiling_s`), or
 * nobody read it for its lease (`lease_s`). Each read of the job by its owner renews that. */
export interface AiJob {
  job_id: string
  kind: 'design' | 'variations'
  state: JobState
  progress: JobProgress
  calls: number
  partial: Variant[]
  result: null | { design?: Design; layout?: LayoutReport; variants?: Variant[]; requested?: number }
  error: string | null
  created: number
  started: number | null
  finished: number | null
  ceiling_s: number
  stopped: null | 'ceiling' | 'unwatched'
  lease_s: number
}

/** A row of GET /api/ai/jobs: the viewer's own jobs, newest first, for a reloaded page. */
export interface AiJobSummary {
  job_id: string
  kind: 'design' | 'variations'
  state: JobState
  progress: JobProgress
  created: number
  finished: number | null
  delivered: boolean
}

export interface SavedRow {
  id: string
  name: string
  updated: number
  created: number
  platforms: string[]
  elements: number
}

/** A starting document, so the editor is never empty.
 *
 * Percentages throughout and `font: "auto"` on the text, because that is the combination
 * that makes one document correct on all seven screens. A new user's first face should
 * demonstrate the right habit rather than the absolute-pixel one.
 */
export function blankDesign(): Design {
  return {
    schema: SCHEMA_VERSION,
    name: 'Untitled face',
    background: '#000000',
    // Emery alone, matching DEFAULT_PLATFORMS on the backend. Tick gabbro when you want
    // the round screen; the round layout is a different design decision, not a resolution.
    platforms: ['emery'],
    elements: [
      { type: 'time', x: 0, y: '30%', w: '100%', color: '#FFFFFF', align: 'center', font: 'auto' },
      {
        type: 'date', x: 0, y: '62%', w: '100%', color: '#AAAAAA', align: 'center',
        font: 'auto-small', format: '%a %d %b',
      },
    ],
  }
}

/** Fields the inspector should offer for each element type, in order. */
export const FIELDS: Record<ElementType, string[]> = {
  time: ['x', 'y', 'w', 'color', 'align', 'font'],
  date: ['x', 'y', 'w', 'color', 'align', 'font', 'format'],
  text: ['text', 'x', 'y', 'w', 'color', 'align', 'font'],
  rect: ['x', 'y', 'w', 'h', 'color', 'radius', 'filled', 'stroke'],
  circle: ['x', 'y', 'r', 'color', 'filled', 'stroke'],
  line: ['x', 'y', 'x2', 'y2', 'color', 'stroke'],
  'battery-bar': ['x', 'y', 'w', 'h', 'color'],
  'battery-ring': ['x', 'y', 'w', 'h', 'thickness', 'color'],
  'battery-text': ['x', 'y', 'w', 'color', 'align', 'font'],
  steps: ['x', 'y', 'w', 'color', 'align', 'font'],
  bluetooth: ['x', 'y', 'color'],
  analog: ['x', 'y', 'w', 'h', 'color', 'hand_color'],
  image: ['x', 'y', 'w', 'h', 'fit'],
  // No 'points' row: a vertex list is not a text field. A polygon is edited by picking a
  // preset that has one, or by hand in the JSON tab. Offering a broken text input would be
  // worse than offering nothing.
  polygon: ['color', 'filled', 'stroke'],
  arc: ['x', 'y', 'w', 'h', 'thickness', 'angle_start', 'angle_end', 'color'],
}

export const ELEMENT_LABEL: Record<ElementType, string> = {
  time: 'Time',
  date: 'Date',
  text: 'Text',
  rect: 'Rectangle',
  circle: 'Circle',
  line: 'Line',
  'battery-bar': 'Battery bar',
  'battery-ring': 'Battery ring',
  'battery-text': 'Battery %',
  steps: 'Steps',
  bluetooth: 'Disconnect dot',
  analog: 'Analog dial',
  image: 'Image',
  polygon: 'Polygon',
  arc: 'Arc',
}

/** Sensible defaults per type, so adding an element never lands an invalid document. */
export function newElement(type: ElementType): Element {
  const base: Element = { type, x: 'center', y: 'center', color: '#FFFFFF', align: 'center' }
  switch (type) {
    case 'time': return { ...base, x: 0, y: '30%', w: '100%', font: 'auto' }
    case 'date': return { ...base, x: 0, y: '62%', w: '100%', font: 'auto-small', format: '%a %d %b' }
    case 'text': return { ...base, x: 0, y: '50%', w: '100%', font: 'auto-small', text: 'HELLO' }
    case 'rect': return { ...base, x: '20%', y: '45%', w: '60%', h: '10%' }
    case 'circle': return { ...base, r: '10%' }
    case 'line': return { ...base, x: '20%', y: '50%', x2: '80%', y2: '50%', stroke: 1 }
    case 'battery-bar': return { ...base, x: 0, y: '4%', w: '100%', h: 4 }
    case 'battery-ring': return { ...base, x: '6%', y: '6%', w: '88%', h: '88%', thickness: 5 }
    case 'battery-text': return { ...base, x: 0, y: '80%', w: '100%', font: 'auto-tiny' }
    case 'steps': return { ...base, x: 0, y: '80%', w: '100%', font: 'auto-small' }
    case 'bluetooth': return { ...base, x: '50%', y: '90%', color: '#FF0000' }
    case 'analog': return { ...base, x: 'center', y: 'center', w: '80%', h: '80%', hand_color: '#FFFFFF' }
    // Percentages, so the image is resampled to the right pixel box on every platform
    // rather than being correct on one screen and cropped or tiled on the others.
    case 'image': return { ...base, x: 'center', y: '10%', w: '70%', h: '40%', fit: 'contain' }
    // A triangle, so a new polygon is immediately visible and immediately valid: three
    // points is the schema minimum, and an empty vertex list fails validation.
    case 'polygon': return {
      ...base, points: [['35%', '30%'], ['65%', '30%'], ['50%', '58%']],
    }
    case 'arc': return {
      ...base, x: 'center', y: 'center', w: '70%', h: '62%', thickness: 6,
      angle_start: 0, angle_end: 270,
    }
  }
}

/** A starting point read off a real watchface, with what it does and does not report.
 *
 * `live` names the elements wired to real data; `limits` names the ones that only depict a
 * state. Both ship with the document because a preset derived from a working face carries
 * shapes that LOOK like gauges, and "does this ring show my battery" is a question to answer
 * beside the ring rather than in a document nobody opened.
 */
export interface Preset {
  id: string
  label: string
  after: string
  why: string
  live: string[]
  limits: string[]
  design: Design
}
