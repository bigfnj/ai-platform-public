// Where the desk pet's speech bubble goes: over the pet, and always inside the window.
//
// It used to be `left: <pet centre>` with `transform: translate(-50%, -100%)`. A fixed box with
// a `left` and no width shrinks to the room between that left and the window's right edge, so a
// pet near the right edge got a bubble one word wide, and the translate then pulled half of that
// narrow box past the edge anyway. Near the left edge the same translate pushed it off at x = 0,
// and a pet near the top lifted it above the window.
//
// Now DeskPet sizes the bubble by its text alone (width: max-content, capped by BUBBLE_MAX_W),
// measures it, and places it here: centred over the pet where there is room, slid along the edge
// where there is not, and put under the pet when there is no room over it.
// Tested by tests/deskpet-bubble.test.js.

export const BUBBLE_MARGIN = 8    // px kept clear of every window edge
export const BUBBLE_GAP = 10      // px between the bubble and the pet
export const BUBBLE_MAX_W = 220   // px, the widest a bubble grows before its text wraps

export interface Anchor { x: number; top: number; bottom: number }   // pet centre x, its top and bottom y
export interface Size { w: number; h: number }

function clamp(v: number, lo: number, hi: number): number {
  // A box wider than the room left between the margins is pinned to the left margin.
  return Math.max(lo, Math.min(v, hi))
}

export function placeBubble(pet: Anchor, box: Size, view: Size): { left: number; top: number } {
  const m = BUBBLE_MARGIN
  const left = clamp(pet.x - box.w / 2, m, view.w - m - box.w)
  const above = pet.top - BUBBLE_GAP - box.h
  const below = pet.bottom + BUBBLE_GAP
  let top: number
  if (above >= m) top = above
  else if (below + box.h <= view.h - m) top = below
  else top = clamp(above, m, view.h - m - box.h)
  return { left, top }
}
