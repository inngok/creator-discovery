# KẾ HOẠCH BUILD AI CREATOR DISCOVERY — READY TO TRAIN

> **Mục tiêu của bản này:** cho phép bắt đầu code training AI ngay bây giờ với dữ liệu hiện có, nhưng vẫn giữ architecture đủ ổn định để sau này chỉ cần **import CSV mới → validate → retrain → evaluate → promote model**, không phải thiết kế lại hệ thống.
>
> **Phạm vi hiện tại:** ưu tiên hoàn thành **Offline Training Pipeline** trước. Backend, Crawler, pgvector và Online Inference sẽ nối sau khi AI core đã train/evaluate được.

---

# 0. Trạng thái hiện tại và quyết định cần chốt

## 0.1. Data hiện có

Nguồn:

```text
ViTIED
KOL.GOV.VN
LAMECO
```

Từ package đã chuẩn hóa hiện tại:

```text
Master unique creators      ≈ 756
Category usable             ≈ 291
Niche usable                rất ít
CreatorType ground truth    chưa đủ để train
```

**Kết luận:**

```text
Category Model      → BẮT ĐẦU TRAIN V1
Niche Model         → CHƯA TRAIN; build interface + fallback trước
CreatorType Model   → CHƯA TRAIN; cần human labels trước
Semantic Embedding  → BUILD NGAY vì dùng chung cho Category và semantic search sau này
```

---

# 1. Chốt contract trước khi code training

## 1.1. Input AI contract

AI core sau này nhận đúng cấu trúc này, dù Crawler thật dùng tên field khác:

```json
{
  "username": "creatorabc",
  "followers": 85000,
  "engagementRate": 4.8,
  "bio": "Review mỹ phẩm và skincare",
  "recentPosts": [
    {
      "caption": "Review kem chống nắng cho da dầu",
      "hashtags": ["skincare", "sunscreen"]
    }
  ]
}
```

Training hiện tại chưa cần Crawler chạy thật. Dataset chỉ cần tạo ra `text` tương đương:

```text
bio + 3 recent captions + hashtags
```

## 1.2. Output AI contract

```json
{
  "category": "BEAUTY",
  "categoryConfidence": 0.91,

  "niche": "SKINCARE",
  "nicheConfidence": 0.83,

  "creatorType": "KOC",
  "creatorTypeConfidence": 0.88,

  "semanticTags": [
    "sunscreen",
    "acne",
    "skincare review"
  ],

  "contentSummary": "Creator chuyên review sản phẩm chăm sóc da.",
  "needsReview": false
}
```

**Không cần Backend implement contract này ngay.** Chỉ cần AI code giữ interface này từ đầu.

---

# 2. Chốt taxonomy nhưng KHÔNG train mọi category ngay

## 2.1. Business taxonomy v1

```text
BEAUTY
FASHION
FOOD
TECH
HEALTH_FITNESS
TRAVEL
PARENTING
HOME
ENTERTAINMENT
LIFESTYLE
EDUCATION
FINANCE
GAMING
AUTOMOTIVE
SPORTS
PET
BUSINESS
OTHER
UNKNOWN
```

Mỗi category có trạng thái:

```text
ML_READY       = đủ data để supervised classifier học
NOT_READY      = category có trong taxonomy nhưng chưa đủ training data
SEMANTIC_ONLY  = dùng semantic search/fallback, chưa đưa vào classifier
```

## 2.2. Không đánh dấu `ML_READY` bằng tay nếu chưa check data

Tạo script:

```text
check_training_readiness.py
```

Rule khởi đầu:

```python
MIN_SAMPLES_PER_CLASS = 30
```

Sau khi đọc `category_training.csv`, script tự report:

```text
BEAUTY           ...   ML_READY / NOT_READY
FASHION          ...   ML_READY / NOT_READY
PARENTING        ...   ML_READY / NOT_READY
HEALTH_FITNESS   ...   ML_READY / NOT_READY
FOOD             ...   ML_READY / NOT_READY
HOME             ...   ML_READY / NOT_READY
...
```

> **Không train class chỉ vì class đó tồn tại trong `taxonomy.json`.**

---

# 3. Cấu trúc project dùng từ bây giờ

