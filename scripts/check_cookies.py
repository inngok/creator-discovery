import json
import datetime

def check_state(path, domain_filter):
    d = json.load(open(path))
    cookies = [c for c in d.get("cookies", []) if domain_filter in c.get("domain", "")]
    now = datetime.datetime.now()
    print(f"\n=== {path} | domain={domain_filter} | count={len(cookies)} ===")
    expired = []
    for c in cookies:
        exp = c.get("expires", -1)
        if exp > 0:
            dt = datetime.datetime.fromtimestamp(exp)
            status = "EXPIRED" if dt < now else f"ok (expires {dt.strftime('%Y-%m-%d %H:%M')})"
            if dt < now:
                expired.append(c["name"])
        else:
            status = "session"
        print(f"  {c['name']:40} {status}")
    if expired:
        print(f"\n  !! EXPIRED cookies: {expired}")
    else:
        print(f"\n  All cookies valid or session-only.")

check_state("c:/Crawl data/data/playwright_state.json", "tiktok")
check_state("c:/Crawl data/data/playwright_fb_state.json", "facebook")
