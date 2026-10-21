"""The Claude CLI backend's methods of `app.broker.Broker`, and the hang-up checks its calls
began with (the local chat and the media jobs use them too).

Broker's own methods, written in a file of their own. They were in broker.py until 2026-10-16,
when broker.py came within 1,049 bytes of two thirds of publish.json's data_max_bytes, the line
tools/tests/test_publish.py holds every hand-written file under. broker.py does not import this
file: it runs it in broker.py's own namespace and makes every method of the class below one of
Broker's (`_methods_from`), so each reads broker.py's names when it is called, as a test that
points one of them at a fixture expects, and is found on Broker as if written there. Why, and
what that asks of this file, is said in broker.py, "where Broker's methods live".

So nothing is imported here. Every name a method reads (asyncio, time, ClaudeCliClient,
_GONE_TICK_S, _CliAttempt, ...) is broker.py's, and a name one needs is imported or defined
there. The class is named Broker so that its methods are named as Broker's (Broker.cli_state);
it is only their container, and broker.py takes its methods and leaves the rest.
"""
from __future__ import annotations


class Broker:
    # --- Claude CLI backend -------------------------------------------------

    def _cli_client(self, timeout: float | None = None, model: str | None = None,
                    token_read: tuple[str, str] | None = None) -> ClaudeCliClient | None:
        """A client carrying the CURRENT token. Built per call rather than held, so rotating
        the token file takes effect on the next request like every other hot-read setting.
        `timeout` is what is left of the call's cli_timeout budget after waiting for a slot.
        `model` is the call's `--model` ("" for none, the CLI's own default; see
        _cli_model_for), and None means BROKER_CLI_MODEL. A streamed answer, once its first
        token is out, gets a whole cli_timeout to finish (`answer_timeout`): the budget that
        bounds the wait for a fallback is spent by then, and the rail is already reading.
        `token_read` is the `cli_token()` answer the call's latest decision checked
        (`_cli_slot` hands it in), so the client carries the token that was judged readable
        and a call that never waited reads the token file once; None reads it here."""
        if not self._cli_exe:
            return None
        token, _problem = token_read or self.settings.cli_token()
        return ClaudeCliClient(
            self._cli_exe,
            timeout=self.settings.cli_timeout if timeout is None else timeout,
            workdir=self.settings.cli_workdir,
            oauth_token=token,
            settings_json=self.settings.cli_settings_json,
            model=(self.settings.cli_model or "").strip() if model is None else model,
            answer_timeout=float(self.settings.cli_timeout),
            temp_root=self.settings.cli_temp_root,
        )

    def sweep_cli_temp(self) -> tuple[int, int]:
        """At startup: remove the per-call temp directories CLI calls left behind (a removal a
        file filter outlasted, a broker that died mid-call), under the root calls use now
        (`cli_backend.call_temp_root`). Only those untouched for an hour, or three
        BROKER_CLI_TIMEOUTs if that is longer: a younger one may be another broker's call in
        flight on the same root. With no workdir and no temp root no call ever ran here, so
        there is nothing to sweep. Never raises (a boot must not fail over leftovers); logs what
        it removed and what it could not. `(removed, left)`."""
        s = self.settings
        if not (s.cli_temp_root or "").strip() and not (s.cli_workdir or "").strip():
            return 0, 0
        root = s.cli_temp_root or s.cli_workdir
        try:
            root = cli_backend.call_temp_root(s.cli_temp_root, s.cli_workdir)
            removed, left = cli_backend.sweep_call_temps(
                root, older_than=max(cli_backend._TEMP_STALE_S, 3 * float(s.cli_timeout)))
        except Exception as exc:  # noqa: BLE001 - leftovers are never a reason not to start
            print(f"[broker] CLI temp sweep of {root} failed ({exc.__class__.__name__}: {exc})",
                  file=sys.stderr, flush=True)
            return 0, 0
        if removed or left:
            print(f"[broker] CLI temp sweep: removed {removed} stale per-call temp "
                  f"director{'y' if removed == 1 else 'ies'} under {root}"
                  + (f"; {left} would not go and stay for the next start" if left else ""),
                  file=sys.stderr, flush=True)
        return removed, left

    def _cli_model_for(self, ref: str, switches: CliRoles) -> str:
        """The `--model` a CLI call on the role `ref` (an `@role`) passes: the model the Rails
        tab chose for that role in BROKER_CLI_ROLES_FILE, else BROKER_CLI_MODEL, else "" (none,
        so the CLI's own default). From `switches`, the read the call's latest decision judged
        it by (`_cli_slot` hands it in), so the model and the decision come from one read of the
        switch file. Required: a default that read the file again had no caller but a test."""
        return self.settings.cli_role_model(ref[1:], switches.models)

    @staticmethod
    async def _caller_gone(client_gone: Callable[[], Awaitable[bool]] | None) -> bool:
        """Whether the caller of this chat has hung up. Asking must never be what fails a call,
        so a question that cannot be answered counts as "still there"."""
        if client_gone is None:
            return False
        try:
            return bool(await client_gone())
        except Exception:  # noqa: BLE001
            return False

    def _cli_env_blockers(self, token_read: tuple[str, str] | None = None) -> list[str]:
        """Configuration faults that touch DISK to detect, shared by status and dispatch.

        One function with two readers, because the first version had two hand-written lists
        and they had already drifted: `workdir_missing` made /v1/status report `ready: false`
        while `cli_decision` still routed the call, which then died in the subprocess and was
        reported as `not_installed`. Three different stories about one bad setting.

        `token_read` is a `cli_token()` answer the caller already holds (cli_state reads it for
        `token_problem`; a CLI call's decision hands its read on to the client), so one status
        poll, or one CLI call that took a free slot, reads the token file once.
        """
        s = self.settings
        out: list[str] = []
        _token, problem = token_read or s.cli_token()
        if problem:
            out.append("token_unreadable")
        workdir = (s.cli_workdir or "").strip()
        if not workdir:
            # UNSET is a blocker too, not a default. With no workdir the CLI runs in the
            # broker's own cwd, the repo root, and reads this repo's agent guide (CLAUDE.md)
            # into every rail's prompt; claudeMdExcludes does not cover that file.
            out.append("workdir_unset")
        elif not Path(workdir).is_dir():
            out.append("workdir_missing")
        raw = (s.cli_settings_json or "").strip()
        if raw:
            try:
                json.loads(raw)
            except (ValueError, RecursionError):
                # ValueError covers JSONDecodeError; RecursionError is a value nested deeper
                # than the parser's stack, which would otherwise raise out of cli_state and
                # cli_decision instead of naming the fault.
                # `claude -p` SILENTLY IGNORES a settings blob that fails validation ("no
                # error dialog is shown" in its own --help), so a mangled value here would
                # quietly stop excluding the owner's CLAUDE.md with nothing to notice.
                out.append("settings_json_invalid")
        return out

    def _record_cli_outcome(self, role: str, entry: dict[str, Any]) -> None:
        """Record one CLI outcome twice: as the role's latest whoever called (`last_by_role`)
        and as this requester's latest for the role (`last_by_requester`).

        Least recently updated goes first when a cap is passed. Insertion order is the
        recency order, so an update is popped and re-inserted rather than assigned in place.
        """
        self._cli_last_by_role[role] = entry
        who = str(entry.get("requester") or "")
        mine = self._cli_last_by_requester.pop(who, {})
        mine.pop(role, None)
        mine[role] = dict(entry)
        while len(mine) > _CLI_HISTORY_ROLES:
            mine.pop(next(iter(mine)))
        self._cli_last_by_requester[who] = mine
        while len(self._cli_last_by_requester) > _CLI_HISTORY_REQUESTERS:
            self._cli_last_by_requester.pop(next(iter(self._cli_last_by_requester)))

    def cli_state(self) -> dict[str, Any]:
        """What the CLI backend will do, and when it will not, WHY.

        Reported on /v1/status in full. A backend that can be configured several different
        ways into doing nothing must name which one applies: the alternative is an owner who
        set BROKER_CLI_ENABLED=true, saw their jobs keep running, and had no way to tell that
        the roles list was empty. `blockers` is a list rather than a first-reason string
        because fixing one of three and seeing no change is the same dead end one step on.

        `refusals` answers the complaint `blockers` cannot: "it says ready, and nothing
        changed". A per-reason count makes `no_requester` (a rail that was never wired) and
        `role_not_eligible` (a typo in the allowlist) visible, where before both looked
        identical to "nobody has asked yet".
        """
        s = self.settings
        token_read = s.cli_token()
        problem = token_read[1]
        owners = s.cli_owner_set()
        # ONE read of roles.json for the poll: the switch file is judged against it, and its
        # problem is reported below. Each used to read the file again (three times on a switch
        # file naming a role roles.json lacks), on every /v1/status poll.
        roles_read = s.roles_read()
        # The SAME rule cli_decision routes by, so `roles` here (which every rail's chip keys
        # on) can never list a role the broker would refuse, or omit one it would serve.
        eff = s.cli_effective(roles_read=roles_read)
        roles, roles_source, roles_problem, roles_note = eff.roles, eff.source, eff.problem, eff.note
        # roles.json unreadable is not a blocker (the defaults still route and resolve), but it
        # must not be silent: every role only that file defines is invisible meanwhile, the
        # switch route refuses an ON (set_cli_role) and the model picker a repoint (set_role),
        # so the Rails tab has to say why.
        overlay_problem = roles_read[1]
        if overlay_problem:
            overlay_problem = (f"{overlay_problem}, so every role resolves to the built-in "
                               f"defaults, and no Claude switch can be turned on and no role "
                               f"repointed until it is fixed. Switching Claude off still works.")
        blockers: list[str] = []
        if not s.cli_enabled:
            blockers.append("disabled")
        if s.cli_enabled and not self._cli_exe:
            blockers.append("not_installed")
        if not owners:
            blockers.append("no_owners_configured")
        if roles_problem:
            blockers.append("cli_roles_file_invalid")
        elif not roles and roles_source == "file":
            # Two different stories, so two names: "never configured" (no file, empty env) is
            # setup to do, "every role switched off" (the file says so) is a decision someone
            # made in the Rails tab. One blocker for both would send an operator looking for a
            # missing env var that was never the problem. Each slug is its own literal append:
            # web/tests/model-chips.test.js reads the slugs from this source.
            blockers.append("cli_all_roles_off")
        elif not roles:
            blockers.append("no_roles_configured")
        blockers += self._cli_env_blockers(token_read)
        return {
            "backend": "claude",
            # Whether this broker's CLI path sends images (cli_backend.SENDS_IMAGES), so a
            # gateway can check it before a vision role is switched on. An older broker that
            # refused every image call sends no such key. What the code carries, whatever
            # `ready` says.
            "vision": SENDS_IMAGES,
            # Likewise whether /v1/chat/stream reaches the CLI (cli_backend.STREAMS_TEXT). An
            # older broker, whose streamed calls always stayed local, sends no such key.
            "streams": STREAMS_TEXT,
            "enabled": s.cli_enabled,
            "ready": not blockers,
            "blockers": blockers,
            "exe": self._cli_exe or "",
            # Usernames, not secrets, and /v1/status is itself token-gated. Listing them is
            # the only way an operator can check the routing matches who they meant.
            "owners": sorted(owners),
            "roles": sorted(roles),
            # Where `roles` came from: "env" (BROKER_CLI_ROLES) or "file" (the Rails tab's
            # switches), whether the switch file is configured at all, and why it was refused.
            # `roles_problem` is the refusal when `cli_roles_file_invalid` is a blocker, and
            # otherwise a note about keys the file holds that no role answers to (ignored;
            # see BrokerSettings._parse_cli_roles), which blocks nothing. Either way it leads
            # with why roles.json cannot be read, when it cannot.
            "roles_source": roles_source,
            "roles_file_set": bool((s.cli_roles_file or "").strip()),
            "roles_problem": " ".join(p for p in (overlay_problem, roles_problem or roles_note)
                                      if p),
            # Which Claude model answers. `models` is BROKER_CLI_MODELS, the ids a role may be
            # given; `default_model` is BROKER_CLI_MODEL ("" = the CLI's own default);
            # `role_models` is the model the switch file gives each role it names with an
            # allowed one, switched on or off; `call_models` is the `--model` a call on each
            # eligible role passes right now ("" = none, so the CLI's own default).
            "models": s.cli_model_list(),
            "default_model": (s.cli_model or "").strip(),
            "role_models": dict(sorted(eff.models.items())),
            "call_models": {r: s.cli_role_model(r, eff.models) for r in sorted(roles)},
            "workdir": (s.cli_workdir or "").strip(),
            "token_problem": problem,
            # Seconds per CLI call, the wait for a slot included. Harmless configuration, and
            # the one figure every rail's own client timeout has to outlast plus a local
            # answer, which the broker cannot enforce. Reported so each CLI-wired rail can be
            # checked against it: it is usually set only in the service environment, where no
            # rail can read it.
            "timeout_s": float(s.cli_timeout),
            # The concurrency cap and how many calls are waiting for a slot right now. A call
            # that is waiting is in neither `active` nor the GPU queue.
            "concurrency": {"limit": self._cli_limit, "waiting": self._cli_waiting},
            "calls": self._cli_calls,
            "refusals": dict(sorted(self._cli_refusals.items())),
            "fallbacks": {"count": self._cli_fallbacks, "last": self._last_cli_fallback},
            # Streamed answers the CLI cut off after their first token: no local answer follows
            # (the rail has shown the text), so these are not fallbacks. `last` is
            # {requested, reason, chars}: the role, the failure's slug, how much was sent.
            "interruptions": {"count": self._cli_interruptions,
                              "last": (dict(self._last_cli_interruption)
                                       if self._last_cli_interruption is not None else None)},
            # COPIES, down to each entry. The result is serialised after this returns, by code
            # the broker does not control, while finishing calls keep editing these dicts:
            # handing out the live ones makes what is reported depend on when the encoder
            # happens to run. It also stops an in-process caller that decorates what it was
            # handed from quietly rewriting the broker's own bookkeeping.
            # `requester` (in both blocks) is a platform username: /v1/status is token-gated,
            # but the GATEWAY must filter on it and strip it before this reaches a browser.
            "active": [dict(e) for e in sorted(self._cli_active.values(),
                                               key=lambda e: e["since"])],
            "last_by_role": {role: dict(e) for role, e in self._cli_last_by_role.items()},
            # Each requester's own latest per role, so an owner's outcome survives another
            # owner's later call on the same role. Same entry shape as last_by_role.
            "last_by_requester": {who: {role: dict(e) for role, e in roles.items()}
                                  for who, roles in self._cli_last_by_requester.items()},
        }

    def cli_summary(self) -> dict[str, Any]:
        """What the admin Rails tab needs to draw its per-role Claude switches and model
        selects: is the backend ready, why not, which roles are on, where that list came from,
        which models a role may be given (`models`), the default (`default_model`), each
        role's own (`role_models`), whether this broker's CLI path sends images (`vision`),
        so a gateway can tell whether switching a vision slot on would do anything, and
        whether it streams (`streams`), the same question for a streaming slot.

        A SLICE of cli_state, not a second computation, so the tab, the chips and the routing
        all read one answer. Carried on `roles_inventory` (the tab's one broker read) and on
        the switch route's reply. No owners, no requesters and no machine paths: the tab draws
        switches, and who pays is not one of them.
        """
        st = self.cli_state()
        return {"ready": st["ready"], "blockers": list(st["blockers"]),
                "enabled_roles": list(st["roles"]), "roles_source": st["roles_source"],
                "roles_file_set": st["roles_file_set"], "roles_problem": st["roles_problem"],
                "models": list(st["models"]), "default_model": st["default_model"],
                "role_models": dict(st["role_models"]), "vision": st["vision"],
                "streams": st["streams"]}

    def cli_decision(self, ref: str, requester: str | None) -> tuple[bool, str]:
        """May THIS call go to the CLI? ``(use_cli, reason)``.

        Pure, and separate from the call itself, because this predicate is the licence
        condition in code: Anthropic does not permit routing requests through a Pro/Max
        credential "on behalf of their users", so a request that is not the subscriber's own
        must not reach it. A predicate that is easy to read and easy to test is worth more
        here than one fused into the dispatch path.

        **Only an `@role` is eligible, never a concrete model name.** `route()` deliberately
        lets a caller name `upstream::model` directly, and the backlog records that as a
        widening nobody asked for. The same hole here would be worse: any holder of an
        inference-scoped token could name the CLI and spend the owner's subscription. Roles
        are set by an admin, so keying on them keeps the decision where the operator put it.
        """
        use_cli, why, _switches, _token_read = self._cli_decide(ref, requester)
        return use_cli, why

    def _cli_decide(self, ref: str, requester: str | None, switches: CliRoles | None = None,
                    token_read: tuple[str, str] | None = None,
                    ) -> tuple[bool, str, CliRoles | None, tuple[str, str] | None]:
        """cli_decision, and the two reads it judged the call by: the switch file
        (`cli_effective`) and the token file (`cli_token()`). Each is None when it answered
        before reading it: everyone but an owner asking a role reads neither, and the token is
        read last, once the role is eligible. `switches` and `token_read` are reads the caller
        already holds (a call that took its slot at once is decided again on its arrival's
        reads); None reads the file, after the owner check."""
        s = self.settings
        if not s.cli_enabled:
            return False, "disabled", None, None
        if not self._cli_exe:
            return False, "not_installed", None, None
        owners = s.cli_owner_set()
        if not owners:
            return False, "no_owners_configured", None, None
        if not requester or not requester.strip():
            # Background work (the scheduler, a container-start warm) has no requester, and
            # that is exactly the call that must NOT go to a personal subscription: nobody is
            # waiting on it and nobody consented to it.
            return False, "no_requester", None, None
        # Exact, case included (see cli_owner_set). The gateway forwards the account's stored
        # spelling, so a case variant here is a different account, never the owner.
        if requester.strip() not in owners:
            return False, "not_owner", None, None
        if not ref.startswith("@"):
            return False, "not_a_role", None, None
        # The eligible roles, from the one function cli_state reports too (BROKER_CLI_ROLES, or
        # the Rails tab's switch file once one exists). Read only now, after the owner check,
        # because the switch file is on disk: a non-owner's call never pays for the read.
        if switches is None:
            switches = s.cli_effective()
        roles, roles_problem = switches.roles, switches.problem
        if roles_problem:
            # Fail closed, and say why: a broken switch file must not read as "this role was
            # simply never switched on".
            return False, "cli_roles_file_invalid", switches, None
        # Exact, not folded: role names are case-sensitive everywhere else. `@EDU` used to pass
        # here as @edu, reach the CLI, and then, on any CLI failure, fall back to a local
        # resolve that raised UnknownRoleError, because roles() has no "EDU". A role the local
        # path cannot serve is not one the CLI may serve either.
        if ref[1:] not in roles:
            return False, "role_not_eligible", switches, None
        # LAST, because these touch the disk. Everything above the role check is a set
        # lookup, and the overwhelmingly common answer on this box is "not the owner" or "no
        # requester" -- paying a synchronous file read inside the event loop to reach that
        # answer would put a disk hit on every chat the backend is not even going to serve.
        if token_read is None:
            token_read = s.cli_token()
        for blocker in self._cli_env_blockers(token_read):
            return False, blocker, switches, token_read
        return True, "ok", switches, token_read

    # --- one CLI attempt, shared by chat and chat_stream ------------------------------------
    #
    # The lifecycle of a CLI call in five steps, so the buffered and the streamed path cannot
    # drift apart on the parts that are the licence condition or the accounting: decide
    # (_cli_begin), hold a slot (_cli_slot), then exactly one of served (_cli_served) or failed
    # (_cli_failed), and on failure the gate before a local answer (_cli_fallback_starts).

    def _cli_begin(self, model: str, requester: str | None,
                   rail: str | None) -> _CliAttempt | None:
        """Decide whether this call may go to the CLI. None, with the refusal counted, when it
        may not; otherwise the attempt, carrying its in-flight token, its requester and the
        switch-file and token-file reads it was decided by."""
        use_cli, why, switches, token_read = self._cli_decide(model, requester)
        if not use_cli:
            # Counted, because "it says ready and nothing changed" is the most likely
            # complaint and `blockers` cannot answer it: a mistyped role name and a rail that
            # never passes a requester both look exactly like "nobody has asked yet".
            self._cli_refusals[why] = self._cli_refusals.get(why, 0) + 1
            return None
        # Taken BEFORE anything can raise, so the slot's `finally` can always name it, even
        # when building the entry is the thing that raised.
        self._cli_active_seq += 1
        # cli_decision only said yes because this is non-blank. Stripped exactly as it was
        # matched and NOT case-folded, because the owner check is exact: folding here would
        # file a call under a name the licence check never accepted.
        who = (requester or "").strip()
        return _CliAttempt(model=model, who=who, rail=rail, token=self._cli_active_seq,
                           switches=switches, token_read=token_read)

    @asynccontextmanager
    async def _cli_slot(self, att: _CliAttempt, requester: str | None,
                        client_gone: Callable[[], Awaitable[bool]] | None, *,
                        stream: bool = False) -> AsyncIterator[_CliAttempt]:
        """Hold one of the `cli_concurrency` slots for the body of the `async with`, with the
        call registered in `active` and `att.client` built for what is left of the budget.

        Raises (from the `async with` line, before the body runs) CliUnavailable `busy` when no
        slot came free in time or the wait used the budget, CliUnavailable under the new reason
        when the decision changed while the call waited, and ClientGone when the caller hung
        up meanwhile. `stream` marks the in-flight entry as a streamed call's (`"stream":
        true`), which holds its slot until its last frame."""
        # ONE budget for waiting for a slot AND for the call. "Every failure is a fallback"
        # holds only if the fallback starts before the rail gives up, so a CLI-wired rail's
        # own client timeout has to outlast cli_timeout plus one local answer. Nothing here
        # can enforce that; each wired rail is checked against cli_timeout, which status
        # reports as cli.timeout_s for that purpose. A call that waited gets what is
        # LEFT of cli_timeout, never a fresh one, so its fallback still starts no later
        # than cli_timeout after it arrived.
        budget = float(self.settings.cli_timeout)
        arrived = time.monotonic()
        holding = False
        # Whether this call will wait for its slot. A free one is taken by a bare acquire(),
        # which returns without a turn of the loop on every Python, so nothing can change the
        # switch file between the arrival's decision and the slot. NOT through wait_for: on
        # Python 3.11 it wraps acquire() in a task that runs a turn later, so two calls created
        # in one tick both saw a free slot, and the one that lost it waited with `waits` False
        # and was decided again on its stale arrival read (a role switched off meanwhile still
        # reached the CLI). 3.12 awaits it inline, which hid this here.
        waits = self._cli_slots.locked()
        try:
            self._cli_waiting += 1
            try:
                if waits:
                    await asyncio.wait_for(self._cli_slots.acquire(), timeout=budget)
                else:
                    await self._cli_slots.acquire()
            except asyncio.TimeoutError:
                raise CliUnavailable(
                    "busy", f"no CLI slot came free within {budget:.0f}s "
                            f"({self._cli_limit} already running)") from None
            finally:
                self._cli_waiting -= 1
            holding = True
            # Nobody left to answer. A rail that gave up while this waited (its own timeout,
            # or the person leaving) would otherwise start a CLI call on the owner's
            # subscription for a reply nobody reads, and hold a slot from live calls.
            if await self._caller_gone(client_gone):
                raise ClientGone("the caller hung up while this call waited for a CLI slot")
            # Decided AGAIN now that the call holds a slot. The first decision was taken on
            # arrival, and a call can wait above for most of cli_timeout: a role switched off
            # meanwhile (the chips say local at once), or a switch file that turned invalid,
            # must stop this call as surely as it stops the next one. So a call that WAITED
            # reads the switch file (and roles.json) and the token file again; one that took a
            # free slot is decided again on its arrival's reads, which nothing has had a turn to
            # change, and so reads each file once for the whole call.
            still, why_now, att.switches, att.token_read = self._cli_decide(
                att.model, requester, None if waits else att.switches,
                None if waits else att.token_read)
            if not still:
                raise CliUnavailable(why_now, "refused once a CLI slot came free: the "
                                              "decision changed while the call waited")
            left = budget - (time.monotonic() - arrived)
            if left <= 0:
                raise CliUnavailable("busy", "the wait for a CLI slot used the whole budget")
            # Chosen now, from the read the decision above was taken on: the role's own model in
            # the switch file, else BROKER_CLI_MODEL, else none.
            att.cli_model = self._cli_model_for(att.model, att.switches)
            # And the token the decision checked: the client carries the read judged readable.
            att.client = self._cli_client(timeout=left, model=att.cli_model,
                                          token_read=att.token_read)
            # Registered only once the call HOLDS a slot: a call still waiting for one is
            # not running on the owner's subscription and must not be shown as if it were.
            self._cli_active[att.token] = {
                "role": att.model, "rail": att.rail, "since": time.time(),
                "requester": att.who, **({"stream": True} if stream else {}),
            }
            yield att
        finally:
            # In a FINALLY for the reason cli_backend reaps its subprocess in one: removal
            # written into the success and except paths misses cancellation (a shutdown,
            # an outer timeout), which is a BaseException no `except Exception` sees. The
            # entry left behind would show the owner's call running for ever, and nothing else
            # ever removes it. The slot goes back here too, and before any fallback runs, so a
            # call that fell back never holds a slot while Ollama answers.
            self._cli_active.pop(att.token, None)
            if holding:
                self._cli_slots.release()

    def _cli_served(self, att: _CliAttempt, usage: dict[str, Any]) -> None:
        """Account for an answer the CLI gave: the call count, the log line, and `ok` for the
        role. Called immediately before the answer is handed back, so the only way to record
        "ok" is to actually hand the CLI's answer back."""
        self._cli_calls += 1
        # No prompt text and no account name, deliberately: this line is written for
        # an operator reading a service log, and both of those would put a rail's
        # content and the owner's identity into it.
        dropped = usage.get("dropped")
        print(f"[broker] CLI: {att.model} served by claude cli "
              f"(in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')}"
              + (f"; dropped {','.join(dropped)}" if dropped else "") + ")",
              file=sys.stderr, flush=True)
        # `requester` says WHOSE call this was: the record is per role, and with two owners the
        # latest outcome may be the other one's, which the gateway can only filter out if the
        # entry names it. `model` is the `--model` it passed ("" for none, the CLI's default).
        self._record_cli_outcome(att.model, {"ok": True, "at": time.time(), "requester": att.who,
                                             "model": att.cli_model})

    def _cli_failed(self, att: _CliAttempt, exc: BaseException) -> tuple[dict[str, str], str]:
        """Record a CLI failure for the role and return `(cli_fallback, detail)`: the marker a
        local answer will carry, and the CLI's own words for the log and `fallbacks.last`."""
        reason = getattr(exc, "reason", None) or type(exc).__name__
        detail = getattr(exc, "detail", None) or str(exc)
        # The reason slug only, never `detail`: that is the CLI's own error text, and this
        # per-role record is meant to be shown to people the log line is not. The CLI path did
        # fail (the CLI itself, no slot in time, the re-decision once one came free, or the
        # client refusing before it spawned anything: no user content, an image it will not
        # send, no executable), so this is recorded whether or not a local answer follows.
        # `model` only once one was chosen: a call that got no slot, or was refused when it
        # got one, tried none.
        self._record_cli_outcome(att.model, {"ok": False, "at": time.time(),
                                             "reason": reason, "requester": att.who,
                                             **({} if att.cli_model is None
                                                else {"model": att.cli_model})})
        # Counted as a fallback only once one STARTS (_cli_fallback_starts): a caller that hung
        # up meanwhile gets no local answer, and counting it would report one that never ran.
        return {"backend": "ollama", "reason": reason}, detail[:200]

    async def _cli_fallback_starts(self, model: str, cli_fallback: dict[str, str], detail: str,
                                   client_gone: Callable[[], Awaitable[bool]] | None) -> None:
        """The gate between a failed CLI call and its local answer: ClientGone if the caller
        has hung up meanwhile, otherwise the fallback is counted and logged, and starts."""
        if await self._caller_gone(client_gone):
            # The CLI failed, often by timing out after most of cli_timeout, and the rail
            # gave up meanwhile. A local answer now would load a model on the shared card for
            # nobody, so none starts, and this is NOT a fallback: cli.fallbacks and
            # fallbacks.last are left alone, and the log line says skipped.
            print(f"[broker] CLI: {model} failed ({cli_fallback['reason']}) and was skipped, "
                  f"the caller hung up before the local answer started",
                  file=sys.stderr, flush=True)
            raise ClientGone("the caller hung up before the local fallback started")
        self._cli_fallbacks += 1
        self._last_cli_fallback = {"requested": model, "reason": cli_fallback["reason"],
                                   "detail": detail}
        print(f"[broker] CLI FALLBACK: {model} -> local ({cli_fallback['reason']}): "
              f"{detail}", file=sys.stderr, flush=True)

    def _cli_interrupted(self, att: _CliAttempt, exc: BaseException,
                         chars: int) -> dict[str, Any]:
        """Account for a streamed CLI answer that failed AFTER its first token, and return the
        stream's last line.

        Not a fallback, and none follows: the rail has already shown `chars` characters, and a
        local answer appended to them would read as one answer that changed course half way. So
        the stream ends with a line every rail's reader raises on (`error`), marked
        `cli_interrupted` so a rail can say why, with `done` so a reader that only looks for the
        last frame stops there too. A frame without `error` would be read as a complete answer.
        Recorded for the role as `interrupted` (its cause beside it), counted in
        `cli.interruptions`, and logged without a word of the answer."""
        cause = getattr(exc, "reason", None) or type(exc).__name__
        detail = getattr(exc, "detail", None) or str(exc)
        entry: dict[str, Any] = {"ok": False, "at": time.time(), "reason": "interrupted",
                                 "cause": cause, "requester": att.who}
        if att.cli_model is not None:
            entry["model"] = att.cli_model
        self._record_cli_outcome(att.model, entry)
        self._cli_interruptions += 1
        self._last_cli_interruption = {"requested": att.model, "reason": cause, "chars": chars}
        print(f"[broker] CLI INTERRUPTED: {att.model} stopped after {chars} characters "
              f"({cause}): {detail[:200]}", file=sys.stderr, flush=True)
        return {"error": f"the Claude CLI stopped mid-answer ({cause}); the text above is "
                         f"incomplete",
                "done": True, "done_reason": "cli_interrupted",
                "cli_interrupted": {"reason": cause, "chars": chars}}

    async def _watch(self, frames: AsyncIterator[dict[str, Any]],
                     client_gone: Callable[[], Awaitable[bool]] | None,
                     every: float = 2.0) -> AsyncIterator[dict[str, Any]]:
        """`frames` (the CLI client's stream), re-yielded, asking `client_gone` every `every`
        seconds while the next frame is awaited: ClientGone once the caller has hung up. It owns
        `frames` and closes it on every exit.

        Why a tick: under ASGI 2.4 Starlette never listens for a disconnect while it streams; it
        notices only when a write fails, and a CLI thinking before its first token (or stalled
        after it) writes nothing for minutes. Under 2.3 it cancels the stream itself, and this
        is a second way to the same end.

        The next frame is awaited in a task of its own, so a tick can look without cancelling
        it. That task, not this one, is where the client's own `finally` runs when it is
        stopped, so a cancellation anyio delivers here again and again never reaches the CLI
        client's reap (cli_backend._reap covers the cases that do run in the cancelled task).
        And this generator ends only once that task has, the reap in its `finally` included
        (cli_backend.settle, through any cancellation): the CLI slot this stream holds is given
        back after this ends, and a slot given back while taskkill was still running let one
        more CLI call start than BROKER_CLI_CONCURRENCY allows.
        With nobody to ask (`client_gone` None) there is no tick and no task."""
        if client_gone is None:
            async with aclosing(frames) as only:
                async for frame in only:
                    yield frame
            return
        it = frames.__aiter__()
        nxt: asyncio.Future[dict[str, Any]] | None = None
        try:
            while True:
                nxt = asyncio.ensure_future(anext(it))
                while not nxt.done():
                    await asyncio.wait({nxt}, timeout=every)
                    if not nxt.done() and await self._caller_gone(client_gone):
                        raise ClientGone("the caller hung up while the Claude CLI was answering")
                try:
                    frame = nxt.result()
                except StopAsyncIteration:
                    return
                nxt = None
                yield frame
        finally:
            if nxt is not None and not nxt.done():
                # The next frame was still being produced: cancelling its task stops the client
                # from inside, in the task that was running it, and its finally reaps the tree.
                # Waited for even when THIS task is the one being cancelled (Starlette's, under
                # ASGI 2.3): the slot must not come free before the tree is dead.
                nxt.cancel()
                nxt.add_done_callback(lambda t: t.cancelled() or t.exception())
                await settle(nxt)
            else:
                await frames.aclose()

    async def _while_there(self, call: Awaitable[dict[str, Any]],
                           client_gone: Callable[[], Awaitable[bool]] | None,
                           every: float = 2.0,
                           doing: str = "the Claude CLI was answering") -> dict[str, Any]:
        """`call` (a buffered CLI call: /v1/chat's, or a streamed call's with a `format` or
        images; and a read-aloud's media job, tts_light) awaited in a task of its own, asking
        `client_gone` every `every` seconds while it runs: ClientGone ("... while `doing`") once
        the caller has hung up, with the call stopped and its tree killed.

        `_watch`'s tick for an answer that arrives in one piece. Without it a caller who left
        was noticed only when the call ended: under ASGI 2.4 a stream never hears a disconnect
        until it writes, and a plain request never hears one at all, so a CLI answer nobody
        would read ran to its end or to `cli_timeout` on the owner's subscription, holding a
        slot. Cancelling the task stops the client from inside, in the task that was running
        it, so its own `finally` reaps the tree; this returns or raises only once that task
        has ended (cli_backend.settle, through any cancellation of this one), because the slot
        comes free after it. With nobody to ask (`client_gone` None) it is a plain await."""
        if client_gone is None:
            return await call
        task = asyncio.ensure_future(call)
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=every)
                if not task.done() and await self._caller_gone(client_gone):
                    raise ClientGone(f"the caller hung up while {doing}")
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                task.add_done_callback(lambda t: t.cancelled() or t.exception())
                await settle(task)

    async def _hung_up(self, client_gone: Callable[[], Awaitable[bool]] | None) -> bool:
        """Whether the cancellation that just stopped a CLI call was its caller hanging up.

        Under ASGI 2.3 (what uvicorn reports) Starlette hears a disconnect itself and cancels the
        stream, so the hang-up arrives as a cancellation, never as ClientGone. A cancellation
        can also be a server shutting down, so the connection is asked once whether it has gone,
        and only a yes is a hang-up. Asked inside a shielded anyio scope: anyio delivers its
        cancellation again at every await, this one included. Bounded, and any failure to answer
        (no connection to ask: an in-process caller) is "no"."""
        if client_gone is None:
            return False
        with anyio.CancelScope(shield=True):
            try:
                return await asyncio.wait_for(self._caller_gone(client_gone), _GONE_TICK_S)
            except BaseException:  # noqa: BLE001 -- the caller re-raises its own cancellation
                return False