```text
app/
├── ai/
│   ├── schemas.py
│   ├── config.py
│   ├── taxonomy.json
│   ├── analyzer.py                 # dùng sau khi model đã train
│   │
│   ├── services/
│   │   ├── embedding_service.py
│   │   ├── category_service.py
│   │   ├── niche_service.py
│   │   ├── creator_type_service.py
│   │   ├── semantic_service.py
│   │   └── summary_service.py
│   │
│   └── models/
│       ├── category/
│       ├── niche/
│       └── creator_type/
│
└── ai_development/
    ├── data/
    │   ├── raw/
    │   │   ├── vitied.csv
    │   │   ├── kol_gov.csv
    │   │   └── lameco.csv
    │   │
    │   ├── incoming/
    │   │   └── *.csv
    │   │
    │   ├── processed/
    │   │   ├── vitied_clean.csv
    │   │   ├── kol_gov_clean.csv
    │   │   └── lameco_clean.csv
    │   │
    │   └── final/
    │       ├── master_clean_dataset.csv
    │       ├── category_training.csv
    │       ├── category_gold_test.csv
    │       ├── niche_training.csv
    │       ├── niche_gold_test.csv
    │       ├── creator_type_training.csv
    │       └── creator_type_gold_test.csv
    │
    ├── 01_prepare_dataset.py
    ├── 02_check_training_readiness.py
    ├── 03_build_category_gold_test.py
    ├── 04_train_category_model.py
    ├── 05_evaluate_category_model.py
    ├── 06_tune_unknown_threshold.py
    ├── 07_export_category_model.py
    │
    ├── import_training_csv.py
    ├── retrain_pipeline.py
    │
    ├── train_creator_type_model.py
    └── train_niche_models.py
```

---

# 4. Schema dataset phải cố định ngay từ đầu

`master_clean_dataset.csv`:

```text
creator_id
username
display_name

bio
recent_posts
hashtags
followers
engagement_rate

category
category_source
category_confidence

niche
niche_source
niche_confidence

creator_type
creator_type_source
creator_type_confidence

semantic_tags

source
human_verified
needs_review
created_at
updated_at
```

Không bắt buộc mỗi dòng phải có đủ cả 3 label.

Ví dụ:

```text
category = BEAUTY
niche = SKINCARE
creator_type = null
```

Dòng này vẫn hợp lệ cho Category/Niche training.

---

# 5. BẮT ĐẦU CODE TRAINING TỪ ĐÂY

Đây là phần làm ngay bây giờ.

## CHECKPOINT A — Chuẩn hóa dataset cuối

### File cần có

```text
ai_development/data/final/master_clean_dataset.csv
```

### `01_prepare_dataset.py` phải làm

```text
1. đọc master_clean_dataset.csv
2. normalize null
3. normalize username
4. tạo text = bio + recent_posts + hashtags
5. loại record không có text
6. tạo category_training.csv từ các dòng có category
7. tạo niche_training.csv từ các dòng có niche
8. tạo creator_type_training.csv từ các dòng có creator_type
9. không tự bịa label còn thiếu
10. in thống kê cuối
```

### Output

```text
category_training.csv
niche_training.csv
creator_type_training.csv
```

Schema tối thiểu của `category_training.csv`:

```text
creator_id
username
text
category
source
human_verified
```

> Với Category v1, không cần đưa followers/ER vào model.

---

# 6. CHECKPOINT B — Data Coverage Checker

Tạo:

```text
02_check_training_readiness.py
```

Pseudo-code:

```python
MIN_SAMPLES_PER_CLASS = 30

counts = df["category"].value_counts()

for label, count in counts.items():
    status = "ML_READY" if count >= MIN_SAMPLES_PER_CLASS else "NOT_READY"
    print(label, count, status)
```

Script phải export thêm:

```text
training_readiness.json
```

Ví dụ:

```json
{
  "BEAUTY": {
    "count": 82,
    "status": "ML_READY"
  },
  "FINANCE": {
    "count": 0,
    "status": "SEMANTIC_ONLY"
  }
}
```

**Chỉ những class `ML_READY` mới được đưa vào `04_train_category_model.py`.**

---

