"use client";

import {
  Activity,
  AlertCircle,
  BarChart3,
  Bot,
  BrainCircuit,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  CloudAlert,
  Database,
  Headphones,
  Layers2,
  Menu,
  MessageSquareText,
  MicOff,
  PhoneCall,
  RefreshCw,
  Settings2,
  ShieldAlert,
  Target,
  UserRound,
  UsersRound,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { StartDemoCallButton } from "../voice/StartDemoCallButton";

interface OpsConsoleProps {
  apiBaseUrl: string;
}

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  badge?: string;
}

const NAV_GROUPS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Operations",
    items: [
      { href: "#live-monitor", label: "Live monitor", icon: Activity, badge: "Sample" },
      { href: "#session-queue", label: "Session queue", icon: Headphones, badge: "3" },
      { href: "#performance", label: "Performance", icon: BarChart3 },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "#qualification", label: "Qualification", icon: BrainCircuit },
      { href: "#integrations", label: "MCP gateway", icon: Layers2, badge: "Demo" },
    ],
  },
  {
    label: "Administration",
    items: [{ href: "#system-status", label: "System config", icon: Settings2 }],
  },
];

const SESSION_QUEUE = [
  { id: "S-82910", name: "Marc Chen", company: "Northstar Labs", state: "Objection", tone: "amber", line: "Pricing is a concern. We are seeing cheaper options that promise similar features. Why would we pay a premium?", signal: "Pricing value gap detected" },
  { id: "S-82908", name: "Nadia Ross", company: "Axiom Health", state: "Qualifying", tone: "blue", line: "We need secure routing for several regional teams. What would the first deployment phase look like?", signal: "Deployment scope requested" },
  { id: "S-82902", name: "Dev Patel", company: "Orbital Works", state: "Human review", tone: "red", line: "I would like a person from your security team to join before we discuss next steps.", signal: "Explicit human request detected" },
] as const;

const QUALIFICATION = [
  { label: "Budget", state: "Confirmed", icon: CheckCircle2, tone: "text-ops-green" },
  { label: "Authority", state: "Verifying", icon: CircleDashed, tone: "text-ops-blue" },
  { label: "Need", state: "Confirmed", icon: CheckCircle2, tone: "text-ops-green" },
  { label: "Timeline", state: "Unknown", icon: AlertCircle, tone: "text-ops-muted" },
] as const;

