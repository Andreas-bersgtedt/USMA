// Raw markdown imports — bundled at build time via Vite's `?raw`. The same
// source files (`docs/user-guide/*.md`) are also rendered by GitHub when
// browsing the repo, so there is exactly one canonical version of each
// chapter.
//
// Slugs are derived from the file basename (without extension); the in-app
// route is `/help/<slug>`.

import readme from "../../../docs/user-guide/README.md?raw";
import repoReadme from "../../../README.md?raw";
import repoQuickstart from "../../../QUICKSTART.md?raw";
import repoChangelog from "../../../CHANGELOG.md?raw";
import repoSecurity from "../../../SECURITY.md?raw";
import gettingStarted from "../../../docs/user-guide/01-getting-started.md?raw";
import modes from "../../../docs/user-guide/02-modes.md?raw";
import runPicker from "../../../docs/user-guide/03-run-picker.md?raw";
import dashboard from "../../../docs/user-guide/04-dashboard.md?raw";
import codeObjects from "../../../docs/user-guide/05-code-objects.md?raw";
import recommendations from "../../../docs/user-guide/06-recommendations.md?raw";
import runbook from "../../../docs/user-guide/07-runbook.md?raw";
import delta from "../../../docs/user-guide/08-delta.md?raw";
import runPage from "../../../docs/user-guide/09-run-page.md?raw";
import runsHistory from "../../../docs/user-guide/10-runs-history.md?raw";
import diffPage from "../../../docs/user-guide/11-diff-page.md?raw";
import configuration from "../../../docs/user-guide/12-configuration.md?raw";
import troubleshooting from "../../../docs/user-guide/13-troubleshooting.md?raw";
import security from "../../../docs/user-guide/14-security.md?raw";
import faq from "../../../docs/user-guide/15-faq.md?raw";
import accessSecurity from "../../../docs/user-guide/17-access-and-security.md?raw";
import cost from "../../../docs/user-guide/18-cost.md?raw";
import effortChapter from "../../../docs/user-guide/19-effort.md?raw";
import overview from "../../../docs/user-guide/16-overview.md?raw";
import dataFactory from "../../../docs/user-guide/20-data-factory.md?raw";
import databricks from "../../../docs/user-guide/21-databricks.md?raw";
import databricksAws from "../../../docs/user-guide/21a-databricks-aws.md?raw";
import bigquery from "../../../docs/user-guide/22-bigquery.md?raw";
import snowflake from "../../../docs/user-guide/23-snowflake.md?raw";
import addingASource from "../../../docs/user-guide/99-adding-a-source.md?raw";

export type ChapterSection =
  | "Orientation"
  | "Read-only pages"
  | "Control-plane pages"
  | "Reference"
  | "Sources";

export interface Chapter {
  slug: string;
  title: string;
  short: string;
  section: ChapterSection;
  body: string;
  /** When true, chapter is reachable by route but hidden from the sidebar
   *  and the prev/next pager. Used for repo-root docs (README, QUICKSTART,
   *  CHANGELOG, SECURITY) bundled so internal `../../X.md` links resolve
   *  inside the SPA. */
  hidden?: boolean;
}

