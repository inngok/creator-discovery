"""
Suy luận khu vực (tỉnh/thành) của creator từ tên hiển thị, username và bio.

Ví dụ: "Ăn Sập Sài Gòn" -> "TP. Hồ Chí Minh", "hanoifoodie" -> "Hà Nội".
Không tìm thấy thì trả về DEFAULT_LOCATION ("Vietnam") như hành vi cũ.

Thứ tự ưu tiên nguồn: displayName > username > bio
(caption bài đăng KHÔNG dùng vì creator du lịch nhắc rất nhiều địa danh).
"""
import re
import unicodedata
from typing import Iterable, Optional

DEFAULT_LOCATION = "Vietnam"

# (Tên chuẩn, các alias đã bỏ dấu & viết thường, alias dính liền dùng cho username)
# Alias dính liền chỉ dùng các chuỗi đủ đặc trưng để tránh match nhầm.
_LOCATIONS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("TP. Hồ Chí Minh",
     ("sai gon", "tp hcm", "tp.hcm", "tphcm", "hcm", "ho chi minh", "hochiminh", "hcmc", "saigon"),
     ("saigon", "hochiminh", "tphcm", "hcmc")),
    ("Hà Nội", ("ha noi", "hanoi", "hn"), ("hanoi",)),
    ("Đà Nẵng", ("da nang", "danang"), ("danang",)),
    ("Hải Phòng", ("hai phong", "haiphong"), ("haiphong",)),
    ("Cần Thơ", ("can tho", "cantho"), ("cantho",)),
    ("Lâm Đồng", ("da lat", "dalat", "lam dong"), ("dalat",)),
    ("Khánh Hòa", ("nha trang", "nhatrang", "khanh hoa"), ("nhatrang",)),
    ("Bà Rịa - Vũng Tàu", ("vung tau", "vungtau", "ba ria"), ("vungtau",)),
    # Không dùng "hue" đơn lẻ: dễ trùng tên người (Huệ, Thu Huế...).
    ("Thừa Thiên Huế", ("xu hue", "co do hue", "tp hue", "thanh pho hue", "hue city"), ("xuhue", "codohue")),
    ("Quảng Ninh", ("ha long", "halong", "quang ninh"), ("halong", "quangninh")),
    ("Kiên Giang", ("phu quoc", "phuquoc", "kien giang"), ("phuquoc",)),
    ("Quảng Nam", ("hoi an", "hoian", "quang nam"), ("hoian",)),
    ("Bình Dương", ("binh duong", "binhduong"), ("binhduong",)),
    ("Đồng Nai", ("dong nai", "bien hoa"), ("dongnai", "bienhoa")),
    ("Nghệ An", ("nghe an", "xu nghe"), ("nghean",)),
    ("Bình Định", ("quy nhon", "binh dinh"), ("quynhon",)),
    ("Lào Cai", ("sa pa", "sapa", "lao cai"), ("sapa",)),
    ("Ninh Bình", ("ninh binh", "ninhbinh"), ("ninhbinh",)),
    ("Thanh Hóa", ("thanh hoa", "xu thanh"), ("thanhhoa",)),
    ("Bình Thuận", ("phan thiet", "mui ne", "binh thuan"), ("phanthiet", "muine")),
    ("Đắk Lắk", ("buon ma thuot", "dak lak", "bmt"), ("buonmathuot",)),
    ("Miền Tây", ("mien tay",), ("mientay",)),
]


def _strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _normalize(text: str) -> str:
    """Bỏ dấu, viết thường, gom ký tự phân cách (_ . - | #) thành khoảng trắng."""
    text = _strip_accents(text or "").lower()
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    return f" {text.strip()} "


_PATTERNS = [
    (
        name,
        [re.compile(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])") for a in aliases],
        compact,
    )
    for name, aliases, compact in _LOCATIONS
]


def _match(text: str, allow_compact: bool) -> Optional[str]:
    if not text:
        return None
    norm = _normalize(text)
    for name, patterns, _ in _PATTERNS:
        if any(p.search(norm) for p in patterns):
            return name
    if allow_compact:
        squashed = re.sub(r"[^a-z0-9]", "", norm)
        for name, _, compact in _PATTERNS:
            if any(c in squashed for c in compact):
                return name
    return None


def resolve_location(
    display_name: Optional[str] = None,
    username: Optional[str] = None,
    bio: Optional[str] = None,
    extra_texts: Iterable[Optional[str]] = (),
    default: str = DEFAULT_LOCATION,
) -> str:
    """Trả về tên tỉnh/thành chuẩn nếu nhận diện được, ngược lại trả về `default`."""
    sources = [
        (display_name, True),
        (username, True),
        (bio, False),
        *((t, False) for t in extra_texts),
    ]
    for text, allow_compact in sources:
        found = _match(text or "", allow_compact)
        if found:
            return found
    return default
