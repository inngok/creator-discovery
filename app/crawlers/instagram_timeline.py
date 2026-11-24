"""Parse individual media nodes without borrowing neighboring post metrics."""
from app.core.models import Post


def extract_owned_media(payload: dict, username: str, max_posts: int = 12) -> list[Post]:
    posts = {}
    expected = username.lstrip("@").casefold()

    def walk(value, inherited_owner=""):
        if len(posts) >= max_posts:
            return
        if isinstance(value, list):
            for child in value:
                walk(child, inherited_owner)
        elif isinstance(value, dict):
            owner = value.get("owner") or value.get("user") or {}
            owner_name = owner.get("username", "") if isinstance(owner, dict) else ""
            scope = (owner_name or value.get("username") or inherited_owner).casefold()
            code = value.get("shortcode") or value.get("code")
            if code and scope == expected and any(k in value for k in ("taken_at", "taken_at_timestamp", "media_type", "__typename")):
                caption = value.get("caption")
                if isinstance(caption, dict):
                    caption = caption.get("text", "")
                if not isinstance(caption, str):
                    edges = (value.get("edge_media_to_caption") or {}).get("edges", [])
                    caption = " ".join(e.get("node", {}).get("text", "") for e in edges)
                def metric(direct, edge):
                    raw = value.get(direct)
                    if raw is None:
                        raw = (value.get(edge) or {}).get("count")
                    return int(raw) if raw is not None else None
                posts[str(code)] = Post(platformPostId=str(code), postUrl=f"https://www.instagram.com/p/{code}/",
                    caption=caption, likes=metric("like_count", "edge_media_preview_like"),
                    comments=metric("comment_count", "edge_media_to_comment"),
                    views=value.get("play_count", value.get("video_view_count")), shares=None,
                    postedAt=value.get("taken_at", value.get("taken_at_timestamp")))
                return
            for key, child in value.items():
                if key not in {"suggested_users", "edge_web_discover_media", "recommended_users"}:
                    walk(child, scope)
    walk(payload)
    return list(posts.values())
