# Watch Builder: design record

Why this rail is shaped the way it is, and what was measured rather than assumed.

## What it is for

Design a Pebble watchface in the browser, compile it for all seven Pebble platforms, and see
it rendered on real emulators. Two AI features assist: describe a face in words, and explore
variations of one you have.

## Why build it when three community builders exist

Three were assessed before anything was written.

| | pebbleface-studio | pebble-watchface-builder | pebble-watchface-generator |
|---|---|---|---|
| Last commit | 2026-08-17 | 2026-09-24 | 2025-03-14 |
| Size | ~9k LOC TS + 3.4k JS | ~12k LOC TS | ~1.5k Python |
| Platforms | 4 (basalt, chalk, emery, gabbro) | all 7 | multi |
| Element types | 7 | **17** | 2 |
| Live data | none | steps, HR, weather, calendar, compass, battery, BT | none |
| Output | `.pbw` | C project zip | `.pbw` |
| Self-contained | no, needs a hosted build runner | yes, pure client-side | yes |

**pebble-watchface-builder** is the strongest. Best element model, all seven platforms, and
it derives `capabilities` and `messageKeys` from what is on the canvas, which is the right
instinct. It stops at source: you get a zip to carry to CloudPebble.

**pebbleface-studio** is the most polished and targets only four platforms, and cannot build
anything without a `RUNNER_ORIGIN` service you also have to operate.

**pebble-watchface-generator** patches a prebuilt PBW's resource pack, so it needs no SDK at
all. Technically the most interesting and functionally the thinnest: a background and a
digital clock, with the web designer half never written, dead 18 months.

Two things none of them does, and both are this rail's reason to exist:

1. **Verify the output.** No builder checks that the face it produced is correct. This one
   compiles it and screenshots it on every target emulator.
2. **Adapt across screen sizes.** All three position elements in absolute pixels per
   platform. This one takes percentages, so one document is correct on four resolutions.

## The layout problem, measured

Pebble's own "Your First Watchface" tutorial positions its time layer at
`GRect(0, PBL_IF_ROUND_ELSE(58, 52), bounds.size.w, 50)` with `FONT_KEY_BITHAM_42_BOLD`.

The width adapts. The vertical offset and the font size do not. Built unmodified for all
seven platforms and screenshotted, the five 144x168-class panels look balanced and emery
(200x228) and gabbro (260x260) leave a dead bottom third with the type floating high.

`PBL_IF_ROUND_ELSE` is the trap: it adapts to *shape* while hardcoding *size*, so when
gabbro arrived at 260x260 the "round" branch was still tuned for chalk's 180x180.

Two measured facts behind this:

- **System fonts do not scale.** Every line height is byte-identical between basalt and
  emery, verified through the SDK's own `graphics_text_layout_get_content_size()` and by
  pixel-measuring rendered screenshots. `BITHAM_42_BOLD` lays down exactly 30px of ink on all
  seven platforms.
- **Emery is 202 PPI against basalt's 182.** So unadapted type is smaller in absolute
  physical terms, not just proportionally.

## The safety boundary: the model never writes C

`design.py` defines a document schema. `codegen.py` turns a validated document into C. The
language model only ever emits a document.

This is the whole reason the AI features are safe to ship:

- A model writing C can produce something that does not compile, and the error lands
  hundreds of lines into waf output with no way for the model to have known better.
- A model writing a design document can only fail validation, which is a short list of
  field-level messages it can act on. The same generator that produced every face this rail
  has ever built then produces the C.

Do not "improve" this by letting the model emit source.

### Verified end to end

A document exercising every element type was generated and compiled on the real SDK:
**clean seven-platform build in 5.2 s under `-Wall -Wextra -Werror`**, then installed and
screenshotted on all seven emulators. That run is what licenses the claim that a valid
document produces compilable C.

`tests/test_codegen.py` asserts source properties instead of compiling, because a unit test
that needs a 2 GB toolchain is a unit test that gets skipped.

## The layout audit, and why it exists

The same end-to-end run produced a second finding worth more than the first. The build was
green, every element rendered, and **the time ran straight through the date on five of the
seven panels.**

Proportional coordinates fix the porting problem and introduce a new one: positions scale
with the screen, and a system font's box height does not. Two elements 20% apart sit 32px
apart on a 168px screen and 43px apart on a 228px one, while the text between them is 60px
tall on both.

So `layout.py` resolves every element's box per platform and reports, in pixels:

