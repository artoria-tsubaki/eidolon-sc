// The site's Content-Security-Policy (ADR-0017): only this site's scripts run, and the page
// talks to no one but this site, so a script slipped in through XSS has a hard time
// sending the user's own API keys elsewhere. Inline scripts are allowed, since a nonce
// would make every page render on each request. The one other place pages load from is
// where the stands are kept (`EIDOLON_STAND_ASSETS_URL`): their outfit lists and Spine
// files, thumbnails and the landing page's greetings. Images may also be `data:` and `blob:`:
// the share card is turned into a PNG through them, in the page. Media may be `blob:` too: her
// words read aloud are played from the audio the page was sent (GLOSSARY.md「朗读」).

/** The policy, with the stands' address if it is one. `development` allows `eval`, which
 * React's development build uses to rebuild call stacks. Production never does. */
export function contentSecurityPolicy(
  standAssetsUrl: string | undefined,
  development = false,
): string {
  const stands = origin(standAssetsUrl);
  const standsSource = stands ? ` ${stands}` : "";
  const scripts = development ? " 'unsafe-eval'" : "";
  return [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${scripts}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob:${standsSource}`,
    `media-src 'self' blob:${standsSource}`,
    "font-src 'self'",
    `connect-src 'self'${standsSource}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/** The origin of an http(s) address; null for anything else. */
function origin(url: string | undefined): string | null {
  const parsed = URL.parse(url?.trim() ?? "");
  return parsed?.protocol === "https:" || parsed?.protocol === "http:" ? parsed.origin : null;
}
