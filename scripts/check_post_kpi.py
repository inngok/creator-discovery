import argparse
import json
import os
import sys

# Đảm bảo đường dẫn import từ app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.single_post_tracker_service import single_post_tracker


def format_number(val):
    if val is None:
        return "N/A"
    return f"{val:,}"


def check_post_metrics(post_url: str):
    print("\n" + "=" * 80)
    print("DANG CAO SO LIEU THUC TE (GROUND TRUTH)...")
    print(f"URL: {post_url}")
    print("=" * 80)

    result = single_post_tracker.fetch_post_metrics(post_url)

    if result.get("status") == "error":
        err = result.get("error", {})
        print(f"\nLOI [{err.get('code')}]: {err.get('message')}")
        print(f"Retryable: {err.get('retryable')} | Retry After: {err.get('retryAfterSeconds')}s")
        return

    # Thông tin cơ bản bài viết
    author = result.get("author", {})
    content = result.get("content", {})
    metrics = result.get("metrics", {})
    metric_status = result.get("metricStatus", {})
    metric_errors = result.get("metricErrors", {})
    coll = result.get("collection", {})

    print("\nTHONG TIN BAI VIET:")
    print(f"  - Nen tang:        {result.get('platform')}")
    print(f"  - Tac gia:         {author.get('displayName')} (@{author.get('username')})")
    print(f"  - Author ID:       {author.get('externalId') or 'N/A'}")
    print(f"  - Post ID:         {result.get('postId')}")
    print(f"  - Tieu de/Caption: {content.get('caption')}")
    print(f"  - Thoi gian dang:  {content.get('postedAt') or 'N/A'}")
    print(f"  - Thu thap luc:    {result.get('trackedAt')} (Method: {coll.get('method')})")
    print(f"  - Ty le ER:        {metrics.get('engagementRate')}% (Cong thuc: {coll.get('engagementRateFormula')})")

    # Bảng số liệu Ground Truth trả về cho Java Backend (Java sẽ thực hiện đối soát KPI)
    print("\n" + "-" * 75)
    print(f"{'Chi so':<15} | {'Gia tri thuc te':<18} | {'Trang thai':<16} | {'Ghi chu'}")
    print("-" * 75)

    for metric_name in ("views", "likes", "comments", "shares"):
        actual_val = metrics.get(metric_name)
        status_val = metric_status.get(metric_name, "N/A")
        err_info = metric_errors.get(metric_name)
        note = f"Err: {err_info.get('code')}" if err_info else "-"

        print(
            f"{metric_name.capitalize():<15} | "
            f"{format_number(actual_val):<18} | "
            f"{status_val:<16} | "
            f"{note}"
        )

    print("-" * 75 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tool kiem tra so lieu bai dang thuc te cho Java Backend")
    parser.add_argument("--url", type=str, required=True, help="Link bai dang TikTok, YouTube, Facebook, Instagram")

    args = parser.parse_args()
    check_post_metrics(args.url)
