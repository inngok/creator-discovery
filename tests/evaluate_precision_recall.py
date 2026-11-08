import json
import os
import sys

sys.path.insert(0, os.path.abspath("."))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.ai.rules.rule_config import Decision
from app.ai.rules.rule_engine import RuleEngine
from app.ai.rules.signal_extractor import PostPreview, ProfileMetadata


def evaluate_precision_recall():
    dataset_path = os.path.join(os.path.dirname(__file__), "data", "golden_benchmark_dataset.json")
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = data.get("records", [])

    print("=" * 105)
    print("      INFLUENCERMATCH AI - GOLDEN BENCHMARK DATASET EVALUATION (PRECISION & RECALL)")
    print("=" * 105)
    print(f"{'ID':<12} | {'Category':<20} | {'Expected Decision':<20} | {'Actual Decision':<20} | {'Match':<5}")
    print("-" * 105)

    # Metrics counters
    tp_koc = 0
    fp_koc = 0
    fn_koc = 0

    tp_kol = 0
    fp_kol = 0
    fn_kol = 0

    tp_reject = 0
    fp_reject = 0

    total_actual_creators = 0
    retained_creators = 0

    for rec in records:
        profile = ProfileMetadata(
            username=rec["username"],
            display_name=rec.get("display_name", ""),
            bio=rec.get("bio", ""),
            followers=rec.get("followers", 0),
            bio_links=rec.get("bio_links", []),
        )
        posts = [PostPreview(caption=c) for c in rec.get("posts", [])]
        result = RuleEngine.evaluate(profile, posts)

        expected = rec["expected_decision"]
        actual = result.decision.value
        gt_label = rec["ground_truth_label"]

        is_match = (expected == actual)
        match_str = "✅" if is_match else "⚠️"

        print(f"{rec['id']:<12} | {rec['category']:<20} | {expected:<20} | {actual:<20} | {match_str:<5}")

        # Metrics for KOC
        if actual == Decision.AUTO_ACCEPT_KOC.value:
            if gt_label == "KOC":
                tp_koc += 1
            else:
                fp_koc += 1
        elif gt_label == "KOC" and actual != Decision.AUTO_ACCEPT_KOC.value:
            fn_koc += 1

        # Metrics for KOL
        if actual == Decision.AUTO_ACCEPT_KOL.value:
            if gt_label == "KOL":
                tp_kol += 1
            else:
                fp_kol += 1
        elif gt_label == "KOL" and actual != Decision.AUTO_ACCEPT_KOL.value:
            fn_kol += 1

        # Metrics for AUTO_REJECT
        if actual == Decision.AUTO_REJECT.value:
            if gt_label in ["BRAND", "SELLER", "MEDIA", "MEME", "SPAM"]:
                tp_reject += 1
            else:
                fp_reject += 1

        # Creator Recall (KOC/KOL retained via Auto Accept or Review)
        if gt_label in ["KOC", "KOL", "MIXED_CREATOR_SHOP"]:
            total_actual_creators += 1
            if result.decision in [
                Decision.AUTO_ACCEPT_KOC,
                Decision.AUTO_ACCEPT_KOL,
                Decision.NEEDS_MANAGER_REVIEW,
            ]:
                retained_creators += 1

    print("-" * 105)

    # Calculate metrics
    koc_precision = (tp_koc / (tp_koc + fp_koc) * 100) if (tp_koc + fp_koc) > 0 else 100.0
    kol_precision = (tp_kol / (tp_kol + fp_kol) * 100) if (tp_kol + fp_kol) > 0 else 100.0
    auto_accept_precision = (
        ((tp_koc + tp_kol) / ((tp_koc + fp_koc) + (tp_kol + fp_kol)) * 100)
        if ((tp_koc + fp_koc) + (tp_kol + fp_kol)) > 0
        else 100.0
    )
    reject_precision = (tp_reject / (tp_reject + fp_reject) * 100) if (tp_reject + fp_reject) > 0 else 100.0
    creator_recall = (retained_creators / total_actual_creators * 100) if total_actual_creators > 0 else 100.0

    print("📈 FINAL BENCHMARK EVALUATION METRICS:")
    print(f"  • KOC Precision (Độ chính xác Auto Accept KOC)        : {koc_precision:.1f}% (TP={tp_koc}, FP={fp_koc})")
    print(f"  • KOL Precision (Độ chính xác Auto Accept KOL)        : {kol_precision:.1f}% (TP={tp_kol}, FP={fp_kol})")
    print(f"  • Overall Auto-Accept Precision (Toàn bộ Auto Accept) : {auto_accept_precision:.1f}% (Mục tiêu: >= 95.0%)")
    print(f"  • Auto-Reject Precision (Độ chính xác Auto Reject)    : {reject_precision:.1f}% (TP={tp_reject}, FP={fp_reject})")
    print(f"  • Creator Recall (Tỷ lệ Creator thực tế được giữ lại) : {creator_recall:.1f}% ({retained_creators}/{total_actual_creators})")
    print("=" * 105)

    passed = auto_accept_precision >= 95.0 and creator_recall >= 90.0
    if passed:
        print("🎉 BENCHMARK ACCEPTANCE PASSED: All Precision & Recall targets met!")
    else:
        print("⚠️ BENCHMARK WARNING: Target thresholds need review.")
    print("=" * 105)

    return {
        "koc_precision": koc_precision,
        "kol_precision": kol_precision,
        "auto_accept_precision": auto_accept_precision,
        "reject_precision": reject_precision,
        "creator_recall": creator_recall,
        "passed": passed,
    }


if __name__ == "__main__":
    evaluate_precision_recall()
