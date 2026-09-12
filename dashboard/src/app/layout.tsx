import type { Metadata } from "next"; import "./globals.css";
export const metadata: Metadata={title:"LifeOS Dashboard",description:"Real-time healthspan copilot decisions"};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>}
