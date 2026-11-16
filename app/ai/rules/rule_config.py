import re
from enum import Enum


class Decision(str, Enum):
    """
    Quyết định phân loại của Pre-Crawl Gate.
    """
    AUTO_ACCEPT_KOC = "AUTO_ACCEPT_KOC"
    AUTO_ACCEPT_KOL = "AUTO_ACCEPT_KOL"
    AUTO_REJECT = "AUTO_REJECT"
    NEEDS_MANAGER_REVIEW = "NEEDS_MANAGER_REVIEW"
    SKIP_INSUFFICIENT = "SKIP_INSUFFICIENT"
    RETRY_PREFLIGHT = "RETRY_PREFLIGHT"
    MANAGER_APPROVED = "MANAGER_APPROVED"
    MANAGER_REJECTED = "MANAGER_REJECTED"


class ProcessingStatus(str, Enum):
    """
    Trạng thái quy trình xử lý dữ liệu của Candidate.
    """
    DISCOVERED = "DISCOVERED"
    PREFLIGHT_RUNNING = "PREFLIGHT_RUNNING"
    PREFLIGHT_DONE = "PREFLIGHT_DONE"
    DEEP_CRAWL_QUEUED = "DEEP_CRAWL_QUEUED"
    DEEP_CRAWLING = "DEEP_CRAWLING"
    INGESTED = "INGESTED"
    FAILED = "FAILED"


# Phiên bản bộ quy tắc phân loại
RULE_VERSION = "v2.0-precision"

# Các ngưỡng thử nghiệm cấu hình tập trung
MIN_POST_PREVIEWS = 5
KOC_REVIEW_RATIO_THRESHOLD = 0.6  # Ít nhất 3/5 bài review (60%)
KOC_MIN_OPINION_POSTS = 2         # Ít nhất 2 bài có nhận xét cụ thể ưu/nhược/so sánh
KOL_REPRESENTATIVE_RATIO_THRESHOLD = 0.6  # Ít nhất 3/5 bài chuyên môn/ảnh hưởng nhất quán
MIN_FOLLOWERS_THRESHOLD = 1000  # Ngưỡng sàn chuẩn quốc tế cho Nano KOC (1.000 followers)
MAX_PREFLIGHT_RETRIES = 3


def build_boundary_regex(phrases: list[str]) -> re.Pattern:
    """
    Tạo regex khớp cụm từ có ranh giới từ (Word Boundary) an toàn cho Unicode tiếng Việt.
    Tránh trường hợp bắt nhầm substring như 'báo ' trong 'cảnh báo'.
    """
    escaped = [re.escape(p.strip().lower()) for p in phrases if p.strip()]
    pattern = r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)"
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


# 1. Từ khóa/cụm từ nhận diện Brand, Cửa hàng sỉ lẻ, Tổng kho, Merchant bán hàng
COMMERCIAL_SELLER_PHRASES = [
    # Bán sỉ, kho buôn, xưởng
    "chuyên sỉ", "giá xưởng", "sỉ lẻ", "tổng kho", "tuyển ctv",
    "tuyển cộng tác viên", "tuyển đại lý", "nhập hàng", "xưởng may",
    "kho sỉ", "buôn sỉ", "bỏ sỉ", "đổ sỉ", "order taobao",
    "ship toàn quốc", "bảng giá sỉ", "official shop", "cửa hàng chính thức",
    "cửa hàng chính hãng", "official store", "shop chính hãng",
    "phân phối chính hãng", "đại lý ủy quyền", "tổng đại lý", "tổng phân phối",
    "xưởng gia công", "chuyên may", "chuyên sỉ lẻ", "shop", "store", "boutique",
    "giỏ hàng", "bấm vào giỏ hàng", "kho buôn", "kho hàng", "sẵn kho", "hàng có sẵn",
    # Dấu hiệu bán hàng trực tiếp / merchant bán lẻ qua bio
    "đơn hàng", "từng đơn hàng", "chốt đơn", "lên đơn", "đặt hàng", "mua hàng",
    "bán hàng", "hoàn tiền", "1 đổi 1", "lỗi 1 đổi 1", "lỗi 1, đổi 1", "đổi trả",
    "bao đổi trả", "không ưng hoàn tiền", "cam kết hoàn tiền", "hoàn tiền ạ",
    "ship cod", "đồng kiểm", "cho xem hàng", "kiểm tra hàng", "freeship",
    "xả kho", "thanh lý lỗ", "xả hàng", "thanh lý", "mua 1 tặng 1",
    "inbox báo giá", "check inbox", "báo giá", "tư vấn đặt hàng", "bán lẻ", "giá sỉ",
    "ủng hộ em", "ủng hộ shop", "ủng hộ qua từng đơn hàng", "hotline đặt hàng",
    "hotline", "zalo mua hàng", "liên hệ đặt hàng", "bán buôn",
]
REGEX_COMMERCIAL_SELLER = build_boundary_regex(COMMERCIAL_SELLER_PHRASES)

