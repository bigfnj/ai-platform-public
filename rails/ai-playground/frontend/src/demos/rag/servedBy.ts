// Who answered a RAG question, from /ws/rag's `backend` frame. Kept free of React and of every
// import, so plain node can load it (frontend/tests/served-by.test.js).
//
// Why it exists: the demo's generation label names the LOCAL model ("NVIDIA Nemotron ... on
// the RTX 4090"), and an owner's question on a role switched to the Claude CLI is answered by
// Claude instead. The backend relays the broker's leading frame as
//   {"type": "backend", "backend": "claude", "model": "<id>"}   Claude answers, or
//   {"type": "backend", "backend": "ollama", "reason": "<slug>"} Claude fell back to local,
// and this turns it into the one line the demo shows above that answer.

/** The line to show above the answer, or null for anything that is not a backend frame. */
export function servedByLabel(frame: any): string | null {
  if (!frame || typeof frame !== 'object' || frame.type !== 'backend') return null
  if (frame.backend === 'claude') {
    const model = typeof frame.model === 'string' ? frame.model.trim() : ''
    // "claude-cli" is the broker's placeholder when the CLI named no model: not a model id.
    return model && model !== 'claude-cli' ? `answered by Claude · ${model}` : 'answered by Claude'
  }
  if (frame.backend === 'ollama') {
    const reason = typeof frame.reason === 'string' ? frame.reason.trim() : ''
    return reason
      ? `answered by the local model: Claude was unavailable (${reason})`
      : 'answered by the local model: Claude was unavailable'
  }
  return null
}
