import type { ReactNode } from "react";

import { ConsoleProvider } from "../../features/control-center/ConsoleProvider";
import { ConsoleShell } from "../../features/control-center/ConsoleShell";

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <ConsoleProvider>
      <ConsoleShell>{children}</ConsoleShell>
    </ConsoleProvider>
  );
}
