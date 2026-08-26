import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://ghuang12345.github.io/simulated-mobility-explorer/"),
  title: "Traceframe — Simulated Mobility Explorer",
  description: "Explore time-ordered movement observations from a simulated training dataset.",
  openGraph: {
    title: "Traceframe — Simulated Mobility Explorer",
    description: "Replay and inspect 71,138 fictional mobility observations across 766 simulated tracks.",
    type: "website",
    images: [{ url: "og.png", width: 1730, height: 909, alt: "Traceframe simulated mobility explorer" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Traceframe — Simulated Mobility Explorer",
    description: "Replay and inspect 71,138 fictional mobility observations across 766 simulated tracks.",
    images: ["og.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