# 2. Cơ quan nhà nước, Báo chí, Đài truyền hình, Tập đoàn lớn
STATE_MEDIA_PHRASES = [
    "bộ y tế", "bộ giáo dục", "bộ công an", "an ninh mạng", "cục cảnh sát",
    "cục an toàn", "cục sở hữu trí tuệ", "cục quản lý", "ủy ban nhân dân",
    "đài truyền hình", "truyền hình việt nam", "vtvcab", "vtv thể thao",
    "vtv giải trí", "vtv24", "vtv1", "vtv3", "vtv6", "vtv digital",
    "tin tức 24h", "thể thao 247", "báo thanh niên", "báo tuổi trẻ",
    "báo người lao động", "báo tiền phong", "báo dân trí", "báo điện tử",
    "tạp chí điện tử", "tòa soạn", "thông tấn xã", "công ty tnhh",
    "công ty cổ phần", "tập đoàn", "ngân hàng tmcp", "bệnh viện đa khoa"
]
REGEX_STATE_MEDIA = build_boundary_regex(STATE_MEDIA_PHRASES)

# 3. Dấu hiệu Catalog bán hàng / Spam quảng cáo
SPAM_CATALOG_PHRASES = [
    "giá chỉ từ", "inbox để nhận giá", "bảng giá chi tiết",
    "link giỏ hàng bên dưới", "mã giảm giá số lượng có hạn",
    "xem mẫu tại", "mẫu mới về", "hàng có sẵn", "xả kho",
    "thanh lý lỗ", "check inbox", "inbox báo giá", "mua 1 tặng 1",
    "freeship toàn quốc", "liên hệ đặt hàng"
]
REGEX_SPAM_CATALOG = build_boundary_regex(SPAM_CATALOG_PHRASES)

# 4. Hành động Review / Trải nghiệm của KOC
KOC_REVIEW_ACTION_PHRASES = [
    "review", "unboxing", "mở hộp", "trên tay", "dùng thử",
    "đánh giá", "trải nghiệm", "cảm nhận", "test thử", "thử nghiệm",
    "săn sale", "mua ở đây", "link mua", "mua thử", "so sánh",
    "unbox", "swatch", "haul", "thử thách", "thử đồ"
]
REGEX_KOC_REVIEW = build_boundary_regex(KOC_REVIEW_ACTION_PHRASES)

# 5. Nhận xét cụ thể (Ưu/Nhược điểm, cảm nhận cá nhân sâu)
KOC_OPINION_PHRASES = [
    "ưu điểm", "nhược điểm", "điểm trừ", "điểm cộng", "đáng mua",
    "không nên mua", "thất vọng", "rất thích", "hợp với da", "chê nha",
    "khen nha", "nên cân nhắc", "độ bền", "chất lượng tốt", "chất lượng kém",
    "so với giá", "đáng tiền", "phí tiền", "chấm điểm", "rating",
    "trải nghiệm thực tế", "sau 1 tháng", "sau 2 tuần", "sau khi dùng",
    "chấm 8/10", "chấm 9/10", "chấm 10/10", "chấm 7/10"
]
REGEX_KOC_OPINION = build_boundary_regex(KOC_OPINION_PHRASES)

# 6. Tên miền Bio Link và Affiliate phổ biến
AFFILIATE_DOMAINS = [
    "beacons.ai", "linktr.ee", "shopee.vn", "bio.link",
    "instabio", "kol.eco", "mycollection.shop", "vt.tiktok.com",
    "s.shopee.vn", "shorten.asia", "accesstrade.vn", "ecomobi.com"
]

# 7. Danh xưng chuyên môn & chức danh (Expertise KOL)
KOL_EXPERTISE_TITLES = [
    "bác sĩ", "dr.", "tiến sĩ", "thạc sĩ", "giảng viên", "chuyên gia",
    "huấn luyện viên", "coach", "luật sư", "kiến trúc sư", "founder",
    "ceo", "chủ tịch", "nghệ sĩ", "ca sĩ", "diễn viên", "mc",
    "vận động viên", "masterchef", "dược sĩ", "nhà báo"
]
REGEX_KOL_EXPERTISE = build_boundary_regex(KOL_EXPERTISE_TITLES)

# 8. Bằng chứng ảnh hưởng / Sự kiện / Vai trò độc lập (Influence KOL)
KOL_INFLUENCE_PHRASES = [
    "đại sứ thương hiệu", "brand ambassador", "diễn giả",
    "guest speaker", "keynote speaker", "workshop", "talkshow",
    "hội thảo", "talk show", "ban giám khảo", "giám khảo",
    "sự kiện ra mắt", "fashion week", "tedx", "chia sẻ kiến thức",
    "hướng dẫn", "phân tích chuyên sâu"
]
REGEX_KOL_INFLUENCE = build_boundary_regex(KOL_INFLUENCE_PHRASES)

# Aliases cho các module sử dụng
REGEX_COMMERCIAL_POST = REGEX_SPAM_CATALOG
REGEX_OPINION_KEYWORDS = REGEX_KOC_OPINION

