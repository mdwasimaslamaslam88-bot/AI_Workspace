import type { MessageCitation } from "@work-station/shared";
import { describe, expect, it } from "vitest";

import { citationLocation, citationSourceLabel } from "../src/chat/citations";

const citation: MessageCitation = {
  asset_id: "11111111-1111-4111-8111-111111111111",
  position: 0,
  state: "active",
  original_filename: "owner-notes.pdf",
  page_number: 4,
  row_start: null,
  row_end: null,
  section: "Architecture",
  excerpt: "A bounded private excerpt.",
};

describe("mobile citation presentation", () => {
  it("renders only safe source metadata supplied by the API", () => {
    expect(citationSourceLabel(citation)).toBe("owner-notes.pdf");
    expect(citationLocation(citation)).toBe("page 4 · Architecture");
    expect(
      citationLocation({
        ...citation,
        page_number: null,
        row_start: 7,
        row_end: 9,
        section: null,
      }),
    ).toBe("rows 7–9");
    expect(citationSourceLabel({ ...citation, state: "deleted" })).toBe(
      "Deleted private source",
    );
  });
});