- `OUT_OF_BOUNDS` (error) cropped at an edge
- `TEXT_OVERLAP` (error) two text elements colliding, with the overlap in pixels
- `QUICK_VIEW` (warning) below the 51px the timeline overlay covers
- `OUTSIDE_CIRCLE` (warning) content near a round platform's rim

Deterministic and exact. Deliberately not a model's opinion: cropping and overlap are
measurable, and a language model asked to judge them would be strictly worse than arithmetic.

### Two false positives, found and fixed

The first version of the audit flagged two correct designs, and both mattered enough to fix
rather than tolerate. A warning that fires for a benign reason teaches the reader to dismiss
the whole class.

- **A round-shaped element is inscribed in its box.** Testing the box corners flagged a
  battery ring whose every drawn pixel was comfortably inside the screen. Now the inscribed
  circle is tested.
- **A centre-aligned text box is usually far wider than its text.** Testing the box flagged a
  correctly centred time on gabbro. Now it narrows to an estimated ink width and honours
  alignment.

Both have a paired test asserting the fix did not switch the check off: a ring that really
does touch the rim still warns.

### The counter-intuitive part

The obvious guess about platform-dependent overlap is backwards. With `auto` fonts the type
scales roughly with the screen, so a percentage gap that works on one platform tends to work
on all of them. The divergence comes from a **pinned** font: its box height is constant while
the gap scales, so a pinned face overlaps on the *small* screen and clears on the large one.

That is the argument for `auto` being the default, and `tests/test_layout.py` pins it.

## Images, and why a rect is not a scale factor

`graphics_draw_bitmap_in_rect` does **not** scale. Measured on the real SDK with a 60x60
probe drawn into three rects: a 30x30 rect **clips** to the top-left 30px, a 60x60 rect is
correct, and a 120x120 rect **tiles** the source 2x2. The rect is a viewport.

That single fact decides the whole image feature:

- **Resizing is a resample, not a rect change.** Drag a corner smaller and a runtime-only
  implementation crops the picture; drag it larger and it repeats. Both read as an editor bug.
- **One upload becomes one resource per platform.** A box of `"70%"` is 152px on emery, 198px
  on gabbro and 109px on aplite. Each needs its own file at its own size, declared with its
  own `targetPlatforms` so a platform's resource pack carries only its own variant.
- **The tiling is useful on purpose.** A small seamless source in a large rect is a repeating
  pattern for a few hundred bytes, which is the cheapest texture on the platform. Not exposed
  yet; worth remembering before someone "fixes" it.

Two other decisions worth stating:

**Downscale happens at upload, not at build.** A phone photo is several megabytes against a
256 KB published resource budget. Finding that out at build time produces a waf error about
resource pack size that never mentions the photo, so the stored source is capped at 320px on
the way in and the build reports per-platform byte costs on the way out.

**Two-colour platforms are dithered, not thresholded.** aplite, diorite and flint are three of
the seven, so this is the common case. A photo thresholded at 50% loses every mid-tone at once;
Floyd-Steinberg keeps the structure recognisable.

**Colour platforms snap to NEAREST, unlike `GColorFromRGB`.** The firmware truncates (`>> 6`),
which is right for a colour a developer specified and wrong for resampled photographic pixels,
where it would darken the whole image by up to a level per channel.

## The live canvas, and the one resolver of record

The editor draws a canvas at the platform's real pixel size, scaled up, with drag-to-move and
corner handles.

The fidelity risk is the whole design problem: **a preview that disagrees with the built face
is worse than no preview**, because it teaches a layout that is not the one being shipped. So
the client does not resolve coordinates at all. `/api/validate` returns `boxes`, the resolved
pixel box of every element on every target platform, from the same `layout.resolve_box()` the
overlap audit and the code generator use. The canvas draws from those.

Local arithmetic is used for exactly one thing: the frames of an in-flight drag, so the pointer
feels attached to the element. On release the design changes, validation re-runs, and the
server's numbers take over. A drift can therefore only last as long as a finger is down, and
it corrects visibly.

Two smaller decisions that matter more than they look:

**A drag preserves the unit.** An element positioned in percent stays in percent; one in pixels
stays in pixels. Silently converting a percentage to an absolute pixel on first drag would look
identical and would quietly destroy the cross-platform adaptation that is the point of the
document format. Dragging a `"center"` element turns it into a percentage of where it now is,
because it is genuinely no longer centred.

**The canvas is labelled a layout preview.** It does not render Pebble's fonts, so text is a
measured box with a web-font approximation inside it. The emulator render on the Preview tab is
ground truth, and the two are different affordances rather than one pretending to be the other.

