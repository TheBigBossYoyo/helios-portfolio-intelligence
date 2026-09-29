/**
 * Shared class vocabulary for controls. Components compose these rather than restating a dozen
 * utilities, so a button looks the same on every page and a restyle happens in one place.
 * Every colour is a theme token from app/globals.css.
 */

const BUTTON_BASE =
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-3.5 py-2 text-sm font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

export const BUTTON = {
  /** The one main action in a group. */
  primary: `${BUTTON_BASE} bg-accent text-accent-contrast shadow-card hover:bg-accent-hover`,
  /** Everything else. */
  secondary: `${BUTTON_BASE} border border-border bg-surface text-ink shadow-card hover:bg-surface-2 hover:border-border-strong`,
  /** Quiet, for cancel and inline links styled as buttons. */
  ghost: `${BUTTON_BASE} text-ink-2 hover:bg-surface-3 hover:text-ink`,
  /** Irreversible or costly: the confirming click. */
  danger: `${BUTTON_BASE} bg-negative text-white shadow-card hover:opacity-90`,
  /** Compact variant for dense rows. */
  small: "px-2.5 py-1.5 text-xs",
} as const;

export const FIELD =
  "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink shadow-card placeholder:text-ink-4 transition-colors hover:border-border-strong focus:border-accent focus:outline-none focus:ring-3 focus:ring-accent-soft disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-ink-3";

export const LABEL = "flex flex-col gap-1.5 text-sm font-medium text-ink";

export const HELP = "text-xs leading-relaxed text-ink-3";

export const CARD = "rounded-2xl border border-border bg-surface shadow-card";

export const LINK = "font-medium text-accent hover:text-accent-hover hover:underline underline-offset-4";

export const EYEBROW = "text-xs font-medium text-ink-3";

/**
 * A segmented control's track. On a phone it scrolls sideways rather than wrapping onto a
 * second line; its items never shrink.
 */
export const SEGMENTED =
  "no-scrollbar flex max-w-full gap-1 overflow-x-auto rounded-xl bg-surface-3 p-1 *:shrink-0";
