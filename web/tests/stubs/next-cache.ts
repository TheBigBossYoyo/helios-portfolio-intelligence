// Test stub for `next/cache`. The real `revalidatePath` requires an active Next request
// store and throws outside one. What the action tests care about is the request the action
// sends to the API, not Next's own cache bookkeeping, so this records the calls instead.
export const revalidatedPaths: string[] = [];

export function revalidatePath(path: string): void {
  revalidatedPaths.push(path);
}