# 7. CHECKPOINT C — Tạo Gold Test Set trước khi train

Đây là bước bắt buộc.

Không dùng toàn bộ `category_training.csv` vừa để train vừa để test.

Tạo:

```text
03_build_category_gold_test.py
```

Mục tiêu:

```text
category_training.csv
        ↓
chọn creator theo từng class
        ↓
human review
        ↓
category_gold_test.csv
```

## Quy tắc

- Một `creator_id` chỉ thuộc **một split**.
- Không được có cùng creator ở train và test.
- Nếu label chưa được người kiểm tra, không gọi là Gold.
- Gold Test Set nên ưu tiên khoảng **15–20%** của những class đủ data.
- Nếu class quá ít, không rút quá nhiều test sample làm train set yếu đi.

Sau human review:

```text
human_verified = true
```

## Output

```text
category_gold_test.csv
category_train_v1.csv
```

---

# 8. CHECKPOINT D — Embedding Service dùng chung

Tạo:

```text
app/ai/services/embedding_service.py
```

Model:

```text
paraphrase-multilingual-MiniLM-L12-v2
```

Vai trò:

```text
text
↓
384-d embedding
```

Embedding này sẽ dùng chung cho:

```text
Category Model
CreatorType Model sau này
Niche Model sau này
Semantic Search
Recommendation similarity
```

Không encode một creator nhiều lần trong cùng pipeline.

---

# 9. CHECKPOINT E — Train Category Model v1

Tạo:

```text
04_train_category_model.py
```

Input:

```text
category_train_v1.csv
training_readiness.json
```

## Training flow

```text
text
↓
Sentence Transformer
↓
384-d embedding
↓
LogisticRegression
↓
predict_proba()
```

## Chỉ train class ML_READY

Pseudo-code:

```python
ready_classes = load_ml_ready_classes()
train_df = train_df[train_df["category"].isin(ready_classes)]
```

## Không dùng random post split

Dataset đang ở creator level thì giữ creator-level split.

Nếu cần validation nội bộ từ `category_train_v1.csv`:

```python
train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42,
    stratify=y
)
```

Nhưng **Gold Test Set không tham gia model fitting hoặc threshold tuning trực tiếp**.

## Model config ban đầu

```python
LogisticRegression(
    max_iter=2000,
    class_weight="balanced",
    random_state=42
)
```

`class_weight="balanced"` giúp giảm ảnh hưởng class imbalance ở baseline.

## Output candidate

```text
artifacts/category_v1_candidate/
├── category_model.joblib
├── label_classes.json
├── training_metrics.json
└── training_config.json
```

---

# 10. CHECKPOINT F — Evaluate Category Model

Tạo:

```text
05_evaluate_category_model.py
```

Evaluate trên:

```text
category_gold_test.csv
```

Bắt buộc report:

```text
Accuracy
Precision per class
Recall per class
F1 per class
Macro F1
Weighted F1
Confusion Matrix
```

Không chỉ nhìn Accuracy.

Export:

```text
artifacts/category_v1_candidate/evaluation.json
artifacts/category_v1_candidate/confusion_matrix.csv
```

---

# 11. CHECKPOINT G — UNKNOWN / Out-of-domain

Tạo:

```text
06_tune_unknown_threshold.py
```

Không hard-code `0.60` như final threshold.

Flow:

```text
predict_proba()
↓
max probability
↓
threshold
↓
known category / UNKNOWN
```

Ví dụ:

```python
if best_probability < CATEGORY_THRESHOLD:
    label = "UNKNOWN"
else:
    label = best_label
```

Tạo riêng OOD test cases:

```text
crypto
pet reptile
agriculture
architecture
law
real estate
```

Mục tiêu là tìm threshold cân bằng giữa:

```text
không reject quá nhiều creator hợp lệ
và
không ép creator lạ vào class đã biết
```

Sau tuning, lưu:

```text
CATEGORY_THRESHOLD = ...
```

vào:

```text
app/ai/config.py
```

---

# 12. CHECKPOINT H — Promote Category Model

Tạo:

```text
07_export_category_model.py
```

Chỉ promote khi candidate đạt tiêu chí tối thiểu đã chốt.

Ví dụ:

