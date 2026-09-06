import { OpsConsole } from "../features/control-center/OpsConsole";

export default function HomePage() {
  const apiBaseUrl = process.env.NEXT_PUBLIC_KNOTIC_API_BASE_URL ?? "";

  return <OpsConsole apiBaseUrl={apiBaseUrl} />;
}
