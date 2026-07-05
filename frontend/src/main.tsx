import { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Copy, FileText, Link2, LogOut, Play, Plus, RefreshCw, RotateCw, ScrollText, Search, Server, Square, Trash2 } from "lucide-react";
import "./styles.css";

type User = { id: number; name: string; enabled: number; note: string };
type Profile = {
  id: number;
  user_id: number;
  user_name: string;
  name: string;
  provider: string;
  transport: string;
  room_id: string;
  status: string;
  last_error: string;
  uri: string;
  subscription_url: string;
  profile_subscription_url: string;
};
type Status = { version: string; profiles: Profile[]; users: User[]; running: number };
type JitsiProbe = { url: string; ok: boolean; latency_ms: number; status: string; requires_registration: boolean };

const storedToken = localStorage.getItem("olcrtc-panel-token") || "";

function apiPath(path: string): string {
  const clean = path.replace(/^\/+/, "");
  const base = window.location.pathname.endsWith("/")
    ? window.location.pathname
    : window.location.pathname.slice(0, window.location.pathname.lastIndexOf("/") + 1);
  return `${base}${clean}`;
}

function shortRoom(room: string): string {
  if (room.length <= 48) return room;
  return `${room.slice(0, 30)}...${room.slice(-14)}`;
}

function responseError(text: string, fallback: string): string {
  if (!text) return fallback;
  try {
    const data = JSON.parse(text) as { detail?: unknown };
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && typeof data.detail === "object" && "message" in data.detail) {
      const detail = data.detail as { message?: unknown; attempts?: unknown };
      const message = typeof detail.message === "string" ? detail.message : fallback;
      const attempts = Array.isArray(detail.attempts) ? ` Попыток: ${detail.attempts.length}.` : "";
      return `${message}${attempts}`;
    }
  } catch {
    return text;
  }
  return text;
}

function confirmTyped(message: string, code: string): boolean {
  return window.prompt(`${message}\nВведите ${code} для подтверждения:`) === code;
}