## Colour, and the correction this rail applied twice

Pebble's colour screens are reflective e-paper. Saturated colours come out markedly paler
than their RGB value: `#00FF00` reaches the eye as about `#8EE391`, `#FF0000` as `#E35462`.
Blues survive noticeably better than warm colours.

**`pebble screenshot` applies that correction itself, by default.** Measured on emery with
four flat bars:

| `GColorRed` | `GColorGreen` | `GColorBlue` | `GColorYellow` | command |
|---|---|---|---|---|
| `#E35462` | `#8EE391` | `#0068CA` | `#FFEEAB` | `pebble screenshot` |
| `#FF0000` | `#00FF00` | `#0000FF` | `#FFFF00` | `... --no-correction` |

Row one is each colour's corrected value exactly, all four. So a screenshot is already the
on-wrist appearance, and `--no-correction` is what returns the framebuffer.

This rail shipped the opposite assumption. `/api/build` ran a correction table over the
screenshot and returned that as `sunlight`, with the UI defaulting to it and labelling it
ground truth. That was the correction applied **twice**, and every colour judged through the
toggle was further from the truth than the untouched screenshot would have been.

Now `sdk.render()` takes both captures from the SDK: the default as `on_watch` and
`--no-correction` as `raw`. No post-processing. The UI defaults to `on_watch` and the toggle
opts in to `raw`, which is the unusual thing to want.

`sunlight.correct_image()` was deleted rather than kept with a warning: a helper that is
wrong for the one input anybody has is a trap, and the next reader would reach for it for
exactly that input. `correct_hex` and `worst_shifts` stay, because reporting on a colour the
design **specified** is still right, which is what `/api/validate`'s `colour_shift` does.

Worth recording how it survived, because the shape recurs. The claim was plausible: an
emulator showing raw framebuffer is exactly what you would expect. It was self-confirming:
running the correction over a screenshot produced a visibly different image, which read as
the tool working rather than as the correction landing twice. And it was never checked
against a known value, which is the only thing that would have caught it. A probe drawing
opaque `#FF0000` had measured `(227, 84, 98)` in a screenshot and nobody read it.

Two consequences stated where they are actionable:

- Contrast survives where saturation does not. A red-on-green design loses most of its
  separation; light-on-dark keeps working.
- `GColorFromRGB` **truncates** (`>> 6`), it does not round to nearest. Boundaries fall at
  64, 128 and 192, so an input of 50 becomes 0, and `#7F7F7F` and `#808080` land a whole
  palette step apart. `design.snap_channel` matches the firmware exactly, because a preview
  that rounds differently from the device is the same class of lie as showing uncorrected
  colour.

## Presets, and two element types they forced

Eight starting points read off Jonas Hietala's June 2026 write-up of designing a personal
Pebble Time 2 face. His post is the one detailed public account of the whole job, it shows the
versions he **discarded** and says why, and it is machine-readable: 49 inline SVG prototypes
at `viewBox="0 0 200 228"`, which is emery exactly. So the geometry in `presets.py` is
measured off his coordinates rather than estimated from screenshots.

### Nothing on his face is decoration, and the presets have to say so

Every mark on the original reports something, and most of what drives it is not something
this rail has. Two elements are worth understanding before editing a preset:

**Wedges around the rim are calendar events positioned by TIME UNTIL, not clock time.** An
event drawn at three o'clock is three hours away; it drifts toward twelve as it approaches;
crossing twelve means it is happening now; past events vanish. So the rim is a twelve-hour
countdown and twelve means now, which is why `_event_wedge` takes HOURS. A wedge built from
degrees would be rotated by a factor of thirty and would still look plausible, so there is a
test pinning hour 3 to the right of centre and hour 0 above it.

**The thick ring just outside the dial is a completion countdown**, coloured with the ongoing
event and draining as it runs down, like a Time Timer. It also carries his work timer,
counting up during a work block and down during the deliberate five-minute warm-up.

Neither is wired to anything here: there is no calendar, no task source and no conditional
element. So every preset ships `live` and `limits` alongside its document, and both are
returned by `/api/presets` and rendered on the card where the preset is chosen rather than
left in a README. A ring that looks like a gauge and reports nothing is fine to ship and is
only fine to ship if it says so at the point of choice.

`live` is testable in one narrow way, which is enough to catch the real failure: a preset
claiming live battery must contain a battery element. That test earned its place immediately.
The comic-panel preset had an always-on battery bar, the bar came out after a render showed it
reading as debris at the bottom of the face, and the stale `battery` claim would have shipped
without it.

