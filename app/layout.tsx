import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://ghuang12345.github.io/simulated-mobility-explorer/"),
  title: "Traceframe — Simulated Mobility Explorer",
  description: "Explore time-ordered movement observations from a simulated training dataset.",
  openGraph: {
    title: "Traceframe — Simulated Mobility Explorer",
    description: "Replay and inspect 81,408 fictional mobility observations across 814 simulated tracks, including the Delaware test cohort.",
    type: "website",
    images: [{ url: "og.png", width: 1730, height: 909, alt: "Traceframe simulated mobility explorer" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Traceframe — Simulated Mobility Explorer",
    description: "Replay and inspect 81,408 fictional mobility observations across 814 simulated tracks, including the Delaware test cohort.",
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
