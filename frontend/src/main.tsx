import React, { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type User = { id: number; name: string; enabled: number; note: string };
type Profile = {
  id: number;
  user_name: string;
  provider: string;
  transport: string;
  room_id: string;
  status: string;
  last_error: string;
  uri: string;
  subscription_url: string;
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

function App() {
  const [token, setToken] = useState(storedToken);
  const [draftToken, setDraftToken] = useState(storedToken);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [logs, setLogs] = useState("");
  const [selectedProfile, setSelectedProfile] = useState<number | null>(null);
  const [jitsi, setJitsi] = useState<JitsiProbe[]>([]);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    user_name: "",
    provider: "jitsi",
    transport: "",
    jitsi_server: "https://meet.handyweb.org",
    room_id: "",
    auth_token: "",
    auto_wbstream_room: true,
    start_now: true,
  });

  const headers = useMemo(() => ({ "Content-Type": "application/json", Authorization: `Bearer ${token}` }), [token]);

  async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(apiPath(path), { ...init, headers: { ...headers, ...(init.headers || {}) } });
    if (!response.ok) throw new Error((await response.text()) || response.statusText);
    const contentType = response.headers.get("content-type") || "";
    return contentType.includes("application/json") ? ((await response.json()) as T) : ((await response.text()) as T);
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
          <h1>olcrtc panel</h1>
          <label>
            Admin token
            <input value={draftToken} onChange={(event) => setDraftToken(event.target.value)} autoFocus />
          </label>
          <button type="submit">Войти</button>
        </form>
      </main>
    );
  }

  return (
    <main>
      <header className="topbar">
        <div>
          <h1>olcrtc panel</h1>
          <span>v{status?.version || "0.1.0"} · активных профилей: {status?.running || 0}</span>
        </div>
        <div className="topActions">
          <button onClick={refresh} disabled={busy}>Обновить</button>
          <button onClick={() => { localStorage.removeItem("olcrtc-panel-token"); setToken(""); }}>Выйти</button>
        </div>
      </header>

      {error && <pre className="error">{error}</pre>}

      <section className="band">
        <div className="sectionTitle">
          <h2>Мастер профиля</h2>
          <button onClick={discoverJitsi} disabled={busy}>Проверить Jitsi</button>
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
              <label>WB token<input value={form.auth_token} onChange={(event) => setForm({ ...form, auth_token: event.target.value })} /></label>
              <label className="checkbox"><input type="checkbox" checked={form.auto_wbstream_room} onChange={(event) => setForm({ ...form, auto_wbstream_room: event.target.checked })} />Автосоздание room</label>
            </>
          )}
          <label className="checkbox"><input type="checkbox" checked={form.start_now} onChange={(event) => setForm({ ...form, start_now: event.target.checked })} />Запустить сразу</label>
          <button type="submit" disabled={busy}>Создать</button>
        </form>
        {jitsi.length > 0 && (
          <div className="probeGrid">
            {jitsi.map((item) => (
              <button className={item.ok ? "probe ok" : "probe"} key={item.url} onClick={() => setForm({ ...form, jitsi_server: item.url })}>
                <b>{item.url}</b><span>{item.status} · {item.latency_ms} ms</span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="band">
        <h2>Профили</h2>
        <div className="tableWrap">
          <table>
            <thead><tr><th>ID</th><th>Пользователь</th><th>Provider</th><th>Transport</th><th>Status</th><th>Room</th><th>Действия</th></tr></thead>
            <tbody>
              {(status?.profiles || []).map((profile) => (
                <tr key={profile.id}>
                  <td>{profile.id}</td><td>{profile.user_name}</td><td>{profile.provider}</td><td>{profile.transport}</td>
                  <td><span className={`pill ${profile.status}`}>{profile.status}</span></td>
                  <td className="mono">{profile.room_id}</td>
                  <td className="actions">
                    <button onClick={() => action(profile.id, "start")}>Start</button>
                    <button onClick={() => action(profile.id, "stop")}>Stop</button>
                    <button onClick={() => action(profile.id, "rotate-key")}>Rotate</button>
                    <button onClick={() => navigator.clipboard.writeText(profile.uri)}>URI</button>
                    <button onClick={() => navigator.clipboard.writeText(profile.subscription_url)}>Sub</button>
                    <button onClick={() => loadLogs(profile.id)}>Logs</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="band twoCols">
        <div>
          <h2>Пользователи</h2>
          <div className="tableWrap">
            <table>
              <thead><tr><th>ID</th><th>Имя</th><th>Status</th><th>Заметка</th></tr></thead>
              <tbody>{(status?.users || []).map((user) => <tr key={user.id}><td>{user.id}</td><td>{user.name}</td><td>{user.enabled ? "enabled" : "disabled"}</td><td>{user.note}</td></tr>)}</tbody>
            </table>
          </div>
        </div>
        <div><h2>Логи {selectedProfile ? `#${selectedProfile}` : ""}</h2><pre className="logs">{logs}</pre></div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
