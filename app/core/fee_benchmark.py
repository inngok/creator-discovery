"""
Declarative Fee Benchmark Module
Tính dải giá ước tính tham khảo (VND) dựa trên Followers của Creator.
Thiết kế Data-Driven: 0 if-else, tra cứu bảng cấu hình, dễ dàng mở rộng.
"""

# Bảng mốc cấu hình: (Ngưỡng followers trên, Giá ước tính Min VND, Giá ước tính Max VND)
FEE_BENCHMARK_TABLE: tuple[tuple[float, int, int], ...] = (
    (10_000, 200_000, 500_000),            # Nano (< 10k)
    (50_000, 1_000_000, 2_500_000),        # Micro nhỏ (10k - 50k)
    (100_000, 2_500_000, 5_000_000),       # Micro vừa (50k - 100k)
    (500_000, 5_000_000, 15_000_000),      # Macro (100k - 500k)
    (1_000_000, 15_000_000, 30_000_000),   # Top Macro (500k - 1M)
    (float("inf"), 30_000_000, 60_000_000),# Mega / Celeb (> 1M)
)


def get_estimated_fee(followers: int | None = 0) -> tuple[int, int]:
    """
    Tra cứu khoảng giá ước tính (min_fee, max_fee) theo followers.
    Sử dụng Generator Expression tra cứu trực tiếp từ bảng, không dùng if-else.
    """
    f = max(0, followers or 0)
    return next((fee_min, fee_max) for limit, fee_min, fee_max in FEE_BENCHMARK_TABLE if f < limit)

