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
    if not uri.startswith(("/rest/", "/__probe")):
        continue
    sets = e.get("resp_headers", {}).get("Set-Cookie", [])
    if not (uri.startswith(("/rest/login", "/rest/module-settings", "/rest/logout", "/__probe")) or sets):
        continue
    hdrs = {k.lower(): v for k, v in r.get("headers", {}).items()}
    h = set(hdrs)
    names = sorted(p.split("=", 1)[0].strip() for c in hdrs.get("cookie", []) for p in c.split(";") if "=" in p)
    rh = {k.lower() for k in e.get("resp_headers", {})}
    rows.append("%.0f %-4s %-22s %s %s cookie=%s browser-id=%s set-cookie=%s ip=%s" % (
        e.get("ts", 0), r.get("method"), uri[:22], e.get("status"), r.get("proto"),
        ",".join(names) or "NO", "yes" if "browser-id" in h else "NO",
        "yes" if "set-cookie" in rh else "-", r.get("remote_ip")))
    if True:
        for c in sets:
            name, _, rest = c.partition("=")
            attrs = rest.split(";", 1)[1] if ";" in rest else ""
            rows.append("    Set-Cookie: %s=<%d chars>;%s" % (name, len(rest.split(";", 1)[0]), attrs))
print("\n".join(rows[-22:]) or "no sign-in requests yet")
