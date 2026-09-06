"use client";

import { FileText, RefreshCw, Upload } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { consoleApi } from "./api";
import { EmptyState, PageHeader } from "./ConsoleShell";
import { useConsole } from "./ConsoleProvider";
import type { KnowledgeDocument } from "./types";

const MAX_BYTES = 5_000_000;

export function KnowledgePage() {
  const { session } = useConsole();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [selected, setSelected] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setDocuments((await consoleApi.knowledge()).items);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Knowledge documents are unavailable.");
    }
  }, []);
  useEffect(() => {
    let current = true;
    void consoleApi.knowledge().then((result) => {
      if (current) setDocuments(result.items);
    }).catch((cause: unknown) => {
      if (current) setMessage(cause instanceof Error ? cause.message : "Knowledge documents are unavailable.");
    });
    return () => { current = false; };
  }, []);

  async function choose(file: File | null) {
    setSelected(null);
    setPreview("");
    setMessage("");
    if (!file) return;
    if (!/\.(txt|md)$/i.test(file.name) || file.size > MAX_BYTES) {
      setMessage("Choose a UTF-8 .txt or .md file no larger than 5 MB.");
      return;
    }
    try {
      const text = await file.text();
      if (!text.trim() || text.includes("\u0000")) throw new Error("The file is not valid plain UTF-8 text.");
      setSelected(file);
      setPreview(text.slice(0, 4_000));
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "The file could not be read.");
    }
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !selected || busy) return;
    setBusy(true);
    setMessage("Uploading and queueing indexing…");
    try {
      const data = new FormData(event.currentTarget);
      await consoleApi.uploadKnowledge(session, selected, String(data.get("domain")));
      setSelected(null);
      setPreview("");
      setMessage("Upload persisted. Indexing is queued; it is not yet active.");
      await load();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function act(document: KnowledgeDocument, action: "deactivate" | "reindex") {
    if (!session || busy) return;
    setBusy(true);
    try {
      const result = await consoleApi.knowledgeAction(session, document.id, action);
      setMessage(action === "reindex" ? "Reindexing is queued." : `Deactivation ${result.status}.`);
      await load();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "The action failed.");
    } finally {
      setBusy(false);
    }
  }

  return <>
    <PageHeader eyebrow="Intelligence" title="Knowledge" description="Tenant-isolated approved text sources with durable indexing state." action={<button onClick={() => void load()} className="inline-flex items-center gap-2 rounded-lg border border-ops-border px-3 py-2 text-xs"><RefreshCw className="h-3.5 w-3.5" />Refresh</button>} />
    <div className="grid gap-6 p-5 lg:grid-cols-[minmax(20rem,1fr)_minmax(0,2fr)] lg:p-8">
      <form onSubmit={(event) => void upload(event)} className="rounded-2xl border border-ops-border bg-ops-panel p-5">
        <h2 className="flex items-center gap-2 text-sm font-bold"><Upload className="h-4 w-4 text-ops-blue" />Add approved source</h2>
        <label className="mt-5 block text-xs font-semibold">Document<input type="file" accept=".txt,.md,text/plain,text/markdown" onChange={(event) => void choose(event.target.files?.[0] ?? null)} className="mt-2 block w-full text-xs" /></label>
        <label className="mt-4 block text-xs font-semibold">Domain<input name="domain" defaultValue="sales" pattern="[a-z][a-z0-9_-]{1,63}" required className="mt-2 w-full rounded-lg border border-ops-border bg-ops-base p-3" /></label>
        {preview ? <pre className="ops-scrollbar mt-4 max-h-56 overflow-auto whitespace-pre-wrap rounded-xl border border-ops-border bg-ops-base p-3 text-xs">{preview}</pre> : null}
        <button disabled={!selected || busy} className="mt-4 w-full rounded-lg bg-ops-blue px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{busy ? "Working…" : "Upload and queue indexing"}</button>
        {message ? <p aria-live="polite" className="mt-4 text-xs text-ops-muted">{message}</p> : null}
      </form>
      <section><h2 className="text-xs font-bold tracking-wider text-ops-muted uppercase">Documents · {documents.length}</h2>{documents.length ? <div className="mt-3 space-y-3">{documents.map((document) => <article key={document.id} className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-ops-border bg-ops-panel p-5"><div><FileText className="h-5 w-5 text-ops-blue" /><h3 className="mt-2 font-bold">{document.title}</h3><p className="mt-1 text-xs text-ops-muted">{document.domain} · v{document.document_version} · {document.status.toLowerCase().replaceAll("_", " ")}</p></div><div className="flex gap-2"><button disabled={busy} onClick={() => void act(document, "reindex")} className="rounded-lg border border-ops-border px-3 py-2 text-xs">Reindex</button>{document.status !== "INACTIVE" ? <button disabled={busy} onClick={() => void act(document, "deactivate")} className="rounded-lg border border-ops-red/30 px-3 py-2 text-xs text-ops-red">Deactivate</button> : null}</div></article>)}</div> : <div className="mt-3"><EmptyState title="No knowledge documents" detail="Upload an approved TXT or Markdown source to begin." /></div>}</section>
    </div>
  </>;
}
