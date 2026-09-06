"use client";

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { PhoneCall } from "lucide-react";
import { startDemoCall } from "./startDemoCall";

export interface StartDemoCallButtonProps {
  apiBaseUrl: string;
  className: string;
  children: ReactNode;
}

/**
 * Drives the real "start a demo call" flow: bootstraps a backend session (see
 * `startDemoCall.ts`) then navigates to the actual voice call page. Renders as a `<button>`
 * with the same visual treatment the home page's static CTAs already use, so it's a drop-in
 * replacement for the `<a href="#demo-call">` placeholder.
 */
export function StartDemoCallButton({ apiBaseUrl, className, children }: StartDemoCallButtonProps) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = async () => {
    setPending(true);
    setError(null);
    try {
      const { sessionId, csrfToken } = await startDemoCall(apiBaseUrl);
      router.push(`/call/${sessionId}?csrfToken=${encodeURIComponent(csrfToken)}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The demo call could not be started.");
      setPending(false);
    }
  };

  return (
    <span className="inline-flex flex-col gap-2">
      <button type="button" onClick={() => void handleClick()} disabled={pending} className={className}>
        <PhoneCall className="h-5 w-5" aria-hidden="true" />
        {pending ? "Starting…" : children}
      </button>
      {error ? (
        <span role="alert" className="text-sm text-red-600">
          {error}
        </span>
      ) : null}
    </span>
  );
}
