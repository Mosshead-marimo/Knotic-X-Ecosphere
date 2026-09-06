import { SessionRecord } from "../../../../features/control-center/ConsolePages";
export default async function Page({ params }: { params: Promise<{ sessionId: string }> }) { const { sessionId } = await params; return <SessionRecord sessionId={sessionId} />; }
