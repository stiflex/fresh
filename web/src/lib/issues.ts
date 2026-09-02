export interface ScoredItemData {
  source_id: string;
  kind: string;
  title: string;
  url: string;
  published_at: string;
  summary: string;
  authors: string[];
  meta: Record<string, unknown>;
  section: string;
  score: number;
  why: string;
  mirrors: string[];
  platforms: string[];
  release_date: string | null;
  game_name: string | null;
}

export interface SectionData {
  id: string;
  label: string;
  blurb: string;
  items: ScoredItemData[];
  subtitle?: string;
  summary?: string;
}

export interface IssueData {
  week: string;
  starts_on: string;
  ends_on: string;
  generated_at: string;
  /** Short punchy title (a few words), distinct from `headline`. Optional —
   * empty string when the classification step didn't supply one. */
  title: string;
  headline: string;
  sections: SectionData[];
  stats: Record<string, unknown>;
}

export interface UpcomingGame {
  game_name: string;
  release_date: string;
  platforms: string[];
  url: string;
  first_seen_week: string;
}

const issueModules = import.meta.glob<{ default: IssueData }>("../../../data/*.json", { eager: true });
const upcomingModule = import.meta.glob<{ default: UpcomingGame[] }>("../../../data/upcoming.json", { eager: true });

function isIssueFile(path: string): boolean {
  // data/index.json, data/seen.json, data/upcoming.json are not issues.
  return /\/data\/\d{4}-W\d{2}\.json$/.test(path);
}

export function getAllIssues(): IssueData[] {
  return Object.entries(issueModules)
    .filter(([path]) => isIssueFile(path))
    .map(([, mod]) => mod.default)
    .sort((a, b) => (a.week < b.week ? 1 : -1));
}

export function getIssue(week: string): IssueData | undefined {
  return getAllIssues().find((issue) => issue.week === week);
}

export function getUpcomingGames(): UpcomingGame[] {
  const mod = Object.values(upcomingModule)[0];
  return mod ? mod.default : [];
}
