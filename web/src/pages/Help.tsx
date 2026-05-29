import { useEffect } from "react";
import { Link, NavLink, useParams, useLocation } from "react-router-dom";
import Markdown from "../components/Markdown";
import {
  CHAPTERS_BY_SLUG,
  VISIBLE_CHAPTERS,
  SECTIONS,
  type ChapterSection,
} from "../help/chapters";

export default function Help() {
  const params = useParams<{ slug?: string }>();
  const location = useLocation();
  const slug = params.slug ?? "README";
  const chapter = CHAPTERS_BY_SLUG[slug];

  // Scroll to a `#anchor` on mount / chapter change.
  useEffect(() => {
    if (!chapter) return;
    if (location.hash) {
      const id = decodeURIComponent(location.hash.slice(1));
      // Defer to allow markdown render.
      window.setTimeout(() => {
        const el = document.getElementById(id);
        if (el) el.scrollIntoView({ behavior: "auto", block: "start" });
      }, 0);
    } else {
      window.scrollTo({ top: 0, left: 0 });
    }
  }, [chapter, location.hash]);

  return (
    <div className="help-layout">
      <aside className="help-rail">
        <div className="help-rail-title">User guide</div>
        {SECTIONS.map((section) => (
          <HelpRailSection key={section} section={section} activeSlug={slug} />
        ))}
      </aside>
      <article className="help-article">
        {chapter ? (
          <>
            <Markdown source={chapter.body} />
            <ChapterFooter slug={slug} />
          </>
        ) : (
          <div className="empty">
            <p>No chapter found for <code>{slug}</code>.</p>
            <p>
              <Link to="/help">Back to the user-guide table of contents</Link>.
            </p>
          </div>
        )}
      </article>
    </div>
  );
}

function HelpRailSection({
  section,
  activeSlug,
}: {
  section: ChapterSection;
  activeSlug: string;
}) {
  const items = VISIBLE_CHAPTERS.filter((c) => c.section === section);
  return (
    <div className="help-rail-section">
      <div className="help-rail-section-title">{section}</div>
      <ul>
        {items.map((c) => (
          <li key={c.slug}>
            <NavLink
              to={`/help/${c.slug}`}
              className={c.slug === activeSlug ? "active" : ""}
              end
            >
              {c.short}
            </NavLink>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChapterFooter({ slug }: { slug: string }) {
  const idx = VISIBLE_CHAPTERS.findIndex((c) => c.slug === slug);
  if (idx < 0) return null;
  const prev = idx > 0 ? VISIBLE_CHAPTERS[idx - 1] : null;
  const next = idx < VISIBLE_CHAPTERS.length - 1 ? VISIBLE_CHAPTERS[idx + 1] : null;
  return (
    <nav className="help-pager">
      <div>
        {prev && (
          <Link to={`/help/${prev.slug}`}>&larr; {prev.short}</Link>
        )}
      </div>
      <div style={{ textAlign: "right" }}>
        {next && (
          <Link to={`/help/${next.slug}`}>{next.short} &rarr;</Link>
        )}
      </div>
    </nav>
  );
}
