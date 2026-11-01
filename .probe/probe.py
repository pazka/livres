"""TEMPORARY (removed before merge): fetch candidate URLs from GitHub Actions through PoliteHttpClient
(robots.txt honoured, 1 req/s/host) and print what comes back, to design adapters and record fixtures."""
import sys, urllib.parse
sys.path.insert(0, "src")
from livres.http import PoliteHttpClient, FetchError

http = PoliteHttpClient("livres/0.2 (+https://livres.hosh.it; legal French ebook catalogue)", timeout=30)
seen = set()
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    n = 2500
    if " " in line:
        line, n = line.split(" ", 1)
        n = int(n)
    host = urllib.parse.urlsplit(line).hostname
    origin = urllib.parse.urlsplit(line).scheme + "://" + host
    if origin not in seen:
        seen.add(origin)
        try:
            r = http.get(origin + "/robots.txt", allowed_hosts=(host,), max_bytes=200000) if False else None
        except Exception:
            pass
    print("=" * 100)
    print("URL", line)
    try:
        r = http.get(line, allowed_hosts=("*" + "." + ".".join(host.split(".")[-2:]), ".".join(host.split(".")[-2:])), max_bytes=30_000_000)
        print("STATUS", r.status, "FINAL", r.url, "TYPE", r.headers.get("content-type"), "LEN", len(r.body))
        print(r.body[:n].decode("utf-8", "replace"))
    except FetchError as e:
        print("FETCHERROR", e.kind, e)
    except Exception as e:
        print("ERROR", type(e).__name__, e)
