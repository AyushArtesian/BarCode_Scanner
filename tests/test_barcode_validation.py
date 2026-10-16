import unittest

from barcodescan.validation import is_valid_barcode, normalize_folder_detail_type


class BarcodeValidationTests(unittest.TestCase):
    def test_ro_validation_uses_regex_and_int32_range(self):
        self.assertTrue(is_valid_barcode("12345", "RO"))
        self.assertFalse(is_valid_barcode("1234567890123456", "RO"))
        self.assertFalse(is_valid_barcode("123456789012345678", "RO"))
        self.assertFalse(is_valid_barcode("1234", "RO"))
        self.assertFalse(is_valid_barcode("12345678901234567", "RO"))
        self.assertFalse(is_valid_barcode("1234A", "RO"))

    def test_ro_validation_requires_a_nonnegative_int32_value(self):
        self.assertTrue(is_valid_barcode("00000", "RO"))
        self.assertTrue(is_valid_barcode("2147483647", "RO"))
        self.assertFalse(is_valid_barcode("2147483648", "RO"))
        self.assertTrue(is_valid_barcode("000002147483647", "RO"))
        self.assertFalse(is_valid_barcode("000002147483648", "RO"))
        self.assertFalse(is_valid_barcode("١٢٣٤٥", "RO"))
        self.assertTrue(is_valid_barcode("2147483647\n", "RO"))

    def test_deal_validation_applies_conditional_int32_check(self):
        self.assertTrue(is_valid_barcode("SCHO-ABC-D-123", "Deal"))
        self.assertTrue(is_valid_barcode("SCHO-ABC-D-+123", "Deal"))
        self.assertTrue(is_valid_barcode("SCHO-ABC-D- 123 ", "Deal"))
        self.assertTrue(is_valid_barcode("SCHO-ABC-D-" + ("0" * 5000) + "1", "Deal"))
        self.assertFalse(is_valid_barcode("SCHO-ABC-D-1_2", "Deal"))
        self.assertFalse(is_valid_barcode("SCHO-ABC-D-2147483648", "Deal"))
        self.assertFalse(is_valid_barcode("SCHO-ABC-D-abc123x", "Deal"))
        self.assertFalse(is_valid_barcode("SCHO-ABC-D-123tail", "Deal"))
        self.assertTrue(is_valid_barcode("SCHO-ABC-D-123-extra", "Deal"))
        self.assertFalse(is_valid_barcode("OTHER-ABC-D-123", "Deal"))
        self.assertFalse(is_valid_barcode("   ", "Deal"))

    def test_deal_regex_matches_prefix_and_non_d_codes_skip_integer_check(self):
        self.assertTrue(is_valid_barcode("SCHO-ABC-V-1_2", "Deal"))
        self.assertTrue(is_valid_barcode("SCHO-ABC-DD-overflow99999999999", "Deal"))
        self.assertTrue(is_valid_barcode("SCHO-ABC-D-abc-456", "Deal"))

    def test_normalizes_supported_folder_detail_types(self):
        self.assertEqual(normalize_folder_detail_type(" ro "), "RO")
        self.assertEqual(normalize_folder_detail_type("dEaL"), "Deal")
        with self.assertRaisesRegex(ValueError, "FolderDetailType"):
            normalize_folder_detail_type("other")