### Two new element types

| type | primitive | why the schema needed it |
|---|---|---|
| `polygon` | `GPath` | The only filled-polygon primitive the SDK has, and his signature wedges and comic blasts are nothing else. Points are screen-absolute without `gpath_move_to`, verified by probe. |
| `arc` | `graphics_fill_radial` | A thick ring segment between two angles. `battery-ring` uses the same call with the sweep tied to battery level, so this is a sweep-from-the-document variant rather than new drawing machinery. |

Paths are created once in `load_paths` and destroyed in `unload_paths`, never in the update
proc: an update proc runs many times a second on a heap of 24 to 128 KB, and `GPath`
allocates every point up front. There is a test asserting `gpath_create` does not appear
after `canvas_update_proc`.

### One check was relaxed, narrowly

A polygon is this schema's primitive for a shape that bleeds off the screen edge, and the SDK
clips `GPath` against the layer bounds exactly as his own prototypes clip against an SVG
`clipPath`. So vertices outside the screen are the intended construction, and both
containment checks were firing per wedge: `OUT_OF_BOUNDS` as an error, `OUTSIDE_CIRCLE` as a
warning on gabbro. The busiest preset produced eight errors on a design that renders
correctly.

For polygons only, both checks became "does this shape reach the screen at all". A polygon
that misses the screen entirely is still an error, and that is the defect worth catching. No
other type is affected, because for a rect or a text box being cropped IS the defect.

That is the third false positive fixed in `layout.py`, after round-shaped elements tested by
their box corners and centred text tested by its container width. Same lesson each time: a
warning that fires for a benign reason teaches the reader to dismiss the whole class.

Mutation-tested, because a relaxed check that cannot fail is worse than the false positive it
replaced. Emptying `_BLEEDS` fails the bleeding-polygon test; forcing `_box_reaches_screen`
true fails the fully-off-screen test. Neither passes both ways, so the relaxation is narrower
than "stop checking polygons".

### His palette does not survive the watch, measured

His mockup colours are off-palette. Through this rail's own `snap_channel`, eighteen become
thirteen and four pairs collide:

    #0E0C0A bg  #141210 ink  #2A251D frame  -> all #000000
    #C4452E rust ring     #C07A2E bronze    -> both #FF5500
    #FFC300 event yellow  #F5C518 batt low  -> both #FFFF00
    #7EA88A sage          #6E86A8 bt slate  -> both #55AAAA

The first line changes what the design can express. His dial has a thin rim and his screen a
thin frame, both in near-blacks a hair off the background, and every label sits on a dark
offset plate as a comic drop shadow. On a Pebble all of that is `GColorBlack`, so the presets
omit it rather than emit invisible elements.

The other three lines are pairs of roles that can be on screen together in operation, so the
presets pick separated palette colours (bronze `#AA5500`, battery warning `#FFAA55`) instead
of his hexes. There is a test asserting every preset colour snaps to itself, which is what
keeps a future edit from quietly reintroducing a collision.

### What the audit could not tell

The presets audit clean and the renders were still wrong, which is the part worth recording.

Eight presets were built on the real SDK across both platforms, screenshotted, and put on a
contact sheet. Four defects came out of looking, and none of them is expressible as a check:

- **The week number sat on the dial.** Grey text over a white disc, illegible, in seven of
  eight presets. `TEXT_OVERLAP` compares text against text, so text over a filled circle
  passes and every audit was clean.
- **The hour and minute hands were identical.** A pre-existing codegen bug, not a preset one:
  `max(3, radius // 12)` and `max(1, radius // 20)` both give 3 at radius 43, which is a
  Time 2 dial at a normal size. The floor hid it. Now 14% and 10% of the radius, which are
  Hietala's ratios and conventional practice, with a test per platform.
- **The first draft was the loudest thing on the sheet.** A thick pink countdown ring on the
  one design he describes as uniform and low-contrast. Thinner, and in the dusty rust his own
  first draft uses.
- **The step badge read as a flower.** Its cream centre needed the dark pip his rosette has.

And two more came from the owner opening the editor, which found what building and
screenshotting had not.

**A date element's preview ignored its format.** `Canvas.tsx` hardcoded the string
`Sat 26 Sep` for every date, and `_GLYPHS` in `layout.py` hardcoded nine glyphs for every
date. The Next Task preset uses `%d/%m`, which the watch renders as five glyphs and the real
emulator build places neatly on its plate; the editor drew ten, overflowed the plate, clipped
mid-word to "Sat 26 S" and collided with the week number beside it. So the face looked broken
in the editor and was correct on the watch, which is the exact inversion this rail's canvas
header warns about.

