import rss from "@astrojs/rss";
import { getAllIssues } from "../lib/issues";

export function GET(context) {
  const issues = getAllIssues();
  return rss({
    title: "fresh",
    description: "A weekly video-game news digest, picked and summarized by Claude.",
    site: context.site,
    items: issues.map((issue) => ({
      title: issue.title || issue.week,
      description: issue.headline,
      pubDate: new Date(issue.generated_at),
      link: `/fresh/w/${issue.week}`,
    })),
  });
}
