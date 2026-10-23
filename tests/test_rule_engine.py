import unittest

from app.ai.rules.rule_config import Decision
from app.ai.rules.rule_engine import RuleEngine
from app.ai.rules.signal_extractor import PostPreview, ProfileMetadata


class TestRuleEngine(unittest.TestCase):

    def test_tc01_koc_clear_beauty_review(self):
        """TC01: KOC review mỹ phẩm rõ ràng -> AUTO_ACCEPT_KOC."""
        profile = ProfileMetadata(
            username="huyen_skincare",
            display_name="Huyền Review Skincare",
            bio="Kênh chia sẻ và review mỹ phẩm chân thực. Link mua đồ chính hãng ở bio nha!",
            followers=25000,
            bio_links=["https://beacons.ai/huyen_skincare"],
        )
        posts = [
            PostPreview(caption="Review serum phục hồi B5, ưu điểm thấm nhanh, nhược điểm hơi dính."),
            PostPreview(caption="So sánh 2 loại kem chống nắng cho da dầu mụn. Chấm 9/10 nha!"),
            PostPreview(caption="Unboxing và trải nghiệm thực tế kem dưỡng ẩm mới mua."),
            PostPreview(caption="Đi dạo phố cuối tuần cùng bạn bè."),
            PostPreview(caption="Đánh giá mặt nạ đất sét sau 1 tháng sử dụng, rất thích nha."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertEqual(result.decision, Decision.AUTO_ACCEPT_KOC)
        self.assertEqual(result.primary_type, "KOC")
        self.assertIn("BR05_AUTO_ACCEPT_KOC", result.reason_codes)

    def test_tc02_koc_food_review_without_affiliate(self):
        """TC02: KOC review ẩm thực không có affiliate -> AUTO_ACCEPT_KOC nếu đủ bằng chứng."""
        profile = ProfileMetadata(
            username="khoa_foodie",
            display_name="Khoa Ăn Sập Sài Gòn",
            bio="Chuyên trải nghiệm và review quán ăn ngon, ẩm thực đường phố Sài Gòn.",
            followers=85000,
            bio_links=[],
        )
        posts = [
            PostPreview(caption="Trải nghiệm quán hủ tiếu 30 năm, ưu điểm nước lèo ngọt, nhược điểm gửi xe xa."),
            PostPreview(caption="Review quán lẩu bò bình dân siêu ngon, điểm trừ hơi cay."),
            PostPreview(caption="Đánh giá buffet hải sản 199k, chấm 8/10 đáng tiền nha."),
            PostPreview(caption="Hôm nay đi cà phê ngắm hoàng hôn."),
            PostPreview(caption="Trải nghiệm ăn thử quán bún riêu cua đồng."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertEqual(result.decision, Decision.AUTO_ACCEPT_KOC)
        self.assertEqual(result.primary_type, "KOC")

    def test_tc03_koc_high_followers_with_booking(self):
        """TC03: KOC 300K Followers và có booking -> Không tự biến thành KOL."""
        profile = ProfileMetadata(
            username="linh_unbox",
            display_name="Linh Unboxing",
            bio="Kênh chuyên unbox và review đồ gia dụng thông minh. Contact for work: linh@gmail.com",
            followers=320000,
            public_email="linh@gmail.com",
            bio_links=["https://linktr.ee/linh_unbox"],
        )
        posts = [
            PostPreview(caption="Unboxing máy hút bụi cầm tay, ưu điểm nhẹ, nhược điểm pin hơi yếu."),
            PostPreview(caption="Review nồi chiên không dầu sau 6 tháng sử dụng, rất thích."),
            PostPreview(caption="Đánh giá máy xay sinh tố mini, so với giá rất đáng tiền."),
            PostPreview(caption="Trải nghiệm robot hút bụi lau nhà thông minh."),
            PostPreview(caption="Tips vệ sinh bếp sạch sẽ mỗi ngày."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertEqual(result.decision, Decision.AUTO_ACCEPT_KOC)
        self.assertEqual(result.primary_type, "KOC")

    def test_tc04_kol_expertise_with_independent_evidence(self):
        """TC04: Bác sĩ da liễu 45K Followers chia sẻ chuyên môn -> AUTO_ACCEPT_KOL."""
        profile = ProfileMetadata(
            username="dr_nam_da_lieu",
            display_name="Bác sĩ Nam Da Liễu",
            bio="Bác sĩ chuyên khoa da liễu. Chia sẻ kiến thức chăm sóc da khoa học. Workshop da liễu 2026.",
            followers=45000,
            public_email="drnam@hospital.vn",
        )
        posts = [
            PostPreview(caption="Bác sĩ hướng dẫn cách phục hồi hàng rào bảo vệ da bị tổn thương."),
            PostPreview(caption="Phân tích chuyên sâu về hoạt chất Retinol và Tretinoin."),
            PostPreview(caption="Bác sĩ chia sẻ kiến thức trị mụn chuẩn y khoa."),
            PostPreview(caption="Tham gia workshop da liễu quốc tế tại Hà Nội."),
            PostPreview(caption="Một ngày làm việc tại phòng khám của bác sĩ."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertEqual(result.decision, Decision.AUTO_ACCEPT_KOL)
        self.assertEqual(result.primary_type, "KOL")
        self.assertIn("BR05_AUTO_ACCEPT_KOL", result.reason_codes)

    def test_tc05_meme_account_high_followers_only_booking(self):
        """TC05: Tài khoản 500K Followers reup video chỉ có email booking -> Không Auto Accept KOL."""
        profile = ProfileMetadata(
            username="giai_tri_tong_hop",
            display_name="Giải Trí Mỗi Ngày",
            bio="Cười mỗi ngày xả stress. Booking contact: booking@meme.vn",
            followers=520000,
            public_email="booking@meme.vn",
        )
        posts = [
            PostPreview(caption="Hài hước cuối tuần cười xỉu."),
            PostPreview(caption="Khi bạn đi làm ngày thứ 2."),
            PostPreview(caption="Tình huống dở khóc dở cười."),
            PostPreview(caption="Xem đến cuối bất ngờ chưa."),
            PostPreview(caption="Đố bạn nhịn được cười."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertNotEqual(result.decision, Decision.AUTO_ACCEPT_KOL)
        self.assertEqual(result.decision, Decision.SKIP_INSUFFICIENT)

    def test_tc06_official_store_catalog(self):
        """TC06: Official Store với catalog sản phẩm -> AUTO_REJECT."""
        profile = ProfileMetadata(
            username="tong_kho_my_pham_si",
            display_name="Tổng Kho Mỹ Phẩm Sỉ",
            bio="Tổng kho chuyên sỉ mỹ phẩm giá xưởng. Tuyển CTV và đại lý toàn quốc. Zalo 0901234567.",
            followers=15000,
        )
        posts = [
            PostPreview(caption="Mẫu mới về, hàng có sẵn, giá chỉ từ 50k inbox báo giá."),
            PostPreview(caption="Xả kho thanh lý lô kem chống nắng, chuyên sỉ số lượng lớn."),
            PostPreview(caption="Bảng giá sỉ cho đại lý và CTV toàn quốc."),
            PostPreview(caption="Hàng về ngập kho, ship toàn quốc kiểm tra trước khi thanh toán."),
            PostPreview(caption="Tuyển CTV bán hàng online chiết khấu cao."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertEqual(result.decision, Decision.AUTO_REJECT)
        self.assertEqual(result.primary_type, "BRAND")
        self.assertIn("BR01_REJECT_COMMERCIAL_STORE", result.reason_codes)

    def test_tc07_state_media_organization(self):
        """TC07: Tài khoản cơ quan báo chí chính thức -> AUTO_REJECT."""
        profile = ProfileMetadata(
            username="vtv24_official",
            display_name="VTV24 Tin Tức",
            bio="Kênh tin tức chính thức của Đài Truyền hình Việt Nam. Bản tin 24h.",
            followers=2000000,
            is_verified=True,
        )
        posts = [
            PostPreview(caption="Bản tin thời sự tối nay cập nhật tình hình kinh tế."),
            PostPreview(caption="Bộ Y Tế khuyến cáo người dân phòng chống dịch bệnh mùa đông."),
            PostPreview(caption="Cục An toàn thông tin cảnh báo các chiêu trò lừa đảo mới."),
            PostPreview(caption="Dự báo thời tiết các khu vực trên cả nước ngày mai."),
            PostPreview(caption="Điểm tin thể thao 24h nổi bật trong ngày."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertEqual(result.decision, Decision.AUTO_REJECT)
        self.assertEqual(result.primary_type, "ORGANIZATION")
        self.assertIn("BR01_REJECT_ORGANIZATION", result.reason_codes)

    def test_tc08_creator_with_own_shop_still_reviews(self):
        """TC08: Creator có shop cá nhân nhưng vẫn review chân thật -> Không bị reject nhầm."""
        profile = ProfileMetadata(
            username="lan_makeup",
            display_name="Lan Makeup Shop",
            bio="Lan chia sẻ tips làm đẹp & Lan có bán một số dòng cọ makeup chính hãng nha.",
            followers=40000,
            bio_links=["https://shopee.vn/lan_shop"],
        )
        posts = [
            PostPreview(caption="Review phấn phủ kiềm dầu, ưu điểm mịn, nhược điểm dễ bay màu."),
            PostPreview(caption="So sánh 3 loại son môi hot trend, điểm trừ hơi khô môi."),
            PostPreview(caption="Set cọ trang điểm mới của shop Lan, hàng có sẵn nha."),
            PostPreview(caption="Trải nghiệm trang điểm phong cách Douyin."),
            PostPreview(caption="Đánh giá kem nền, chấm 8/10 rất đáng mua."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertEqual(result.decision, Decision.NEEDS_MANAGER_REVIEW)
        self.assertIn("BR06_SELECTIVE_REVIEW_CREATOR_WITH_SHOP", result.reason_codes)

    def test_tc09_only_linktree_or_review_keyword(self):
        """TC09: Chỉ có Linktree hoặc keyword review ở bio nhưng video không có review -> Không Auto Accept."""
        profile = ProfileMetadata(
            username="nam_vlog",
            display_name="Nam Vlog",
            bio="Review cuộc sống thường ngày. Link ở bio:",
            followers=12000,
            bio_links=["https://linktr.ee/nam_vlog"],
        )
        posts = [
            PostPreview(caption="Đi cà phê buổi sáng đẹp trời."),
            PostPreview(caption="Cuối tuần dọn dẹp phòng."),
            PostPreview(caption="Nấu bữa trưa đơn giản."),
            PostPreview(caption="Dắt cún cưng đi dạo công viên."),
            PostPreview(caption="Ngồi chill nghe nhạc ngắm mưa."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertNotEqual(result.decision, Decision.AUTO_ACCEPT_KOC)
        self.assertEqual(result.decision, Decision.SKIP_INSUFFICIENT)

    def test_tc10_dual_koc_kol_signals(self):
        """TC10: Đạt cả tín hiệu KOC và KOL mạnh -> NEEDS_MANAGER_REVIEW (BR-05)."""
        profile = ProfileMetadata(
            username="dr_an_review",
            display_name="Bác sĩ An Da Liễu",
            bio="Bác sĩ da liễu. Kênh review mỹ phẩm khoa học & chia sẻ kiến thức chuẩn y khoa. Diễn giả hội thảo da liễu.",
            followers=150000,
            public_email="contact@dran.com",
            bio_links=["https://beacons.ai/dran"],
        )
        posts = [
            PostPreview(caption="Bác sĩ review kem trị mụn, ưu điểm gom cồi nhanh, nhược điểm châm chích."),
            PostPreview(caption="Đánh giá serum vitamin C, chấm 9/10, đáng tiền nha."),
            PostPreview(caption="Bác sĩ unboxing và review serum dưỡng ẩm mới, dùng thử rất thích."),
            PostPreview(caption="Phân tích chuyên sâu cơ chế tăng sắc tố sau viêm."),
            PostPreview(caption="Bác sĩ chia sẻ tại workshop thẩm mỹ quốc tế."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertEqual(result.decision, Decision.NEEDS_MANAGER_REVIEW)
        self.assertIn("BR05_DUAL_KOC_KOL_AMBIGUITY", result.reason_codes)

    def test_tc11_insufficient_vague_bio(self):
        """TC11: Bio mơ hồ và không có bài viết -> SKIP_INSUFFICIENT."""
        profile = ProfileMetadata(
            username="user123456",
            display_name="User",
            bio="Xin chào mọi người",
            followers=8000,
        )
        posts = []
        result = RuleEngine.evaluate(profile, posts)
        self.assertEqual(result.decision, Decision.SKIP_INSUFFICIENT)

    def test_tc12_insufficient_posts_count(self):
        """TC12: Preflight chỉ có 2 bài thay vì 5 bài -> Không tự coi 3/5 đã đạt."""
        profile = ProfileMetadata(
            username="nga_beauty",
            display_name="Nga Beauty",
            bio="Review đồ làm đẹp",
            followers=15000,
            bio_links=["https://beacons.ai/nga_beauty"],
        )
        posts = [
            PostPreview(caption="Review son dưỡng, ưu điểm mềm môi, nhược điểm nhanh trôi."),
            PostPreview(caption="Đánh giá cushion kiềm dầu, chấm 8/10 đáng mua."),
        ]
        result = RuleEngine.evaluate(profile, posts)
        self.assertNotEqual(result.decision, Decision.AUTO_ACCEPT_KOC)
        self.assertIn(result.decision, [Decision.NEEDS_MANAGER_REVIEW, Decision.SKIP_INSUFFICIENT])


if __name__ == "__main__":
    unittest.main()