The format string declares the answer and two places were inferring it, differently from each
other and from codegen, which passes the format straight to `strftime`. Now `sample_text`
expands it once on the backend, ships the result in the resolved box, and both the width
estimate and the canvas read from that. The reference moment is Wednesday 30 September,
chosen because "Wednesday" is the longest weekday name and "September" the longest month
name while every abbreviated form is three characters, so one deterministic moment is
simultaneously the worst case for `%a`, `%A`, `%b` and `%B`. Padding flags are stripped and
retried before giving up, because `%-d` compiles for the watch and raises in Windows Python.

The CSS gained `text-overflow: ellipsis` in the same pass, since codegen passes
`GTextOverflowModeTrailingEllipsis` and clipping without the ellipsis reads as a rendering
fault rather than as text that does not fit.

**The default target was two watches.** Every build compiled and booted two emulators before
the layout was settled, and every audit reported round-screen findings about a design not yet
aimed at a round screen. `DEFAULT_PLATFORMS`, `blankDesign()` and the presets all default to
emery alone now. Seven of the eight presets still render correctly on gabbro, which was
verified by building and screenshotting both, and on the round screen the wedges clip into
clean arc segments that arguably look better. It is one tick to see it. Choosing a round
layout is a design decision rather than a resolution setting, which is Hietala's own
conclusion.

A third thing was stale rather than broken: the colour-shift hint in the editor still read
"The emulator returns raw framebuffer values", the claim this rail was built on and then
measured to be false. `sunlight.py`, `DESIGN.md` and the knowledge base were all corrected
when that was found; this one string was missed, and it sat directly under a working feature
telling the user the opposite of what the section below documents.

The build harness needed fixing first, and that failure is the more instructive one. Its
first version reported "shot ok" when the PNG existed. Six of eight screenshots were Pebble's
"is not responding" dialog, and all six passed, because a screenshot of a crash is also a
file. The cause was installing eight apps against one long-lived emulator, which wedges it
after the first with `[Errno 111] Connection refused` — already recorded for this SDK image
under `pebble wipe`, whose documented recovery makes it worse. One fresh container per preset
fixes it, and the harness's second version (a working script for that pass, not kept in the
repository) asserted the design's background is the dominant colour and covers a large share,
plus that at least two colours the document specifies are present. Both are things only a
working render can satisfy.

## The canvas had no verification at all

Every check on the presets went through the SDK: generate the project, build it, install it on
an emulator, screenshot, compare against Hietala's originals. That is a real gate and it found
real defects. It also never executes one line of `Canvas.tsx`, because the editor's canvas is a
separate renderer that draws from resolved boxes rather than from a build.

So the canvas had 226 passing tests, a clean conformance run, a clean smoke run, eight verified
emulator renders, and no coverage whatsoever. Both canvas defects in this rail were found by
the owner looking at the screen.

**The second one was a z-order inversion.** `.wb-el.sel` carried `z-index: 2`, to keep the
selection outline and corner handles from being buried under later elements. The side effect
was that selecting an element also raised its CONTENT above every element drawn after it,
inverting the paint order the sidebar promises: "drawn top to bottom, so later elements paint
over earlier".

Every dial in every preset is a filled disc with `analog` on top, which is the only way to get
a cream face with dark marks, since `analog` draws no circle of its own. Select the disc and it
covered the dial completely: no ticks, no hands, a plain white circle. That reads as a broken
renderer rather than as a z-order bug, and it made a correct `analog` renderer look like it had
never shipped.

Selection chrome is its own overlay layer now, `.wb-sel`, drawn above the design with
`pointer-events: none` and the handles opting back in. Element paint order is document order
again, whatever is selected.

### The screen had no visible edge, and it read as content escaping

Reported four times: presets that appeared to fall outside the emery preview screen, and later
a screenshot with arrows around the bottom band asking whether it was off the canvas.

**Nothing was ever drawn outside the screen.** `.wb-canvas` has `overflow: hidden`, and it was
measured rather than argued: every light pixel beyond the canvas rectangle belongs to the
caption above it or the footer below it, and the canvas itself measures exactly 400x456 at
zoom 2, which is emery at the declared zoom.

