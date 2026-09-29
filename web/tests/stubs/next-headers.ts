/**
 * `headers()` needs an active Next request. Tests set the incoming request's headers here,
 * e.g. to play a paired phone arriving through the gateway.
 */
export const requestHeaders = new Headers();

export async function headers(): Promise<Headers> {
  return requestHeaders;
}
