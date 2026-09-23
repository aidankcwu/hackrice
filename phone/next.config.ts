import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `npm run dev -- -H 0.0.0.0`, opened from the iPhone at this PC's Wi-Fi
  // address: the dev server refuses its own scripts to any origin not listed
  // here, so allow the private LAN ranges (dev only; no effect on a build).
  allowedDevOrigins: ["10.*.*.*", "172.*.*.*", "192.168.*.*", "*.local"],
  // The dev badge would sit in every design screenshot.
  devIndicators: false,
};

export default nextConfig;
