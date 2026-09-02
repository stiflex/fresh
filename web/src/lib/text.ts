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
