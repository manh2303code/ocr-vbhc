import unittest

from legal_relations import extract_relations, normalize_so_ki_hieu


CAN_CU_BLOCK = """QUYẾT ĐỊNH
Căn cứ Luật Tổ chức chính quyền địa phương ngày 19 tháng 6 năm 2015;
Căn cứ Quyết định số 12/2020/QĐ-UBND ngày 05/3/2020 của UBND tỉnh;
Căn cứ Thông tư số 01/2019/TT-BNV của Bộ Nội vụ;
Theo đề nghị của Chánh Văn phòng."""

THAY_THE_LINE = ("Quyết định này có hiệu lực kể từ ngày ký và thay thế "
                 "Quyết định số 05/2018/QĐ-UBND ngày 10/01/2018 của UBND tỉnh.")

BAI_BO_LINE = "Bãi bỏ Thông báo số 34/TB-VP ngày 02/02/2021 của Văn phòng UBND tỉnh."

SUA_DOI_LINE = ("Sửa đổi, bổ sung một số điều của Quy chế ban hành kèm theo "
                "Quyết định số 22/2019/QĐ-UBND ngày 15/5/2019.")


class NormalizeTests(unittest.TestCase):
    def test_normalize_spacing_and_case(self):
        self.assertEqual(normalize_so_ki_hieu("22 /bc-hĐnd."), "22/BC-HĐND")


class ExtractTests(unittest.TestCase):
    def test_can_cu_extracts_only_numbered_docs(self):
        rels = extract_relations(CAN_CU_BLOCK)
        can_cu = [r for r in rels if r.relation_type == "can_cu"]
        targets = sorted(r.target_so_ki_hieu for r in can_cu)
        # Luật gọi tên (không số) bị bỏ qua — đúng thiết kế
        self.assertEqual(targets, ["01/2019/TT-BNV", "12/2020/QĐ-UBND"])

    def test_thay_the(self):
        rels = extract_relations(THAY_THE_LINE)
        self.assertEqual(rels[0].relation_type, "thay_the")
        self.assertEqual(rels[0].target_so_ki_hieu, "05/2018/QĐ-UBND")
        self.assertIn("thay thế", rels[0].evidence)

    def test_bai_bo(self):
        rels = extract_relations(BAI_BO_LINE)
        self.assertEqual((rels[0].relation_type, rels[0].target_so_ki_hieu),
                         ("bai_bo", "34/TB-VP"))

    def test_sua_doi(self):
        rels = extract_relations(SUA_DOI_LINE)
        self.assertEqual((rels[0].relation_type, rels[0].target_so_ki_hieu),
                         ("sua_doi", "22/2019/QĐ-UBND"))

    def test_no_false_positive_on_plain_citation(self):
        text = "Phúc đáp Công văn số 418/BCA-TCAN về việc phối hợp."
        rels = extract_relations(text)
        # trích dẫn thường KHÔNG phải can_cu/thay_the/sua_doi/bai_bo
        self.assertEqual([r for r in rels if r.relation_type != "trich_dan"], [])


if __name__ == "__main__":
    unittest.main()
