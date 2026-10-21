# CREATOR MASTER IDENTITY MANAGEMENT
## CLAIM-BASED IDENTITY & OAUTH ACCOUNT LINKING – CODEBASE AUDIT & IMPLEMENTATION CHECKLIST

**Hệ thống:** InfluencerMatch AI (SEP490)  
**Mã nguồn đối soát trực tiếp:**  
- Core Enterprise Backend: `D:\SEP_BE` (Java 17 / Spring Boot 3 / PostgreSQL 16 + pgvector / Flyway)  
- Async AI & Crawler Engine: `D:\creator-discovery` (Python 3.11 / FastAPI / Celery / Playwright)  
- Frontend Portal: `D:\INFLUENCERMATCH` (React 19 / TypeScript / Ant Design)  
**Phương pháp cốt lõi:** Public Account Registry → Invitation → OAuth Verification → Creator Master Creation → Cross-platform Account Linking  

---

## MỤC LỤC
1. [Sự Thay Đổi Triết Lý Kiến Trúc (Paradigm Shift)](#1-sự-thay-đổi-triết-lý-kiến-trúc-paradigm-shift)
2. [Bảng Đối Soát Hiện Trạng Mã Nguồn (Codebase Gap Analysis)](#2-bảng-đối-soát-hiện-trạng-mã-nguồn-codebase-gap-analysis)
3. [Checklist Triển Khai Cho `creator-discovery` (Python)](#3-checklist-triển-khai-cho-creator-discovery-python)
4. [Checklist Triển Khai Cho `SEP_BE` (Java Spring Boot)](#4-checklist-triển-khai-cho-sep_be-java-spring-boot)
   - [4.1. Database Migrations (Flyway V8 & V9)](#41-database-migrations-flyway-v8--v9)
   - [4.2. Ingestion Pipeline Decoupling](#42-ingestion-pipeline-decoupling)
   - [4.3. Discovery Hybrid Grouping](#43-discovery-hybrid-grouping)
   - [4.4. Invitation & Profile Claim Engine](#44-invitation--profile-claim-engine)
   - [4.5. OAuth Verification & Atomic Master Creation](#45-oauth-verification--atomic-master-creation)
   - [4.6. Connect Additional Accounts Service](#46-connect-additional-accounts-service)
   - [4.7. Dual References & CRM/Shortlist Adaptation](#47-dual-references--crmshortlist-adaptation)
   - [4.8. Data Manager Conflict Resolution](#48-data-manager-conflict-resolution)
5. [Giải Quyết Bẫy Kỹ Thuật (Technical Gotchas & Traps)](#5-giải-quyết-bẫy-kỹ-thuật-technical-gotchas--traps)
6. [Bảng Kiểm Thử Nghiệm Thu (Acceptance Testing Matrix - 18 TCs)](#6-bảng-kiểm-thử-nghiệm-thu-acceptance-testing-matrix---18-tcs)
7. [Lộ Trình Triển Khai Review 2 (3-Week Implementation Roadmap)](#7-lộ-trình-triển-khai-review-2-3-week-implementation-roadmap)

---

## 1. SỰ THAY ĐỔI TRIẾT LÝ KIẾN TRÚC (PARADIGM SHIFT)

```
CƠ CHẾ CŨ (Auto-create Master on Crawl):
Crawler cào KOC mới ──> Tạo CreatorMaster ngay ──> 1 KOC đa kênh bị tách thành nhiều Master ──> Data Manager duyệt Merge thủ công.
(Hệ quả: Phình to Master rác, phụ thuộc phán đoán chủ quan của con người khi merge).

CƠ CHẾ MỚI (Claim-based & OAuth Account Linking):
Crawler cào KOC mới ──> Lưu Platform Account (master_id = NULL) ──> Vẫn tham gia Discovery bình thường.
Brand Shortlist / Mời KOC ──> KOC nhận email & đăng nhập OAuth tài khoản đầu tiên ──> Xác thực quyền kiểm soát ──> Tạo CreatorMaster lần đầu.
KOC vào Portal kết nối kênh thứ 2, 3 qua OAuth ──> Backend đối chiếu Account đã cào trước đó ──> Tự động liên kết vào cùng Master.
(Ưu điểm: Không sinh Master rác, liên kết dựa trên bằng chứng kỹ thuật Proof of Control, giảm 90% tải cho Data Manager).
```

---

## 2. BẢNG ĐỐI SOÁT HIỆN TRẠNG MÃ NGUỒN (CODEBASE GAP ANALYSIS)

| Hạng mục / Module | Hiện trạng trong Codebase (`SEP_BE` & `creator-discovery`) | Tác động theo Phương án Claim-Based Mới | Đánh giá |
| :--- | :--- | :--- | :---: |
| **Bảng `creator`** | Đang lưu trực tiếp `platform`, `external_id`, `embedding vector(768)`. Chưa có cột `master_id`. | Thêm cột `master_id UUID NULL REFERENCES creator_master(id)`. Tuyệt đối không để `NOT NULL`. | 🟡 Cần sửa migration |
| **Cột `search_vector`** | Đang nằm ở bảng `public_creator_metric` (Generated Column FTS `tsvector` từ V5). | Giữ nguyên tại `public_creator_metric`. Không đụng chạm vào bảng `creator`. | 🟢 Hoàn toàn tương thích |
| **Ingestion Service** | `findByPlatformAndExternalId` -> nếu chưa có thì tạo mới `Creator`. | Sửa: Tạo `Creator` với `master_id = null`. Không tạo `CreatorMaster`. Thêm advisory lock chống race condition. | 🟡 Cần sửa code Java |
| **Discovery Query** | `JOIN creator c ON m.creator_id = c.id` -> trả về từng Account đơn lẻ. | Sửa: Thêm Hybrid Grouping: `COALESCE('MASTER:' || c.master_id, 'ACCOUNT:' || c.id)` để KOC chưa claim vẫn được tìm thấy. | 🟡 Cần sửa query |
| **Google OAuth Login** | Đã có Flyway `V3__google_oauth2_login.sql` với bảng `external_identity` và `auth_exchange_code`. | Kế thừa kiến trúc token exchange này để xây dựng bảng `creator_provider_identity` cho Creator OAuth. | 🟢 Nền tảng sẵn có |
| **Lời mời (Invitation)** | Chưa có bảng hoặc logic nào trong cả 2 repo. | Tạo bảng `creator_invitation` và service sinh/băm token mời xác minh hồ sơ. | 🔴 Cần viết mới |
| **Creator Portal User** | `RolePermissions.java` chỉ có `BRAND`, `DATA_MANAGER`, `ADMIN`. Chưa có role `CREATOR`. | Tạo `creator_master_member` để phân quyền Portal User quản lý Creator Master. | 🔴 Cần viết mới |
| **Liên kết đa kênh** | Chưa có logic nhận OAuth kênh thứ 2 và link vào Master. | Tạo `CreatorAccountLinkService.java` xử lý 4 kịch bản liên kết tài khoản. | 🔴 Cần viết mới |
| **Facebook Crawler** | Dòng 74 của `facebook_crawler.py` đang dùng `clean_slug` làm `externalId`. | Đánh dấu `isStableExternalId = False` để phân loại vào hồ sơ chờ xác minh định danh (Provisional). | 🟡 Cần sửa code Python |

---

## 3. CHECKLIST TRIỂN KHAI CHO `creator-discovery` (PYTHON)

> **Nguyên tắc**: `creator-discovery` hoàn toàn độc lập với khái niệm Master. Nó chỉ chịu trách nhiệm cào dữ liệu công khai và đẩy sang Java.

- [ ] **Tệp [d:\creator-discovery\app\core\models.py](file:///d:/creator-discovery/app/core/models.py)**:
  ```python
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
- [ ] **Tệp [d:\creator-discovery\app\crawlers\facebook_crawler.py](file:///d:/creator-discovery/app/crawlers/facebook_crawler.py)**:
  - Cập nhật dòng gán `CreatorInfo`: gán rõ `isStableExternalId = False` vì Facebook slug có thể đổi.
- [ ] **Tệp [d:\creator-discovery\app\mappers\java_mapper.py](file:///d:/creator-discovery/app/mappers/java_mapper.py)**:
  - Cập nhật `JavaMapper.from_ingest_payload()` để gửi đủ các trường: `crawlRunId`, `observedAt`, `isStableExternalId`, `bioCrossLinks`.

---

## 4. CHECKLIST TRIỂN KHAI CHO `SEP_BE` (JAVA SPRING BOOT)

### 4.1. Database Migrations (Flyway V8 & V9)

#### Tệp `V8__claim_based_identity_foundation.sql`:
```sql
-- D:\SEP_BE\src\main\resources\db\migration\V8__claim_based_identity_foundation.sql

-- 1. Tạo bảng Creator Master (Đại diện danh tính con người sau khi claim)
CREATE TABLE creator_master (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE, SUSPENDED
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 2. Bổ sung master_id NULLABLE vào bảng creator (Platform Account Registry)
ALTER TABLE creator ADD COLUMN master_id UUID;
ALTER TABLE creator ADD COLUMN is_stable_id BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE creator ADD CONSTRAINT fk_creator_master 
    FOREIGN KEY (master_id) REFERENCES creator_master(id);
CREATE INDEX idx_creator_master_id ON creator(master_id);

-- 3. Bổ sung Provenance cho bảng public_creator_metric
ALTER TABLE public_creator_metric ADD COLUMN crawl_run_id VARCHAR(64);
ALTER TABLE public_creator_metric ADD COLUMN observed_at TIMESTAMP;
CREATE INDEX idx_metric_crawl_run ON public_creator_metric(creator_id, crawl_run_id);

-- 4. Bổ sung Dual Reference (Nullable) cho M1 & Shortlist
ALTER TABLE shortlist_item ADD COLUMN master_id UUID;
ALTER TABLE shortlist_item ADD CONSTRAINT fk_shortlist_master 
    FOREIGN KEY (master_id) REFERENCES creator_master(id);
CREATE INDEX idx_shortlist_master ON shortlist_item(master_id);

ALTER TABLE relationship ADD COLUMN master_id UUID;
ALTER TABLE relationship ADD CONSTRAINT fk_relationship_master 
    FOREIGN KEY (master_id) REFERENCES creator_master(id);
CREATE INDEX idx_relationship_master ON relationship(master_id);

ALTER TABLE collaboration ADD COLUMN master_id UUID;
ALTER TABLE collaboration ADD CONSTRAINT fk_collaboration_master 
    FOREIGN KEY (master_id) REFERENCES creator_master(id);
ALTER TABLE collaboration ALTER COLUMN creator_id DROP NOT NULL;
CREATE INDEX idx_collaboration_master ON collaboration(master_id);

ALTER TABLE historical_campaign ADD COLUMN master_id UUID;
ALTER TABLE historical_campaign ADD CONSTRAINT fk_historical_master 
    FOREIGN KEY (master_id) REFERENCES creator_master(id);
ALTER TABLE historical_campaign ALTER COLUMN creator_id DROP NOT NULL;
CREATE INDEX idx_historical_master ON historical_campaign(master_id);

-- LƯU Ý: Tuyệt đối KHÔNG backfill Master ID! Toàn bộ Account cũ giữ master_id = NULL (Unclaimed).
```

#### Tệp `V9__creator_claim_and_oauth_tables.sql`:
```sql
-- D:\SEP_BE\src\main\resources\db\migration\V9__creator_claim_and_oauth_tables.sql

-- 1. Lời mời xác minh hồ sơ
CREATE TABLE creator_invitation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_creator_id UUID NOT NULL REFERENCES creator(id),
    campaign_id UUID REFERENCES campaign(id),
    invited_email VARCHAR(255) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING', -- PENDING, ACCEPTED, EXPIRED, CANCELLED
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_invitation_token ON creator_invitation(token_hash);

-- 2. Lưu định danh trả về từ OAuth Provider
CREATE TABLE creator_provider_identity (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id UUID NOT NULL REFERENCES creator(id),
    provider VARCHAR(50) NOT NULL, -- TIKTOK, YOUTUBE, FACEBOOK, INSTAGRAM
    provider_user_id VARCHAR(255) NOT NULL,
    provider_username VARCHAR(255),
    raw_profile_data JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_provider_identity UNIQUE(provider, provider_user_id)
);

-- 3. Bằng chứng xác minh quyền kiểm soát tài khoản
CREATE TABLE creator_account_verification (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id UUID NOT NULL REFERENCES creator(id),
    verified_by_user_id UUID NOT NULL REFERENCES "user"(id),
    verification_method VARCHAR(50) NOT NULL DEFAULT 'OAUTH_AUTHORIZATION',
    granted_scopes TEXT,
    access_token_encrypted TEXT,
    refresh_token_encrypted TEXT,
    token_expires_at TIMESTAMP,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE, REVOKED, EXPIRED
    verified_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 4. Phân quyền Portal User quản lý Master
CREATE TABLE creator_master_member (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    master_id UUID NOT NULL REFERENCES creator_master(id),
    user_id UUID NOT NULL REFERENCES "user"(id),
    role VARCHAR(32) NOT NULL DEFAULT 'OWNER', -- OWNER, MANAGER
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_master_user UNIQUE(master_id, user_id)
);

-- 5. Bảng xử lý ngoại lệ và xung đột liên kết
CREATE TABLE creator_identity_conflict (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conflict_type VARCHAR(64) NOT NULL, -- ACCOUNT_ALREADY_LINKED, NAMESPACE_MISMATCH, DUPLICATE_CLAIM
    source_account_id UUID REFERENCES creator(id),
    target_master_id UUID REFERENCES creator_master(id),
    conflict_details JSONB NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_MANAGER', -- PENDING_MANAGER, RESOLVED, REJECTED
    resolved_by UUID REFERENCES "user"(id),
    resolved_at TIMESTAMP,
    resolution_notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 6. Lịch sử kiểm toán danh tính (Audit Trail)
CREATE TABLE creator_identity_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(64) NOT NULL, -- CLAIM_MASTER, LINK_ACCOUNT, UNLINK_ACCOUNT, REVOKE_ACCESS
    master_id UUID REFERENCES creator_master(id),
    account_id UUID REFERENCES creator(id),
    performed_by UUID NOT NULL REFERENCES "user"(id),
    details JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

### 4.2. Ingestion Pipeline Decoupling

- [ ] Cập nhật [CreatorIngestionRequest.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/creator/dto/CreatorIngestionRequest.java) bổ sung:
  `Boolean isStableExternalId`, `String crawlRunId`, `LocalDateTime observedAt`, `List<String> bioCrossLinks`.
- [ ] Cập nhật [CreatorIngestionService.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/creator/service/CreatorIngestionService.java):
  - Áp dụng Advisory Lock: `creatorRepository.acquireIdentityLock(request.getPlatform() + ":" + request.getExternalId())`.
  - **Khi tạo Account mới**:
    ```java
    Creator creator = Creator.builder()
        .master(null) // LUÔN LÀ NULL, KHÔNG TỰ TẠO MASTER
        .platform(request.getPlatform())
        .externalId(request.getExternalId())
        .isStableId(request.getIsStableExternalId() != null ? request.getIsStableExternalId() : true)
        .userName(request.getUserName())
        .displayName(request.getDisplayName())
        .avatarUrl(request.getAvatarUrl())
        .profileUrl(request.getProfileUrl())
        .location(request.getLocation())
        .build();
    creatorRepository.save(creator);
    ```
  - **Khi cào lại Account đã có**: Giữ nguyên `id` và `master_id` hiện tại, chỉ cập nhật metadata mới.
  - **Snapshot Idempotency**: Kiểm tra nếu `(creator.id, request.getCrawlRunId())` đã tồn tại thì bỏ qua snapshot trùng lặp.

---

### 4.3. Discovery Hybrid Grouping

- [ ] Sửa đổi native query trong [PublicCreatorMetricRepository.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/creator/repository/PublicCreatorMetricRepository.java):
```sql
SELECT DISTINCT ON (COALESCE('MASTER:' || c.master_id::text, 'ACCOUNT:' || c.id::text))
    m.id AS id,
    c.id AS creatorId,
    c.master_id AS masterId,
    COALESCE(cm.canonical_name, c.display_name, c.user_name) AS displayName,
    c.platform AS platform,
    c.user_name AS userName,
    c.avatar_url AS avatarUrl,
    m.followers AS followers,
    m.engagement_rate AS engagementRate,
    CASE WHEN c.master_id IS NOT NULL THEN 'LINKED_PROFILE' ELSE 'PUBLIC_PROFILE' END AS claimStatus,
    (0.6 * (1 - (c.embedding <=> cast(NULLIF(:queryVector, '') as vector))) + 
     0.4 * ts_rank(m.search_vector, to_tsquery('simple', :formattedKeyword))) AS hybridScore
FROM public_creator_metric m
JOIN creator c ON m.creator_id = c.id
LEFT JOIN creator_master cm ON c.master_id = cm.id
WHERE 
    (:hasPlatforms = false OR c.platform IN (:platforms))
    AND (:followerMin IS NULL OR m.followers >= :followerMin)
    AND (:followerMax IS NULL OR m.followers <= :followerMax)
ORDER BY 
    COALESCE('MASTER:' || c.master_id::text, 'ACCOUNT:' || c.id::text),
    (0.6 * (1 - (c.embedding <=> cast(NULLIF(:queryVector, '') as vector))) + 
     0.4 * ts_rank(m.search_vector, to_tsquery('simple', :formattedKeyword))) DESC;
```
> **Điểm ưu việt**: Nếu KOC đã liên kết 2 kênh TikTok & YouTube vào `master_id = M001`, hệ thống nhóm lại thành 1 dòng duy nhất và chọn kênh có điểm cao nhất. Nếu KOC chưa claim (`master_id = NULL`), nhóm theo chính `ACCOUNT:c.id` nên vẫn hiển thị độc lập, không bị gom nhầm với các KOC chưa claim khác!

---

### 4.4. Invitation & Profile Claim Engine

- [ ] Tạo mới Service `CreatorInvitationService.java`:
  - `sendInvitation(UUID creatorId, String email, UUID brandId, UUID campaignId)`:
    - Băm token bằng SHA-256 lưu `token_hash`.
    - Tạo lời mời với thời hạn 7 ngày (`expires_at = now + 7 days`).
    - Gửi email mời xác minh: *"Nhãn hàng X muốn hợp tác với kênh của bạn..."*.
  - `verifyInvitationToken(String rawToken)`:
    - Băm `rawToken` tìm kiếm trong bảng `creator_invitation`.
    - Trả về thông tin kênh mục tiêu cần xác minh (`platform`, `userName`, `avatarUrl`).

---

### 4.5. OAuth Verification & Atomic Master Creation

- [ ] Tạo mới Controller `CreatorClaimOAuthController.java`:
  - `GET /api/v1/creators/claim/oauth/{platform}/start?token=RAW_TOKEN`: Chuyển hướng sang OAuth của nền tảng tương ứng.
  - `GET /api/v1/creators/claim/oauth/{platform}/callback?code=...&state=...`: Tiếp nhận Authorization Code.
- [ ] Tạo mới Service `CreatorClaimResolutionService.java`:
  - Gọi API provider lấy thông tin kênh thực tế của người dùng.
  - **Kiểm tra khớp tài khoản**:
    - *YouTube*: So sánh `channel.id` từ API `/youtube/v3/channels?mine=true` với `creator.external_id`.
    - *TikTok*: So sánh normalized username từ `/v2/user/info/` với `creator.user_name`.
  - **Thực thi tạo Master lần đầu trong 1 Transaction**:
    1. Lock tài khoản A001 (`Pessimistic Lock`).
    2. Kiểm tra nếu `A001.master_id != null` ➔ Đã bị claim ➔ Ném `ConflictException`.
    3. Tạo mới `CreatorMaster` M001 (canonical_name lấy theo tên kênh).
    4. Cập nhật `A001.setMaster(M001)`.
    5. Lưu bản ghi `creator_account_verification` và `creator_provider_identity`.
    6. Thêm bản ghi `creator_master_member` (M001, portal_user_id, 'OWNER').
    7. Đánh dấu `creator_invitation.status = 'ACCEPTED'`.
    8. Ghi log `creator_identity_audit`.

---

### 4.6. Connect Additional Accounts Service

- [ ] Tạo mới Service `CreatorAccountLinkService.java`:
  - Xử lý khi KOC đã sở hữu Master M001 và muốn kết nối thêm kênh thứ 2 qua OAuth Portal.
  - **Triển khai 4 kịch bản liên kết**:
    - **Case 1 (Tài khoản đã có trong DB qua crawler, `master_id = NULL`)**:
      - Giữ nguyên Account ID cũ A002 cùng toàn bộ metrics và bài đăng.
      - Gán: `A002.setMaster(M001)`.
      - Lưu verification và audit.
    - **Case 2 (Tài khoản hoàn toàn mới chưa từng cào)**:
      - Tạo bản ghi mới A003 với `master_id = M001`.
    - **Case 3 (Tài khoản đã thuộc M001)**:
      - Cập nhật token và bỏ qua.
    - **Case 4 (Tài khoản đã thuộc Master M002 khác)**:
      - **Không tự động đổi chủ**. Ghi bản ghi vào `creator_identity_conflict` (loại `ACCOUNT_ALREADY_LINKED`) để Data Manager xử lý.

---

### 4.7. Dual References & CRM/Shortlist Adaptation

- [ ] Cập nhật [ShortlistItemRepository.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/relationship/repository/) & Service:
  - Cho phép thêm vào Shortlist khi KOC chưa claim (`creator_id = A001, master_id = null`).
  - Khi KOC claim thành công: Tự động cập nhật `master_id = M001` cho các bản ghi Shortlist liên quan.
- [ ] Cập nhật [Collaboration.java](file:///d:/SEP_BE/src/main/java/com/influencermatch/backend/collaboration/model/Collaboration.java):
  - Hỗ trợ hợp đồng hợp tác khi KOC chưa claim (`creator_id != null, master_id = null`).

---

### 4.8. Data Manager Conflict Resolution

- [ ] Tạo Controller `AdminIdentityConflictController.java`:
  - `GET /api/v1/admin/creator-conflicts`: Danh sách các trường hợp tranh chấp hoặc lỗi mapping namespace.
  - `POST /api/v1/admin/creator-conflicts/{id}/resolve`: Quyết định phê duyệt chuyển kênh (`APPROVE_TRANSFER`), từ chối (`REJECT`) hoặc yêu cầu thêm bằng chứng.

---

## 5. GIẢI QUYẾT BẪY KỸ THUẬT (TECHNICAL GOTCHAS & TRAPS)

1. **Bẫy 1: Namespace Mismatch giữa OAuth Provider ID và Crawler Public ID**:
   - *Vấn đề*: TikTok OAuth trả về `open_id` (được băm theo Client Key), trong khi crawler lưu `user_id` số.
   - *Giải pháp*: Trong bước Callback, Backend bắt buộc gọi API chính thức `/v2/user/info/` với scope `user.info.basic` để trích xuất `username` chuẩn hóa và đối chiếu với `creator.user_name`.
2. **Bẫy 2: Shortlist Duplicate khi liên kết kênh thứ 2**:
   - *Vấn đề*: Chiến dịch đã thêm cả TikTok A001 và YouTube A002 của cùng 1 KOC trước khi họ claim. Khi KOC liên kết cả 2 vào M001, nếu áp đặt UNIQUE(campaign_id, master_id) sẽ văng lỗi.
   - *Giải pháp*: Giữ các bản ghi ở cấp Account (`creator_id`) trong bảng Shortlist; giao diện nhóm lại hiển thị theo Master ID nếu đã có.
3. **Bẫy 3: Race Condition khi nhiều Claim Sessions mở cùng lúc**:
   - *Giải pháp*: Bắt buộc dùng `Pessimistic Lock (SELECT ... FOR UPDATE)` trên bản ghi `creator` mục tiêu trước khi thực hiện logic tạo Master.

---

## 6. BẢNG KIỂM THỬ NGHIỆM THU (ACCEPTANCE TESTING MATRIX - 18 TCS)

| Mã TC | Kịch bản Kiểm thử | Thao tác Kỹ thuật | Kết quả Nghiệm thu Kỳ vọng |
| :---: | :--- | :--- | :--- |
| **TC-01** | Cào KOC mới ngoài mạng xã hội | Ingest TikTok `@linh.beauty`. | Tạo `creator` với `master_id = NULL`. Bảng `creator_master` không sinh bản ghi. |
| **TC-02** | Cào lại cùng Stable ID | Ingest lại cùng `external_id`. | Cập nhật metadata, giữ nguyên `creator.id`, không sinh duplicate. |
| **TC-03** | KOC chưa claim tham gia Discovery | Brand tìm kiếm KOC trên UI. | Hiển thị bình thường với nhãn `Public Profile`. |
| **TC-04** | Gửi lời mời Claim hồ sơ | Brand tạo lời mời tới email KOC. | Sinh bản ghi `creator_invitation` (token hash SHA-256), chưa tạo Master. |
| **TC-05** | Recipient đăng nhập OAuth sai kênh | KOC đăng nhập tài khoản TikTok khác với tài khoản được mời. | Báo lỗi `IdentityMismatchException`, từ chối tạo Master. |
| **TC-06** | OAuth đúng tài khoản được mời | KOC đăng nhập đúng tài khoản TikTok A001. | Tạo Master M001, gán `A001.master_id = M001`, lưu verification audit. |
| **TC-07** | Hai Claim Sessions chạy đồng thời | 2 tab trình duyệt cùng submit OAuth callback cho A001. | Row lock chặn lại: 1 Master duy nhất được tạo, phiên thứ 2 báo đã hoàn tất. |
| **TC-08** | Kết nối kênh thứ 2 đã có sẵn trong DB | KOC vào Portal OAuth YouTube A002 (đã cào trước đó). | Tái sử dụng A002, gán `A002.master_id = M001`. Không tạo Account trùng. |
| **TC-09** | Kết nối kênh thứ 2 chưa có trong DB | KOC OAuth kênh Instagram A003 hoàn toàn mới. | Tạo bản ghi `creator` mới gán `master_id = M001`. |
| **TC-10** | OAuth ID khác namespace Public ID | TikTok trả về `open_id`. | Gọi `/v2/user/info/` bóc tách username để ánh xạ an toàn. |
| **TC-11** | Kết nối kênh đã thuộc Master khác | KOC muốn liên kết kênh đã thuộc M002. | Ghi vào `creator_identity_conflict`, không tự ý chuyển quyền. |
| **TC-12** | KOC sở hữu 2 kênh đã xác minh | Xem kết quả Discovery. | Hiển thị 1 Creator Card duy nhất, chọn kênh tốt nhất làm đại diện. |
| **TC-13** | Hai KOC chưa claim có `master_id = NULL` | Xem danh sách Discovery. | Mỗi KOC hiển thị thành 1 Card độc lập, không bị gom chung. |
| **TC-14** | Claim sau khi đã có Shortlist/Hợp đồng | A001 đã có trong Campaign của Brand trước khi claim. | Sau khi claim thành công, các bản ghi tự động cập nhật `master_id = M001`. |
| **TC-15** | OAuth Access Token hết hạn | Token YouTube hết hạn sau 1 giờ. | Cập nhật quyền cần gia hạn, không xóa Account, Master hay dữ liệu M2. |
| **TC-16** | Data Manager xử lý tranh chấp kênh | Data Manager duyệt chuyển kênh trên Admin UI. | Chuyển `master_id` có audit log đầy đủ kèm lý do duyệt. |
| **TC-17** | Trình duyệt retry callback OAuth | Bấm refresh trang callback. | Không tạo trùng Master hay bản ghi verification. |
| **TC-18** | Shortlist cả 2 kênh trước khi KOC claim | Campaign shortlist cả TikTok và YouTube của KOC. | Không văng lỗi unique constraint sau khi KOC claim thành công. |

---

## 7. LỘ TRÌNH TRIỂN KHAI REVIEW 2 (3-WEEK IMPLEMENTATION ROADMAP)

```
Tuần 1: Nền Tảng & Decoupling Ingestion (P0)
├── Chạy Flyway V8 (bảng creator_master, cột creator.master_id NULL).
├── Sửa CreatorIngestionService (dừng tự tạo Master, gán master_id = null).
└── Cập nhật Discovery Native Query (triển khai Hybrid Grouping).

Tuần 2: Lời Mời & Claim Kênh Đầu Tiên (P0)
├── Chạy Flyway V9 (các bảng invitation, verification, provider_identity, conflict, audit).
├── Triển khai CreatorInvitationService (tạo lời mời, gửi email, hash token).
├── Triển khai Google YouTube OAuth Callback & Namespace Resolution.
└── Triển khai CreatorClaimResolutionService (tạo Master M001 nguyên tử lần đầu).

Tuần 3: Kết Nối Kênh Thứ Hai & Demo Xuyên Suốt (P1)
├── Triển khai CreatorAccountLinkService (kết nối kênh thứ 2, tái sử dụng account đã cào).
├── Cập nhật màn hình Creator Portal cho phép bấm "Connect Another Channel".
└── Thực hiện kịch bản Demo hoàn chỉnh cho Hội đồng Review 2:
    Crawl 2 kênh -> Discovery -> Shortlist -> Gửi lời mời -> KOC OAuth kênh 1 -> Tạo Master -> Kết nối kênh 2 -> Master Grouping hoàn chỉnh.
```
