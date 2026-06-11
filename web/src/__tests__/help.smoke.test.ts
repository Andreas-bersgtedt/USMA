import { describe, expect, it } from "vitest";
import { CHAPTERS, CHAPTERS_BY_SLUG } from "../help/chapters";

describe("user-guide chapters", () => {
  it("loads every chapter with non-empty body", () => {
    expect(CHAPTERS.length).toBe(50);
    for (const c of CHAPTERS) {
      expect(c.slug, `slug for ${c.title}`).toMatch(/^[A-Za-z0-9_-]+$/);
      expect(c.body.length, `body for ${c.slug}`).toBeGreaterThan(50);
      // First non-blank line should be a level-1 markdown heading.
      const firstHeading = c.body
        .split("\n")
        .find((l) => l.trim().length > 0);
      expect(firstHeading, `heading for ${c.slug}`).toMatch(/^#\s/);
    }
  });

  it("indexes every slug", () => {
    expect(Object.keys(CHAPTERS_BY_SLUG).sort()).toEqual(
      CHAPTERS.map((c) => c.slug).sort(),
    );
  });

  it("contains the expected per-page chapters", () => {
    for (const slug of [
      "04-dashboard",
      "05-code-objects",
      "06-recommendations",
      "07-runbook",
      "08-delta",
      "09-run-page",
      "10-runs-history",
      "11-diff-page",
      "12-configuration",
      "19-effort",
    ]) {
      expect(CHAPTERS_BY_SLUG[slug], slug).toBeDefined();
    }
  });
});
