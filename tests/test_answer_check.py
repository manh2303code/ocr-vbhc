import unittest

from answer_check import Evidence, check, hedge, is_comparison, note, table_counts
from doc_resolver import DocumentCatalog

CATALOG = DocumentCatalog([
    (8, "195/KH-HĐND", "Kế hoạch", "02/06/2017", "Tiếp xúc cử tri"),
    (3, "166-KH/ĐĐHĐND", "Kế hoạch", "08/04/2022", "Phòng, chống tham nhũng"),
    (9, "M2/TTr-UBND", "Tờ trình", "23/06/2021", "Mua sắm thiết bị"),
    (10, "09/CV-ĐĐBQH", "Công văn", "20/02/2020", "Góp ý dự thảo luật"),
    (12, "641/HĐND-VP", "Thông báo", "03/10/2022", "Đính chính nghị quyết"),
    (22, "03/CV-ĐBBQH", "Công văn", "16/03/2020", "Báo cáo tình hình"),
])
SOURCES = (
    "[1] 【195/KH-HĐND · Kế hoạch · 02/06/2017 · trang 3】\n"
    "Báo cáo kết quả gửi về trước ngày 30/6/2017. Dự kiến tổng mức đầu tư 180.500 triệu đồng; "
    "vốn bố trí 180.544 triệu đồng. Hội nghị tổ chức ngày 23 tháng 6 năm 2017 tại Hội trường 408. "
    "Cắt giảm 70% kinh phí hội nghị."
)


def _evidence(**kwargs):
    values = dict(text=SOURCES, numbers=["195/KH-HĐND"], catalog=CATALOG, refs=[8], shown=[8],
                  ref_numbers={8: [1]})
    values.update(kwargs)
    return Evidence(**values)


