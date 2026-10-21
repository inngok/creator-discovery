"""
Vector EMA Experiment (Thử nghiệm toán học công thức Vector EMA)
Phục vụ đề xuất hệ số Alpha tối ưu cho Brand Preference Vector trên Java Backend.
"""

import sys
import numpy as np

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def cosine_similarity(u: np.ndarray, v: np.ndarray) -> float:
    """Tính độ tương đồng Cosine giữa 2 vector."""
    norm_u = np.linalg.norm(u)
    norm_v = np.linalg.norm(v)
    if norm_u < 1e-9 or norm_v < 1e-9:
        return 0.0
    return float(np.dot(u, v) / (norm_u * norm_v))


def normalize(v: np.ndarray) -> np.ndarray:
    """Chuẩn hóa L2 norm về độ dài = 1."""
    norm = np.linalg.norm(v)
    if norm < 1e-9:
        return v
    return v / norm


def update_brand_vector_ema(
    brand_vec: np.ndarray,
    koc_vecs: list[np.ndarray],
    feedback_weights: list[float],
    alpha: float = 0.8,
) -> np.ndarray:
    """
    Cập nhật Brand Preference Vector bằng công thức EMA:
    V_new = alpha * V_old + (1 - alpha) * V_feedback

    Parameters:
    - brand_vec: Vector hiện tại của Brand (768 chiều).
    - koc_vecs: Danh sách vector KOC tham gia chiến dịch.
    - feedback_weights: Trọng số đánh giá (+1.0: Rất thích, 0.0: Trung lập, -1.0: Thất vọng).
    - alpha: Hệ số quán tính (0.0 < alpha < 1.0).

    Returns:
    - Vector mới đã được chuẩn hóa L2 (norm = 1.0).
    """
    # Edge case 1: Không có dữ liệu phản hồi
    if not koc_vecs or len(koc_vecs) == 0:
        return brand_vec

    # Edge case 2: Vector brand rỗng / độ lớn bằng 0
    if np.linalg.norm(brand_vec) < 1e-9:
        # Nếu brand chưa có gu, lấy trung bình vector KOC tích cực làm gu ban đầu
        pos_kocs = [
            k for k, w in zip(koc_vecs, feedback_weights, strict=False) if w > 0
        ]
        if pos_kocs:
            return normalize(np.mean(pos_kocs, axis=0))
        return brand_vec

    # Tính vector đại diện cho phản hồi từ chiến dịch
    # Vector hướng dịch chuyển = tổng có trọng số (weighted sum)
    feedback_shift = np.zeros_like(brand_vec)
    total_abs_weight = sum(abs(w) for w in feedback_weights)

    if total_abs_weight < 1e-9:
        return brand_vec  # Phản hồi trung lập hoàn toàn, giữ nguyên

    for k_vec, weight in zip(koc_vecs, feedback_weights, strict=False):
        k_norm = normalize(k_vec)
        feedback_shift += weight * k_norm

    feedback_shift = feedback_shift / total_abs_weight

    # Công thức EMA:
    # V_raw = alpha * brand_vec + (1 - alpha) * feedback_shift
    updated_vec = alpha * brand_vec + (1.0 - alpha) * feedback_shift

    # Edge case 3: Vector sau cộng bị triệt tiêu (độ lớn xấp xỉ 0)
    if np.linalg.norm(updated_vec) < 1e-9:
        return brand_vec  # Fallback giữ nguyên vector cũ để an toàn

    # Chuẩn hóa L2 norm về độ dài = 1
    return normalize(updated_vec)


