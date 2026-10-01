# Shopping list → "Send to Phone" (Google Tasks, per-user)

The shopping list has an optional **📲 Send to Phone** button that pushes the still-unchecked
items into **Google Tasks** — a checkable list that surfaces in the Google Tasks app, the
Google Calendar side-panel, and Gmail on your phone. Tick items off as you shop.

> **Why Tasks, not Keep?** Google Keep has *no* public API for personal (@gmail) accounts — the
> only official Keep API is a Google Workspace *Enterprise* governance tool. Google **Tasks**
> has a real, stable OAuth2 API and (bonus) checkable items, which fit a grocery list better.

**Per-user.** Each person connects **their own** Google account from inside the app, so their
list lands on *their own* phone. admin, alice, and bob each click **Connect Google
Tasks** once and link their own account — no shared inbox, no CLI.

## One-time setup (admin, ~10 min)

You register a single OAuth **app** once; after that, every household member self-connects.

### 1. Enable the Tasks API + configure consent

1. <https://console.cloud.google.com/> → create a project (or reuse one).
2. **APIs & Services → Library →** search **"Google Tasks API" → Enable**.
3. **APIs & Services → OAuth consent screen:**
   - User type **External**; fill in app name/support email.
   - **Publishing status → "Publish app" → In production.**
     ⚠ **This matters.** In *Testing* mode Google **expires refresh tokens after 7 days** and
     everyone's Send button silently dies. "In production" (even unverified, for personal use)
     issues long-lived tokens. Each user will see a one-time "Google hasn't verified this app"
     screen at consent — expected for a private app; they click **Advanced → Go to…**.

### 2. Create a **Web** OAuth client with the redirect URI

**APIs & Services → Credentials → Create credentials → OAuth client ID →** application type
**Web application**. Under **Authorized redirect URIs**, add the gateway-fronted callback:

```
https://platform.example.com/recipe-book/api/gtasks/callback
```

(Use your platform's public host. It **must** be reachable from your users' phones/browsers, so
the public Cloudflare URL — not localhost — is what lets alice/bob connect from their own
devices.) Copy the **Client ID** and **Client secret**.

### 3. Put the app creds in `deploy/.env` and rebuild

```ini
RECIPE_BOOK_GTASKS_CLIENT_ID=...apps.googleusercontent.com
RECIPE_BOOK_GTASKS_CLIENT_SECRET=...
RECIPE_BOOK_GTASKS_REDIRECT_URI=https://platform.example.com/recipe-book/api/gtasks/callback
# optional — the Tasks list items land in (defaults to "Shopping List"):
# RECIPE_BOOK_GTASKS_LIST_TITLE=Groceries
```

Then rebuild just the rail (Claude runs deploys):

```powershell
$env:Path = "$env:ProgramFiles\Docker\Docker\resources\bin;$env:Path"
cd deploy; docker compose up -d --build --no-deps recipe-book
```

## Using it (each user, once)

1. Open the recipe book → **Shopping list**. A **🔗 Connect Google Tasks** button appears.
2. Click it → a Google popup opens → approve (click through the unverified-app notice) → the
   popup closes and the button becomes **📲 Send to Phone**, showing which account you linked.
3. Click **Send to Phone** to push the unchecked items. **Disconnect** any time from the same
   row.

## Behaviour & notes

- **Sends the unchecked items** (what you still need to buy). Pantry/bar-covered items are
  already dropped upstream; checked ("got it") items are skipped.
- **Per-owner tokens** live in the recipe-book DB (`gtasks_tokens`, keyed by owner). The shared
  app id/secret live only in `deploy/.env` (gitignored). CSRF-protected with a one-time `state`
  nonce bound to the connecting user.
- **Expired/revoked connection:** if a user revokes access at
  <https://myaccount.google.com/permissions>, the next send returns a "please reconnect"
  message and the stored token is dropped so the Connect button returns.
- **Idempotency:** each send *appends* tasks — it doesn't de-dupe against what's already in the
  Google list. Send once per trip, or clear the Tasks list first.
