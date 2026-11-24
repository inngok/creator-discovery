import unittest

from app.core.location_resolver import resolve_location

CASES = [
    ("Ăn Sập Sài Gòn", "ansapsaigon", "", "TP. Hồ Chí Minh"),
    ("Review Food", "saigonfoodie", "", "TP. Hồ Chí Minh"),
    ("Bếp nhà Linh", "beplinh", "Sống tại TP.HCM 🌆", "TP. Hồ Chí Minh"),
    ("Vietnam.vn | Hanoi", "congthongtin", "", "Hà Nội"),
    ("Hà Nội Phố", "hanoipho", "", "Hà Nội"),
    ("Linh Review", "linh_review_hn", "", "Hà Nội"),
    ("Đà Lạt Trong Tôi", "dalattrongtoi", "", "Lâm Đồng"),
    ("Check-in Đà Nẵng", "checkindn", "", "Đà Nẵng"),
    ("Ngọc Huệ", "ngochue", "", "Vietnam"),
    ("Thu Huế Beauty", "thuhue", "", "Vietnam"),
    ("Creator ABC", "abc", "Chia sẻ cuộc sống", "Vietnam"),
    (None, None, None, "Vietnam"),
]


class LocationResolverTest(unittest.TestCase):
    def test_cases(self):
        for display_name, username, bio, expected in CASES:
            with self.subTest(display_name=display_name, username=username):
                self.assertEqual(
                    resolve_location(display_name=display_name, username=username, bio=bio),
                    expected,
                )

    def test_display_name_has_priority_over_bio(self):
        self.assertEqual(
            resolve_location(display_name="Hà Nội Ăn Gì", username="x", bio="Từng sống ở Sài Gòn"),
            "Hà Nội",
        )


if __name__ == "__main__":
    unittest.main()
