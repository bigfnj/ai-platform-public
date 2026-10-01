// Client for the gateway's platform endpoints (broker/GPU). Shared by every app's
// top-bar model widget so the whole suite shows one truth about the GPU.

import type { AdminUser, AppEntry, BrokerToken, Me, ModelCategory, ModelPoolEntry, PlatformStatus, RailManagerEntry, RailSchedules, RailsSettings, Recurrence, ThemeState, Workspace } from './types'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { 'content-type': 'application/json' }, ...init })
  if (!res.ok) {
    // Read the body ONCE (res.json() consumes the stream; a second read —
    // e.g. res.text() in a catch — throws "body stream already read" and masks
    // the real error). Take text, then try to parse it as JSON for {detail}.
    const raw = await res.text().catch(() => '')
    let detail = raw
    try {
      const body = JSON.parse(raw)
      detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body)
    } catch {
      /* body wasn't JSON (e.g. a plain-text 500 / proxy HTML) — keep raw text */
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  const text = await res.text()
  return (text ? JSON.parse(text) : {}) as T
}

export const platformApi = {
  // auth + per-user rail
  me: () => req<Me>('/api/platform/me'),
  apps: () => req<{ apps: AppEntry[]; user: Me }>('/api/platform/apps'),
  login: (username: string, password: string) =>
    req<Me>('/api/platform/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => req<{ ok: boolean }>('/api/platform/logout', { method: 'POST' }),

  // theming: personal override (any user) + platform default (admin)
  setTheme: (body: { palette?: string; mode?: string; clear?: boolean }) =>
    req<ThemeState>('/api/platform/theme', { method: 'PUT', body: JSON.stringify(body) }),
  setDefaultTheme: (body: { palette: string; mode: string }) =>
    req<ThemeState>('/api/platform/admin/theme', { method: 'PUT', body: JSON.stringify(body) }),

  // broker / GPU
  //
  // No `models` / `load` wrappers here: both had zero callers, so they were a shape nobody
  // depended on. What that leaves behind is NOT symmetric, and the difference is why neither
  // gateway route was deleted with them:
  //   GET  /api/platform/models  still has a caller -- deploy/installer/smoke-test.ps1 probes
  //                              it end to end to prove the container -> native-broker hop.
  //   POST /api/platform/load    now has no caller anywhere in the repo.
  // Removing either route is a separate decision, recorded here rather than taken.
  status: () => req<PlatformStatus>('/api/platform/status'),
  unload: (model: string) =>
    req('/api/platform/unload', { method: 'POST', body: JSON.stringify({ model }) }),
  cancel: (seq: number) =>
    req('/api/platform/cancel', { method: 'POST', body: JSON.stringify({ seq }) }),

  // Voice. Both are ungated on the broker (CPU/ONNX, no eviction), so a rail may call them
  // mid-conversation without displacing the model the user is talking to.
  ttsLight: (body: { text: string; voice?: string; lang_code?: string; speed?: number }) =>
    req<{ audio_b64: string; sample_rate: number; voice: string; lang: string }>(
      '/api/platform/tts_light', { method: 'POST', body: JSON.stringify(body) }),
  transcribe: (body: { audio_b64: string; suffix?: string; language?: string }) =>
    req<{ text: string; language: string; duration: number; model: string }>(
      '/api/platform/transcribe', { method: 'POST', body: JSON.stringify(body) }),

  // admin: user + entitlement management
  adminUsers: () => req<{ users: AdminUser[]; catalog: AppEntry[]; grantable: string[] }>('/api/platform/admin/users'),
  adminCreate: (payload: { username: string; password: string; is_admin: boolean; is_superadmin?: boolean; apps: string[] }) =>
    req<AdminUser>('/api/platform/admin/users', { method: 'POST', body: JSON.stringify(payload) }),
  adminUpdate: (id: number, payload: { password?: string; is_admin?: boolean; is_superadmin?: boolean; apps?: string[] }) =>
    req<AdminUser>(`/api/platform/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  adminDelete: (id: number) =>
    req<{ ok: boolean }>(`/api/platform/admin/users/${id}`, { method: 'DELETE' }),

  // admin: shared workspaces (the 'Workspaces' tab)
  adminWorkspaces: () =>
    req<{ workspaces: Workspace[]; manageable: string[] }>('/api/platform/admin/workspaces'),
  adminWorkspaceCreate: (payload: { name: string; members: string[] }) =>
    req<Workspace>('/api/platform/admin/workspaces', { method: 'POST', body: JSON.stringify(payload) }),
  adminWorkspaceUpdate: (id: number, payload: { name?: string; members?: string[] }) =>
    req<Workspace>(`/api/platform/admin/workspaces/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  adminWorkspaceDelete: (id: number) =>
    req<{ ok: boolean }>(`/api/platform/admin/workspaces/${id}`, { method: 'DELETE' }),

  // admin: named broker access tokens (the 'Broker' tab). Super-admin only.
  adminBrokerTokens: () =>
    req<{ tokens: BrokerToken[]; scopes: string[]; shared_token_in_use: boolean }>(
      '/api/platform/admin/broker/tokens'),
  // The ONLY response that carries the plaintext. Do not log it, do not re-fetch it: there is
  // no second copy anywhere.
  adminBrokerTokenCreate: (payload: { label: string; scope: string }) =>
    req<BrokerToken & { token: string }>('/api/platform/admin/broker/tokens',
      { method: 'POST', body: JSON.stringify(payload) }),
  adminBrokerTokenRevoke: (id: string) =>
    req<{ revoked: string }>(`/api/platform/admin/broker/tokens/${id}`, { method: 'DELETE' }),

  // admin: per-rail model settings (the 'Rails' tab)
  adminRails: () => req<RailsSettings>('/api/platform/admin/rails'),
  /** `upstream` defaults to 'local'; naming a registered remote broker delegates the role to
   *  it. The gateway composes the stored `upstream::model` pattern and 400s on an unregistered
   *  name, so no caller has to know that syntax. */
  adminSetRailModel: (role: string, model: string, upstream = 'local') =>
    req<RailsSettings>(`/api/platform/admin/rails/${role}`,
      { method: 'PUT', body: JSON.stringify({ model, upstream }) }),

  // admin: workstation model pool (the 'Models' tab)
  adminRailManager: () => req<{ rails: RailManagerEntry[] }>('/api/platform/admin/rail-manager'),
  adminRailToggle: (railId: string, enabled: boolean) =>
    req<{ rail_id: string; state: string }>('/api/platform/admin/rail-manager/toggle',
      { method: 'POST', body: JSON.stringify({ rail_id: railId, enabled }) }),
  adminModels: () => req<{ models: ModelPoolEntry[]; categories: ModelCategory[] }>('/api/platform/admin/models'),
  adminModelToggle: (name: string, enabled: boolean) =>
    req<{ name: string; enabled: boolean }>('/api/platform/admin/models/toggle',
      { method: 'POST', body: JSON.stringify({ name, enabled }) }),
  adminModelDelete: (model: string) =>
    req<{ deleted: string }>('/api/platform/admin/models/delete',
      { method: 'POST', body: JSON.stringify({ model }) }),

  // admin: central scheduler (the 'Schedule' tab)
  adminSchedules: () => req<{ rails: RailSchedules[] }>('/api/platform/admin/schedules'),
  adminSetSchedule: (rail: string, taskId: string, body: { recurrence: Recurrence; enabled: boolean }) =>
    req<{ rails: RailSchedules[] }>(`/api/platform/admin/schedules/${rail}/${taskId}`,
      { method: 'PUT', body: JSON.stringify(body) }),
  adminRunSchedule: (rail: string, taskId: string) =>
    req<{ rail: string; task_id: string; status: string }>(
      `/api/platform/admin/schedules/${rail}/${taskId}/run`, { method: 'POST' }),
}