class CheckTests(unittest.TestCase):
    def test_an_answer_that_holds_has_no_violation(self):
        report = check("Kế hoạch 195/KH-HĐND yêu cầu gửi báo cáo trước ngày 30/6/2017 [1]. Hội nghị họp "
                       "ngày 23/6/2017 tại Hội trường 408; tổng mức đầu tư 180.500 triệu đồng, cắt giảm "
                       "70% kinh phí.\n1. Lớp 2\n2. Lớp 6", _evidence())
        self.assertEqual([], report.kinds())
        self.assertEqual(1, report.checked["numbers"])
        self.assertEqual(2, report.checked["dates"])

    def test_a_figure_date_or_number_in_no_source(self):
        report = check("Hai văn bản cách nhau 114 ngày. Hạn là 25/6/2017. Xem thêm 12/KH-UBND.", _evidence())
        self.assertEqual(["date", "figure", "number"], sorted(report.kinds()))
        figure = next(v for v in report.violations if v.kind == "figure")
        self.assertEqual("114", figure.item)
        report = check("Tổng mức đầu tư là 1.479.300.000 đồng.", _evidence())
        self.assertIn("180.500", next(v for v in report.violations if v.kind == "figure").note)

    def test_dates_written_in_words_match_numeric_ones(self):
        report = check("Ban hành ngày 2 tháng 6 năm 2017; họp ngày 23/06/2017.", _evidence())
        self.assertEqual([], report.kinds())

    def test_an_ocr_slip_in_a_stored_number_is_not_a_violation(self):
        report = check("Công văn 03/CV-ĐĐBQH gửi Ủy ban nhân dân tỉnh.", _evidence())
        self.assertNotIn("number", report.kinds())

    def test_a_document_called_by_another_type(self):
        report = check("Tờ trình 09/CV-ĐĐBQH đề nghị góp ý dự thảo luật.", _evidence())
        self.assertEqual(["type"], report.kinds())
        self.assertIn("là Công văn", report.violations[0].note)
        report = check("Công văn số 09/CV-ĐĐBQH và Thông báo 641/HĐND-VP.", _evidence())
        self.assertEqual([], report.kinds())

    def test_not_found_with_the_asked_document_shown(self):
        report = check("Không tìm thấy thông tin về văn bản 195.", _evidence(), not_found=True)
        self.assertEqual(["not_found"], report.kinds())
        self.assertIn("195/KH-HĐND (nguồn [1])", report.violations[0].note)
        # A search of the whole store that found nothing named: no claim to check.
        report = check("Không tìm thấy thông tin.", _evidence(refs=[]), not_found=True)
        self.assertEqual([], report.kinds())

    def test_counts_of_a_metadata_table(self):
        counts = {"cong van": 15, "ke hoach": 7}
        report = check("Có 24 văn bản: 12 Kế hoạch, 10 Công văn.", _evidence(
            text="Tổng 24 văn bản — Công văn 15, Kế hoạch 7", refs=[], aggregate_counts=counts))
        self.assertEqual(["count", "count"], report.kinds())
        report = check("Có 24 văn bản, gồm Công văn: 15 và 7 kế hoạch.", _evidence(
            text="Tổng 24 văn bản — Công văn 15, Kế hoạch 7", refs=[], aggregate_counts=counts))
        self.assertEqual([], report.kinds())

    def test_not_found_next_to_a_table(self):
        report = check("Không tìm thấy thông tin.", _evidence(refs=[], aggregate_counts={}), not_found=True)
        self.assertEqual(["not_found"], report.kinds())
        report = check("Không có văn bản nào khác.", _evidence(refs=[], aggregate_counts={},
                                                              aggregate_empty=True), not_found=True)
        self.assertEqual([], report.kinds())

    def test_a_comparison_must_say_something_of_each_document(self):
        evidence = _evidence(refs=[8, 3], shown=[8, 3], compare=True, ref_numbers={8: [1], 3: [2]})
        report = check("Kế hoạch 195/KH-HĐND về tiếp xúc cử tri.", evidence)
        self.assertEqual(["compare"], report.kinds())
        self.assertIn("166-KH/ĐĐHĐND (nguồn [2])", report.violations[0].note)
        self.assertEqual([], check("Văn bản 195/KH-HĐND về cử tri, văn bản 166 về tham nhũng.", evidence).kinds())
        self.assertEqual([], check("Một văn bản về cử tri [1], một về tham nhũng [2].",
                                   _evidence(refs=[8, 3], compare=True, cited=[8, 3])).kinds())

    def test_source_label_lines_copied_into_the_answer(self):
        report = check("[1] CHÁNH VĂN PHÒNG Nguyễn Văn A [2] KT. CHÁNH VĂN PHÒNG PHÓ CHÁNH VĂN PHÒNG", _evidence())
        self.assertIn("raw", report.kinds())
        self.assertEqual([], check("Chánh văn phòng là ông Nguyễn Văn A [1].", _evidence()).kinds())

    def test_the_result_of_a_count(self):
        evidence = _evidence(text="KẾT QUẢ: 24 văn bản (kho có tổng cộng 26 văn bản)", refs=[],
                             aggregate_counts={}, totals=[24])
        report = check("Có tổng cộng 26 văn bản liên quan; 3 văn bản loại Công văn.", evidence)
        self.assertEqual(["count"], report.kinds())
        self.assertIn("KẾT QUẢ là 24", report.violations[0].note)
        self.assertEqual([], check("Kho có 26 văn bản, trong đó 24 văn bản liên quan.", evidence).kinds())

    def test_a_list_must_name_every_row(self):
        evidence = _evidence(text="339-KH/ĐĐHĐND · Kế hoạch; 566/KH-HĐND · Kế hoạch", refs=[],
                             aggregate_counts={}, required=[
            ("339-KH/ĐĐHĐND", ["339-KH/ĐĐHĐND"]), ("566/KH-HĐND", ["566/KH-HĐND"]),
            ("Tạ Thị Cúc", ["Tạ Thị Cúc"])])
        report = check("Gồm 339-KH/ĐĐHĐND và Tạ Thị Cúc.", evidence)
        self.assertEqual(["list"], report.kinds())
        self.assertIn("566/KH-HĐND", report.violations[0].note)
        # A documents table's result line is to be kept when the list is written again.
        evidence.result_line = "KẾT QUẢ: 2 văn bản — theo loại: Kế hoạch 2"
        self.assertIn("như dòng \"KẾT QUẢ: 2 văn bản — theo loại: Kế hoạch 2\", rồi liệt kê",
                      check("Gồm 339-KH/ĐĐHĐND và Tạ Thị Cúc.", evidence).violations[0].note)
        self.assertEqual([], check("339-KH/ĐĐHĐND, 566/KH-HĐND, bà Tạ Thị Cúc.", evidence).kinds())

    def test_a_bulleted_breakdown_is_counts_not_document_numbers(self):
        evidence = _evidence(text="KẾT QUẢ: 26 văn bản — theo loại: Công văn 15, Kế hoạch 8, Thông báo 1",
                             refs=[], aggregate_counts={"cong van": 15, "ke hoach": 8}, totals=[26])
        report = check("Kho có tổng cộng 26 văn bản. Cụ thể:  \n- Công văn: 15  \n- Kế hoạch: 8  \n"
                       "- Thông báo: 1", evidence)
        self.assertEqual([], report.kinds())
        self.assertEqual(0, report.checked["numbers"])
        self.assertEqual(3, report.checked["counts"])

    def test_counting_and_listing_questions(self):
        from answer_check import is_counting, is_listing

        self.assertTrue(is_counting("liệt kê các văn bản liên quan tới hưng yên gồm số lượng bao nhiêu văn bản"))
        self.assertTrue(is_listing("thống kê toàn bộ các lãnh đạo phòng ban cho tôi"))
        self.assertTrue(is_listing("Ông ấy ký những văn bản nào?"))
        self.assertFalse(is_listing("ai là chánh văn phòng"))
        self.assertFalse(is_counting("Ai ký nhiều văn bản nhất?"))

    def test_the_question_counts_as_evidence(self):
        report = check("Kế hoạch năm 2099 không có.", _evidence(text=SOURCES + "\nCó kế hoạch năm 2099 không?"))
        self.assertEqual([], report.kinds())


