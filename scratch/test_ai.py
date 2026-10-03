import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.models import CreatorInfo, Post
from app.ai.analyzer import process_ai_enrichment, _predict_category, _predict_niche
from app.ai.services.embedding_service import embedding_service
import numpy as np

creator_info = CreatorInfo(
    platform="TIKTOK",
    externalId="72357433530",
    username="@halinhofficial",
    displayName="Ha Linh Official",
    profileUrl="https://www.tiktok.com/@halinhofficial",
    avatarUrl="",
    followers=5600000,
    bio="🔥 REVIEW DẠO🔥 SĂN SEO DẠO🔥",
    publicEmail=None,
    publicPhone=None
)

recent_posts = [
    Post(
        platformPostId="7683516014092668178",
        postUrl="https://...",
        caption="Tới gòy, tới gòyyyyyy! Lên! #CapCut ",
        views=1700000,
        likes=15200,
        comments=409,
        shares=692,
        postedAt="2026-09-09T12:46:29+00:00"
    ),
    Post(
        platformPostId="7683424829495790866",
        postUrl="",
        caption="Rồi, tưng bừng đây rồi!",
        views=1500000,
        likes=25600,
        comments=141,
        shares=1492,
        postedAt="2026-09-09T06:52:34+00:00"
    ),
    Post(
        platformPostId="7683396227290942738",
        postUrl="",
        caption="- Tung ngay Super deal vào ngay đầu live (số lượng rất có hạn, mọi người chú ý theo dõi nhé). - Deal List bổ sung thêm, Deal thay đổi, Deal không xuất hiện trong phiên live vì lý do khách quan! - Giá trong bài đăng chỉ mang tính tham khảo, giá cuối cùng sẽ phụ thuộc voucher và ưu đãi trên live. - Một số deal đang ẩn giá, cả nhà đoán giá dưới comment nhé. - Toàn bộ sản phẩm trong giỏ hàng của phiên SuperLive đều được các nhãn hàng tài trợ, nhằm mang đến thông tin cho người xem theo quy định. Nhớ set lịch và canh sẵn trước 5-10 phút, đúng 20h là vào việc nhé cả nhà!",
        views=7400000,
        likes=17700,
        comments=613,
        shares=5167,
        postedAt="2026-09-09T05:01:35+00:00"
    ),
    Post(
        platformPostId="7671613130803268882",
        postUrl="",
        caption="SUPERLIVE 20H 08.08: Siêu Seo Tưng Bừng - Không Ngừng Chốt Đơn!",
        views=2700000,
        likes=25200,
        comments=309,
        shares=3293,
        postedAt="2026-08-08T10:57:14+00:00"
    )
]

bio = creator_info.bio or ""
captions = " | ".join([post.caption for post in recent_posts if post.caption])
text_combined = f"{bio} {captions}"

print("--- TEXT COMBINED ---")
print(text_combined)

res = _predict_category(text_combined)
print("\n--- CATEGORY PREDICTION ---")
print(f"Result Label: {res.label}")
print(f"Confidence: {res.confidence}")
print(f"Engine: {res.engine}")

from app.ai.analyzer import CATEGORY_PROTOTYPES
import app.ai.analyzer as analyzer

X_array = np.array(embedding_service.get_embedding(text_combined)).reshape(1, -1)
probs = analyzer.cat_model.predict_proba(X_array)[0]
sorted_idx = np.argsort(probs)[::-1]

top1_idx = sorted_idx[0]
top2_idx = sorted_idx[1] if len(sorted_idx) > 1 else sorted_idx[0]
predicted_label = analyzer.cat_classes[top1_idx]
print(f"ML Predict: {predicted_label} (prob: {probs[top1_idx]}, margin: {probs[top1_idx] - probs[top2_idx]})")

proto_text = CATEGORY_PROTOTYPES.get(predicted_label, "")
from numpy.linalg import norm
predicted_sim = 0.0
if proto_text:
    proto_emb = embedding_service.get_embedding(proto_text)
    proto_emb = np.array(proto_emb).flatten()
    x_flat = X_array.flatten()
    denom = (norm(x_flat) * norm(proto_emb))
    if denom > 0:
        predicted_sim = np.dot(x_flat, proto_emb) / denom
print(f"Semantic sim with {predicted_label}: {predicted_sim}")

print("\n--- NICHE PREDICTION ---")
print("Niche predicted:", _predict_niche(predicted_label, text_combined))
