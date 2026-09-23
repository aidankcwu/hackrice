import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { TokenCapture } from "@/components/TokenCapture";
import "./globals.css";

// Add to Home Screen: `manifest.ts` (basePath-aware), `icon.png` and `apple-icon.png`
// sit beside this file and Next links them. The name matches the native app's
// CFBundleDisplayName (ios/Brian/project.yml).
export const metadata: Metadata = {
  title: "Brian",
  applicationName: "Brian",
  appleWebApp: { capable: true, title: "Brian", statusBarStyle: "default" },
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
    <html lang="en">
      <body>
        <TokenCapture />
        {children}
      </body>
    </html>
  );
}
