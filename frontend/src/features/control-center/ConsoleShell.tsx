"use client";

import { Activity, BarChart3, BookOpenText, Headphones, Layers2, LogOut, Menu, PhoneCall, Settings2, X, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { useConsole } from "./ConsoleProvider";

const NAV: Array<{ href: string; label: string; icon: LucideIcon; admin?: boolean }> = [
  { href: "/console/live", label: "Live monitor", icon: Activity },
  { href: "/console/sessions", label: "Sessions", icon: Headphones },
  { href: "/console/performance", label: "Performance", icon: BarChart3 },
  { href: "/console/knowledge", label: "Knowledge", icon: BookOpenText, admin: true },
  { href: "/console/integrations", label: "MCP gateway", icon: Layers2 },
  { href: "/console/system", label: "System", icon: Settings2 },
];

function Sidebar({ close }: { close?: () => void }) {
  const pathname = usePathname();
  const { session, liveStatus, signOut } = useConsole();
  const canAdmin = session?.actor.roles.some((role) => role === "ADMIN" || role === "SUPERVISOR");
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-ops-border bg-ops-base">
      <div className="flex h-16 items-center gap-3 px-5">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-ops-blue text-white"><PhoneCall className="h-4 w-4" /></span>
        <span><strong className="block text-sm">VoxSales</strong><span className="text-[9px] font-bold tracking-[0.18em] text-ops-muted uppercase">AI Ops Console</span></span>
      </div>
      <nav aria-label="Primary" className="flex-1 space-y-1 px-3 py-5">
        {NAV.filter((item) => !item.admin || canAdmin).map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return <Link key={item.href} href={item.href} onClick={close} aria-current={active ? "page" : undefined} className={`flex min-h-11 items-center gap-3 rounded-lg border px-3 text-sm font-medium ${active ? "border-ops-blue/30 bg-ops-blue/10 text-white" : "border-transparent text-ops-muted hover:bg-ops-raised hover:text-white"}`}><Icon className={`h-4 w-4 ${active ? "text-ops-blue" : ""}`} />{item.label}</Link>;
        })}
      </nav>
      <div className="border-t border-ops-border p-3">
        <div className="rounded-xl border border-ops-border bg-ops-panel p-3">
          <p className="truncate text-xs font-semibold">{session?.actor.display_name ?? "Connecting…"}</p>
          <p className="mt-1 flex items-center gap-2 text-[10px] text-ops-muted"><span className={`h-2 w-2 rounded-full ${liveStatus === "connected" ? "bg-ops-green" : "bg-ops-amber"}`} />{liveStatus === "connected" ? "Live updates" : "Polling fallback"}</p>
          <button type="button" onClick={() => void signOut()} className="mt-3 inline-flex items-center gap-2 text-xs text-ops-muted hover:text-white"><LogOut className="h-3.5 w-3.5" />Sign out</button>
        </div>
      </div>
    </aside>
  );
}

export function ConsoleShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const { loading, error } = useConsole();
  return <div className="flex min-h-dvh bg-ops-base">
    <div className="hidden md:block"><Sidebar /></div>
    {open ? <div className="fixed inset-0 z-50 bg-black/70 md:hidden" onClick={() => setOpen(false)}><div onClick={(event) => event.stopPropagation()} className="relative h-full w-64"><Sidebar close={() => setOpen(false)} /><button aria-label="Close navigation" onClick={() => setOpen(false)} className="absolute top-4 right-3 rounded-lg p-2 text-ops-muted"><X className="h-5 w-5" /></button></div></div> : null}
    <main className="min-w-0 flex-1">
      <header className="flex h-16 items-center justify-between border-b border-ops-border bg-ops-panel/80 px-4 backdrop-blur md:px-6"><button aria-label="Open navigation" onClick={() => setOpen(true)} className="rounded-lg border border-ops-border p-2 md:hidden"><Menu className="h-5 w-5" /></button><div className="ml-auto text-right"><p className="text-[10px] font-bold tracking-wider text-ops-muted uppercase">Operations control</p><p className="text-xs text-ops-text">Tenant-isolated workspace</p></div></header>
      {error ? <div role="alert" className="border-b border-ops-red/30 bg-ops-red/10 px-6 py-3 text-sm text-ops-red">{error}</div> : null}
      {loading ? <div role="status" className="grid min-h-[60vh] place-items-center text-sm text-ops-muted">Loading secure workspace…</div> : children}
    </main>
  </div>;
}

export function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <header className="flex flex-col gap-4 border-b border-ops-border px-5 py-6 sm:flex-row sm:items-end sm:justify-between lg:px-8"><div><p className="text-[10px] font-bold tracking-[0.16em] text-ops-blue uppercase">{eyebrow}</p><h1 className="mt-1 text-2xl font-bold tracking-tight">{title}</h1><p className="mt-2 max-w-2xl text-sm text-ops-muted">{description}</p></div>{action}</header>;
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <div className="rounded-2xl border border-dashed border-ops-border bg-ops-panel/60 p-10 text-center"><p className="font-semibold">{title}</p><p className="mt-2 text-sm text-ops-muted">{detail}</p></div>;
}
