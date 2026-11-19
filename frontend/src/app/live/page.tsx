"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

type LiveSession = {
  id: string; platform: string; url: string; status: string;
  task_id: string | null; error_message: string | null;
  task_ids: string[];
  chat_available: boolean; stop_requested: boolean;
};
type YouTubeAccount = { id: string; display_name: string | null; username: string | null; provider: string; status: string };

export default function LivePage() {
  const [url, setUrl] = useState("");
  const [sessions, setSessions] = useState<LiveSession[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [accounts, setAccounts] = useState<YouTubeAccount[]>([]);
  const [accountId, setAccountId] = useState("");

  useEffect(() => {
    let mounted = true;
    const refresh = async () => {
      try {
        const response = await fetch("/api/live-sessions", { cache: "no-store" });
        if (response.ok && mounted) setSessions(await response.json());
      } catch { /* Keep the last known status during a temporary network outage. */ }
    };
    void refresh();
    void fetch("/api/social/connections", { cache: "no-store" }).then(async response => {
      if (!response.ok || !mounted) return;
      const data = await response.json();
      const connected = (data.connections || []).filter((item: YouTubeAccount) => item.provider === "youtube" && item.status === "active");
      setAccounts(connected);
      setAccountId(current => current || connected[0]?.id || "");
    }).catch(() => {});
    const timer = setInterval(() => void refresh(), 10000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/live-sessions", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url, youtube_account_id: accountId }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || result.error || "Could not start recording");
      setSessions(current => [result, ...current.filter(item => item.id !== result.id)]);
      setUrl("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not start recording"); }
    finally { setBusy(false); }
  }

  async function stop(id: string) {
    const response = await fetch(`/api/live-sessions/${encodeURIComponent(id)}`, { method: "POST" });
    if (!response.ok) { setError("Could not stop recording"); return; }
    const result = await response.json();
    setSessions(current => current.map(item => item.id === id ? result : item));
  }

  return <main className="mx-auto max-w-2xl px-4 py-12">
    <Link href="/" className="text-sm underline">← Back to SupoClip</Link>
    <h1 className="mt-8 text-3xl font-semibold">Clip a live stream</h1>
    <p className="mt-3 text-sm text-muted-foreground">Paste a live Twitch channel, YouTube live video, or Kick channel. SupoClip selects and renders highlights while the broadcast continues, then uploads each ready clip as a public YouTube Short.</p>
    <form onSubmit={submit} className="mt-8 flex flex-col gap-3">
      <input type="url" required value={url} onChange={event => setUrl(event.target.value)} placeholder="https://www.twitch.tv/channel" aria-label="Live-stream URL" className="min-w-0 flex-1 rounded-lg border bg-background px-4 py-3" />
      {accounts.length ? <label className="text-sm">Publish public Shorts to
        <select value={accountId} onChange={event => setAccountId(event.target.value)} className="mt-2 block w-full rounded-lg border bg-background px-4 py-3">
          {accounts.map(account => <option key={account.id} value={account.id}>{account.display_name || account.username || "YouTube account"}</option>)}
        </select>
      </label> : <p className="text-sm text-muted-foreground">Connect a YouTube account in <Link href="/settings/social" className="underline">Social settings</Link> to enable public auto-upload.</p>}
      <button disabled={busy || !accountId} className="rounded-lg bg-foreground px-5 py-3 text-background disabled:opacity-50">{busy ? "Starting…" : "Start live clipping and public upload"}</button>
    </form>
    {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
    <h2 className="mt-12 text-xl font-semibold">Your streams</h2>
    {sessions.length === 0 ? <p className="mt-4 text-sm text-muted-foreground">No live streams submitted yet.</p> :
      <ul className="mt-4 space-y-3">{sessions.map(item => <li key={item.id} className="rounded-xl border p-4">
        <p className="truncate text-sm font-medium">{item.url}</p>
        <p className="mt-1 text-sm capitalize text-muted-foreground">{item.status}{item.stop_requested ? " · stopping" : ""}</p>
        {item.error_message && <p role="alert" className="mt-2 text-sm text-destructive">{item.error_message}</p>}
        {(item.task_ids || (item.task_id ? [item.task_id] : [])).map((taskId, index) =>
          <Link key={taskId} href={`/tasks/${taskId}`} className="mt-3 mr-4 inline-block text-sm underline">View highlight {index + 1}</Link>)}
        {(item.status === "queued" || item.status === "recording") && !item.stop_requested &&
          <button onClick={() => void stop(item.id)} className="mt-3 block text-sm underline">Stop recording</button>}
      </li>)}</ul>}
  </main>;
}
