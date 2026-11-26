// Gives a browser its visitor cookie when it first loads a page, before the page makes any API
// call. (If parallel API calls each issued one, the last would win and could orphan a review.)
import { NextResponse, type NextRequest } from "next/server";
import { isHttps, isVisitorId, newVisitorId, VISITOR_COOKIE, visitorCookie } from "@/lib/visitor";

export function proxy(request: NextRequest) {
  const response = NextResponse.next();
  if (!isVisitorId(request.cookies.get(VISITOR_COOKIE)?.value)) {
    response.cookies.set(visitorCookie(newVisitorId(), isHttps(request.nextUrl, request.headers)));
  }
  return response;
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
