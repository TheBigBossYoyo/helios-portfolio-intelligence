import type { MetadataRoute } from "next";

/** Lets a phone install Helios to its home screen and open it full screen, like an app. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Helios",
    short_name: "Helios",
    description: "Your Trading 212 portfolio, card spending and news, read-only.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#f5f6f8",
    theme_color: "#ffffff",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
