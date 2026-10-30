// REST access to the brain. Every call carries the current role token, which is
// the only access control in the system (§7.9) and gates evidence alone.
import { BRAIN_URL, type Role } from "./constants";
import type {
  Alert, AuditRow, Cam, EvidenceSummary, Line, Rule, Stats, Zone,
} from "./types";

const ROLE_KEY = "ghost.role";
const TOKEN_KEY = "ghost.token";

export interface Identity {
  role: Role | null;
  token: string | null;
}

export function getIdentity(): Identity {
  if (typeof window === "undefined") return { role: null, token: null };
  try {
    return {
      role: (window.localStorage.getItem(ROLE_KEY) as Role | null) ?? null,
      token: window.localStorage.getItem(TOKEN_KEY),
    };
  } catch {
    // Private browsing can refuse storage entirely; the app still works
    // unauthenticated, it just cannot approve evidence.
    return { role: null, token: null };
  }
}

export function setIdentity(role: Role, token: string): void {
  try {
    window.localStorage.setItem(ROLE_KEY, role);
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* ignore: see getIdentity */
  }
  notifyIdentity();
}

export function clearIdentity(): void {
  try {
    window.localStorage.removeItem(ROLE_KEY);
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
  notifyIdentity();
}

// --- identity as an external store -------------------------------------
// localStorage only exists on the client, so components read it through
// useSyncExternalStore: the server renders null, the client swaps in the real
// role after hydration, and nothing has to setState inside an effect.
const identityListeners = new Set<() => void>();
let roleSnapshot: Role | null | undefined;

function notifyIdentity(): void {
  roleSnapshot = undefined;
  for (const listener of identityListeners) listener();
}

export function subscribeIdentity(listener: () => void): () => void {
  identityListeners.add(listener);
  if (typeof window !== "undefined") window.addEventListener("storage", listener);
  return () => {
    identityListeners.delete(listener);
    if (typeof window !== "undefined") window.removeEventListener("storage", listener);
  };
}

/** Cached so repeated renders get a stable value, as the store contract requires. */
export function getRoleSnapshot(): Role | null {
  if (roleSnapshot === undefined) roleSnapshot = getIdentity().role;
  return roleSnapshot;
}

export function getServerRoleSnapshot(): Role | null {
  return null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { token } = getIdentity();
  const headers = new Headers(init.headers);
  if (token) headers.set("X-Role-Token", token);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${BRAIN_URL}/api${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!res.ok) {
    let detail: unknown;
    let message = `${res.status} ${res.statusText}`;
    try {
      detail = await res.json();
      const d = (detail as { detail?: unknown })?.detail;
      if (typeof d === "string") message = d;
      else if (d) message = JSON.stringify(d);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});

export const api = {
  health: () => request<Record<string, unknown>>("/health"),
  stats: () => request<Stats>("/stats"),

  cams: () => request<Cam[]>("/cams"),
  updateCam: (id: string, body: Partial<Cam>) =>
    request<Cam>(`/cams/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  snapshotUrl: (id: string) => `${BRAIN_URL}/api/cams/${id}/snapshot`,
  requestSnapshot: (id: string) =>
    fetch(`${BRAIN_URL}/api/cams/${id}/snapshot`, { method: "POST" }),
  setHomography: (id: string, cam_pts: number[][], floor_pts: number[][]) =>
    request<{ calibrated: boolean }>(`/cams/${id}/homography`, json({ cam_pts, floor_pts })),
  clearHomography: (id: string) =>
    request<{ calibrated: boolean }>(`/cams/${id}/homography`, { method: "DELETE" }),

  zones: () => request<Zone[]>("/zones"),
  createZone: (body: Partial<Zone>) => request<Zone>("/zones", json(body)),
  updateZone: (id: string, body: Partial<Zone>) =>
    request<Zone>(`/zones/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteZone: (id: string) => request<unknown>(`/zones/${id}`, { method: "DELETE" }),

  lines: () => request<Line[]>("/lines"),
  createLine: (body: Partial<Line>) => request<Line>("/lines", json(body)),
  updateLine: (id: string, body: Partial<Line>) =>
    request<Line>(`/lines/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteLine: (id: string) => request<unknown>(`/lines/${id}`, { method: "DELETE" }),

  rules: () => request<Rule[]>("/rules"),
  ruleTypes: () =>
    request<{
      types: string[];
      implemented: string[];
      defaults: Record<string, Record<string, number>>;
      severities: string[];
    }>("/rules/types"),
  createRule: (body: Partial<Rule>) => request<Rule>("/rules", json(body)),
  updateRule: (id: string, body: Partial<Rule>) =>
    request<Rule>(`/rules/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteRule: (id: string) => request<unknown>(`/rules/${id}`, { method: "DELETE" }),
  parseNlRule: (text: string) => request<{ rule: Partial<Rule> }>("/rules/nl", json({ text })),

  alerts: (q: {
    status?: string; type?: string; cam?: string; since?: number; limit?: number;
  } = {}) => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) {
      if (v !== undefined && v !== "" && v !== null) params.set(k, String(v));
    }
    const qs = params.toString();
    return request<Alert[]>(`/alerts${qs ? `?${qs}` : ""}`);
  },
  alert: (id: string) => request<Alert>(`/alerts/${id}`),
  ackAlert: (id: string) => request<Alert>(`/alerts/${id}/ack`, { method: "POST" }),
  resolveAlert: (id: string) => request<Alert>(`/alerts/${id}/resolve`, { method: "POST" }),
  alertSnapshotUrl: (id: string) => `${BRAIN_URL}/api/alerts/${id}/snapshot`,

  evidence: (id: string) => request<EvidenceSummary>(`/evidence/${id}`),
  approveEvidence: (id: string) =>
    request<EvidenceSummary>(`/evidence/${id}/approve`, { method: "POST" }),
  flagEvidence: (id: string) =>
    request<EvidenceSummary>(`/evidence/${id}/flag`, { method: "POST" }),
  clipUrl: (id: string) => `${BRAIN_URL}/api/evidence/${id}/clip`,
  fetchClip: (id: string) => {
    const { token } = getIdentity();
    return fetch(`${BRAIN_URL}/api/evidence/${id}/clip`, {
      headers: token ? { "X-Role-Token": token } : undefined,
    });
  },

  audit: (q: { limit?: number; target?: string } = {}) => {
    const params = new URLSearchParams();
    if (q.limit) params.set("limit", String(q.limit));
    if (q.target) params.set("target", q.target);
    const qs = params.toString();
    return request<AuditRow[]>(`/audit${qs ? `?${qs}` : ""}`);
  },

  whoami: (token: string) =>
    fetch(`${BRAIN_URL}/api/auth/whoami`, {
      method: "POST",
      headers: { "X-Role-Token": token },
    }).then(async (res) => {
      if (!res.ok) throw new ApiError(res.status, "that token was not recognised");
      return (await res.json()) as { role: Role };
    }),

  learning: () => request<Record<string, unknown>>("/learning"),
  startLearning: (cam: string) =>
    request<Record<string, unknown>>(`/learning/${cam}/start`, { method: "POST" }),
  stopLearning: (cam: string) =>
    request<Record<string, unknown>>(`/learning/${cam}/stop`, { method: "POST" }),
};