```text
Macro F1 >= ngưỡng project đặt ra
không có class recall quá thấp
không có data leakage
Gold test không overlap train
```

Output production:

```text
app/ai/models/category/
├── category_model.joblib
├── metadata.json
└── classes.json
```

`metadata.json` nên chứa:

```json
{
  "modelVersion": "category-v1.0",
  "embeddingModel": "paraphrase-multilingual-MiniLM-L12-v2",
  "threshold": 0.62,
  "macroF1": 0.0,
  "trainedAt": "...",
  "datasetVersion": "dataset-v1"
}
```

`macroF1` ở đây chỉ là placeholder trong tài liệu; code phải ghi metric thật sau evaluate.

---

# 13. Sau Category v1 mới làm CreatorType

Không train CreatorType nếu chưa có ground truth.

## Data requirement

Mục tiêu ban đầu:

```text
BRAND     ~100–150
KOC       ~100–150
KOL       ~100–150
```

Nếu chưa đủ:

```text
creator_type_training.csv
→ NOT_READY_TO_TRAIN
```

## Label policy

```text
BRAND
= account đại diện thương hiệu / tổ chức / shop / official account

KOC
= ảnh hưởng chủ yếu từ review, trải nghiệm, comparison, recommendation, affiliate, shopping

KOL
= ảnh hưởng chủ yếu từ chuyên môn, danh tiếng, nghề nghiệp, public personality, recognized authority

UNKNOWN
= inference fallback; không nên là training class chính ở v1
```

> `followers` không phải rule quyết định KOC/KOL.

Sau khi đủ data, train bằng:

```text
text embedding
+
log1p(followers)
+
engagement_rate
```

---

# 14. Niche build interface ngay nhưng chưa ép train

Interface:

```python
predict_niche(text, category, embedding)
```

V1:

```text
Category
↓
allowed niches từ taxonomy.json
↓
Gemini constrained classification
↓
niche / UNKNOWN
```

Sau này category nào đủ data thì thay engine:

```text
BEAUTY → beauty_niche_model.joblib
TECH   → tech_niche_model.joblib
```

Backend không cần biết engine đổi từ LLM sang ML.

---

# 15. PredictionResult chuẩn từ đầu

Tất cả classifier/service trả chung format:

```python
class PredictionResult:
    label: str
    confidence: float
    engine: str
    needs_review: bool
```

Ví dụ:

```json
{
  "label": "BEAUTY",
  "confidence": 0.91,
  "engine": "ML",
  "needs_review": false
}
```

Điều này giúp sau này đổi ML/LLM/fallback mà `analyzer.py` không phải viết lại.

---

# 16. Retrain sau này: chỉ import CSV mới

Tạo ngay framework dù chưa dùng bây giờ.

## `import_training_csv.py`

Input:

```text
ai_development/data/incoming/*.csv
```

Script phải:

```text
1. validate schema
2. normalize column names
3. normalize labels
4. normalize username
5. dedupe với master dataset
6. detect conflict
7. append valid records
8. conflict → needs_review.csv
9. tạo master dataset version mới
```

## `retrain_pipeline.py`

```text
new master dataset
↓
prepare_dataset
↓
check_training_readiness
↓
train candidate
↓
evaluate
↓
compare current model
↓
PASS → promote
FAIL → giữ model cũ
```

Sau này workflow chỉ còn:

```bash
python import_training_csv.py --input data/incoming/new_training_data.csv
python retrain_pipeline.py
```

---

# 17. Model versioning bắt buộc

Dataset:

```text
datasets/
├── v1/
├── v2/
└── v3/
```

Model:

```text
models/
├── v1/
├── v2/
└── v3/
```

Không overwrite model đang chạy ngay sau khi retrain.

Flow:

```text
train candidate
↓
evaluate
↓
compare current model
↓
PASS → promote
FAIL → keep current
```

---

# 18. Khi Category Model chạy ổn mới nối Online AI

Phần này **chưa phải việc đầu tiên**, nhưng architecture giữ sẵn.

```text
Crawler
↓
Normalize Adapter
↓
AI Worker
↓
prepare_creator_text()
↓
Embedding 384-d
↓
Category
↓
Niche
↓
CreatorType
↓
Semantic Tags + Summary
↓
AIEnrichment
↓
Backend
↓
PostgreSQL / pgvector
```

