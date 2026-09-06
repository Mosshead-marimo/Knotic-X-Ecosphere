import { AuthenticatedCall } from "../../../features/voice/AuthenticatedCall";

// Development demo sessions currently pass the CSRF token through this route. Replace the
// query parameter with the production OIDC session projection before public deployment.
interface CallPageProps {
  params: Promise<{ sessionId: string }>;
}

export default async function CallPage({ params }: CallPageProps) {
  const { sessionId } = await params;
  const mediaRegion = process.env.NEXT_PUBLIC_KNOTIC_VOICE_MEDIA_REGION ?? "GLOBAL";

  return (
    <main className="ops-grid flex min-h-screen items-center justify-center bg-ops-base px-6 py-16">
      <AuthenticatedCall sessionId={sessionId} mediaRegion={mediaRegion} />
    </main>
  );
}
