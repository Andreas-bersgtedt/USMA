import { Link } from "react-router-dom";

interface Props {
  /** Chapter slug, e.g. "04-dashboard". */
  slug: string;
  /** Optional friendly text override; defaults to a `?` icon. */
  label?: string;
}

/**
 * Inline "Help on this page" link. Renders a small `?` button that
 * navigates to the matching user-guide chapter inside the SPA.
 */
export default function HelpLink({ slug, label }: Props) {
  return (
    <Link
      to={`/help/${slug}`}
      className="help-link"
      title="Open the user-guide chapter for this page"
      aria-label="Help on this page"
    >
      {label ?? "?"}
    </Link>
  );
}
