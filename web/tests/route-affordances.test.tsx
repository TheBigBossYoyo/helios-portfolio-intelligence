import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import GlobalError from "@/app/error";
import NotFound from "@/app/not-found";
import { Pager } from "@/components/pager";

describe("GlobalError", () => {
  it("never renders the raw error message or stack", () => {
    const error = Object.assign(
      new Error("secret internal detail: postgres://user:pass@host/db"),
      { stack: "at somePrivateFunction (/app/secret/path.ts:42:9)" },
    );

    render(<GlobalError error={error} reset={() => {}} />);

    expect(screen.queryByText(/secret internal detail/)).not.toBeInTheDocument();
    expect(screen.queryByText(/somePrivateFunction/)).not.toBeInTheDocument();
    expect(screen.queryByText(/postgres:\/\//)).not.toBeInTheDocument();
  });

  it("shows the digest as a safe reference when one is present", () => {
    const error = Object.assign(new Error("boom"), { digest: "abc123" });

    render(<GlobalError error={error} reset={() => {}} />);

    expect(screen.getByText(/abc123/)).toBeInTheDocument();
  });

  it("still explains the failure in plain terms when there is no digest", () => {
    const error = new Error("boom");

    render(<GlobalError error={error} reset={() => {}} />);

    expect(screen.getByText(/API may be down/)).toBeInTheDocument();
  });

  it("calls reset when the retry control is used", async () => {
    const reset = vi.fn();
    const error = new Error("boom");
    const { getByRole } = render(<GlobalError error={error} reset={reset} />);

    getByRole("button", { name: "Retry" }).click();

    expect(reset).toHaveBeenCalledOnce();
  });
});

describe("NotFound", () => {
  it("links back to the overview", () => {
    render(<NotFound />);

    const link = screen.getByRole("link", { name: /Back to overview/ });
    expect(link).toHaveAttribute("href", "/");
  });
});

describe("Pager", () => {
  it("renders nothing when there is only one page", () => {
    const { container } = render(<Pager basePath="/news" page={1} pageCount={1} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("disables Previous on the first page and enables Next", () => {
    render(<Pager basePath="/news" page={1} pageCount={3} />);

    expect(screen.getByText("← Previous")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("link", { name: "Next →" })).toHaveAttribute("href", "/news?page=2");
  });

  it("disables Next on the last page and enables Previous", () => {
    render(<Pager basePath="/news" page={3} pageCount={3} />);

    expect(screen.getByText("Next →")).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("link", { name: "← Previous" })).toHaveAttribute(
      "href",
      "/news?page=2",
    );
  });

  it("omits the page param for page 1 so the base URL stays canonical", () => {
    render(<Pager basePath="/news" page={2} pageCount={3} />);

    expect(screen.getByRole("link", { name: "← Previous" })).toHaveAttribute("href", "/news");
  });

  it("preserves extra params like the news ticker filter across page links", () => {
    render(
      <Pager
        basePath="/news"
        extraParams={{ ticker: "AAPL_US_EQ" }}
        page={1}
        pageCount={2}
      />,
    );

    expect(screen.getByRole("link", { name: "Next →" })).toHaveAttribute(
      "href",
      "/news?ticker=AAPL_US_EQ&page=2",
    );
  });

  it("shows the current page and total", () => {
    render(<Pager basePath="/holdings" page={2} pageCount={4} />);

    expect(screen.getByText("Page 2 of 4")).toBeInTheDocument();
  });
});
