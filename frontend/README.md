# DataWhisper frontend

React SPA built with [Vite](https://vitejs.dev/). Talks to the backend at
same-origin `/api` (proxied by nginx in prod, by Vite's dev server locally).

## Scripts

| Command | What it does |
|---------|--------------|
| `npm run dev` (or `npm start`) | Start the Vite dev server on http://localhost:3000 with `/api` and `/health` proxied to `http://localhost:8000`. |
| `npm run build` | Production build to `build/` (content-hashed assets in `build/assets/`). |
| `npm run preview` | Serve the production build locally. |
| `npm test` | Run the Vitest suite once. |
| `npm run test:watch` | Vitest in watch mode. |

## Configuration

- `VITE_API_URL` — override the API base for split-origin/dev setups
  (e.g. `VITE_API_URL=http://localhost:8000/api`). Defaults to same-origin `/api`.

Put local overrides in `frontend/.env.local` (git-ignored).

## Notes

- Source files that contain JSX use the `.jsx` extension.
- The result/chart view (`ResultView`, which pulls in Recharts) is lazy-loaded
  via `React.lazy`, so it lands in its own chunk and stays out of the initial
  bundle.