The actual defect was that the screen's extent was invisible. The canvas is black, it sat on a
`#18181c` surround, and its only decoration was a box shadow that does not read on a dark
panel. So three of the four screen edges were undetectable, and the ONE place the boundary
became visible was the bottom, where the lighter Quick View hatch meets the black. A band you
can see, below a region whose edges you cannot, looks exactly like a band that is wider than
the screen.

Two lines fixed it: a 1px light `outline` on the canvas, and a lighter surround so the black
screen reads as a device on a surface rather than as a hole in a dark panel.

`outline` and not `border`, deliberately. An outline is painted outside the box and does not
change the content area, so every element box stays where the server resolved it. A border
would have shrunk the screen to 398x454 and moved everything, which is the kind of fix that
creates the bug it was meant to remove. Re-measured after the change: still 400x456, and the
canvas-versus-emulator diff is unmoved at 3.2% to 5.6%.

The transferable part: the complaint named a symptom, the symptom was not the defect, and
four rounds went into answering the question as asked. A rendering that is correct but
unreadable will be reported as a rendering that is wrong, and the report is right even when
its diagnosis is not.

### `tools/canvas-shot`, which closes the gap

The harness is committed, because the gap cost three rounds on one bug.
`frontend/tools/canvas-shot/shoot.ps1` renders the real `Canvas.tsx` and `theme.css` for every
preset with no backend, and screenshots them in headless Chrome. Fixtures are captured output
from `GET /api/presets` and `POST /api/validate`, not hand-written: writing boxes by hand would
repeat the original sin, which was the canvas inventing geometry the server had already
resolved. `-Refresh` re-captures them through the gateway container, since the rail's port is
not published to the host.

Two useful query flags: `?clean=1` hides the editor's own affordances so the result can be
pixel-diffed, and `?specified=1` shows raw design colours instead of on-watch ones.

**Measured after the fidelity pass: 3.2% to 5.6% of pixels differ from the emulator render, at
a tolerance of 24/255.** That band is the floor rather than a defect, and it is made of three
things:

  * type, because the canvas has no Pebble fonts, only the server's measured box and a web
    face,
  * edges, because a CSS `border-radius` circle and a rasterised `graphics_fill_circle` differ
    by about a pixel,
  * seconds, because the two renders are taken moments apart so the hands can differ.

`quiet-dial`, the simplest preset, sets the floor at 3.2%; `break-alarm` and `loaded-day`, the
busiest, reach 5.6%. A structural defect shows up as a large contiguous region in the delta,
not as a rise of a point or two.

Getting to that number took four attempts at the comparison itself, and the failures are worth
recording because each produced a confident wrong answer about the product:

  1. ImageMagick `-compose Minus` with its operands in a guessed order. Reported 53%
     "emulator-only" for a preset that is 85% black.
  2. `-negate` plus `-compose Multiply`, same confusion. Produced a column that exactly
     equalled its own input, which is impossible for a set difference.
  3. Correct arithmetic, wrong crop. The canvas starts at (50,14) in the screenshot, not
     (0,0), so every canvas pixel was displaced and the diff showed a clean systematic offset
     in all eight presets, which reads exactly like a real layout bug. The evidence was in the
     comparison's own output: every canvas-only bounding box began at x=50, y=14.
  4. Stale harness. `fixtures.json` is bundled at build time and was refreshed after the
     build, so the colour comparison ran against boxes with no `on_watch` field and
     reported 49% disagreement on the wedge-heavy presets.

Hence the detector in the script asserts the rect it found against the size the zoom implies,
and `shoot.ps1` rebuilds after `-Refresh` rather than before.

## The AI features

Both are the same operation: emit a design document. One chat slot, `@watch-builder-designer`,
pointed at the same model as the platform's other writing roles, so no new download and no
new VRAM class.

**Describe a face.** A prompt plus the schema brief. One repair round trip on failure, then
give up with the problems visible. One repair, not a loop: a model that produced an invalid
document twice given explicit field-level errors will not converge on the third attempt, and
an unbounded loop on a shared GPU is the rudest thing this rail could do.

**Variation explorer.** N variants, each a separate call along a **named axis**. Asking for
nine in one completion produces nine near-identical documents, because the model is
completing a list rather than exploring. The named axis is also what makes the grid legible
afterwards. Temperature climbs across the pass so the early variants stay recognisable and
the later ones range further.

This is the one watchface design workflow with field evidence behind it. Jonas Hietala,
writing up a personal Pebble Time 2 face in June 2026, was direct about both halves: the
model "seems to lack creativity and taste (but sometimes it comes up with great ideas)", and
"I've gotten good usage from telling it to generate dozens of variations of a concept and
iterating from there." Model as search, person as filter.

