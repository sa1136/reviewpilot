// Same rule as the API's parse_pr_url, so the form can say what's wrong before sending.
const PR_URL = /^https?:\/\/github\.com\/([\w.-]+)\/([\w.-]+)\/pull\/(\d+)(?:[/?#].*)?$/;

export type PrLink = { repo: string; pr: number };

export function parsePrUrl(input: string): PrLink | null {
  const match = PR_URL.exec(input.trim());
  if (!match) return null;
  return { repo: `${match[1]}/${match[2]}`, pr: Number(match[3]) };
}

/** Why a link is not a PR link, or null when it is one (or when nothing is typed yet). */
export function prUrlProblem(input: string): string | null {
  const value = input.trim();
  if (!value) return null;
  if (parsePrUrl(value)) return null;
  if (!/^https?:\/\//.test(value)) return "Paste the full link, starting with https://github.com/";
  if (!/^https?:\/\/github\.com\//.test(value)) return "Only github.com pull request links are supported";
  if (/\/issues\/\d+/.test(value)) return "That's an issue link; paste a pull request link (/pull/123)";
  return "Link should look like https://github.com/owner/repo/pull/123";
}
