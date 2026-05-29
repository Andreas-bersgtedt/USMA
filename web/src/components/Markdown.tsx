import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import { Link } from "react-router-dom";
import { useEffect, useId, useRef, useState } from "react";

interface Props {
  source: string;
}

// Lazy-import mermaid so the help bundle stays small for the (common)
// case of a chapter that has no diagrams. The first ```mermaid``` block
// pulls in the library; subsequent blocks reuse the same instance.
let _mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
function getMermaid(): Promise<typeof import("mermaid").default> {
  if (_mermaidPromise) return _mermaidPromise;
  _mermaidPromise = import("mermaid").then((mod) => {
    const m = mod.default;
    // Match the SPA's dark-on-light styling — same look as the inline
    // pre/code blocks the diagram replaces.
    m.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "default",
      fontFamily: "inherit",
    });
    return m;
  });
  return _mermaidPromise;
}

/** Renders a single ```mermaid``` code block. Falls back to raw source on parse error. */
function MermaidBlock({ source }: { source: string }) {
  const reactId = useId();
  // Mermaid requires the id to start with a letter and contain no `:`.
  const id = `mermaid-${reactId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const [svg, setSvg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMermaid()
      .then((m) => m.render(id, source))
      .then(({ svg }) => {
        if (!cancelled) setSvg(svg);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setErr(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id, source]);

  if (err) {
    return (
      <pre className="mermaid-error" title={err}>
        <code>{source}</code>
      </pre>
    );
  }
  return (
    <div
      ref={ref}
      className="mermaid"
      // eslint-disable-next-line react/no-danger
      dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
    >
      {svg ? null : <div className="muted small">Rendering diagram…</div>}
    </div>
  );
}

/**
 * Rewrites a markdown link target so that internal links to other user-guide
 * chapters (`05-code-objects.md`, `12-configuration.md#save`) become SPA
 * routes (`/help/05-code-objects`, `/help/12-configuration#save`). External
 * URLs and pure anchor links pass through unchanged.
 */
function rewriteHref(href: string): { internal: boolean; href: string } {
  if (!href) return { internal: false, href };
  // Pure anchor: keep as-is.
  if (href.startsWith("#")) return { internal: true, href };
  // External: http(s), mailto, etc.
  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return { internal: false, href };
  // Repo-root docs referenced from a user-guide chapter as `../../FILE.md`.
  // The matching chapters are bundled with slug `repo-<lower>` (see
  // `web/src/help/chapters.ts`).
  const repoMatch = href.match(/^(?:\.\.\/)+(README|QUICKSTART|CHANGELOG|SECURITY)\.md(#.*)?$/i);
  if (repoMatch) {
    const slug = `repo-${repoMatch[1].toLowerCase()}`;
    const anchor = repoMatch[2] || "";
    return { internal: true, href: `/help/${slug}${anchor}` };
  }
  // Strip a leading ./ for normalisation.
  const target = href.replace(/^\.\//, "");
  // Match `<slug>.md` or `<slug>.md#anchor`.
  const m = target.match(/^([A-Za-z0-9_-]+)\.md(#.*)?$/);
  if (m) {
    const slug = m[1];
    const anchor = m[2] || "";
    return { internal: true, href: `/help/${slug}${anchor}` };
  }
  // Anything else escapes out — let it open externally.
  return { internal: false, href };
}

export default function Markdown({ source }: Props) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSlug]}
        components={{
          a: ({ href, children, ...rest }) => {
            const { internal, href: target } = rewriteHref(href || "");
            if (internal && target.startsWith("/help/")) {
              return <Link to={target}>{children}</Link>;
            }
            if (internal) {
              return <a href={target}>{children}</a>;
            }
            return (
              <a href={target} target="_blank" rel="noreferrer noopener" {...rest}>
                {children}
              </a>
            );
          },
          code: ({ className, children, ...rest }) => {
            // react-markdown emits ```lang``` fences as <code class="language-lang">.
            // Intercept ```mermaid``` and render an SVG diagram instead of a code block.
            if (className === "language-mermaid") {
              const raw = String(children ?? "").replace(/\n$/, "");
              return <MermaidBlock source={raw} />;
            }
            return (
              <code className={className} {...rest}>
                {children}
              </code>
            );
          },
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
