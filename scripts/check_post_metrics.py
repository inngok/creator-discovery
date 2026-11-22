import sys
from scripts.check_post_kpi import check_post_metrics

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Tool kiem tra so lieu bai dang thuc te cho Java Backend")
    parser.add_argument("--url", type=str, required=True, help="Link bai dang TikTok, YouTube, Facebook, Instagram")
    parser.add_argument("--sync-java", action="store_true", help="Ban payload so lieu sang Java Backend sau khi cao")

    args = parser.parse_args()
    check_post_metrics(args.url, sync_java=args.sync_java)
