import type { Metadata, Viewport } from "next";
import { DM_Sans } from "next/font/google";
import "./globals.css";

// One bold sans for everything (SKILL.md token table). Exposed as a CSS
// variable so the Tailwind `font-sans` token in globals.css picks it up;
// `display: swap` keeps text visible while the face loads, and next/font
// self-hosts the files so there is no third-party connection to preconnect to.
const dmSans = DM_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "700", "800"],
  variable: "--font-dm-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Bryan",
  description: "Healthy-life hours earned and cost today, from what the glasses and the wearable saw.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#000000",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={dmSans.variable}>
      <body>
        {/* First tab stop on every page: the header's five pills sit between the
            top of the document and the content, so a keyboard user gets past
            them in one keystroke. Visible only once focused. */}
        <a
          href="#main"
          className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:top-3 focus-visible:left-3 focus-visible:z-50 focus-visible:rounded-full focus-visible:bg-bg focus-visible:px-4 focus-visible:py-2 focus-visible:text-sm focus-visible:font-medium focus-visible:text-ink"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
