# Platform gateway + shell — backlog

First backlog at this level; the convention so far has been one per rail
(`rails/<id>/BACKLOG.md`). Items here are the gateway API or the admin shell, which belong to no
rail.

## 1. A "Test" button on Admin → Rails, per model slot

The panel now lets you point a slot at a broker and a model. It cannot tell you whether that
choice **works**, and the four-state chip only says whether the model is *resident* — not whether
a real call returns something usable. Those are different questions, and the gap between them is
where this session's actual bugs lived.

Put a **Test** next to Apply on each slot. It sends one small, bounded completion through the
same path the rail uses and reads back what a person actually needs to know.

### What it should report

Grounded in failure modes seen on this platform, not a generic health check:

| Readout | Why it earns its place |
|---|---|
| **Where it ran** — `local` or the upstream name | A delegated role is invisible otherwise. Until the residency fix, a working remote model showed a red chip; the reverse (you *think* it is off-site and it is not) has no signal at all today. |
| **Resolved model** — the concrete name | The picker shows a glob (`mistral-small3*:24b`). Which release answered is a different fact, and on a delegated role it was resolved against a different box's inventory. |
| **Cold or warm**, and the time | A 15 GB cold load is ~15 s against ~7 s warm. A number without that label is unreadable, and the *second* click is the useful one. Say which was measured. |
| **Throughput** — eval tokens/sec | The number that answers "is the remote box worth the hop". |
| **Content was non-empty** | A thinking model asked for structured output returns EMPTY content about a third of the time at ~8x latency — measured on this platform and documented in the broker's own schema comments. A test that checks only HTTP 200 passes on an empty answer, which is the single most misleading possible result. |
| **`finish_reason`** | `length` means the reply was truncated at `num_predict`. Reporting "success" over a clipped answer is the same lie the chip contract exists to prevent. |
| **VRAM headroom on the serving box, and anything evicted** | Clicking Test is not free — see below. |

### Costs to design around, not discover

- **It can evict another rail's resident model.** The broker holds one heavy model; a Test on a
  cold slot pays a real load and pushes something else out. Either warn in the button's title,
  or prefer a slot that is already resident, or make the eviction part of the readout so the
  cost is visible rather than mysterious.
- **It takes the GPU gate**, so it queues behind real work and a busy box makes Test look slow
  when nothing is wrong. Show the queue position if it waits.
- **A delegated slot spends the REMOTE box's GPU**, not this one. Worth saying which machine the
  click just cost.
- Keep `num_predict` small and `temperature` at 0. This is a probe, not a demo.

### Where it goes

- Gateway: a new `POST /api/platform/admin/rails/{role}/test`, admin-gated like its siblings,
  returning the fields above. It already resolves a slot's broker for the model picker, so the
  routing work is done.
- Broker: `/v1/chat` already returns `model`, `done_reason`, `prompt_eval_count`, `eval_count`
  and `eval_duration`; `/v1/status` has VRAM and the loaded list. Everything needed is on the
  wire — this is mostly assembly, not new plumbing.
- Frontend: `apps/platform/frontend/src/AdminPage.tsx`, beside the existing Apply.

### One thing to get right

Report failures in the same shape as successes. A broker that is down, a token the upstream
rejects, and a model that answers with nothing are three different problems with three different
fixes, and collapsing them into "Test failed" would make the button worth less than the chip it
sits next to.