function BrandMark() {
  return (
    <div className="flex items-center gap-3">
      <span className="grid h-10 w-10 place-items-center rounded-xl bg-ops-blue text-white shadow-[0_10px_30px_rgba(79,140,255,0.22)]">
        <PhoneCall className="h-5 w-5" aria-hidden="true" />
      </span>
      <span>
        <span className="block text-base font-bold tracking-tight">VoxSales</span>
        <span className="block text-[10px] font-bold tracking-[0.2em] text-ops-muted uppercase">AI Ops Console</span>
      </span>
    </div>
  );
}

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Primary" className="flex-1 overflow-y-auto px-4 py-6">
      {NAV_GROUPS.map((group) => (
        <div key={group.label} className="mb-7">
          <p className="mb-2 px-3 text-[10px] font-bold tracking-[0.18em] text-ops-muted/70 uppercase">
            {group.label}
          </p>
          <div className="space-y-1">
            {group.items.map((item, itemIndex) => {
              const Icon = item.icon;
              const active = group.label === "Operations" && itemIndex === 0;
              return (
                <a
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={`group flex min-h-10 items-center gap-3 rounded-lg border px-3 py-2 text-sm transition-colors ${
                    active
                      ? "border-ops-blue/20 bg-ops-blue/10 text-white"
                      : "border-transparent text-ops-muted hover:bg-ops-raised hover:text-white"
                  }`}
                >
                  <Icon
                    className={`h-4 w-4 ${active ? "text-ops-blue" : "group-hover:text-ops-cyan"}`}
                    aria-hidden="true"
                  />
                  <span className="font-medium">{item.label}</span>
                  {item.badge ? (
                    <span className="ml-auto rounded-full bg-ops-soft px-2 py-0.5 font-mono text-[10px] text-ops-muted">
                      {item.badge}
                    </span>
                  ) : null}
                </a>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

function StatusRail() {
  return (
    <div className="ops-scrollbar flex min-h-12 items-center gap-5 overflow-x-auto border-b border-ops-border bg-ops-panel/90 px-4 whitespace-nowrap lg:px-6">
      <StatusItem indicator={<span className="status-pulse h-2 w-2 rounded-full bg-ops-green" />} label="Console" value="UI ready" />
      <StatusItem indicator={<Zap className="h-3 w-3 text-ops-amber" />} label="Latency" value="Awaiting live call" />
      <StatusItem indicator={<Database className="h-3 w-3 text-ops-blue" />} label="Knowledge" value="Status unavailable" />
      <StatusItem indicator={<CloudAlert className="h-3 w-3 text-ops-amber" />} label="MCP" value="Demo data" warning />
    </div>
  );
}

function StatusItem({ indicator, label, value, warning = false }: { indicator: ReactNode; label: string; value: string; warning?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2 border-r border-ops-border pr-5 text-[10px] font-bold tracking-wider uppercase last:border-r-0">
      <span aria-hidden="true">{indicator}</span>
      <span className="text-ops-muted">{label}</span>
      <span className={`font-mono ${warning ? "text-ops-amber" : "text-ops-text"}`}>{value}</span>
    </span>
  );
}

function SessionQueue({ selectedId, onSelect }: { selectedId: string; onSelect: (id: string) => void }) {
  return (
    <section id="session-queue" aria-labelledby="session-queue-title" className="border-b border-ops-border px-4 py-4 lg:px-6">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <p className="text-[10px] font-bold tracking-[0.16em] text-ops-muted uppercase">Queue preview</p>
          <h2 id="session-queue-title" className="mt-1 text-sm font-semibold">Live sessions</h2>
        </div>
        <button type="button" disabled title="Connect a live session data source to refresh" className="inline-flex items-center gap-2 rounded-lg border border-ops-border bg-ops-raised px-3 py-2 text-xs font-semibold text-ops-muted disabled:cursor-not-allowed disabled:opacity-50">
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" /> Refresh
        </button>
      </div>
      <div className="ops-scrollbar flex gap-3 overflow-x-auto pb-1">
        {SESSION_QUEUE.map((session) => (
          <button
            key={session.id}
            type="button"
            aria-pressed={session.id === selectedId}
            onClick={() => onSelect(session.id)}
            className={`min-w-64 rounded-xl border p-3 text-left transition-colors ${
              session.id === selectedId ? "border-ops-blue/50 bg-ops-blue/8" : "border-ops-border bg-ops-panel hover:border-ops-muted/60"
            }`}
          >
            <span className="flex items-center justify-between">
              <span className="font-mono text-[10px] text-ops-muted">{session.id}</span>
              <span className={`rounded-full px-2 py-0.5 text-[9px] font-bold uppercase ${
                session.tone === "amber"
                  ? "bg-ops-amber/10 text-ops-amber"
                  : session.tone === "red"
                    ? "bg-ops-red/10 text-ops-red"
                    : "bg-ops-blue/10 text-ops-blue"
              }`}>
                {session.state}
              </span>
            </span>
            <span className="mt-2 block text-sm font-semibold">{session.name}</span>
            <span className="mt-0.5 block text-xs text-ops-muted">{session.company}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function Transcript({ prospectName, prospectLine, signal }: { prospectName: string; prospectLine: string; signal: string }) {
  return (
    <div className="space-y-7">
      <article className="flex gap-3 sm:gap-4">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-ops-blue/30 bg-ops-blue/10 text-ops-blue">
          <Bot className="h-4 w-4" aria-hidden="true" />
        </span>
        <div className="max-w-2xl flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-bold tracking-wider text-ops-blue uppercase">VoxSales Agent</span>
            <span className="font-mono text-[9px] text-ops-muted">14:20:05</span>
          </div>
          <p className="rounded-2xl rounded-tl-sm border border-ops-border bg-ops-panel p-4 text-sm leading-6 text-ops-text/90">
            I understand that secure, high-volume routing is the priority. How many seats are you planning to deploy in the first phase?
          </p>
          <details className="mt-3 rounded-xl border border-ops-border bg-ops-panel/60 p-3 open:border-ops-blue/30">
            <summary className="cursor-pointer list-none text-[10px] font-bold tracking-wider text-ops-muted uppercase">
              <span className="inline-flex items-center gap-2">
                <BrainCircuit className="h-3.5 w-3.5 text-ops-blue" aria-hidden="true" />
                Decision trace <span className="font-mono text-ops-green">98% confidence</span>
              </span>
            </summary>
            <dl className="mt-3 grid gap-2 text-[10px] sm:grid-cols-2">
              <TraceItem label="Intent" value="BANT_QUAL" accent />
              <TraceItem label="State" value="NEED_CONFIRM" />
              <TraceItem label="Tool" value="knowledge.search" accent />
              <TraceItem label="Next" value="HANDLE_OBJECTION" />
            </dl>
          </details>
        </div>
      </article>

      <article className="flex flex-row-reverse gap-3 sm:gap-4">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-ops-border bg-ops-raised text-ops-muted">
          <UserRound className="h-4 w-4" aria-hidden="true" />
        </span>
        <div className="max-w-2xl flex-1 text-right">
          <div className="mb-2 flex flex-wrap items-center justify-end gap-2">
            <span className="font-mono text-[9px] text-ops-muted">14:20:18</span>
            <span className="text-[10px] font-bold tracking-wider text-ops-muted uppercase">{prospectName}</span>
          </div>
          <p className="inline-block rounded-2xl rounded-tr-sm border border-ops-border bg-ops-raised/70 p-4 text-left text-sm leading-6 text-ops-text/85">
            {prospectLine}
          </p>
          <p className="mt-2 flex items-center justify-end gap-1.5 text-[10px] font-bold text-ops-amber uppercase">
            <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" /> {signal}
          </p>
        </div>
      </article>

      <div className="flex items-center gap-3 text-xs text-ops-muted" role="status" aria-live="polite">
        <span className="grid h-9 w-9 place-items-center rounded-xl border border-ops-blue/20 bg-ops-blue/5">
          <MessageSquareText className="h-4 w-4 text-ops-blue" aria-hidden="true" />
        </span>
        <span className="inline-flex items-center gap-2">
          <CircleDashed className="h-3.5 w-3.5 animate-spin text-ops-blue" aria-hidden="true" />
          Demo trace paused — no live model request is running.
        </span>
      </div>
    </div>
  );
}

function TraceItem({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex justify-between border-t border-ops-border pt-2">
      <dt className="text-ops-muted">{label}</dt>
      <dd className={`font-mono ${accent ? "text-ops-blue" : "text-ops-text"}`}>{value}</dd>
    </div>
  );
}

function IntelligencePanel() {
  return (
    <aside className="ops-scrollbar overflow-y-auto bg-ops-base/80 p-4 lg:p-6" aria-label="Session intelligence">
      <section id="qualification" aria-labelledby="qualification-title">
        <p className="text-[10px] font-bold tracking-[0.16em] text-ops-muted uppercase">Live context</p>
        <div className="mt-3 rounded-2xl border border-ops-border bg-ops-panel p-4">
          <div className="flex items-center justify-between">
            <h2 id="qualification-title" className="text-sm font-semibold">Conversion signal</h2>
            <Target className="h-4 w-4 text-ops-blue" aria-hidden="true" />
          </div>
          <div className="mt-4 flex items-end justify-between">
            <span className="text-3xl font-bold">84<span className="text-base text-ops-muted">%</span></span>
            <span className="rounded-full bg-ops-green/10 px-2 py-1 text-[9px] font-bold text-ops-green uppercase">Sample score</span>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-ops-soft"><div className="h-full w-[84%] rounded-full bg-gradient-to-r from-ops-blue to-ops-cyan" /></div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          {QUALIFICATION.map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.label} className="rounded-xl border border-ops-border bg-ops-panel p-3">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-bold text-ops-muted">{item.label}</span>
                  <Icon className={`h-4 w-4 ${item.tone}`} aria-hidden="true" />
                </div>
                <p className="mt-2 text-xs font-semibold">{item.state}</p>
              </div>
            );
          })}
        </div>
      </section>

      <section id="integrations" aria-labelledby="integrations-title" className="mt-7">
        <div className="flex items-center justify-between">
          <h2 id="integrations-title" className="text-[10px] font-bold tracking-[0.16em] text-ops-muted uppercase">Integration outcomes</h2>
          <Layers2 className="h-4 w-4 text-ops-blue" aria-hidden="true" />
        </div>
        <div className="mt-3 space-y-2">
          <IntegrationRow name="CRM lead update" detail="Awaiting provider" icon={CircleDashed} tone="text-ops-blue" />
          <IntegrationRow name="Calendar booking" detail="Not requested" icon={CalendarClock} tone="text-ops-muted" />
          <IntegrationRow name="Follow-up delivery" detail="Queued for review" icon={AlertCircle} tone="text-ops-amber" />
        </div>
      </section>

      <section id="performance" aria-labelledby="performance-title" className="mt-7 rounded-2xl border border-ops-blue/20 bg-ops-blue/5 p-4">
        <div className="flex items-center justify-between">
          <h2 id="performance-title" className="text-xs font-semibold text-ops-blue">Reconciliation watch</h2>
          <RefreshCw className="h-4 w-4 text-ops-blue" aria-hidden="true" />
        </div>
        <p className="mt-2 text-xs leading-5 text-ops-muted">
          One follow-up is waiting for provider confirmation. No delivery success is shown until reconciliation completes.
        </p>
      </section>
      <section id="system-status" aria-labelledby="system-status-title" className="mt-7 rounded-2xl border border-ops-border bg-ops-panel p-4">
        <h2 id="system-status-title" className="text-[10px] font-bold tracking-[0.16em] text-ops-muted uppercase">System configuration</h2>
        <p className="mt-2 text-xs leading-5 text-ops-muted">Configuration changes are intentionally unavailable from the demo workspace.</p>
      </section>
    </aside>
  );
}

function IntegrationRow({ name, detail, icon: Icon, tone }: { name: string; detail: string; icon: LucideIcon; tone: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-ops-border bg-ops-panel p-3">
      <Icon className={`h-4 w-4 ${tone}`} aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-semibold">{name}</span>
        <span className="mt-0.5 block text-[10px] text-ops-muted">{detail}</span>
      </span>
      <ChevronRight className="h-4 w-4 text-ops-muted" aria-hidden="true" />
    </div>
  );
}

export function OpsConsole({ apiBaseUrl }: OpsConsoleProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [handoffOpen, setHandoffOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string>(SESSION_QUEUE[0].id);
  const closeHandoffRef = useRef<HTMLButtonElement>(null);
  const selectedSession = SESSION_QUEUE.find((session) => session.id === selectedId) ?? SESSION_QUEUE[0];

  useEffect(() => {
    if (!handoffOpen) return;
    closeHandoffRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setHandoffOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [handoffOpen]);

  return (
    <div className="flex min-h-dvh bg-ops-base">
      <aside className="hidden w-64 shrink-0 border-r border-ops-border bg-ops-base md:flex md:flex-col">
        <div className="px-6 py-5"><BrandMark /></div>
        <Navigation />
        <div className="border-t border-ops-border p-4">
          <div className="flex items-center gap-3 rounded-xl border border-ops-border bg-ops-panel p-3">
            <span className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-ops-blue to-violet-600 text-[10px] font-bold">OP</span>
            <span className="min-w-0">
              <span className="block truncate text-xs font-semibold">Operations team</span>
              <span className="block truncate text-[9px] font-bold tracking-wider text-ops-muted uppercase">Demo workspace</span>
            </span>
          </div>
        </div>
      </aside>

      {menuOpen ? (
        <div className="fixed inset-0 z-50 bg-black/70 md:hidden" role="presentation" onClick={() => setMenuOpen(false)}>
          <aside className="flex h-full w-[min(20rem,88vw)] flex-col border-r border-ops-border bg-ops-base" aria-label="Mobile navigation" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-4">
              <BrandMark />
              <button type="button" onClick={() => setMenuOpen(false)} aria-label="Close navigation" className="rounded-lg border border-ops-border p-2 text-ops-muted hover:text-white"><X className="h-5 w-5" /></button>
            </div>
            <Navigation onNavigate={() => setMenuOpen(false)} />
          </aside>
        </div>
      ) : null}

      <main className="min-w-0 flex-1">
        <header className="flex h-16 items-center justify-between border-b border-ops-border bg-ops-base px-4 md:hidden">
          <BrandMark />
          <button type="button" onClick={() => setMenuOpen(true)} aria-label="Open navigation" aria-expanded={menuOpen} className="rounded-lg border border-ops-border p-2 text-ops-muted hover:text-white"><Menu className="h-5 w-5" /></button>
        </header>
        <StatusRail />

        <div className="ops-grid min-h-[calc(100dvh-3rem)]">
          <section id="live-monitor" aria-labelledby="monitor-heading" className="border-b border-ops-border px-4 py-5 lg:px-6">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex items-start gap-3">
                <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-ops-blue/25 bg-ops-blue/10 text-ops-blue"><Activity className="h-5 w-5" aria-hidden="true" /></span>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h1 id="monitor-heading" className="text-lg font-bold tracking-tight">Session {selectedSession.id}</h1>
                    <span className="rounded bg-ops-soft px-2 py-1 font-mono text-[9px] text-ops-muted">SAMPLE DATA</span>
                  </div>
                  <p className="mt-1 text-xs text-ops-muted">{selectedSession.name} · {selectedSession.company} · enterprise lead</p>
                </div>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <StartDemoCallButton apiBaseUrl={apiBaseUrl} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-ops-blue px-4 text-xs font-bold text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50">
                  Start live demo
                </StartDemoCallButton>
                <button type="button" onClick={() => setHandoffOpen(true)} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-ops-red/40 bg-ops-red/10 px-4 text-xs font-bold text-ops-red hover:bg-ops-red/15">
                  <UsersRound className="h-4 w-4" aria-hidden="true" /> Review handoff
                </button>
              </div>
            </div>
          </section>

          <SessionQueue selectedId={selectedId} onSelect={setSelectedId} />

          <div className="grid min-h-[42rem] xl:grid-cols-[minmax(0,1fr)_24rem]">
            <section aria-labelledby="transcript-title" className="flex min-w-0 flex-col border-b border-ops-border xl:border-r xl:border-b-0">
              <div className="flex items-center justify-between border-b border-ops-border px-4 py-4 lg:px-6">
                <div>
                  <p className="text-[10px] font-bold tracking-[0.16em] text-ops-muted uppercase">Selected conversation</p>
                  <h2 id="transcript-title" className="mt-1 text-sm font-semibold">Transcript & decision trace</h2>
                </div>
                <button type="button" disabled aria-label="Mute unavailable for sample session" title="Start a live demo to use microphone controls" className="rounded-lg border border-ops-border p-2 text-ops-muted opacity-50"><MicOff className="h-4 w-4" /></button>
              </div>
              <div className="ops-scrollbar flex-1 overflow-y-auto px-4 py-6 lg:px-6">
                <Transcript prospectName={selectedSession.name} prospectLine={selectedSession.line} signal={selectedSession.signal} />
              </div>
              <div className="border-t border-ops-border bg-ops-panel/40 p-4 lg:px-6">
                <p className="mb-2 text-[9px] font-bold tracking-wider text-ops-muted uppercase">Suggested next actions · operator review required</p>
                <div className="ops-scrollbar flex gap-2 overflow-x-auto">
                  {["Schedule demo", "Send security brief", "Create follow-up"].map((action) => (
                    <button key={action} type="button" disabled className="shrink-0 rounded-lg border border-ops-border bg-ops-panel px-3 py-2 text-[10px] font-bold text-ops-muted disabled:cursor-not-allowed">{action}</button>
                  ))}
                </div>
              </div>
            </section>
            <IntelligencePanel />
          </div>
        </div>
      </main>

      {handoffOpen ? (
        <div className="fixed inset-0 z-[60] grid place-items-center bg-black/75 p-4" role="presentation" onClick={() => setHandoffOpen(false)}>
          <section role="dialog" aria-modal="true" aria-labelledby="handoff-title" className="w-full max-w-md rounded-2xl border border-ops-border bg-ops-panel p-6 shadow-2xl" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-start justify-between gap-4">
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-ops-red/10 text-ops-red"><UsersRound className="h-5 w-5" aria-hidden="true" /></span>
              <button ref={closeHandoffRef} type="button" onClick={() => setHandoffOpen(false)} aria-label="Close handoff review" className="rounded-lg p-2 text-ops-muted hover:bg-ops-raised hover:text-white"><X className="h-4 w-4" /></button>
            </div>
            <h2 id="handoff-title" className="mt-5 text-lg font-bold">Human handoff review</h2>
            <p className="mt-2 text-sm leading-6 text-ops-muted">
              A live handoff requires an active authorized session and a provider acknowledgement. This sample session cannot be transferred.
            </p>
            <div className="mt-5 rounded-xl border border-ops-amber/25 bg-ops-amber/5 p-3 text-xs text-ops-amber">
              <strong>Required packet:</strong> customer context, qualification, objections, transcript summary, requested action, and correlation ID.
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button type="button" onClick={() => setHandoffOpen(false)} className="rounded-lg border border-ops-border px-4 py-2 text-xs font-bold text-ops-muted hover:text-white">Cancel</button>
              <button type="button" disabled className="rounded-lg bg-ops-red px-4 py-2 text-xs font-bold text-white disabled:cursor-not-allowed disabled:opacity-45">Request handoff</button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
