"""Read structured evidence delivered in public Meta pages, without executing JS."""
import json
import re
from urllib.parse import urlsplit

from app.core.models import Post


def embedded_payloads(soup):
    for script in soup.find_all("script"):
        text = script.string or ""
        try:
            if script.get("type") == "application/json":
                yield json.loads(text)
            else:
                # Facebook's public Page Plugin wraps JSON in ServerJS.handle(...).
                for match in re.finditer(r"\.handle\(\s*(?=\{)", text):
                    value, _ = json.JSONDecoder().raw_decode(text[match.end():])
                    yield value
        except (ValueError, TypeError):
            continue


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


def instagram_profile(soup, username):
    best = {}
    for payload in embedded_payloads(soup):
        for node in dictionaries(payload):
            if str(node.get("username", "")).casefold() != username.lstrip("@").casefold():
                continue
            if "follower_count" in node or "edge_followed_by" in node:
                if len(node) > len(best):
                    best = node
    return best


def facebook_plugin(soup):
    for payload in embedded_payloads(soup):
        for node in dictionaries(payload):
            if node.get("pageID") and isinstance(node.get("timelinePosts"), list):
                return node
    return {}


def facebook_structured_posts(soup, username, max_posts=12):
    """Only timeline units from the requested actor; prune comments/shared stories."""
    posts = {}
    expected = username.lstrip("@").casefold()
    excluded = {"attached_story", "interesting_top_level_comments", "comments", "top_reactions"}

    def scoped_nodes(value):
        if isinstance(value, dict):
            yield value
            for key, child in value.items():
                if key not in excluded:
                    yield from scoped_nodes(child)
        elif isinstance(value, list):
            for child in value:
                yield from scoped_nodes(child)

    def walk(value):
        if len(posts) >= max_posts:
            return
        if isinstance(value, dict):
            actors = value.get("actors") or []
            owns = any(isinstance(actor, dict) and (
                str(actor.get("id", "")).casefold() == expected or
                urlsplit(actor.get("url") or "").path.strip("/").casefold() == expected)
                for actor in actors)
            if value.get("post_id") and value.get("creation_time") and owns:
                caption = ""
                metrics = {"likes": None, "comments": None, "shares": None, "views": None}
                keys = {"reaction_count": "likes", "total_comment_count": "comments",
                        "share_count": "shares", "video_view_count": "views"}
                for node in scoped_nodes(value):
                    comments = (node.get("comment_rendering_instance") or {}).get("comments")
                    if isinstance(comments, dict) and type(comments.get("total_count")) is int:
                        metrics["comments"] = comments["total_count"]
                    message = node.get("message")
                    if isinstance(message, dict) and isinstance(message.get("text"), str) and not caption:
                        caption = message["text"]
                    for key, target in keys.items():
                        raw = node.get(key)
                        if isinstance(raw, dict):
                            raw = raw.get("count")
                        if type(raw) is int and metrics[target] is None:
                            metrics[target] = raw
                pid = str(value["post_id"])
                link = value.get("permalink_url") or f"https://www.facebook.com/{username}/posts/{pid}"
                if caption:
                    posts[pid] = Post(platformPostId=pid, postUrl=link, caption=caption,
                                      postedAt=value["creation_time"], **metrics)
                return
            for key, child in value.items():
                if key not in excluded:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for payload in embedded_payloads(soup):
        walk(payload)
    return list(posts.values())


def fetch_facebook_public_html(username):
    """Read Facebook's public crawler representation, with a bounded timeout.

    No account session, paid provider, or private endpoint is involved. Facebook
    can choose to restrict this representation independently of the browser view.
    """
    import httpx
    from bs4 import BeautifulSoup
    response = httpx.get(f"https://www.facebook.com/{username}/?sk=posts",
                         headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"},
                         follow_redirects=True, timeout=15)
    if response.status_code != 200:
        return None
    return BeautifulSoup(response.text, "html.parser")


def enrich_from_plugin(posts, plugin):
    """Join by timestamp AND exact caption; the plugin does not expose post IDs."""
    from app.core.post_dates import normalize_post_date
    for post in posts:
        for row in plugin.get("timelinePosts", []):
            if (post.postedAt and post.postedAt == normalize_post_date(row.get("createdTime"))
                    and post.caption.strip() == (row.get("message") or "").strip()):
                for field, key in (("likes", "reactionCount"), ("comments", "commentCount"), ("shares", "shareCount")):
                    if type(row.get(key)) is int:
                        setattr(post, field, row[key])
                if row.get("photoURL"):
                    post.coverUrl = row["photoURL"]
                break
    return posts
