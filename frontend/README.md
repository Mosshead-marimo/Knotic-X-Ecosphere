# Frontend

Next.js App Router application for the customer call experience. This component owns browser UI, device controls, and the Agora Web SDK boundary. It must never receive server credentials.

The application entry point is `src/app/page.tsx`, with the root layout at `src/app/layout.tsx`. Runtime and dependencies are pinned at the repository root and in this component's exact manifest. Use root `npm ci`, `npm run typecheck`, and `npm run build` commands so workspace enforcement remains active.

Follow `../docs/AGENTS.md` and `../docs/COMPONENT_OWNERSHIP.md`.
