// The Users tab's mirror of the gateway's reach rule. Pure, so node can test it
// (apps/platform/frontend/tests/admin-rails.test.js).
//
// main.py _out_of_reach: a plain admin gets 403 on a password reset, a demotion or a deletion of a
// user who holds an app the admin could not grant. A reset hands the admin that user's login, and
// with it every app they hold, so the tab offering "reset pw" on every non-super-admin row invited
// a plain admin to take apps they were never given. The gateway stays the authority; the tab
// says so up front, from the two lists the users route already sends (`apps`, `grantable`).

/** The apps on a row the actor could not grant, sorted; empty means the row is within reach.
 *  Only catalog ids count, as in the gateway: a row for a rail since removed grants nothing. A
 *  super-admin's `grantable` is the whole catalog, so nothing is ever beyond theirs. */
export function outOfReach(apps: readonly string[], grantable: readonly string[],
                           catalogIds: readonly string[]): string[] {
  const can = new Set(grantable)
  const known = new Set(catalogIds)
  return apps.filter((a) => known.has(a) && !can.has(a)).sort()
}

/** What a greyed-out reset, demote or delete says, or null when the row is within reach. */
export function reachNote(beyond: readonly string[]): string | null {
  if (!beyond.length) return null
  return `Holds apps you cannot grant (${beyond.join(', ')}): only a super-admin can reset, demote or delete this user.`
}
