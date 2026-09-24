import type { MetadataRoute } from "next";

// Add to Home Screen. Every URL carries the build's base path (`NEXT_BASE_PATH`,
// e.g. /t/alice/app), which Next does not add inside a manifest by itself: a bare
// "/" would launch the Home Screen app at the domain root, outside the tester.
// start_url is the base path itself, no trailing slash: `/t/alice/app/` 308s to it.
const base = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: `${base}/`,
    name: "Bryan",
    short_name: "Bryan",
    start_url: base || "/",
    scope: `${base}/`,
    display: "standalone",
    orientation: "portrait",
    background_color: "#ffffff",
    theme_color: "#ffffff",
    icons: [
      { src: `${base}/icon-192.png`, sizes: "192x192", type: "image/png", purpose: "any" },
      { src: `${base}/icon-512.png`, sizes: "512x512", type: "image/png", purpose: "any" },
      { src: `${base}/icon-512.png`, sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
