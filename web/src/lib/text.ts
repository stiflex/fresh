const ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  "#39": "'",
  apos: "'",
  nbsp: " ",
};

function decodeEntities(text: string): string {
  return text.replace(/&(#\d+|[a-zA-Z]+);/g, (match, code) => {
    if (code in ENTITIES) return ENTITIES[code];
    if (code.startsWith("#")) {
      const codePoint = Number(code.slice(1));
      return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
    }
    return match;
  });
}

/** Strip HTML tags from a raw feed summary, decode entities, collapse
 * whitespace, and truncate to a whole-word boundary. Feed summaries carry
 * arbitrary markup (images, links, truncated paragraphs) — this is a
 * best-effort cleanup for display, not a full HTML sanitizer. */
export function stripHtml(html: string, maxLength = 220): string {
  const withoutTags = html.replace(/<[^>]+>/g, " ");
  const decoded = decodeEntities(withoutTags);
  const collapsed = decoded.replace(/\s+/g, " ").trim();

  if (collapsed.length <= maxLength) return collapsed;
  const truncated = collapsed.slice(0, maxLength);
  const lastSpace = truncated.lastIndexOf(" ");
  return `${truncated.slice(0, lastSpace > 0 ? lastSpace : maxLength)}…`;
}

/** Short outlet label for a mirror link, e.g. "eurogamer.net" from
 * "https://www.eurogamer.net/story" — falls back to the raw url if it
 * doesn't parse (never throws on a malformed value). */
export function hostLabel(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
