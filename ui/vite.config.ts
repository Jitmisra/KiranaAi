import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * The till is served by FastAPI at the origin root, so `base` stays '/'.
 *
 * In `npm run dev` the API lives on another port, so every server route is
 * proxied. The list is explicit rather than a catch-all: a typo'd fetch should
 * 404 loudly in development instead of being quietly forwarded and blamed on
 * the server.
 */
// EVERY server prefix the pages call. A route missing from this list works in
// the built site — same origin, no proxy involved — and 404s only under
// `npm run dev`, which is the one place a developer will read it as a bug in
// the code they just wrote.
const API_ROUTES = [
  '/health', '/codes', '/scan', '/enrol', '/recognise', '/shop',
  '/qr', '/api', '/sample', '/analyse', '/reference', '/demo',
  '/counter', '/detector', '/saaf',          // the whole-counter read
  '/store', '/orders',                       // the customer's side
  '/manage',                                 // history, inventory, settings
  '/offers',                                 // the shopkeeper's discounts
  // The rest of the shop. This list had stopped keeping up with the batch, and
  // a prefix missing here works perfectly in the BUILT site (same origin, no
  // proxy involved) and 404s only under `npm run dev` — a confusing way to
  // lose an afternoon. They are listed together so the next module is added
  // in an obvious place.
  '/auth', '/assistant', '/advisor', '/categories', '/stock', '/customers',
  '/receipt', '/purchases', '/expenses', '/cash', '/search', '/daybook',
  '/gst', '/expiry', '/weighed', '/labels', '/loyalty', '/share', '/po',
  '/insights',
  '/parchi',                                 // the photographed bill
  '/khata',                                  // the udhaar book
  '/milan',                                  // the settlement match
];

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // The counter runs on a shop's phone or an old laptop. Fail the build loudly
    // if a bundle grows past what that can parse quickly on a cold cache.
    chunkSizeWarningLimit: 400,
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      API_ROUTES.map((p) => [p, { target: 'http://127.0.0.1:8790', changeOrigin: false }]),
    ),
  },
});
