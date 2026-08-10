// Client for the gateway's platform endpoints (broker/GPU). Shared by every app's
// top-bar model widget so the whole suite shows one truth about the GPU.

import type { AdminUser, AppEntry, InstalledModel, Me, ModelPoolEntry, PlatformStatus, RailSchedules, RailsSettings, Recurrence, ThemeState } from './types'

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
  status: () => req<PlatformStatus>('/api/platform/status'),
  models: () => req<{ models: InstalledModel[] }>('/api/platform/models'),
  load: (model: string) =>
    req('/api/platform/load', { method: 'POST', body: JSON.stringify({ model }) }),
  unload: (model: string) =>
    req('/api/platform/unload', { method: 'POST', body: JSON.stringify({ model }) }),
  cancel: (seq: number) =>
    req('/api/platform/cancel', { method: 'POST', body: JSON.stringify({ seq }) }),

  // admin: user + entitlement management
  adminUsers: () => req<{ users: AdminUser[]; catalog: AppEntry[]; grantable: string[] }>('/api/platform/admin/users'),
  adminCreate: (payload: { username: string; password: string; is_admin: boolean; is_superadmin?: boolean; apps: string[] }) =>
    req<AdminUser>('/api/platform/admin/users', { method: 'POST', body: JSON.stringify(payload) }),
  adminUpdate: (id: number, payload: { password?: string; is_admin?: boolean; is_superadmin?: boolean; apps?: string[] }) =>
    req<AdminUser>(`/api/platform/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  adminDelete: (id: number) =>
    req<{ ok: boolean }>(`/api/platform/admin/users/${id}`, { method: 'DELETE' }),

  // admin: per-rail model settings (the 'Rails' tab)
  adminRails: () => req<RailsSettings>('/api/platform/admin/rails'),
  adminSetRailModel: (role: string, model: string) =>
    req<RailsSettings>(`/api/platform/admin/rails/${role}`, { method: 'PUT', body: JSON.stringify({ model }) }),

  // admin: workstation model pool (the 'Models' tab)
  adminModels: () => req<{ models: ModelPoolEntry[] }>('/api/platform/admin/models'),
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
