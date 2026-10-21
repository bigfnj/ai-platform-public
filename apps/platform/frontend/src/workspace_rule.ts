// The Workspaces tab's mirror of the gateway's room rules. Pure, so node can test it
// (apps/platform/frontend/tests/admin-rails.test.js).
//
// main.py _refuse_superadmin_room: a non-super-admin gets 403 on any rename, member change or
// delete of a workspace that holds a super-admin NOW, the Users tab's "only a super-admin can
// modify a super-admin" carried over to the rooms that decide whose records a super-admin
// shares. The tab offered all three controls anyway, so a plain admin was invited to click
// something the server refuses. The server stays the authority; the UI says so up front.
//
// main.py _refuse_room_out_of_reach (2026-10-15): the same 403 for a room that holds a member
// beyond the actor's reach, the Users tab's reach rule (user_rule.ts) carried over the same way.
// A room decides whose records its members read, so changing one that holds a user this admin
// could not reset changes that user's sharing. Judged from the two lists the users route
// already sends (`apps` per row, `grantable`), as the Users tab judges reach.

import { outOfReach } from './user_rule.ts'

/** What the row says when its controls are locked: the gateway's own sentence. */
export const SUPERADMIN_ROOM_NOTE = 'Only a super-admin can change or delete a workspace that contains a super-admin.'

/** What a name the actor may not place in a room says, when no app explains it. */
export const MANAGE_NOTE = 'a workspace decides whose records this user can read, so it is limited to the users you may already manage'

/** The stored name's limit (main.py _WORKSPACE_NAME_MAX, the column width). */
export const WORKSPACE_NAME_MAX = 64

/** True when this actor may not change or delete the room: it holds a super-admin and the
 *  actor is not one. A super-admin is never locked out; a member is still not a super-admin. */
export function roomLocked(members: string[], superadmins: ReadonlySet<string>, actorIsSuper: boolean): boolean {
  if (actorIsSuper) return false
  return members.some((m) => superadmins.has(m))
}

/** A member beyond the actor's reach, with the apps that put them there. */
export interface BeyondReach { user: string; apps: string[] }

/** The room's members the actor could not reset, each with the apps beyond reach, by name.
 *  Empty for a room within reach, and for a super-admin actor: their `grantable` is the whole
 *  catalog, so nothing is beyond them (no separate exemption, as in the gateway). */
export function roomBeyondReach(members: readonly string[], appsOf: ReadonlyMap<string, readonly string[]>,
                                grantable: readonly string[], catalogIds: readonly string[]): BeyondReach[] {
  return [...members].sort().flatMap((user) => {
    const apps = outOfReach(appsOf.get(user) ?? [], grantable, catalogIds)
    return apps.length ? [{ user, apps }] : []
  })
}

/** Why the actor may not change or delete the room, or null: the gateway's two refusals in its
 *  order, the super-admin one first. */
export function roomLockNote(members: string[], superadmins: ReadonlySet<string>, actorIsSuper: boolean,
                             appsOf: ReadonlyMap<string, readonly string[]>, grantable: readonly string[],
                             catalogIds: readonly string[]): string | null {
  if (roomLocked(members, superadmins, actorIsSuper)) return SUPERADMIN_ROOM_NOTE
  const beyond = roomBeyondReach(members, appsOf, grantable, catalogIds)
  if (!beyond.length) return null
  const who = beyond.map((b) => `${b.user} holds apps you cannot grant (${b.apps.join(', ')})`).join('; ')
  return `Only a super-admin can change or delete this workspace: ${who}.`
}

/** Why a name the actor may not place in a room (one outside the gateway's `manageable`) is
 *  greyed out: the apps beyond reach when there are any, else the general rule. */
export function memberNote(apps: readonly string[], grantable: readonly string[],
                           catalogIds: readonly string[]): string {
  const beyond = outOfReach(apps, grantable, catalogIds)
  if (!beyond.length) return MANAGE_NOTE
  return `Holds apps you cannot grant (${beyond.join(', ')}): only a super-admin can place this user in a workspace.`
}
