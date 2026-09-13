import type { Metadata, Viewport } from "next";
import { DM_Sans } from "next/font/google";
import "./globals.css";

// One bold sans for everything (brief §1). Exposed as a CSS variable so the
// Tailwind `font-sans` token in globals.css picks it up; falls back to the
// system stack if Google Fonts is unreachable at build time.
const dmSans = DM_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "700", "800"],
  variable: "--font-dm-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Bryan",
  description: "Healthy-life hours earned and lost today, from what the glasses and the wearable saw.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#000000",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={dmSans.variable}>
      <body>{children}</body>
    </html>
  );
}
