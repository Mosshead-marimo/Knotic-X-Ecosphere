"use client";

import { AlertTriangle, Bot, CheckCircle2, CircleDashed, Headphones, RefreshCw, ShieldAlert, UserRound } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { consoleApi } from "./api";
import { EmptyState, PageHeader } from "./ConsoleShell";
import { useConsole } from "./ConsoleProvider";
import type { SessionDetail } from "./types";

export function LiveMonitor() {
  const { session, sessions, refreshSessions, liveStatus } = useConsole();
  const active = useMemo(
    () => sessions.filter((item) => item.status === "ACTIVE" || item.status === "CREATED"),
    [sessions],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [handoffOpen, setHandoffOpen] = useState(false);
  const [handoffState, setHandoffState] = useState<"idle" | "submitting" | "requested" | "failed">("idle");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const chosenId = selectedId ?? active[0]?.session_id ?? null;

  useEffect(() => {
    if (!chosenId) return;
    let current = true;
    void consoleApi.sessionDetail(chosenId).then((value) => {
      if (current) {
        setDetail(value);
        setDetailError(null);
      }
    }).catch((cause: unknown) => {
      if (current) setDetailError(cause instanceof Error ? cause.message : "Session detail is unavailable.");
    });
    return () => { current = false; };
  }, [chosenId, sessions]);

  const selectedDetail = detail?.session_id === chosenId ? detail : null;

  useEffect(() => {
    if (handoffOpen) dialogRef.current?.showModal();
    else dialogRef.current?.close();
  }, [handoffOpen]);

  async function requestHandoff(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !detail || handoffState === "submitting") return;
    const data = new FormData(event.currentTarget);
    setHandoffState("submitting");
    try {
      await consoleApi.handoff(
        session,
        detail.session_id,
        detail.version,
        String(data.get("reason")),
        String(data.get("priority")),
      );
      setHandoffState("requested");
      await refreshSessions();
    } catch {
      setHandoffState("failed");
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Operations"
        title="Live monitor"
        description="Realtime sales sessions, durable conversation state, and confirmed integration outcomes."
        action={<button onClick={() => void refreshSessions()} className="inline-flex items-center gap-2 rounded-lg border border-ops-border bg-ops-raised px-3 py-2 text-xs font-semibold"><RefreshCw className="h-3.5 w-3.5" />Refresh</button>}
      />
      <div className="border-b border-ops-border bg-ops-panel/60 px-5 py-3 text-xs text-ops-muted lg:px-8">
        <span className={`mr-2 inline-block h-2 w-2 rounded-full ${liveStatus === "connected" ? "bg-ops-green" : "bg-ops-amber"}`} />
        {liveStatus === "connected" ? "Live event stream connected" : "Live stream degraded; polling every 15 seconds"}
      </div>
      <div className="grid min-h-[calc(100vh-10rem)] xl:grid-cols-[18rem_minmax(0,1fr)_20rem]">
        <section className="border-r border-ops-border p-4 lg:p-5" aria-label="Active sessions">
          <h2 className="mb-3 text-xs font-bold tracking-wider text-ops-muted uppercase">Active sessions · {active.length}</h2>
          {active.length ? <div className="space-y-2">{active.map((item) => (
            <button key={item.session_id} onClick={() => setSelectedId(item.session_id)} aria-pressed={chosenId === item.session_id} className={`w-full rounded-xl border p-3 text-left ${chosenId === item.session_id ? "border-ops-blue/50 bg-ops-blue/10" : "border-ops-border bg-ops-panel hover:border-ops-muted"}`}>
              <span className="flex justify-between gap-2"><span className="truncate text-sm font-semibold">{item.customer.name}</span><span className="rounded-full bg-ops-green/10 px-2 py-0.5 text-[9px] font-bold text-ops-green">{item.status}</span></span>
              <span className="mt-1 block truncate text-xs text-ops-muted">{item.customer.company ?? "No company supplied"}</span>
              <span className="mt-3 block font-mono text-[9px] text-ops-muted">{item.session_id.slice(0, 13)}</span>
            </button>
          ))}</div> : <EmptyState title="No live sessions" detail="New authenticated calls will appear here automatically." />}
        </section>
        <section className="ops-scrollbar min-w-0 overflow-y-auto p-5 lg:p-8" aria-label="Transcript">
          {detailError ? <p role="alert" className="rounded-xl border border-ops-red/30 bg-ops-red/10 p-4 text-sm text-ops-red">{detailError}</p> : null}
          {!selectedDetail ? <EmptyState title="Select a session" detail="Choose a live session to inspect its persisted transcript and state." /> : <>
            <div className="mb-8 flex flex-wrap items-center justify-between gap-4"><div><p className="text-xs text-ops-muted">{selectedDetail.customer.company ?? "Prospect"}</p><h2 className="mt-1 text-lg font-bold">{selectedDetail.customer.name}</h2></div><div className="flex gap-2"><Link href={`/console/sessions/${selectedDetail.session_id}`} className="rounded-lg border border-ops-border bg-ops-raised px-3 py-2 text-xs font-semibold">Full record</Link><button onClick={() => { setHandoffState("idle"); setHandoffOpen(true); }} className="inline-flex items-center gap-2 rounded-lg bg-ops-blue px-3 py-2 text-xs font-semibold text-white"><Headphones className="h-3.5 w-3.5" />Request handoff</button></div></div>
            {selectedDetail.transcript.length ? <div className="space-y-6">{selectedDetail.transcript.map((message) => (
              <article key={message.id} className={`flex gap-3 ${message.speaker === "CUSTOMER" ? "flex-row-reverse" : ""}`}>
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-ops-border bg-ops-raised">{message.speaker === "CUSTOMER" ? <UserRound className="h-4 w-4" /> : <Bot className="h-4 w-4 text-ops-blue" />}</span>
                <div className={`max-w-2xl ${message.speaker === "CUSTOMER" ? "text-right" : ""}`}><p className="mb-1 text-[10px] font-bold tracking-wider text-ops-muted uppercase">{message.speaker} · {new Date(message.created_at).toLocaleTimeString()}</p><p className="rounded-2xl border border-ops-border bg-ops-panel p-4 text-left text-sm leading-6">{message.content}</p>{message.interrupted ? <p className="mt-1 text-[10px] text-ops-amber">Delivery interrupted</p> : null}</div>
              </article>
            ))}</div> : <EmptyState title="Transcript not available yet" detail="Persisted turns appear here after the conversation begins." />}
          </>}
        </section>
        <aside className="border-l border-ops-border bg-ops-base/60 p-5">
          <h2 className="text-xs font-bold tracking-wider text-ops-muted uppercase">Session intelligence</h2>
          {selectedDetail ? <div className="mt-4 space-y-4"><div className="rounded-2xl border border-ops-border bg-ops-panel p-4"><p className="text-xs text-ops-muted">Qualification</p><p className="mt-2 text-3xl font-bold">{selectedDetail.qualification_score}<span className="text-sm text-ops-muted">%</span></p><div className="mt-3 h-1.5 rounded-full bg-ops-soft"><div className="h-full rounded-full bg-ops-blue" style={{ width: `${selectedDetail.qualification_score}%` }} /></div></div><div className="grid grid-cols-2 gap-2">{selectedDetail.requirements.map((item) => <div key={item.field} className="rounded-xl border border-ops-border bg-ops-panel p-3"><CheckCircle2 className="h-4 w-4 text-ops-green" /><p className="mt-2 text-[10px] text-ops-muted uppercase">{item.field.replaceAll("_", " ")}</p><p className="mt-1 truncate text-xs font-semibold">{String(item.value)}</p></div>)}</div>{selectedDetail.objections.map((item) => <div key={item.id} className="rounded-xl border border-ops-amber/30 bg-ops-amber/5 p-3"><p className="flex items-center gap-2 text-xs font-semibold text-ops-amber"><ShieldAlert className="h-4 w-4" />{item.category}</p><p className="mt-2 text-xs text-ops-muted">{item.detail}</p></div>)}</div> : <p className="mt-4 text-sm text-ops-muted">Select a session to inspect structured state.</p>}
        </aside>
      </div>
      <dialog ref={dialogRef} onClose={() => setHandoffOpen(false)} className="m-auto w-[min(32rem,calc(100%-2rem))] rounded-2xl border border-ops-border bg-ops-panel p-0 text-ops-text backdrop:bg-black/70">
        <form onSubmit={(event) => void requestHandoff(event)} className="p-6"><h2 className="text-lg font-bold">Request human handoff</h2><p className="mt-2 text-sm text-ops-muted">This records a request. Assignment is shown only after provider confirmation.</p><label className="mt-5 block text-xs font-semibold">Reason<textarea name="reason" required minLength={3} maxLength={500} className="mt-2 min-h-24 w-full rounded-xl border border-ops-border bg-ops-base p-3 text-sm" /></label><label className="mt-4 block text-xs font-semibold">Priority<select name="priority" defaultValue="NORMAL" className="mt-2 w-full rounded-xl border border-ops-border bg-ops-base p-3 text-sm"><option>LOW</option><option>NORMAL</option><option>HIGH</option><option>URGENT</option></select></label>{handoffState === "requested" ? <p role="status" className="mt-4 flex items-center gap-2 text-sm text-ops-green"><CheckCircle2 className="h-4 w-4" />Handoff requested; awaiting assignment.</p> : null}{handoffState === "failed" ? <p role="alert" className="mt-4 flex items-center gap-2 text-sm text-ops-red"><AlertTriangle className="h-4 w-4" />Request failed. Refresh and retry.</p> : null}<div className="mt-6 flex justify-end gap-2"><button type="button" onClick={() => setHandoffOpen(false)} className="rounded-lg border border-ops-border px-4 py-2 text-sm">Close</button><button disabled={handoffState === "submitting" || handoffState === "requested"} className="inline-flex items-center gap-2 rounded-lg bg-ops-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{handoffState === "submitting" ? <CircleDashed className="h-4 w-4 animate-spin" /> : null}Submit request</button></div></form>
      </dialog>
    </>
  );
}