function App() {
  const [token, setToken] = useState(storedToken);
  const [draftToken, setDraftToken] = useState(storedToken);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [logs, setLogs] = useState("");
  const [copied, setCopied] = useState("");
  const [selectedProfile, setSelectedProfile] = useState<number | null>(null);
  const [jitsi, setJitsi] = useState<JitsiProbe[]>([]);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    user_name: "",
    provider: "jitsi",
    transport: "",
    jitsi_server: "https://fairmeeting.net",
    room_id: "",
    auth_token: "",
    auto_wbstream_room: true,
    start_now: true,
  });

  const profiles = status?.profiles || [];
  const users = status?.users || [];
  const profilesByUser = useMemo(() => {
    const grouped = new Map<number, Profile[]>();
    for (const profile of profiles) {
      const list = grouped.get(profile.user_id) || [];
      list.push(profile);
      grouped.set(profile.user_id, list);
    }
    return grouped;
  }, [profiles]);
  const headers = useMemo(() => ({ "Content-Type": "application/json", Authorization: `Bearer ${token}` }), [token]);

  async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(apiPath(path), { ...init, headers: { ...headers, ...(init.headers || {}) } });
    if (!response.ok) throw new Error(responseError(await response.text(), response.statusText));
    const contentType = response.headers.get("content-type") || "";
    return contentType.includes("application/json") ? ((await response.json()) as T) : ((await response.text()) as T);
  }

  async function copyText(label: string, text: string) {
    if (!text) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
      }
      setCopied(label);
      window.setTimeout(() => setCopied(""), 1800);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function refresh() {
    if (!token) return;
    try {
      setError("");
      setStatus(await api<Status>("/api/status"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [token]);

  async function login(event: FormEvent) {
    event.preventDefault();
    localStorage.setItem("olcrtc-panel-token", draftToken);
    setToken(draftToken);
  }

  async function createProfile(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api<Profile>("/api/profiles", { method: "POST", body: JSON.stringify({ ...form, transport: form.transport || undefined }) });
      setForm({ ...form, user_name: "", room_id: "" });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function action(profileId: number, name: "start" | "stop" | "rotate-key") {
    setBusy(true);
    try {
      await api(`/api/profiles/${profileId}/${name}`, { method: "POST" });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteProfile(profile: Profile) {
    if (!confirmTyped(`Удалить профиль #${profile.id} (${profile.name || profile.provider})? Пользователь останется.`, `DELETE-PROFILE-${profile.id}`)) return;
    setBusy(true);
    try {
      await api(`/api/profiles/${profile.id}`, { method: "DELETE" });
      if (selectedProfile === profile.id) {
        setSelectedProfile(null);
        setLogs("");
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteUser(user: User) {
    const count = profilesByUser.get(user.id)?.length || 0;
    if (!confirmTyped(`Удалить пользователя #${user.id} (${user.name}) и все его профили: ${count}?`, `DELETE-USER-${user.id}`)) return;
    setBusy(true);
    try {
      await api(`/api/users/${user.id}`, { method: "DELETE" });
      setSelectedProfile(null);
      setLogs("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function loadLogs(profileId: number) {
    setSelectedProfile(profileId);
    setLogs(await api<string>(`/api/profiles/${profileId}/logs`));
  }

  async function discoverJitsi() {
    setBusy(true);
    try {
      const result = await api<JitsiProbe[]>("/api/jitsi/discover", { method: "POST", body: JSON.stringify({ candidates: [] }) });
      setJitsi(result);
      const first = result.find((item) => item.ok);
      if (first) setForm({ ...form, jitsi_server: first.url });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <main className="login">
        <form onSubmit={login} className="loginBox">
          <div className="loginMark">ol</div>
          <h1>olcrtc panel</h1>
          <label>
            Admin token
            <input value={draftToken} onChange={(event) => setDraftToken(event.target.value)} autoFocus />
          </label>
          <button className="primaryButton" type="submit">Войти</button>
        </form>
      </main>
    );
  }

  return (
    <main className="appShell">
      <header className="topbar">
        <div>
          <p className="eyebrow">olcrtc panel</p>
          <h1>Панель управления</h1>
          <span>v{status?.version || "0.1.0"}</span>
        </div>
        <div className="topActions">
          <button onClick={refresh} disabled={busy}><RefreshCw size={16} />Обновить</button>
          <button onClick={() => { localStorage.removeItem("olcrtc-panel-token"); setToken(""); }}><LogOut size={16} />Выйти</button>
        </div>
      </header>

      <section className="metrics" aria-label="Сводка">
        <div className="metric"><span>Активные</span><strong>{status?.running || 0}</strong></div>
        <div className="metric"><span>Профили</span><strong>{profiles.length}</strong></div>
        <div className="metric"><span>Пользователи</span><strong>{users.length}</strong></div>
        <div className="metric"><span>Jitsi серверы</span><strong>{jitsi.length || "..."}</strong></div>
      </section>

      {error && <pre className="error">{error}</pre>}
      {copied && <div className="toast">{copied}</div>}

      <section className="panel">
        <div className="sectionTitle">
          <div>
            <h2>Мастер профиля</h2>
            <span>Jitsi, WBStream, transport и запуск</span>
          </div>
          <button onClick={discoverJitsi} disabled={busy}><Search size={16} />Проверить Jitsi</button>
        </div>
        <form className="gridForm" onSubmit={createProfile}>
          <label>Пользователь<input value={form.user_name} onChange={(event) => setForm({ ...form, user_name: event.target.value })} /></label>
          <label>
            Provider
            <select value={form.provider} onChange={(event) => setForm({ ...form, provider: event.target.value })}>
              <option value="jitsi">Jitsi</option>
              <option value="wbstream">WBStream</option>
            </select>
          </label>
          <label>
            Transport
            <select value={form.transport} onChange={(event) => setForm({ ...form, transport: event.target.value })}>
              <option value="">Авто</option>
              <option value="datachannel">datachannel</option>
              <option value="vp8channel">vp8channel</option>
              <option value="seichannel">seichannel</option>
            </select>
          </label>
          {form.provider === "jitsi" && (
            <label>Jitsi server<input value={form.jitsi_server} onChange={(event) => setForm({ ...form, jitsi_server: event.target.value })} /></label>
          )}
          <label>Room ID<input value={form.room_id} onChange={(event) => setForm({ ...form, room_id: event.target.value })} /></label>
          {form.provider === "wbstream" && (
            <>
              <label>WB account token<input value={form.auth_token} onChange={(event) => setForm({ ...form, auth_token: event.target.value })} /></label>
              <label className="switch"><input type="checkbox" checked={form.auto_wbstream_room} onChange={(event) => setForm({ ...form, auto_wbstream_room: event.target.checked })} />Автосоздание room</label>
            </>
          )}
          <label className="switch"><input type="checkbox" checked={form.start_now} onChange={(event) => setForm({ ...form, start_now: event.target.checked })} />Запустить сразу</label>
          <button className="primaryButton" type="submit" disabled={busy}><Plus size={16} />Создать</button>
        </form>
        {jitsi.length > 0 && (
          <div className="probeGrid">
            {jitsi.map((item) => (
              <button
                className={item.ok ? "probe ok" : "probe"}
                disabled={!item.ok || busy}
                key={item.url}
                onClick={() => item.ok && setForm({ ...form, jitsi_server: item.url })}
                title={item.ok ? "Выбрать Jitsi server" : "Этот сервер не подходит для запуска"}
              >
                <span className="probeHead"><Server size={16} />{item.url}</span>
                <span>{item.status} · {item.latency_ms} ms{item.requires_registration ? " · auth" : ""}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="split">
        <div className="panel">
          <div className="sectionTitle">
            <div>
              <h2>Пользователи и профили</h2>
              <span>{users.length} пользователей · {profiles.length} профилей</span>
            </div>
          </div>
          <div className="userStack">
            {users.map((user) => {
              const userProfiles = profilesByUser.get(user.id) || [];
              return (
                <article className="userCard" key={user.id}>
                  <div className="userHead">
                    <div>
                      <h3>{user.name}</h3>
                      <p>#{user.id} · {user.enabled ? "enabled" : "disabled"}{user.note ? ` · ${user.note}` : ""}</p>
                    </div>
                    <div className="userMetaActions">
                      <span className="profileCount">{userProfiles.length}</span>
                      <button className="dangerButton" onClick={() => deleteUser(user)} disabled={busy} title="Удалить пользователя и все его профили">
                        <Trash2 size={16} />Удалить пользователя
                      </button>
                    </div>
                  </div>
                  <div className="nestedProfiles">
                    {userProfiles.map((profile) => (
                      <div className="nestedProfile" key={profile.id}>
                        <div className="nestedProfileMain">
                          <div className="cardHead compact">
                            <div>
                              <span className="profileId">#{profile.id}</span>
                              <h4>{profile.name || profile.provider}</h4>
                              <p>{profile.provider} · {profile.transport}</p>
                            </div>
                            <span className={`pill ${profile.status}`}>{profile.status}</span>
                          </div>
                          <div className="roomLine">
                            <span className="mono">{shortRoom(profile.room_id)}</span>
                            <button className="toolButton" onClick={() => copyText("Room скопирован", profile.room_id)} title="Скопировать room">
                              <Copy size={16} />Room
                            </button>
                          </div>
                          {profile.last_error && <pre className="inlineError">{profile.last_error}</pre>}
                        </div>
                        <div className="cardActions">
                          <button onClick={() => action(profile.id, "start")}><Play size={16} />Start</button>
                          <button onClick={() => action(profile.id, "stop")}><Square size={16} />Stop</button>
                          <button onClick={() => action(profile.id, "rotate-key")}><RotateCw size={16} />Rotate</button>
                          <button onClick={() => copyText("URI скопирован", profile.uri)}><Link2 size={16} />URI</button>
                          <button onClick={() => copyText("Sub скопирован", profile.profile_subscription_url || profile.subscription_url)}><FileText size={16} />Sub</button>
                          <button onClick={() => loadLogs(profile.id)}><ScrollText size={16} />Logs</button>
                          <button className="dangerButton" onClick={() => deleteProfile(profile)} disabled={busy}><Trash2 size={16} />Удалить</button>
                        </div>
                      </div>
                    ))}
                    {userProfiles.length === 0 && <div className="emptyState compactEmpty">Профилей пока нет</div>}
                  </div>
                </article>
              );
            })}
            {users.length === 0 && <div className="emptyState">Пользователей пока нет</div>}
          </div>
        </div>
        <div className="panel logsPanel">
          <div className="sectionTitle">
            <div>
              <h2>Логи {selectedProfile ? `#${selectedProfile}` : ""}</h2>
              <span>{selectedProfile ? "Последний выбранный профиль" : "Профиль не выбран"}</span>
            </div>
          </div>
          <pre className="logs">{logs}</pre>
        </div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
