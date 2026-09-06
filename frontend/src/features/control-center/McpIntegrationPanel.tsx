"use client";

import { CheckCircle2, Database, Plus, RefreshCw, Server, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { consoleApi } from "./api";
import { EmptyState, PageHeader } from "./ConsoleShell";
import { useConsole } from "./ConsoleProvider";
import type { BrowserSession, IntegrationStatus, McpCapability } from "./types";

const CAPABILITIES: Array<{ value: McpCapability; label: string }> = [
  { value: "KNOWLEDGE", label: "Knowledge" },
  { value: "CRM", label: "CRM" },
  { value: "CALENDAR", label: "Calendar" },
  { value: "MESSAGING", label: "Messaging" },
  { value: "HANDOFF", label: "Human handoff" },
];

export function IntegrationsPage() {
  const { session } = useConsole();
  const [data, setData] = useState<IntegrationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const canManage = session?.actor.roles.some((role) => role === "ADMIN" || role === "SUPERVISOR") ?? false;

  const load = useCallback(async () => {
    try {
      const integrations = await consoleApi.integrations();
      setError(null);
      setData(integrations);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Integration status is unavailable.");
    }
  }, []);

  useEffect(() => {
    let active = true;
    consoleApi.integrations().then(
      (integrations) => {
        if (active) {
          setError(null);
          setData(integrations);
        }
      },
      (cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "Integration status is unavailable.");
      },
    );
    return () => {
      active = false;
    };
  }, []);

  const handleAdded = async () => {
    setAnnouncement("MCP server registration requested. It will remain pending until platform validation completes.");
    setDialogOpen(false);
    await load();
  };

  return (
    <>
      <PageHeader
        eyebrow="Intelligence"
        title="MCP gateway"
        description="Tenant MCP servers and provider work. Credentials remain deployment-managed and requests are never shown as active before validation."
        action={
          <div className="flex gap-2">
            <button type="button" onClick={() => void load()} className="inline-flex items-center gap-2 rounded-lg border border-ops-border px-3 py-2 text-xs">
              <RefreshCw className="h-3.5 w-3.5" /> Refresh
            </button>
            {canManage ? (
              <button type="button" onClick={() => setDialogOpen(true)} className="inline-flex items-center gap-2 rounded-lg bg-ops-blue px-3 py-2 text-xs font-semibold text-white hover:bg-ops-blue/90">
                <Plus className="h-3.5 w-3.5" /> Add MCP
              </button>
            ) : null}
          </div>
        }
      />
      <div className="p-5 lg:p-8">
        <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
        {error ? <p role="alert" className="mb-5 text-ops-red">{error}</p> : null}
        {data ? (
          <>
            <div className={`rounded-2xl border p-5 ${data.mcp.status === "available" ? "border-ops-green/30 bg-ops-green/5" : "border-ops-amber/30 bg-ops-amber/5"}`}>
              <p className="text-xs text-ops-muted">MCP gateway</p>
              <p className="mt-2 text-lg font-bold capitalize">{data.mcp.status}</p>
            </div>

            <section className="mt-6" aria-labelledby="mcp-servers-heading">
              <div className="mb-4 flex items-end justify-between gap-4">
                <div>
                  <h2 id="mcp-servers-heading" className="text-sm font-bold">MCP servers</h2>
                  <p className="mt-1 text-xs text-ops-muted">Registration status reflects platform review and runtime activation.</p>
                </div>
                <span className="font-mono text-xs text-ops-muted">{data.registrations.length} registered</span>
              </div>
              {data.registrations.length ? (
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {data.registrations.map((registration) => (
                    <article key={registration.id} className="rounded-2xl border border-ops-border bg-ops-panel p-5">
                      <div className="flex items-start justify-between gap-3">
                        <Server className="h-5 w-5 text-ops-blue" />
                        <span className={`rounded-full px-2 py-1 text-[9px] font-bold tracking-wider uppercase ${registration.status === "active" ? "bg-ops-green/10 text-ops-green" : registration.status === "failed" || registration.status === "rejected" ? "bg-ops-red/10 text-ops-red" : "bg-ops-amber/10 text-ops-amber"}`}>
                          {registration.status}
                        </span>
                      </div>
                      <h3 className="mt-4 font-bold">{registration.display_name}</h3>
                      <p className="mt-1 truncate font-mono text-[10px] text-ops-muted" title={registration.server_url}>{registration.server_url}</p>
                      <p className="mt-4 text-xs text-ops-muted">{registration.transport.replace("_", " ")} · {registration.auth_scheme}</p>
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {registration.capabilities.map((capability) => <span key={capability} className="rounded-md bg-ops-soft px-2 py-1 text-[9px] font-semibold">{capability}</span>)}
                      </div>
                      <p className="mt-4 border-t border-ops-border pt-3 text-[10px] leading-4 text-ops-muted">{registration.safe_status_detail}</p>
                    </article>
                  ))}
                </div>
              ) : (
                <EmptyState title="No MCP servers requested" detail={canManage ? "Use Add MCP to submit a server for secure platform validation." : "An administrator or supervisor can request an MCP server."} />
              )}
            </section>

            <section className="mt-8" aria-labelledby="provider-work-heading">
              <h2 id="provider-work-heading" className="mb-4 text-sm font-bold">Provider work</h2>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {data.providers.map((provider) => (
                  <article key={provider.provider} className="rounded-2xl border border-ops-border bg-ops-panel p-5">
                    <Database className="h-5 w-5 text-ops-blue" />
                    <h3 className="mt-4 font-bold">{provider.provider}</h3>
                    <p className="mt-2 text-xs text-ops-muted">{Object.entries(provider.counts).map(([key, value]) => `${key}: ${value}`).join(" · ") || "No queued work"}</p>
                    <p className="mt-4 text-[10px] text-ops-muted">Last activity: {provider.last_activity_at ? new Date(provider.last_activity_at).toLocaleString() : "Never"}</p>
                  </article>
                ))}
              </div>
              {!data.providers.length ? <div className="mt-4"><EmptyState title="No provider work" detail="No external provider operations have been recorded for this tenant." /></div> : null}
            </section>
          </>
        ) : <p role="status" className="text-sm text-ops-muted">Loading integration status…</p>}
      </div>
      {dialogOpen && session ? <AddMcpDialog session={session} onClose={() => setDialogOpen(false)} onAdded={handleAdded} /> : null}
    </>
  );
}

