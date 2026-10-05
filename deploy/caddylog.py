"""Summarises n8n sign-in requests from Caddy's JSON access log (stdin) — no cookie values, only presence."""
import json
import sys

rows = []
for line in sys.stdin:
    try:
        e = json.loads(line)
    except ValueError:
        continue
    r = e.get("request", {})
    uri = r.get("uri", "")
    if not uri.startswith(("/rest/login", "/rest/module-settings", "/rest/logout", "/__probe")):
        continue
    h = {k.lower() for k in r.get("headers", {})}
    rh = {k.lower() for k in e.get("resp_headers", {})}
    rows.append("%.0f %-4s %-22s %s %s cookie=%s browser-id=%s set-cookie=%s ip=%s" % (
        e.get("ts", 0), r.get("method"), uri[:22], e.get("status"), r.get("proto"),
        "yes" if "cookie" in h else "NO", "yes" if "browser-id" in h else "NO",
        "yes" if "set-cookie" in rh else "-", r.get("remote_ip")))
    if uri.startswith("/rest/login") and r.get("method") == "POST":
        for c in e.get("resp_headers", {}).get("Set-Cookie", []):
            name, _, rest = c.partition("=")
            attrs = rest.split(";", 1)[1] if ";" in rest else ""
            rows.append("    Set-Cookie: %s=<%d chars>;%s" % (name, len(rest.split(";", 1)[0]), attrs))
print("\n".join(rows[-16:]) or "no sign-in requests yet")
