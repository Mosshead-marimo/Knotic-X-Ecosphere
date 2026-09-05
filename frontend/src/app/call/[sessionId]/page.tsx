import { VoiceCallPanel } from "../../../features/voice/VoiceCallPanel";

// The browser session and CSRF cookie/token bootstrap (login) flow is not yet implemented
// anywhere in this codebase (a pre-existing gap outside Phase 4's scope), so this page accepts
// the CSRF token as a query parameter as a placeholder wiring point rather than inventing an
// undocumented auth mechanism. Replace this once the authenticated session bootstrap lands.
interface CallPageProps {
  params: Promise<{ sessionId: string }>;
  searchParams: Promise<{ csrfToken?: string }>;
}

export default async function CallPage({ params, searchParams }: CallPageProps) {
  const { sessionId } = await params;
  const { csrfToken } = await searchParams;
  const apiBaseUrl = process.env.NEXT_PUBLIC_KNOTIC_API_BASE_URL ?? "";
  const mediaRegion = process.env.NEXT_PUBLIC_KNOTIC_VOICE_MEDIA_REGION ?? "GLOBAL";

  return (
    <main>
      <VoiceCallPanel
        sessionId={sessionId}
        apiBaseUrl={apiBaseUrl}
        csrfToken={csrfToken ?? ""}
        mediaRegion={mediaRegion}
      />
    </main>
  );
}