### Deliberately not built

- **Vision critique of the contact sheet.** Designed and deferred. Cropping, centring and
  contrast are already exact measurements, and a model opinion on them would be worse than
  the number. The remaining subjective question ("which panel looks least intentional") is
  real but had no caller yet.
- **Generated backgrounds via `flux-schnell`.** Same reason: no caller yet. A declared slot
  with no caller is a chip reporting on a model nothing uses.
- **LLM-generated C.** See the safety boundary above.
- **A build-error explainer.** If the generator emits C that does not compile, that is a
  generator bug to fix, not something to paper over.

### GPU manners

The GPU may be shared with other work, and the broker evicts rather than queues. So every AI
action is a two-step: `/api/ai/estimate` reports the call count and approximate seconds, the
UI shows it, and nothing runs until a second click.

The broker's gate is FIFO and held per call, so a nine-variant pass is nine queue entries
that interleave with someone else's question rather than one long lock. That is stated in the
estimate payload because it changes whether the cost is acceptable. For a viewer the broker
answers from a platform owner's Claude CLI subscription (they are an owner and the designer's
role is switched on), the estimate says so instead and claims no GPU, which is used then only
when a call falls back to the local model.

### AI actions are jobs

Both AI actions run as background jobs (`backend/watch_builder/jobs.py`) that the page polls.
They used to run inside the request, which had to come back inside the gateway's 600 s read and
Cloudflare's ~100 s edge timeout, so the rail budgeted the request and a call that started late
got less than the broker's full timeout. Such a call could not wait out a hung CLI call plus
the broker's local fallback, so it went nameless: only the first minute or so of a request
could reach Claude, and an owner's later variants ran on the GPU while the chip said Claude.
As a job, every call waits the whole `broker.CHAT_TIMEOUT` and carries the person who asked,
and what bounds a job is a check before each call: cancelled, past its 45-minute ceiling, or
not read by its owner for its 5-minute lease. The lease is what stops a job whose tab was
closed. A tab that stays open keeps its job by reading it on a keep-alive timer whenever the
page's poll is not reading it: hidden, on another rail in the same platform tab (the timer
lives in the module, outside the component the shell unmounts), or out of touch with the rail.
The timer keeps the job's id and nothing it read. A sign-out does not reload the tab, so the
next person to open Watch Builder there may be someone else: a page shows only what a read
made as its own viewer answered, and anyone else's read of the job is a 404. A page that has
not been shown the job takes that 404 in silence, since it cannot tell it from a lost job, and
says only that it has lost contact while no read of it is answered. The timer's read carries
`?keepalive=1` only when no page is mounted to show its answer: that renews the lease without
marking a finished job delivered, so a pass that finished while no page was mounted is still
offered by the listing, and every read a page shows delivers it (a pass that finished in a
hidden tab is delivered there, so a discarded tab's reload is not offered it again).

## Why the SDK is in the image

The rail's image carries the Pebble SDK: ARM toolchain, seven platform SDKs, seven QEMU
emulators, about 2 GB. It is the largest image in the tree.

The alternative was mounting the host's Docker socket and driving a sibling SDK image. That
was rejected on two grounds: a rail that can drive the host's Docker is a
privilege-escalation path, and a rail that cannot run without the host's daemon is no longer
an independent deployable. The rail contract's first line is that a rail is an independent
deployable, and this keeps it one.

Size is paid once at build. A warm seven-platform compile measures about 4 s.

### The container runs as root, unlike the other rails

A deliberate exception. `pebble` writes to the SDK tree it owns under `/root` and spawns QEMU
processes; the SDK is installed by root at build time and the tool does not support a uid
split. Splitting would mean installing the entire 2 GB SDK twice.

The containment that matters is elsewhere: the only thing it executes is C that its own
deterministic generator produced from a schema-validated document, and it never runs one
user's design for another.

It does take one untrusted file: an image upload (`POST /api/images`), which Pillow decodes
inside this root container. It is bounded before it is read, and again before it is decoded.
A request body over `MAX_UPLOAD_BYTES` (12 MB) plus 64 KB of multipart framing gets a 413
from an ASGI ceiling in front of the app (`api/body_limit.py`): before one byte is read when
the declared length is over, and the moment the bytes received pass it otherwise, so the form
parser never spools more than that. The route then reads the file in 64 KB steps and stops one
step past 12 MB. `images.store` hands `Image.open` the allowlist (`formats=`: PNG, JPEG, GIF,
BMP and WEBP), so a file in any other format is refused without reaching its decoder, and the
format is checked before a pixel is decoded; `Image.MAX_IMAGE_PIXELS` is lowered to 40 Mpixel,
and Pillow checks the declared size at open, before decoding, warning past that and refusing
past twice it; and what is kept is re-encoded as a PNG of at most 320 px a side, in the
uploader's own directory. The SDK's resource pipeline only ever reads PNGs this rail wrote.
Hostile bytes in an allowlisted format still reach that format's decoder, so keeping Pillow
current is part of this exception. The platform's gateway streams a request body rather than
buffering it, and holds this rail to the same 12 MB + 64 KB (`max_request_bytes` in rail.json),
so an oversized image is normally refused there before any of it is relayed.

**If it ever accepts a hand-written `.c` or a third-party project, that changes and the uid
split becomes mandatory.** Written down here because that is the change that would make the
current arrangement wrong, and it would not announce itself.

### Windows has no Pebble SDK

So standalone dev on the host cannot compile. `sdk.available()` is a real question with a real
"no", and `/api/capabilities` reports `build_available: false` rather than offering a build
button that always fails. Design, validation, the layout audit and the colour report all work
without it, which is most of the rail.

An unavailable toolchain is a **third outcome**, not a degraded version of "built and found
nothing wrong". Collapsing the two would let the frontend report a clean build on a box that
never compiled anything.

## Not on the lean installer path

Seven rails are profiled in `deploy/installer/docker-compose.installer.yml`. This one is not,
and that is a decision rather than an omission: a 2 GB image carrying a cross-compiler is the
wrong thing to offer in a lean install. RC025-RC027 key off that compose file, so they skip
this rail cleanly.

If it is ever added, the contract's step 5 lists all five restatements, and missing any of
them is silent.

## Operational notes

**`pebble wipe` is not a recovery step here.** Measured on the Pebble SDK container: wipe left
every one of the seven emulators answering `[Errno 111] Connection refused`, and the
documented recovery (kill, then wipe again) made it worse. Recreating the container was the
only reliable reset, which a rail cannot do to itself. `sdk.kill_emulators()` kills and stops
there.

**The emulators stop after every render.** `pebble install --emulator` boots a QEMU emulator and
leaves it running when the command returns, and until 2026-10-14 `kill_emulators()` had no
caller, so each platform ever rendered kept a QEMU process for the container's life. `render()`
now calls it in a `finally`. Killed rather than kept warm on an idle timer, because a
long-lived emulator is the failure measured above (installs against one wedge it), and a
fresh one is the reliable state; the cost is a cold boot per build, for one platform by
default.

**Screenshots are deleted before capture.** `pebble screenshot` failing leaves the previous
image in place, and re-measuring a stale screenshot is how a broken layout reports as clean.

**Renders are serial.** Each emulator is a QEMU process on a fixed per-platform socket. Seven
in parallel would work and would put seven QEMU instances on a box that is also running a GPU
broker. Two builds' renders are serial too (`sdk._render_lock`): the kill that ends one would
otherwise cut the other's emulators mid-capture.

## Where the subject knowledge came from

A separate knowledge base on Pebble watchface design, built alongside this rail: measured
font metrics for all 28 system fonts, the generated 64-colour palette with the sunlight
correction table, seven-platform contact sheets, and documented corrections to upstream
docs. Every measured claim in this file traces to it. Those notes are not published, so the
corrections this rail depends on are restated below. The presets' source is public: Jonas
Hietala's post, <https://jonashietala.se/blog/2026/06/26/designing_a_personal_pebble_watchface>.

The corrections worth knowing, because they are the ones that produce a wrong watchface:

- **Flint (Pebble 2 Duo) is black and white**, not 64-colour. Core Devices' own watchface
  agent skill says otherwise; the SDK's platform table defines it with `PBL_BW`, which is
  what the compiler sees. A `PBL_COLOR` branch written for flint silently compiles out.
- **System fonts do not scale across platforms.** Measured, byte-identical line heights.
- **The `LECO_60` fonts exist only on flint, emery and gabbro.** Naming one elsewhere is a
  hard compile error, not a fallback.
- **`GColorFromRGB` truncates rather than rounding to nearest.**
- **Alloy (Pebble's JavaScript runtime) builds for flint and cannot run there**, because the
  firmware has no XS engine. Not relevant to this rail, which generates C, but it is the same
  class of trap: a successful build proves the toolchain accepted the input, not that the
  target can run the output.
