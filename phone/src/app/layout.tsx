import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { EmbedCapture } from "@/components/EmbedCapture";
import { TokenCapture } from "@/components/TokenCapture";
import { EMBED_SCRIPT } from "@/lib/embed";
import "./globals.css";

// Add to Home Screen: `manifest.ts` (basePath-aware), `icon.png` and `apple-icon.png`
// sit beside this file and Next links them. The name matches the native app's
// CFBundleDisplayName (ios/Brian/project.yml).
export const metadata: Metadata = {
  title: "Bryan",
  applicationName: "Bryan",
  appleWebApp: { capable: true, title: "Bryan", statusBarStyle: "default" },
  formatDetection: { telephone: false },
  // Next writes `mobile-web-app-capable`; older iOS reads only the prefixed tag.
  other: { "apple-mobile-web-app-capable": "yes" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Draw under the notch and home indicator; the shell pads by the safe-area insets.
  viewportFit: "cover",
  // `--page` in both appearances (globals.css, the F.0 tokens).
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#000000" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // The head script sets `data-embed` on <html> before first paint (src/lib/embed.ts); the DOM wins over React there.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: EMBED_SCRIPT }} />
      </head>
      <body>
        <EmbedCapture />
        <TokenCapture />
        {children}
      </body>
    </html>
  );
}
