# ĐẶC TẢ TRIỂN KHAI CREATOR MASTER IDENTITY MANAGEMENT DỰA TRÊN BASE CODE HIỆN CÓ

**Hệ thống:** InfluencerMatch AI  
**Mã nguồn đối soát:**  
- Core Enterprise Backend: `D:\SEP_BE` (Java 17 / Spring Boot 3 / PostgreSQL 16 + pgvector)  
- Async AI & Crawler Engine: `D:\creator-discovery` (Python 3.11 / FastAPI / Celery / Playwright)  
- Web Portal: `D:\INFLUENCERMATCH` (React 19 / TypeScript / Ant Design)  
**Tài liệu tham chiếu:** 10 Flows Creator Master Identity Management  

---

## BẢNG ÁNH XẠ BASE CODE HIỆN TẠI VỚI 10 FLOWS KIẾN TRÚC

| Flow | Tên Flow Nghiệp vụ | File Base Code Hiện Tại | Trạng thái Code Hiện Tại | File Cần Sửa / Tạo Mới |
| :---: | :--- | :--- | :---: | :--- |
| **Flow 1** | Creator Data Acquisition | `tiktok_crawler.py`<br>`youtube_crawler.py`<br>`facebook_crawler.py`<br>`app/core/models.py` | Đã cào được TikTok, YouTube, FB. Chưa có CSV/Manual Import. Thiếu `observedAt`, `crawlRunId`. | Sửa `models.py`<br>Sửa `java_mapper.py` |
| **Flow 2** | Identity Normalization & Validation | `java_mapper.py`<br>`CreatorIngestionRequest.java` | Đã chuẩn hóa cơ bản text BKAI. Facebook dùng slug làm External ID (Unstable). | Sửa `java_mapper.py`<br>Sửa `CreatorIngestionRequest.java` |
| **Flow 3** | Same-Platform Identity Matching & Upsert | `CreatorIngestionService.java`<br>`Creator.java`<br>`CreatorRepository.java` | Chưa an toàn concurrency (dùng `findBy...` rồi `orElseGet`). Bảng `creator` chưa có `master_id`. | Tạo Flyway `V8`, `V9`<br>Tạo `CreatorMaster.java`<br>Sửa `CreatorIngestionService.java` |
| **Flow 4** | Public Intelligence Update (M2) | `PublicCreatorMetric.java`<br>`PostRepository.java` | Chưa có idempotent snapshot window. `search_vector` ở metric, `embedding` ở creator. | Sửa `PublicCreatorMetric.java`<br>Sửa `CreatorIngestionService.java` |
| **Flow 5** | Cross-Platform Candidate Detection | Chưa có | Chưa có logic phát hiện ứng viên nghi trùng. Chưa có bảng review queue. | Tạo `CreatorIdentityReview.java`<br>Tạo `CreatorIdentityReviewRepository.java` |
| **Flow 6** | DATA_MANAGER Identity Review | `RolePermissions.java`<br>`DuplicatesMergeTab.tsx` | Đã có quyền `RESOLVE_CREATOR_DUPLICATE` cho DATA_MANAGER/ADMIN. UI tab mock. | Tạo `AdminCreatorIdentityController.java`<br>Nối UI Frontend |
| **Flow 7** | Creator Master Merge & Audit | Chưa có | Chưa có bảng audit merge. Chưa có logic sáp nhập atomic. | Tạo `IdentityMergeAudit.java`<br>Tạo `CreatorMergeService.java` |
| **Flow 8** | Master Identity Consumption | `CreatorDiscoveryService.java`<br>`PublicCreatorMetricRepository.java`<br>`Collaboration.java` | Discovery query đang `JOIN creator` lấy Account ID. Chưa Group by Master ID. | Sửa `PublicCreatorMetricRepository.java`<br>Sửa `CreatorDiscoveryService.java`<br>Sửa `Collaboration.java` |
| **Flow 9** | Exception Handling & Conflict-Aware Unmerge | Chưa có | Chưa có cơ chế preflight conflict detection và rollback audit. | Tạo `IdentityUnmergeConflict.java`<br>Tạo `CreatorUnmergeService.java` |
| **Flow 10** | Final Identity State | Database PostgreSQL | Toàn vẹn dữ liệu hai cấp: Master UUID ổn định + Platform Account Unique. | Kiểm thử qua 12 Test Cases |

---

## HƯỚNG DẪN TRIỂN KHAI CHI TIẾT TỪNG FLOW DỰA VÀO BASE CODE

---

### FLOW 1 — CREATOR DATA ACQUISITION