export const CHAPTERS: Chapter[] = [
  {
    slug: "README",
    title: "Overview",
    short: "Overview",
    section: "Orientation",
    body: readme,
  },
  {
    slug: "01-getting-started",
    title: "01. Getting started",
    short: "Getting started",
    section: "Orientation",
    body: gettingStarted,
  },
  {
    slug: "02-modes",
    title: "02. Static vs. control-plane mode",
    short: "Modes",
    section: "Orientation",
    body: modes,
  },
  {
    slug: "03-run-picker",
    title: "03. The run picker",
    short: "Run picker",
    section: "Orientation",
    body: runPicker,
  },
  {
    slug: "04-dashboard",
    title: "04. Dashboard",
    short: "Dashboard",
    section: "Read-only pages",
    body: dashboard,
  },
  {
    slug: "05-code-objects",
    title: "05. Code objects",
    short: "Code objects",
    section: "Read-only pages",
    body: codeObjects,
  },
  {
    slug: "06-recommendations",
    title: "06. Recommendations",
    short: "Recommendations",
    section: "Read-only pages",
    body: recommendations,
  },
  {
    slug: "07-runbook",
    title: "07. Runbook",
    short: "Runbook",
    section: "Read-only pages",
    body: runbook,
  },
  {
    slug: "08-delta",
    title: "08. Delta",
    short: "Delta",
    section: "Read-only pages",
    body: delta,
  },
  {
    slug: "09-run-page",
    title: "09. Run",
    short: "Run",
    section: "Control-plane pages",
    body: runPage,
  },
  {
    slug: "10-runs-history",
    title: "10. Runs history",
    short: "Runs history",
    section: "Control-plane pages",
    body: runsHistory,
  },
  {
    slug: "11-diff-page",
    title: "11. Diff",
    short: "Diff",
    section: "Control-plane pages",
    body: diffPage,
  },
  {
    slug: "12-configuration",
    title: "12. Configuration",
    short: "Configuration",
    section: "Control-plane pages",
    body: configuration,
  },
  {
    slug: "16-overview",
    title: "16. Estate overview",
    short: "Estate overview",
    section: "Control-plane pages",
    body: overview,
  },
  {
    slug: "13-troubleshooting",
    title: "13. Troubleshooting",
    short: "Troubleshooting",
    section: "Reference",
    body: troubleshooting,
  },
  {
    slug: "14-security",
    title: "14. Security & deployment",
    short: "Security",
    section: "Reference",
    body: security,
  },
  {
    slug: "17-access-and-security",
    title: "17. Tool access & security implications",
    short: "Access & security",
    section: "Reference",
    body: accessSecurity,
  },
  {
    slug: "18-cost",
    title: "18. Cost of running the analyzer",
    short: "Cost",
    section: "Reference",
    body: cost,
  },
  {
    slug: "19-effort",
    title: "19. Effort estimates & rate card",
    short: "Effort",
    section: "Reference",
    body: effortChapter,
  },
  {
    slug: "15-faq",
    title: "15. FAQ",
    short: "FAQ",
    section: "Reference",
    body: faq,
  },
  {
    slug: "20-data-factory",
    title: "20. Data Factory (ADF) sources",
    short: "Data Factory",
    section: "Sources",
    body: dataFactory,
  },
  {
    slug: "21-databricks",
    title: "21. Databricks sources",
    short: "Databricks",
    section: "Sources",
    body: databricks,
  },
  {
    slug: "21a-databricks-aws",
    title: "21a. Databricks on AWS (alpha)",
    short: "Databricks on AWS",
    section: "Sources",
    body: databricksAws,
  },
  {
    slug: "22-bigquery",
    title: "22. Google BigQuery sources",
    short: "BigQuery",
    section: "Sources",
    body: bigquery,
  },
  {
    slug: "23-snowflake",
    title: "23. Snowflake sources",
    short: "Snowflake",
    section: "Sources",
    body: snowflake,
  },
  {
    slug: "99-adding-a-source",
    title: "99. Adding a new source",
    short: "Adding a source",
    section: "Reference",
    body: addingASource,
  },
  // Repo-root docs, bundled so `../../README.md` style links inside the user
  // guide resolve to in-app routes. Hidden from the sidebar / pager.
  {
    slug: "repo-readme",
    title: "Project README",
    short: "Project README",
    section: "Reference",
    body: repoReadme,
    hidden: true,
  },
  {
    slug: "repo-quickstart",
    title: "QUICKSTART",
    short: "QUICKSTART",
    section: "Reference",
    body: repoQuickstart,
    hidden: true,
  },
  {
    slug: "repo-changelog",
    title: "CHANGELOG",
    short: "CHANGELOG",
    section: "Reference",
    body: repoChangelog,
    hidden: true,
  },
  {
    slug: "repo-security",
    title: "Security policy",
    short: "Security policy",
    section: "Reference",
    body: repoSecurity,
    hidden: true,
  },
];

export const VISIBLE_CHAPTERS: Chapter[] = CHAPTERS.filter((c) => !c.hidden);

export const CHAPTERS_BY_SLUG: Record<string, Chapter> = Object.fromEntries(
  CHAPTERS.map((c) => [c.slug, c]),
);

export const SECTIONS: ChapterSection[] = [
  "Orientation",
  "Read-only pages",
  "Control-plane pages",
  "Sources",
  "Reference",
];
