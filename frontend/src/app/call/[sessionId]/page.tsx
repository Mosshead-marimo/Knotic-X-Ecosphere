import { VoiceCallPanel } from "../../../features/voice/VoiceCallPanel";

// Development demo sessions currently pass the CSRF token through this route. Replace the
// query parameter with the production OIDC session projection before public deployment.
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
    <main className="ops-grid flex min-h-screen items-center justify-center bg-ops-base px-6 py-16">
      <VoiceCallPanel
        sessionId={sessionId}
        apiBaseUrl={apiBaseUrl}
        csrfToken={csrfToken ?? ""}
        mediaRegion={mediaRegion}
      />
    </main>
  );
}