#### 1. Hiện trạng Codebase:
- [youtube_crawler.py](file:///d:/creator-discovery/app/crawlers/youtube_crawler.py): Gọi Google API v3 lấy `channel["id"]` (`UC...`) làm `externalId` ổn định 100%.
- [tiktok_crawler.py](file:///d:/creator-discovery/app/crawlers/tiktok_crawler.py): Bóc tách script tag `__UNIVERSAL_DATA_FOR_REHYDRATION__` lấy `user_info.get("id")` (ID số của TikTok). Nếu không bóc tách được (dính captcha), code ném `ValueError` và trả về `None` ngay lập tức.
- [facebook_crawler.py](file:///d:/creator-discovery/app/crawlers/facebook_crawler.py): Dòng 74 gán `externalId = clean_slug`. Đây là định danh **chưa ổn định (Unstable Identity)** vì KOC có thể đổi vanity username.
- [models.py](file:///d:/creator-discovery/app/core/models.py): Class `IngestPayload` hiện chỉ có `collectedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))` và `source: str`. **Chưa có `observedAt` và `crawlRunId`**.

#### 2. Việc cần làm:
1. Cập nhật [d:\creator-discovery\app\core\models.py](file:///d:/creator-discovery/app/core/models.py):
   Bổ sung 2 trường `observedAt` và `crawlRunId`, đồng thời thêm cờ `isStableExternalId: bool = True` vào `CreatorInfo`.
2. Cập nhật [d:\creator-discovery\app\mappers\java_mapper.py](file:///d:/creator-discovery/app/mappers/java_mapper.py):
   Đóng gói thêm `observedAt`, `crawlRunId`, `isStableExternalId`, và `bioCrossLinks`.

```python
# Sửa file: d:\creator-discovery\app\core\models.py
class CreatorInfo(BaseModel):
    platform: Platform
    externalId: str
    isStableExternalId: bool = True  # Facebook slug = False, TikTok/YouTube = True
    username: str
    displayName: str
    profileUrl: str
    avatarUrl: str | None = None
    followers: int
    bio: str | None = None
    publicEmail: str | None = None
    publicPhone: str | None = None
    bioCrossLinks: list[str] = Field(default_factory=list)

class IngestPayload(BaseModel):
    creator: CreatorInfo
    recentPosts: list[Post] = Field(default_factory=list)
    aiEnrichment: AIEnrichment | None = None
    source: str
    crawlRunId: str = Field(default_factory=lambda: f"CR-{uuid.uuid4().hex[:12].upper()}")
    observedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    collectedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

---

### FLOW 2 — IDENTITY NORMALIZATION & VALIDATION

#### 1. Hiện trạng Codebase:
- Trong [java_mapper.py](file:///d:/creator-discovery/app/mappers/java_mapper.py): Đang ghép chuỗi tạo đoạn text ngữ cảnh để gọi `embedding_service.get_search_embedding(...)` sinh vector 768 chiều BKAI Bi-Encoder.
- Trong [SEP_BE/CreatorIngestionRequest.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/creator/dto/CreatorIngestionRequest.java): Chưa có các trường `observedAt`, `crawlRunId`, `isStableExternalId`.

#### 2. Việc cần làm:
1. Cập nhật `CreatorIngestionRequest.java` trong `SEP_BE`:
```java
// Sửa file: D:\SEP_BE\src\main\java\com\influencermatch\backend\creator\dto\CreatorIngestionRequest.java
package com.influencermatch.backend.creator.dto;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import lombok.*;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class CreatorIngestionRequest {
  private String platform;
  private String externalId;
  private Boolean isStableExternalId; // True cho TikTok, YouTube; False cho FB vanity slug
  private String userName;
  private String displayName;
  private String avatarUrl;
  private String creatorType;
  private String location;
  private String profileUrl;

  // Metrics
  private Long followers;
  private BigDecimal avgViews;
  private BigDecimal avgLikes;
  private BigDecimal avgComments;
  private BigDecimal avgShares;
  private BigDecimal engagementRate;

  // Additional data
  private String niche;
  private String contentSummary;
  private String contact;
  private String category;
  private List<String> bioCrossLinks;

  // Data Provenance
  private String source;
  private String crawlRunId;
  private LocalDateTime observedAt;

  private String freshnessStatus;
  private BigDecimal dataConfidence;
  
  private List<PostDto> recentPosts;
  private List<Float> embedding;
}
```

2. Xử lý **Decision B (Thiếu Stable Platform ID / Facebook Slug)**:
   - Trong `CreatorIngestionService.java`, nếu `request.getIsStableExternalId() == false`:
     Đánh dấu tài khoản vào trạng thái cảnh báo danh tính (`identity_status = 'UNVERIFIED_SLUG'`), không tự động coi slug đó là khóa vĩnh cửu.

---

### FLOW 3 — DETERMINISTIC IDENTITY MATCHING & UPSERT

#### 1. Hiện trạng Codebase:
- [Creator.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/creator/model/Creator.java) có: `id` (UUID PK), `platform`, `external_id`, `user_name`, `display_name`, `avatar_url`, `profile_url`, `embedding vector(768)`. Có ràng buộc `@UniqueConstraint(columnNames = {"platform", "external_id"})`. **Chưa có trường `master_id`**.
- [CreatorIngestionService.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/creator/service/CreatorIngestionService.java):
  ```java
  Creator creator = creatorRepository.findByPlatformAndExternalId(request.getPlatform(), request.getExternalId())
      .orElseGet(() -> Creator.builder()...build());
  ```
  👉 **Lỗ hổng Concurrency**: Nếu 2 request chạy đồng thời cùng cào 1 KOC, cả 2 thread đều không tìm thấy bản ghi, cùng chạy `orElseGet` và cùng tạo 2 Master mới, sau đó thread thứ hai bị văng lỗi `DataIntegrityViolationException` tại database.

#### 2. Việc cần làm:

##### Bước 3.1: Viết Flyway Migration `V8__expand_creator_master_model.sql`
Tạo bảng `creator_master`, thêm nullable `master_id` vào `creator`, backfill với **UUID Master độc lập hoàn toàn** thông qua bảng tạm:

```sql
-- File: D:\SEP_BE\src\main\resources\db\migration\V8__expand_creator_master_model.sql

-- 1. Tạo bảng Creator Master
CREATE TABLE creator_master (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 2. Thêm master_id dạng NULLABLE vào creator
ALTER TABLE creator ADD COLUMN master_id UUID;
ALTER TABLE creator ADD COLUMN is_stable_id BOOLEAN NOT NULL DEFAULT TRUE;

-- 3. Backfill dữ liệu: Tạo UUID MỚI HOÀN TOÀN cho Master
CREATE TEMP TABLE temp_account_master_map AS
SELECT 
    id AS account_id,
    gen_random_uuid() AS new_master_id,
    COALESCE(display_name, user_name, 'Unknown Creator') AS canonical_name,
    created_at,
    updated_at
FROM creator;

INSERT INTO creator_master (id, canonical_name, status, created_at, updated_at)
SELECT new_master_id, canonical_name, 'ACTIVE', created_at, updated_at
FROM temp_account_master_map;

UPDATE creator c
SET master_id = m.new_master_id
FROM temp_account_master_map m
WHERE c.id = m.account_id;

CREATE INDEX idx_creator_master_id ON creator(master_id);

-- 4. Bổ sung Dual Reference (Nullable) cho M1 & Shortlist
ALTER TABLE shortlist_item ADD COLUMN master_id UUID;
UPDATE shortlist_item s SET master_id = c.master_id FROM creator c WHERE s.creator_id = c.id;
CREATE INDEX idx_shortlist_master_id ON shortlist_item(master_id);

ALTER TABLE relationship ADD COLUMN master_id UUID;
UPDATE relationship r SET master_id = c.master_id FROM creator c WHERE r.creator_id = c.id;
CREATE INDEX idx_relationship_master_id ON relationship(master_id);

ALTER TABLE collaboration ADD COLUMN master_id UUID;
UPDATE collaboration col SET master_id = c.master_id FROM creator c WHERE col.creator_id = c.id;
CREATE INDEX idx_collaboration_master_id ON collaboration(master_id);
ALTER TABLE collaboration ALTER COLUMN creator_id DROP NOT NULL;

ALTER TABLE historical_campaign ADD COLUMN master_id UUID;
UPDATE historical_campaign h SET master_id = c.master_id FROM creator c WHERE h.creator_id = c.id;
CREATE INDEX idx_historical_master_id ON historical_campaign(master_id);
ALTER TABLE historical_campaign ALTER COLUMN creator_id DROP NOT NULL;

-- 5. Bổ sung Provenance cho public_creator_metric
ALTER TABLE public_creator_metric ADD COLUMN crawl_run_id VARCHAR(64);
ALTER TABLE public_creator_metric ADD COLUMN observed_at TIMESTAMP;

-- 6. Hàng đợi Candidate Review & Audit
CREATE TABLE creator_identity_review (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_creator_id UUID NOT NULL REFERENCES creator(id),
    target_master_id UUID NOT NULL REFERENCES creator_master(id),
    heuristic_score DOUBLE PRECISION NOT NULL,
    match_evidence JSONB NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    reviewed_by UUID REFERENCES "user"(id),
    reviewed_at TIMESTAMP,
    review_note TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_review_candidate UNIQUE (source_creator_id, target_master_id)
);
CREATE INDEX idx_identity_review_status ON creator_identity_review(status);

CREATE TABLE identity_merge_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merge_event_code VARCHAR(64) NOT NULL UNIQUE,
    target_master_id UUID NOT NULL REFERENCES creator_master(id),
    source_master_id UUID NOT NULL,
    merged_account_ids JSONB NOT NULL,
    pre_merge_snapshot JSONB NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    performed_by UUID NOT NULL REFERENCES "user"(id),
    unmerged_by UUID REFERENCES "user"(id),
    merged_at TIMESTAMP NOT NULL DEFAULT NOW(),
    unmerged_at TIMESTAMP,
    unmerge_resolution_notes TEXT
);

CREATE TABLE identity_unmerge_conflict (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merge_audit_id UUID NOT NULL REFERENCES identity_merge_audit(id),
    entity_type VARCHAR(64) NOT NULL,
    entity_id UUID NOT NULL,
    conflict_reason VARCHAR(255) NOT NULL,
    conflict_data JSONB NOT NULL,
    resolution_status VARCHAR(32) NOT NULL DEFAULT 'PENDING_ADMIN',
    resolved_by UUID REFERENCES "user"(id),
    resolved_at TIMESTAMP,
    resolution_notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

DROP TABLE temp_account_master_map;
```

##### Bước 3.2: Tạo Entity Java `CreatorMaster.java`
Tạo mới file tại `D:\SEP_BE\src\main\java\com\influencermatch\backend\creator\model\CreatorMaster.java`:
```java
package com.influencermatch.backend.creator.model;

import com.influencermatch.backend.creator.enums.MasterStatus;
import jakarta.persistence.*;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;
import lombok.*;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

@Entity
@Table(name = "creator_master")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class CreatorMaster {

  @Id
  @GeneratedValue(strategy = GenerationType.UUID)
  private UUID id;

  @Column(name = "canonical_name", nullable = false)
  private String canonicalName;

  @Enumerated(EnumType.STRING)
  @Column(nullable = false, length = 32)
  private MasterStatus status; // ACTIVE, MERGED, SUSPENDED

  @Column(columnDefinition = "TEXT")
  private String notes;

  @OneToMany(mappedBy = "master", fetch = FetchType.LAZY)
  private List<Creator> accounts;

  @CreationTimestamp
  @Column(name = "created_at", nullable = false, updatable = false)
  private LocalDateTime createdAt;

  @UpdateTimestamp
  @Column(name = "updated_at", nullable = false)
  private LocalDateTime updatedAt;
}
```

##### Bước 3.3: Thêm Khóa Phân Tán / Advisory Lock trong `CreatorRepository.java`
```java
// Sửa file: D:\SEP_BE\src\main\java\com\influencermatch\backend\creator\repository\CreatorRepository.java
package com.influencermatch.backend.creator.repository;

import com.influencermatch.backend.creator.model.Creator;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

@Repository
public interface CreatorRepository extends JpaRepository<Creator, UUID> {
  Optional<Creator> findByPlatformAndExternalId(String platform, String externalId);

  // PostgreSQL Transaction-scoped Advisory Lock (Thời gian thực thi < 0.1ms trong RAM)
  @Query(value = "SELECT pg_advisory_xact_lock(hashtext(:lockKey))", nativeQuery = true)
  void acquireIdentityLock(@Param("lockKey") String lockKey);
}
```

##### Bước 3.4: Viết lại `CreatorIngestionService.java` (Idempotent & Concurrency Safe)
```java
// Sửa file: D:\SEP_BE\src\main\java\com\influencermatch\backend\creator\service\CreatorIngestionService.java
@Transactional(isolation = Isolation.READ_COMMITTED)
public CreatorIngestionResponse ingestCreatorData(CreatorIngestionRequest request) {
    // 1. Khóa theo Identity Key trong RAM PostgreSQL (< 0.1ms)
    String lockKey = request.getPlatform() + ":" + request.getExternalId();
    creatorRepository.acquireIdentityLock(lockKey);

    // 2. Safe Account Lookup
    Optional<Creator> existingOpt = creatorRepository
        .findByPlatformAndExternalId(request.getPlatform(), request.getExternalId());

    Creator creator;
    if (existingOpt.isPresent()) {
        // Case A — Account Exists: Cập nhật metadata, giữ nguyên Master ID
        creator = existingOpt.get();
        creator.setUserName(request.getUserName());
        if (request.getDisplayName() != null) creator.setDisplayName(request.getDisplayName());
        if (request.getAvatarUrl() != null) creator.setAvatarUrl(request.getAvatarUrl());
        if (request.getProfileUrl() != null) creator.setProfileUrl(request.getProfileUrl());
        creator = creatorRepository.save(creator);
    } else {
        // Case B — Account Does Not Exist: Tạo Master độc lập (UUID mới) + Account
        CreatorMaster master = CreatorMaster.builder()
            .canonicalName(request.getDisplayName() != null ? request.getDisplayName() : request.getUserName())
            .status(MasterStatus.ACTIVE)
            .build();
        master = creatorMasterRepository.save(master);

        creator = Creator.builder()
            .master(master)
            .platform(request.getPlatform())
            .externalId(request.getExternalId())
            .userName(request.getUserName())
            .displayName(request.getDisplayName())
            .avatarUrl(request.getAvatarUrl())
            .profileUrl(request.getProfileUrl())
            .location(request.getLocation())
            .build();
        creator = creatorRepository.save(creator);

        // Kích hoạt Flow 5: Quét ứng viên nghi trùng xuyên nền tảng
        detectAndQueueCandidates(creator, request);
    }

    // Chuyển sang Flow 4: Lưu Snapshot M2
    updatePublicIntelligence(creator, request);

    return CreatorIngestionResponse.from(creator);
}
```

---

### FLOW 4 — PUBLIC CREATOR INTELLIGENCE UPDATE (M2)

#### 1. Hiện trạng Codebase:
- [PublicCreatorMetric.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/creator/model/PublicCreatorMetric.java) đang lưu: `followers`, `avg_views`, `engagement_rate`, `content_summary`, `niche`, `category`, `data_confidence`, `freshness_status`, `collected_at`. Cột `search_vector` là Generated Column tự động cập nhật.
- Logic hiện tại của Java đang tạo bản ghi mới bất chấp lần crawl trước cách bao lâu.

#### 2. Việc cần làm trong `CreatorIngestionService.java`:
1. **Kiểm tra Out-of-order Updates**: Nếu DB đã có snapshot với `observed_at > request.getObservedAt()`, từ chối ghi đè lên current metrics.
2. **Snapshot Idempotency**: Kiểm tra cặp `(account_id, crawl_run_id)`. Nếu cùng `crawl_run_id` gửi lại, bỏ qua không tạo snapshot trùng.

```java
private void updatePublicIntelligence(Creator creator, CreatorIngestionRequest request) {
    // 1. Chống lặp theo crawlRunId
    if (request.getCrawlRunId() != null) {
        boolean duplicateRun = metricRepository.existsByCreatorIdAndCrawlRunId(
            creator.getId(), request.getCrawlRunId());
        if (duplicateRun) {
            log.info("Bo qua ingest duplicate crawlRunId: {}", request.getCrawlRunId());
            return;
        }
    }

    // 2. Chống Out-of-order Updates
    if (request.getObservedAt() != null) {
        Optional<PublicCreatorMetric> latestOpt = metricRepository.findTopByCreatorIdOrderByObservedAtDesc(creator.getId());
        if (latestOpt.isPresent() && latestOpt.get().getObservedAt() != null) {
            if (request.getObservedAt().isBefore(latestOpt.get().getObservedAt())) {
                log.warn("Payload den muon (observedAt {} < latest {}). Luu vao lich su, khong cap nhat active status.",
                    request.getObservedAt(), latestOpt.get().getObservedAt());
            }
        }
    }

    // 3. Tạo snapshot M2 gắn chặt với Platform Account (creator)
    PublicCreatorMetric metric = PublicCreatorMetric.builder()
        .creator(creator)
        .followers(request.getFollowers())
        .avgViews(request.getAvgViews())
        .avgLikes(request.getAvgLikes())
        .avgComments(request.getAvgComments())
        .avgShares(request.getAvgShares())
        .engagementRate(request.getEngagementRate())
        .niche(request.getNiche())
        .category(request.getCategory())
        .contact(request.getContact())
        .crawlRunId(request.getCrawlRunId())
        .observedAt(request.getObservedAt() != null ? request.getObservedAt() : LocalDateTime.now())
        .collectedAt(LocalDateTime.now())
        .dataConfidence(request.getDataConfidence())
        .freshnessStatus(MetricDataStatus.FRESH)
        .build();
    metricRepository.save(metric);

    // 4. Lưu bài đăng gần đây vào bảng post
    if (request.getRecentPosts() != null && !request.getRecentPosts().isEmpty()) {
        saveRecentPostsIdempotent(creator, request.getRecentPosts());
    }
}
```

---

### FLOW 5 — CROSS-PLATFORM CANDIDATE DETECTION

#### 1. Hiện trạng Codebase:
- Hệ thống hiện tại **hoàn toàn chưa có bảng và logic này**.

#### 2. Việc cần làm:
1. Tạo Entity `CreatorIdentityReview.java` tại `D:\SEP_BE\src\main\java\com\influencermatch\backend\creator\model\CreatorIdentityReview.java`:
```java
package com.influencermatch.backend.creator.model;

import jakarta.persistence.*;
import java.time.LocalDateTime;
import java.util.UUID;
import lombok.*;
import org.hibernate.annotations.CreationTimestamp;

@Entity
@Table(
    name = "creator_identity_review",
    uniqueConstraints = {@UniqueConstraint(columnNames = {"source_creator_id", "target_master_id"})})
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class CreatorIdentityReview {

  @Id
  @GeneratedValue(strategy = GenerationType.UUID)
  private UUID id;

  @ManyToOne(fetch = FetchType.LAZY, optional = false)
  @JoinColumn(name = "source_creator_id", nullable = false)
  private Creator sourceCreator;

  @ManyToOne(fetch = FetchType.LAZY, optional = false)
  @JoinColumn(name = "target_master_id", nullable = false)
  private CreatorMaster targetMaster;

  @Column(name = "heuristic_score", nullable = false)
  private Double heuristicScore; // Điểm gợi ý tham khảo (không phải xác suất)

  @Column(name = "match_evidence", columnDefinition = "JSONB", nullable = false)
  private String matchEvidence; // JSON lưu các bằng chứng

  @Column(nullable = false, length = 32)
  private String status; // PENDING, APPROVED, REJECTED, IGNORED

  @Column(name = "reviewed_by")
  private UUID reviewedBy;

  @Column(name = "reviewed_at")
  private LocalDateTime reviewedAt;

  @Column(name = "review_note", columnDefinition = "TEXT")
  private String reviewNote;

  @CreationTimestamp
  @Column(name = "created_at", nullable = false, updatable = false)
  private LocalDateTime createdAt;
}
```

2. Logic phát hiện ứng viên trong `detectAndQueueCandidates`:
- Quét danh sách có chung booking email: `metricRepository.findAllByContactAndCreatorNot(...)`.
- Kiểm tra danh sách bio link chéo (ví dụ: bio TikTok chứa link Instagram).
- **Loại trừ**:
  - Không tạo nếu targetMaster chính là Master của tài khoản hiện tại.
  - Không tạo nếu cặp này đã từng có bản ghi với trạng thái `REJECTED` hoặc `IGNORED`.
  - Không tạo nếu email nằm trong danh sách email agency dùng chung (Agency Whitelist).

---

### FLOW 6 — DATA_MANAGER IDENTITY REVIEW

#### 1. Hiện trạng Codebase:
- [RolePermissions.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/security/RolePermissions.java): Đã có sẵn quyền `Permission.RESOLVE_CREATOR_DUPLICATE` cho cả `Role.DATA_MANAGER` và `Role.ADMIN`.
- [DuplicatesMergeTab.tsx](file:///d:/INFLUENCERMATCH/src/pages/admin/creators/components/DuplicatesMergeTab.tsx): Frontend đã có giao diện danh sách nghi trùng side-by-side nhưng đang dùng mock data.

#### 2. Việc cần làm:
1. Tạo Controller mới `AdminCreatorIdentityController.java` trong `SEP_BE`:
```java
// File: D:\SEP_BE\src\main\java\com\influencermatch\backend\creator\controller\AdminCreatorIdentityController.java
package com.influencermatch.backend.creator.controller;

import com.influencermatch.backend.creator.dto.*;
import com.influencermatch.backend.creator.service.CreatorIdentityReviewService;
import com.influencermatch.backend.creator.service.CreatorMergeService;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/admin/creator-identities")
@RequiredArgsConstructor
public class AdminCreatorIdentityController {

  private final CreatorIdentityReviewService reviewService;
  private final CreatorMergeService mergeService;

  @GetMapping("/reviews")
  @PreAuthorize("hasAuthority('RESOLVE_CREATOR_DUPLICATE')")
  public ResponseEntity<Page<IdentityReviewResponse>> getPendingReviews(Pageable pageable) {
    return ResponseEntity.ok(reviewService.getPendingReviews(pageable));
  }

  @PostMapping("/reviews/{id}/decision")
  @PreAuthorize("hasAuthority('RESOLVE_CREATOR_DUPLICATE')")
  public ResponseEntity<Void> processReviewDecision(
      @PathVariable UUID id,
      @RequestBody ReviewDecisionRequest request,
      @AuthenticationPrincipal UUID userId) {
    reviewService.processDecision(id, request, userId);
    return ResponseEntity.noContent().build();
  }
}
```

2. Các quyết định của `DATA_MANAGER`:
- **Decision A (APPROVE)**: Chuyển thẳng sang Flow 7 (Thực thi Merge).
- **Decision B (REJECT)**: Cập nhật `status = 'REJECTED'`, lưu `review_note`. Hệ thống không bao giờ tự sinh lại bản ghi này từ các job crawl sau.
- **Decision C (NEED MORE EVIDENCE)**: Cập nhật `status = 'PENDING_EVIDENCE'`.

---

### FLOW 7 — CREATOR MASTER MERGE & AUDIT

#### 1. Hiện trạng Codebase:
- Chưa có service thực hiện gộp Master an toàn trong transaction.

#### 2. Việc cần làm:
1. Tạo Entity `IdentityMergeAudit.java` tại `D:\SEP_BE\src\main\java\com\influencermatch\backend\creator\model\IdentityMergeAudit.java`.
2. Tạo Service `CreatorMergeService.java` thực hiện trong 1 transaction nguyên tử:

```java
// File: D:\SEP_BE\src\main\java\com\influencermatch\backend\creator\service\CreatorMergeService.java
@Service
@RequiredArgsConstructor
public class CreatorMergeService {

  private final CreatorMasterRepository masterRepository;
  private final CreatorRepository creatorRepository;
  private final IdentityMergeAuditRepository auditRepository;
  private final ShortlistItemRepository shortlistRepository;
  private final CollaborationRepository collaborationRepository;

  @Transactional
  public void executeMerge(UUID targetMasterId, UUID sourceMasterId, String customCanonicalName, UUID adminId) {
    // 1. Pre-merge Validation
    if (targetMasterId.equals(sourceMasterId)) {
      throw new IllegalArgumentException("Khong the merge mot Master vao chinh no.");
    }
    CreatorMaster targetMaster = masterRepository.findById(targetMasterId)
        .orElseThrow(() -> new ResourceNotFoundException("Target Master", "id", targetMasterId));
    CreatorMaster sourceMaster = masterRepository.findById(sourceMasterId)
        .orElseThrow(() -> new ResourceNotFoundException("Source Master", "id", sourceMasterId));

    if (sourceMaster.getStatus() == MasterStatus.MERGED) {
      throw new IllegalStateException("Source Master da o trang thai MERGED truoc do.");
    }

    // 2. Lưu Pre-merge Snapshot (Dùng JSONB)
    List<Creator> sourceAccounts = creatorRepository.findAllByMasterId(sourceMasterId);
    List<UUID> accountIds = sourceAccounts.stream().map(Creator::getId).toList();

    String snapshotJson = buildPreMergeSnapshot(sourceMasterId, accountIds);
    String eventCode = "MRG-" + System.currentTimeMillis();

    IdentityMergeAudit audit = IdentityMergeAudit.builder()
        .mergeEventCode(eventCode)
        .targetMaster(targetMaster)
        .sourceMasterId(sourceMasterId)
        .mergedAccountIds(JsonUtils.toJson(accountIds))
        .preMergeSnapshot(snapshotJson)
        .status("ACTIVE")
        .performedBy(adminId)
        .build();
    auditRepository.save(audit);

    // 3. Chuyển Platform Accounts của Source sang Target
    for (Creator acc : sourceAccounts) {
      acc.setMaster(targetMaster);
      creatorRepository.save(acc);
    }

    // 4. Chuyển Master Reference trong Shortlist, Collaboration, CRM
    shortlistRepository.updateMasterId(sourceMasterId, targetMasterId);
    collaborationRepository.updateMasterId(sourceMasterId, targetMasterId);

    // 5. Cập nhật trạng thái Master
    sourceMaster.setStatus(MasterStatus.MERGED);
    sourceMaster.setNotes("Merged into Master ID: " + targetMasterId + " via Event: " + eventCode);
    masterRepository.save(sourceMaster);

    if (customCanonicalName != null && !customCanonicalName.isBlank()) {
      targetMaster.setCanonicalName(customCanonicalName);
      masterRepository.save(targetMaster);
    }
  }
}
```

---

### FLOW 8 — MASTER IDENTITY CONSUMPTION

#### 1. Hiện trạng Codebase:
- [CreatorDiscoveryService.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/discovery/service/CreatorDiscoveryService.java): Đang gọi native query trong [PublicCreatorMetricRepository.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/creator/repository/PublicCreatorMetricRepository.java). Query này `JOIN creator c ON m.creator_id = c.id`, trả về từng dòng là 1 Account.
- Nếu một Creator có cả TikTok và YouTube cùng đạt điểm cao, trang Discovery hiện tại sẽ hiển thị **2 Creator Cards trùng nhau**!

#### 2. Việc cần làm:
1. Cập nhật Native Query trong `PublicCreatorMetricRepository.java`:
   Sử dụng PostgreSQL `DISTINCT ON (c.master_id)` để **nhóm kết quả theo Master**, chỉ chọn ra 1 kênh xuất sắc nhất làm `recommended_channel`:

```sql
-- Cập nhật query trong PublicCreatorMetricRepository.java
SELECT DISTINCT ON (c.master_id)
    m.id AS id,
    c.master_id AS masterId,
    cm.canonical_name AS canonicalName,
    c.id AS recommendedAccountId,
    c.platform AS recommendedPlatform,
    c.user_name AS recommendedUserName,
    c.avatar_url AS avatarUrl,
    m.followers AS followers,
    m.engagement_rate AS engagementRate,
    -- Điểm Hybrid Match Score (Semantic + FTS)
    (0.6 * (1 - (c.embedding <=> cast(NULLIF(:queryVector, '') as vector))) + 
     0.4 * ts_rank(m.search_vector, to_tsquery('simple', :formattedKeyword))) AS matchScore
FROM public_creator_metric m
JOIN creator c ON m.creator_id = c.id
JOIN creator_master cm ON c.master_id = cm.id
WHERE 
    cm.status = 'ACTIVE'
    AND (:hasPlatforms = false OR c.platform IN (:platforms))
    AND (:followerMin IS NULL OR m.followers >= :followerMin)
    AND (:followerMax IS NULL OR m.followers <= :followerMax)
ORDER BY 
    c.master_id,
    -- Ưu tiên kênh có Match Score cao nhất đại diện cho Master
    (0.6 * (1 - (c.embedding <=> cast(NULLIF(:queryVector, '') as vector))) + 
     0.4 * ts_rank(m.search_vector, to_tsquery('simple', :formattedKeyword))) DESC;
```

2. Cập nhật Shortlist & Collaboration:
- Khi Brand click *"Thêm vào Shortlist"*: Lưu `master_id` (đại diện con người) và `creator_id` (kênh được gợi ý).
- Ràng buộc: `UNIQUE (campaign_id, master_id)` trên `shortlist_item` để đảm bảo 1 KOC chỉ xuất hiện 1 dòng trong Shortlist của 1 chiến dịch.

---

### FLOW 9 — EXCEPTION HANDLING & CONFLICT-AWARE UNMERGE

#### 1. Hiện trạng Codebase:
- Chưa có flow hoàn tác sáp nhập.

#### 2. Việc cần làm:
Tạo Service `CreatorUnmergeService.java` theo đúng **quy trình 4 bước có kiểm soát xung đột (Preflight Conflict Detection)**:

```
[Admin Yêu cầu Unmerge Event Code]
            │
            ▼
[Bước 1: Preflight Conflict Check]
- Kiểm tra các Collaboration có master_id = TargetMaster tạo SAU ngày merge.
- Nếu Collaboration có creator_id trỏ về kênh cụ thể -> Không xung đột (tự phân bổ).
- Nếu Collaboration có creator_id = NULL (hợp đồng gói chưa chọn kênh) -> PHÁT HIỆN XUNG ĐỘT!
            │
      ┌─────┴─────────────────────────────────────┐
      ▼ CÓ XUNG ĐỘT                               ▼ KHÔNG XUNG ĐỘT
[Ghi identity_unmerge_conflict]             [Bắt đầu Transaction Unmerge]
[Dừng lại, yêu cầu Admin giải quyết]         1. Chuyển Account cũ về Source Master
                                            2. Khôi phục Source Master = 'ACTIVE'
                                            3. Đưa Hợp đồng cũ về đúng chủ sở hữu
                                            4. Đặt Audit Status = 'REVERTED'
```

Mã nguồn Preflight trong `CreatorUnmergeService.java`:
```java
// File: D:\SEP_BE\src\main\java\com\influencermatch\backend\creator\service\CreatorUnmergeService.java
@Service
@RequiredArgsConstructor
public class CreatorUnmergeService {

  private final IdentityMergeAuditRepository auditRepository;
  private final IdentityUnmergeConflictRepository conflictRepository;
  private final CollaborationRepository collaborationRepository;
  private final CreatorRepository creatorRepository;
  private final CreatorMasterRepository masterRepository;

  @Transactional(readOnly = true)
  public PreflightUnmergeResult preflightCheck(UUID mergeAuditId) {
    IdentityMergeAudit audit = auditRepository.findById(mergeAuditId)
        .orElseThrow(() -> new ResourceNotFoundException("MergeAudit", "id", mergeAuditId));

    List<Collaboration> postMergeCollabs = collaborationRepository
        .findAllByMasterIdAndCreatedAtAfter(audit.getTargetMaster().getId(), audit.getMergedAt());

    List<ConflictItem> conflicts = new ArrayList<>();
    for (Collaboration col : postMergeCollabs) {
      if (col.getChannelAccount() == null) {
        // Hợp đồng cấp Master không chỉ định kênh xuất bản -> Không thể tự động phân bổ!
        conflicts.add(new ConflictItem("COLLABORATION", col.getId(), "Hợp đồng tạo sau merge chưa rõ kênh triển khai."));
      }
    }

    return new PreflightUnmergeResult(conflicts.isEmpty(), conflicts);
  }
}
```

---

### FLOW 10 — FINAL IDENTITY STATE & TIÊU CHUẨN NGHIỆM THU

Sau khi hoàn tất cả 10 Flows, trạng thái danh tính của hệ thống đạt chuẩn:
1. **Một Platform Account**: Có duy nhất 1 bản ghi trong bảng `creator` với `(platform, external_id)` bất biến.
2. **Một Master Identity**: Có 1 bản ghi trong bảng `creator_master`. Một Master sở hữu 1 hoặc nhiều Platform Accounts.
3. **M1 – CRM & Collaboration**: Gắn chặt với `master_id` (con người ký kết) và tùy chọn `creator_id` (kênh đăng bài).
4. **M2 – Public Intelligence & Embedding**: Gắn chặt với từng `creator_id` (tài khoản mạng xã hội).
5. **Đầy đủ khả năng truy vết**: Mọi thao tác sáp nhập đều có snapshot nguyên tử tại `identity_merge_audit` và có thể hoàn tác an toàn qua Preflight Conflict Resolution.