def run_experiment():
    np.random.seed(42)
    dim = 768  # Chuẩn vector BKAI Bi-Encoder

    # Khởi tạo Vector Brand gốc (Ví dụ: Gu làm đẹp nhẹ nhàng)
    brand_vec = normalize(np.random.randn(dim))

    # Tạo 1 KOC đối tác có độ tương đồng ban đầu ~ 0.50 (Khác biệt vừa phải)
    noise = np.random.randn(dim)
    koc_positive = normalize(0.5 * brand_vec + 0.5 * normalize(noise))

    sim_before = cosine_similarity(brand_vec, koc_positive)

    print("=" * 80)
    print("THỬ NGHIỆM 1: PHẢN HỒI TÍCH CỰC (+1.0) VỚI CÁC HỆ SỐ ALPHA KHÁC NHAU")
    print(f"Độ tương đồng ban đầu trước cập nhật: {sim_before:.4f}")
    print("=" * 80)
    print(
        f"{'Alpha':<10} | {'Tỷ lệ (Cũ / Mới)':<18} | {'Sim Trước':<12} | {'Sim Sau':<12} | {'Mức độ dịch chuyển':<20}"
    )
    print("-" * 80)

    alphas = [0.6, 0.7, 0.8, 0.9, 0.95]
    for a in alphas:
        new_vec = update_brand_vector_ema(
            brand_vec=brand_vec,
            koc_vecs=[koc_positive],
            feedback_weights=[1.0],
            alpha=a,
        )
        sim_after = cosine_similarity(new_vec, koc_positive)
        diff = sim_after - sim_before
        pct_change = (diff / sim_before) * 100
        print(
            f"{a:<10} | {int(a*100)}% / {int((1-a)*100)}%{'':<7} | {sim_before:<12.4f} | {sim_after:<12.4f} | +{diff:.4f} (+{pct_change:.1f}%)"
        )

    print("\n" + "=" * 80)
    print("THỬ NGHIỆM 2: SO SÁNH 3 TÌNH HUỐNG (TÍCH CỰC, TIÊU CỰC, TRUNG LẬP) VỚI ALPHA = 0.8")
    print("=" * 80)

    # Kịch bản 1: Tích cực
    vec_pos = update_brand_vector_ema(
        brand_vec, [koc_positive], [1.0], alpha=0.8
    )
    sim_pos = cosine_similarity(vec_pos, koc_positive)

    # Kịch bản 2: Tiêu cực (-1.0)
    vec_neg = update_brand_vector_ema(
        brand_vec, [koc_positive], [-1.0], alpha=0.8
    )
    sim_neg = cosine_similarity(vec_neg, koc_positive)

    # Kịch bản 3: Trung lập (0.0)
    vec_neu = update_brand_vector_ema(
        brand_vec, [koc_positive], [0.0], alpha=0.8
    )
    sim_neu = cosine_similarity(vec_neu, koc_positive)

    print(f"1. Tích cực (+1.0) : {sim_before:.4f} -> {sim_pos:.4f} (Nhích lại gần KOC)")
    print(f"2. Tiêu cực (-1.0) : {sim_before:.4f} -> {sim_neg:.4f} (Đẩy xa khỏi KOC)")
    print(f"3. Trung lập (0.0) : {sim_before:.4f} -> {sim_neu:.4f} (Giữ nguyên tuyệt đối)")

    print("\n" + "=" * 80)
    print("THỬ NGHIỆM 3: ĐÁNH GIÁ NGUY CƠ LỆCH GU (DRIFT RISK) DO 1 KOC NGOẠI LAI")
    print("=" * 80)
    # KOC dị biệt hoàn toàn (độ tương đồng ban đầu rất thấp ~ 0.05)
    koc_outlier = normalize(np.random.randn(dim))
    outlier_sim_before = cosine_similarity(brand_vec, koc_outlier)

    print(
        f"KOC ngoại lai (hoàn toàn khác gu Brand). Sim ban đầu: {outlier_sim_before:.4f}"
    )
    for a in [0.6, 0.8, 0.9]:
        vec_outlier = update_brand_vector_ema(
            brand_vec, [koc_outlier], [1.0], alpha=a
        )
        sim_brand_original = cosine_similarity(
            vec_outlier, brand_vec
        )  # Độ giữ gu cũ
        sim_to_outlier = cosine_similarity(vec_outlier, koc_outlier)
        print(
            f"Alpha = {a}: Giữ gu cũ = {sim_brand_original*100:.1f}%, Bị cuốn theo ngoại lai = {sim_to_outlier:.4f}"
        )

    print("\n" + "=" * 80)
    print("THỬ NGHIỆM 4: KIỂM TRA TÍNH AN TOÀN VÀ CÁC TRƯỜNG HỢP BIÊN (EDGE CASES)")
    print("=" * 80)

    # Case A: Vector Brand có độ lớn = 0
    zero_vec = np.zeros(dim)
    res_zero = update_brand_vector_ema(
        zero_vec, [koc_positive], [1.0], alpha=0.8
    )
    print(
        f"- Brand vector = 0: Xử lý an toàn, Norm mới = {np.linalg.norm(res_zero):.4f} (Không bị lỗi chia 0)"
    )

    # Case B: Không có phản hồi
    res_empty = update_brand_vector_ema(brand_vec, [], [], alpha=0.8)
    print(
        f"- Không có feedback: Trả về nguyên gốc, Sim = {cosine_similarity(res_empty, brand_vec):.4f}"
    )

    # Case C: Kiểm tra độ dài chuẩn hóa sau cập nhật
    print(
        f"- Độ dài vector sau cập nhật (L2 Norm): {np.linalg.norm(vec_pos):.6f} (Chuẩn xác = 1.0)"
    )


if __name__ == "__main__":
    run_experiment()
