// Which assistant replies still belong on screen, kept free of React and of every import so
// plain node can test it (frontend/tests/chat-guard.test.js).
//
// THE DEFECT. sendChat captured the open toy A, awaited the reply, then checked the
// reply's item against that CAPTURED A. Open toy B before it lands and the check still passed:
// A's tuning was merged into params.current (already reset to B's defaults) and sent as an APPLY
// frame on B's socket, and A's answer appeared in B's fresh conversation. The guard has to
// compare against what is open NOW, and a toy re-opened after a detour is a new screen too
// (its conversation was cleared), so a screen is an identity, not just an item id.

/** One opening of one screen. A new object every time the open item changes. */
export interface Screen {
  item: string | null
}

export interface ChatAction {
  type?: string
  item?: string
  params?: Record<string, unknown>
}

export interface Settled {
  /** Show the reply in the conversation. */
  show: boolean
  /** The params to apply (and send) for the open toy, or null to apply nothing. */
  apply: Record<string, unknown> | null
}

export function settleReply(sentFrom: Screen, current: Screen, action: ChatAction | null | undefined,
  params: Record<string, unknown>): Settled {
  if (sentFrom !== current) return { show: false, apply: null }
  if (action?.type === 'set_params' && sentFrom.item !== null && action.item === sentFrom.item) {
    return { show: true, apply: { ...params, ...(action.params ?? {}) } }
  }
  return { show: true, apply: null }
}