AI tạo embedding một lần và dùng lại cho:

```text
classification
semantic search
similarity ranking
```

---

# 19. Definition of Done cho Training Phase 1

Chỉ coi **Category Training Phase 1 hoàn thành** khi đạt đủ:

```text
[ ] master_clean_dataset.csv hợp lệ
[ ] category_training.csv được generate tự động
[ ] training_readiness.json được generate
[ ] class chưa đủ data không bị train
[ ] category_gold_test.csv human-verified
[ ] không creator overlap train/test
[ ] embedding service chạy ổn
[ ] category model candidate train thành công
[ ] có predict_proba()
[ ] có Accuracy / Precision / Recall / F1 / Macro F1
[ ] có Confusion Matrix
[ ] có UNKNOWN threshold
[ ] có OOD test
[ ] category_model.joblib được export
[ ] metadata.json lưu model version + metrics + threshold
```

Khi checklist trên hoàn tất, mới chuyển trọng tâm sang CreatorType.

---

# 20. THỨ TỰ CODE TỪ HÔM NAY

Không code ngẫu nhiên. Làm đúng thứ tự này:

## Ngày/Bước 1 — Data pipeline

```text
01_prepare_dataset.py
02_check_training_readiness.py
```

Kết quả cần thấy:

```text
category_training.csv
training_readiness.json
```

## Ngày/Bước 2 — Gold Test

```text
03_build_category_gold_test.py
```

Sau đó manual verify file test.

Kết quả:

```text
category_train_v1.csv
category_gold_test.csv
```

## Ngày/Bước 3 — Embedding + train

```text
embedding_service.py
04_train_category_model.py
```

Kết quả:

```text
category_model.joblib candidate
```

## Ngày/Bước 4 — Evaluation

```text
05_evaluate_category_model.py
```

Kết quả:

```text
evaluation.json
confusion_matrix.csv
```

## Ngày/Bước 5 — UNKNOWN

```text
06_tune_unknown_threshold.py
```

Kết quả:

```text
CATEGORY_THRESHOLD
```

## Ngày/Bước 6 — Export production model

```text
07_export_category_model.py
```

Kết quả:

```text
app/ai/models/category/category_model.joblib
```

## Sau đó

```text
CreatorType data labeling
→ CreatorType training
→ Niche fallback
→ analyzer.py
→ semantic search
→ Crawler/Backend integration
```

---

# 21. Nguyên tắc không được phá trong lúc code

1. **Không tự điền label còn thiếu chỉ để đủ data.**
2. **Không train class có quá ít mẫu.**
3. **Không để cùng creator xuất hiện ở train và gold test.**
4. **Không đánh giá model chỉ bằng Accuracy.**
5. **Không dùng Gold Test Set để fit model.**
6. **Không hard-code threshold vĩnh viễn trước evaluation.**
7. **Không để production đọc CSV để predict.**
8. **Không để Crawler quyết định category/niche/creatorType.**
9. **Không overwrite model cũ trước khi candidate model pass evaluation.**
10. **Không nhồi toàn bộ logic vào `analyzer.py`.**

---

# 22. Kết luận triển khai

Bắt đầu training AI theo đúng thứ tự:

```text
DATA
↓
PREPARE
↓
READINESS CHECK
↓
GOLD TEST
↓
EMBEDDING
↓
CATEGORY MODEL
↓
EVALUATE
↓
UNKNOWN THRESHOLD
↓
EXPORT MODEL V1
```

Sau khi Category v1 chạy thật, giữ nguyên architecture và bổ sung:

```text
CreatorType
Niche
Semantic Search
Online Inference
Crawler / Backend / DB
```

Như vậy hệ thống **được build hoàn chỉnh về architecture ngay từ đầu**, nhưng chỉ train những phần mà data thực sự đủ chất lượng. Sau này có data mới, workflow vẫn chỉ là:

```text
IMPORT CSV
↓
VALIDATE
↓
RETRAIN
↓
EVALUATE
↓
PROMOTE
```

không phải quay lại thiết kế AI từ đầu.