function AddMcpDialog({ session, onClose, onAdded }: { session: BrowserSession; onClose: () => void; onAdded: () => Promise<void> }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<McpCapability[]>(["KNOWLEDGE"]);

  useEffect(() => {
    dialogRef.current?.showModal();
  }, []);

  const toggleCapability = (capability: McpCapability) => {
    setCapabilities((current) => current.includes(capability) ? current.filter((item) => item !== capability) : [...current, capability]);
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!capabilities.length) {
      setError("Select at least one capability.");
      return;
    }
    setSubmitting(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      await consoleApi.requestMcpRegistration(session, {
        display_name: String(form.get("display_name")),
        server_url: String(form.get("server_url")),
        transport: String(form.get("transport")) as "STREAMABLE_HTTP" | "SSE",
        auth_scheme: String(form.get("auth_scheme")) as "NONE" | "BEARER" | "OAUTH2",
        capabilities,
      });
      await onAdded();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "The MCP registration could not be requested.");
      setSubmitting(false);
    }
  };

  return (
    <dialog ref={dialogRef} onCancel={onClose} className="m-auto w-[min(38rem,calc(100%-2rem))] rounded-2xl border border-ops-border bg-ops-panel p-0 text-ops-text shadow-2xl backdrop:bg-black/75">
      <form onSubmit={(event) => void submit(event)} className="p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div><p className="text-[10px] font-bold tracking-wider text-ops-blue uppercase">Governed registration</p><h2 className="mt-1 text-xl font-bold">Add MCP server</h2></div>
          <button type="button" onClick={onClose} aria-label="Close dialog" className="rounded-lg p-2 text-ops-muted hover:bg-ops-soft hover:text-white"><X className="h-4 w-4" /></button>
        </div>
        <p className="mt-3 text-sm leading-6 text-ops-muted">Submit public connection metadata only. Never paste API keys, tokens, passwords, or OAuth secrets here.</p>
        {error ? <p role="alert" className="mt-4 rounded-lg border border-ops-red/30 bg-ops-red/10 p-3 text-sm text-ops-red">{error}</p> : null}
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <label className="text-xs font-semibold">Display name<input required name="display_name" minLength={2} maxLength={80} autoFocus className="mt-2 w-full rounded-lg border border-ops-border bg-ops-base px-3 py-2.5 font-normal" placeholder="Product knowledge" /></label>
          <label className="text-xs font-semibold">Transport<select name="transport" className="mt-2 w-full rounded-lg border border-ops-border bg-ops-base px-3 py-2.5 font-normal"><option value="STREAMABLE_HTTP">Streamable HTTP</option><option value="SSE">SSE (legacy)</option></select></label>
          <label className="text-xs font-semibold sm:col-span-2">HTTPS server URL<input required name="server_url" type="url" pattern="https://.*" maxLength={500} className="mt-2 w-full rounded-lg border border-ops-border bg-ops-base px-3 py-2.5 font-mono text-xs font-normal" placeholder="https://mcp.example.com/mcp" /></label>
          <label className="text-xs font-semibold">Authentication<select name="auth_scheme" className="mt-2 w-full rounded-lg border border-ops-border bg-ops-base px-3 py-2.5 font-normal"><option value="BEARER">Bearer token</option><option value="OAUTH2">OAuth 2.0</option><option value="NONE">None</option></select></label>
          <fieldset className="sm:col-span-2"><legend className="text-xs font-semibold">Capabilities</legend><div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">{CAPABILITIES.map((capability) => <label key={capability.value} className="flex items-center gap-2 rounded-lg border border-ops-border bg-ops-base px-3 py-2 text-xs"><input type="checkbox" checked={capabilities.includes(capability.value)} onChange={() => toggleCapability(capability.value)} />{capability.label}</label>)}</div></fieldset>
        </div>
        <div className="mt-6 rounded-xl border border-ops-border bg-ops-base p-4 text-xs leading-5 text-ops-muted"><CheckCircle2 className="mr-2 inline h-4 w-4 text-ops-green" />The request is tenant-scoped and audited. Activation requires contract, egress, scope, and credential validation.</div>
        <div className="mt-6 flex justify-end gap-2"><button type="button" onClick={onClose} disabled={submitting} className="rounded-lg border border-ops-border px-4 py-2 text-xs">Cancel</button><button type="submit" disabled={submitting} className="rounded-lg bg-ops-blue px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">{submitting ? "Submitting…" : "Request MCP"}</button></div>
      </form>
    </dialog>
  );
}
