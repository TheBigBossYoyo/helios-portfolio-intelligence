import "@testing-library/jest-dom/vitest";

/**
 * Recharts sizes itself from its container. jsdom reports every element as 0x0 and never fires a
 * ResizeObserver, so a ResponsiveContainer would render an empty box and every chart assertion
 * would pass vacuously. Give it a deterministic viewport instead.
 */
const CHART_WIDTH = 800;
const CHART_HEIGHT = 300;

for (const [property, value] of [
  ["clientWidth", CHART_WIDTH],
  ["offsetWidth", CHART_WIDTH],
  ["clientHeight", CHART_HEIGHT],
  ["offsetHeight", CHART_HEIGHT],
] as const) {
  Object.defineProperty(HTMLElement.prototype, property, {
    configurable: true,
    value,
  });
}

HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
  return {
    width: CHART_WIDTH,
    height: CHART_HEIGHT,
    top: 0,
    left: 0,
    right: CHART_WIDTH,
    bottom: CHART_HEIGHT,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect;
};

class ResizeObserverStub {
  private readonly callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe(target: Element) {
    // Fire immediately so recharts learns its size in the same tick as the render.
    this.callback(
      [
        {
          target,
          contentRect: target.getBoundingClientRect(),
          borderBoxSize: [{ inlineSize: CHART_WIDTH, blockSize: CHART_HEIGHT }],
          contentBoxSize: [{ inlineSize: CHART_WIDTH, blockSize: CHART_HEIGHT }],
          devicePixelContentBoxSize: [{ inlineSize: CHART_WIDTH, blockSize: CHART_HEIGHT }],
        } as unknown as ResizeObserverEntry,
      ],
      this as unknown as ResizeObserver,
    );
  }

  unobserve() {}

  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
