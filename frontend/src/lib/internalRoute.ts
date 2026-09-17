/**
 * Accept only an app-relative route. The pathname is deliberately stricter
 * than the query/hash: a caller may pass Windows paths as query values, but
 * no encoded or raw path separator can alter the route boundary.
 */
export function normalizeInternalRoute(route: unknown): string | null {
  if (typeof route !== "string" || !route.startsWith("/")) return null;

  const suffixStart = [route.indexOf("?"), route.indexOf("#")]
    .filter((index) => index >= 0)
    .reduce((first, index) => Math.min(first, index), route.length);
  let pathname = route.slice(0, suffixStart);

  for (;;) {
    if (pathname.startsWith("//") || pathname.includes("\\")) return null;
    if (/%(?:25)*(?:2f|5c|3f|23)/i.test(pathname)) return null;

    let decoded: string;
    try {
      decoded = decodeURIComponent(pathname);
    } catch {
      return null;
    }

    // A later decode can reveal malformed escape syntax, so keep decoding
    // until stable even after the explicit encoded-separator check above.
    if (decoded !== pathname && (decoded.includes("\\") || decoded.includes("//"))) {
      return null;
    }
    if (decoded === pathname) return route;
    pathname = decoded;
  }
}
