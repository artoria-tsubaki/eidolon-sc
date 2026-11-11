// Gives every page the site's Content-Security-Policy as it is served. It is set here rather
// than in next.config, which is fixed when the image is built: where the stands are kept is
// only known where it runs. Pages rendered ahead of time stay so.
// Old and unknown settings addresses (`/settings?tab=…`, `/memories`, `/settings/<xxx>`) and the
// old gifts page, the old tasks subpage and unknown play addresses (`/gifts`, `/play/tasks`,
// `/play/<xxx>`) are redirected here to the new paths, before any page loads; a redirect leaves
// no entry in the history, so going back does not land on the old address again.

import { NextResponse, type NextRequest } from "next/server";
import { contentSecurityPolicy } from "@/lib/contentSecurityPolicy";
import { playRedirect } from "@/lib/playRoute";
import { settingsRedirect } from "@/lib/settingsCategory";

export function proxy(request: NextRequest) {
  const address = request.nextUrl.pathname + request.nextUrl.search;
  const to = settingsRedirect(address) ?? playRedirect(address);
  const response = to ? NextResponse.redirect(new URL(to, request.url)) : NextResponse.next();
  response.headers.set(
    "Content-Security-Policy",
    contentSecurityPolicy(
      process.env.EIDOLON_STAND_ASSETS_URL,
      process.env.NODE_ENV === "development",
    ),
  );
  return response;
}

export const config = {
  // Not the API or Next's own files; what is served from public/ gets it too, harmlessly.
  matcher: ["/((?!api/|_next/).*)"],
};