class NoteAndHedgeTests(unittest.TestCase):
    def test_the_note_lists_each_violation(self):
        report = check("Tờ trình 09/CV-ĐĐBQH, cách nhau 114 ngày.", _evidence())
        text = note(report)
        self.assertTrue(text.startswith("KIỂM TRA TỰ ĐỘNG"))
        self.assertIn("\"114\"", text)
        self.assertIn("không phải tờ trình", text)

    def test_hedge_drops_an_unsupported_sentence_when_others_remain(self):
        answer = "Kế hoạch 195/KH-HĐND họp tại Hội trường 408 [1]. Hai văn bản cách nhau 114 ngày."
        hedged, changed = hedge(answer, check(answer, _evidence()))
        self.assertTrue(changed)
        self.assertEqual("Kế hoạch 195/KH-HĐND họp tại Hội trường 408 [1].", hedged)

    def test_hedge_marks_the_item_when_nothing_else_remains(self):
        answer = "Cách nhau 114 ngày."
        hedged, changed = hedge(answer, check(answer, _evidence()))
        self.assertTrue(changed)
        self.assertEqual("Cách nhau (chưa xác minh) ngày.", hedged)

    def test_hedge_finds_items_as_the_answer_wrote_them_and_whole(self):
        answer = "Hạn là ngày 25 tháng 6 năm 2017 [1]. Kế hoạch 195/KH-HĐND họp ở Hội trường 408."
        report = check(answer, _evidence())
        self.assertEqual(["ngày 25 tháng 6 năm 2017"], [v.item for v in report.violations])
        self.assertEqual("Kế hoạch 195/KH-HĐND họp ở Hội trường 408.", hedge(answer, report)[0])
        # "114" is not part of "1149".
        answer = "Cách nhau 114 ngày, khác số 1149."
        report = check(answer, _evidence(text=SOURCES + " số 1149"))
        self.assertEqual(["114"], [v.item for v in report.violations])
        self.assertEqual("Cách nhau (chưa xác minh) ngày, khác số 1149.", hedge(answer, report)[0])

    def test_hedge_writes_the_documents_own_type(self):
        answer = "Tờ trình 09/CV-ĐĐBQH đề nghị góp ý."
        hedged, changed = hedge(answer, check(answer, _evidence()))
        self.assertTrue(changed)
        self.assertEqual("Công văn 09/CV-ĐĐBQH đề nghị góp ý.", hedged)

    def test_counts_and_comparisons(self):
        self.assertEqual({"ke hoach": 2, "to trinh": 1}, table_counts(CATALOG, [8, 3, 9]))
        self.assertTrue(is_comparison("So sánh hai văn bản 195 và 166"))
        self.assertFalse(is_comparison("Kế hoạch 195 do ai ký?"))


if __name__ == "__main__":
    unittest.main()
