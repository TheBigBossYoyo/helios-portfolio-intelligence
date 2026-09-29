import "server-only";

import { headers } from "next/headers";

/**
 * Whether this request came from a paired phone through the phone gateway.
 *
 * The gateway (helios.gateway) always sets `x-helios-remote: 1` and strips any value a phone
 * sends, and it is the only way in from the network: the dashboard itself listens on loopback.
 * A request without the header is the computer's own window.
 */
export async function isRemoteRequest(): Promise<boolean> {
  return (await headers()).get("x-helios-remote") === "1";
}
