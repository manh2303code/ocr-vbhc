import unittest

from doc_resolver import ConversationState, TurnRecord
from metadata_query import answer, build_index, parse_position, people_of, sentence_case

# ToolExecutor._latest_versions() rows: id, file_name, so_ki_hieu, loai_vb,
# ngay_bh, cqbh_tren, cqbh_duoi, nd_chinh, chu_ky, chuc_vu. Fictional
# people; the fields are written the way OCR stores them (capitals, stray
# "ph", a name twice, a document without metadata).
ROWS = [
    (1, "a.pdf", "339-KH/ĐĐHĐND", "Kế hoạch", "23/05/2023", "TỈNH ỦY HƯNG YÊN", "ĐẢNG ĐOÀN HĐND TỈNH",
     "Ô nhiễm môi trường", "Lê Văn An", "T/M ĐẢNG ĐOÀN HĐND\nPHÓ BÍ THƯ"),
    (2, "b.pdf", "566/KH-HĐND", "Kế hoạch", "08/08/2023", "HỘI ĐỒNG NHÂN DÂN", "TỈNH HƯNG YÊN",
     "Lấy phiếu tín nhiệm", "Lê Văn An", "TM. THƯỜNG TRỰC HĐND ph\nKT. CHỦ TỊCH\nPHÓ CHỦ TỊCH THƯỜNG TRỰC"),
    (3, "c.pdf", "166-KH/ĐĐHĐND", "Kế hoạch", "08/04/2022", "TỈNH ỦY HƯNG YÊN", "ĐẢNG ĐOÀN HĐND TỈNH",
     "Phòng, chống tham nhũng", "Phạm Văn Bình\nPhạm Văn Bình", "T/M ĐẢNG ĐOÀN HĐND\nPHÓ BÍ THƯ"),
    (5, "d.pdf", "", "Kế hoạch", "02/08/2022", "UBKT ĐẢNG ỦY CQ VĂN PHÒNG", "ĐOÀN ĐBQH VÀ HĐND TỈNH\nTỔ GIÁM SÁT",
     "Giám sát đảng viên", "Tạ Thị Cúc", "T/M TỔ GIÁM SÁT"),
    (6, "e.pdf", "105/KH-KTNS", "Kế hoạch", "05/04/2017", "HĐND TỈNH HƯNG YÊN", "BAN KINH TẾ NGÂN SÁCH",
     "Kinh phí sự nghiệp", "Trần Quốc Dũng", "TM. BAN KINH TẾ NGÂN SÁCH\nTRƯỞNG BAN"),
    (9, "f.pdf", "M2/TTr-UBND", "Tờ trình", "23/06/2021", "ỦY BAN NHÂN DÂN", "TỈNH HƯNG YÊN",
     "Mua sắm thiết bị", "Nguyễn Duy Em", "TM. ỦY BAN NHÂN DÂN\nKT. CHỦ TỊCH\nPHÓ CHỦ TỊCH"),
    (10, "g.pdf", "09/CV-ĐĐBQH", "Công văn", "20/02/2020", "QUỐC HỘI KHÓA XIV", "ĐOÀN ĐẠI BIỂU QUỐC HỘI\nTỈNH HƯNG YÊN",
     "Góp ý dự thảo luật", "Trần Văn Giang", "TM. ĐOÀN ĐẠI BIỂU QUỐC HỘI TỈNH\nKT. TRƯỞNG ĐOÀN\nPHÓ TRƯỞNG ĐOÀN"),
    (14, "h.pdf", "46/CV-ĐĐBQH", "Công văn", "31/07/2020", "QUỐC HỘI KHÓA XIV", "ĐOÀN ĐẠI BIỂU QUỐC HỘI\nTỈNH HƯNG YÊN",
     "Cắt giảm dự toán", "Trần Văn Giang", "ĐOÀN ĐẠI BIỂU QUỐC HỘI TỈNH\nKT. TRƯỞNG ĐOÀN\nPHÓ TRƯỞNG ĐOÀN"),
    (11, "i.pdf", "06/HĐND-TH", "Công văn", "04/01/2019", "HỘI ĐỒNG NHÂN DÂN", "TỈNH HƯNG YÊN",
     "Đánh giá, xếp loại", "Nguyễn Anh Hà", "CHÁNH VĂN PHÒNG"),
    (12, "j.pdf", "641/HĐND-VP", "Thông báo", "03/10/2022", "HỘI ĐỒNG NHÂN DÂN", "TỈNH HƯNG YÊN",
     "Đính chính nghị quyết", "Nguyễn Anh Hà", "TL. THƯỜNG TRỰC HĐND\nCHÁNH VĂN PHÒNG"),
    (13, "k.pdf", "", "", "16/06/2026", "LIÊN BANG NGA", "", "", "", ""),
    (24, "l.pdf", "205/GQĐT-BPC", "Công văn", "15/05/2019", "HĐND TỈNH HƯNG YÊN", "BAN PHÁP CHẾ",
     "Chuyển đơn", "Nguyễn Văn Khang", "TM. BAN PHÁP CHẾ\nKT. TRƯỞNG BAN\nPHÓ TRƯỞNG BAN"),
    (25, "m.pdf", "110/VP-TH", "Công văn", "09/05/2019", "HĐND TỈNH HƯNG YÊN", "VĂN PHÒNG",
     "Góp ý Luật Thanh niên", "Đỗ Văn Hòa", "KT. CHÁNH VĂN PHÒNG\nPHÓ CHÁNH VĂN PHÒNG"),
    (26, "n.pdf", "111/VP-TH", "Công văn", "10/05/2019", "HĐND TỈNH HƯNG YÊN", "VĂN PHÒNG",
     "Đăng ký đào tạo", "Nguyễn Anh Hà", "CHÁNH VĂN PHÒNG"),
]


def _text(found):
    return "\n\n".join(found.blocks)


def _state(question, answer_text, docs, shown=()):
    return ConversationState([TurnRecord(question=question, answer=answer_text, docs=list(docs),
                                         status="answered", how="named", shown=list(shown))])


class PositionTests(unittest.TestCase):
    def test_a_deputy_signing_for_the_chief_is_the_deputy(self):
        position, _name = parse_position("KT. CHÁNH VĂN PHÒNG\nPHÓ CHÁNH VĂN PHÒNG")
        self.assertEqual("PHÓ CHÁNH VĂN PHÒNG", position.own)
        self.assertEqual((("ký thay", "CHÁNH VĂN PHÒNG"),), position.capacities)
        self.assertEqual("Phó chánh văn phòng (ký thay Chánh văn phòng)", position.describe())

    def test_prefixes_and_ocr_scraps(self):
        position, _name = parse_position("TM. THƯỜNG TRỰC HĐND ph\nKT. CHỦ TỊCH\nPHÓ CHỦ TỊCH THƯỜNG TRỰC")
        self.assertEqual("PHÓ CHỦ TỊCH THƯỜNG TRỰC", position.own)
        self.assertEqual("Phó chủ tịch thường trực (ký thay Chủ tịch; thay mặt Thường trực HĐND)",
                         position.describe())

    def test_a_body_above_the_prefixes_is_signed_for(self):
        position, _name = parse_position("ĐOÀN ĐẠI BIỂU QUỐC HỘI TỈNH\nKT. TRƯỞNG ĐOÀN\nPHÓ TRƯỞNG ĐOÀN")
        self.assertEqual("PHÓ TRƯỞNG ĐOÀN", position.own)
        self.assertIn(("thay mặt", "ĐOÀN ĐẠI BIỂU QUỐC HỘI TỈNH"), position.capacities)

    def test_only_a_body(self):
        position, _name = parse_position("T/M TỔ GIÁM SÁT")
        self.assertEqual("", position.own)
        self.assertEqual("thay mặt Tổ giám sát", position.describe())

    def test_names_once_and_a_position_written_in_the_name_field(self):
        _position, name = parse_position("", "Phạm Văn Bình\nPhạm Văn Bình")
        self.assertEqual("Phạm Văn Bình", name)
        position, name = parse_position("", "PHÓ VĂN PHÒNG\nNGUYỄN VĂN CHÍN")
        self.assertEqual("Nguyễn Văn Chín", name)
        self.assertEqual("PHÓ VĂN PHÒNG", position.own)

    def test_acronyms_and_place_names_keep_their_capitals(self):
        self.assertEqual("Đoàn ĐBQH tỉnh Hưng Yên", sentence_case("ĐOÀN ĐBQH TỈNH HƯNG YÊN"))


class PeopleTests(unittest.TestCase):
    def test_signers_most_documents_first_and_no_empty_signer(self):
        people = people_of(build_index(ROWS))
        self.assertEqual("Nguyễn Anh Hà", people[0].name)
        self.assertEqual(3, len(people[0].docs))
        self.assertEqual(9, len(people))

    def test_every_leader_with_the_position_read_from_the_signature_block(self):
        found = answer("thống kê toàn bộ các lãnh đạo phòng ban cho tôi", ROWS)
        self.assertEqual("people", found.kind)
        text = _text(found)
        self.assertIn("Tổng cộng 9 người ký 13 văn bản", text)
        self.assertIn("Ký nhiều nhất: Nguyễn Anh Hà (3 văn bản)", text)
        self.assertIn("Đỗ Văn Hòa — ký 1 văn bản. Phó chánh văn phòng", text)
        self.assertIn("Nguyễn Anh Hà — ký 3 văn bản.", text)
        self.assertIn("ký thay Chánh văn phòng", text)
        self.assertEqual(len(found.blocks), len(found.sources))
        self.assertNotIn(13, found.docs)

    def test_who_holds_a_position_and_who_only_signed_for_it(self):
        found = answer("ai là chánh văn phòng", ROWS)
        self.assertEqual("position", found.kind)
        text = _text(found)
        self.assertIn("Người giữ chức vụ này khi ký văn bản: Nguyễn Anh Hà.", text)
        self.assertIn("Ký thay Chánh văn phòng (là cấp phó, không phải Chánh văn phòng): Đỗ Văn Hòa", text)
        # "chánh văn phòng" is a title: the issuer "Văn phòng" is not a filter.
        self.assertEqual([11, 12, 26, 25], found.docs)

    def test_a_position_of_one_body(self):
        found = answer("Trưởng Ban Kinh tế Ngân sách là ai?", ROWS)
        self.assertEqual([6], found.docs)
        found = answer("Phó Chủ tịch UBND tỉnh nào ký văn bản trong kho?", ROWS)
        self.assertEqual([9], found.docs)
        self.assertIn("Nguyễn Duy Em", _text(found))

    def test_nobody_holds_it_but_a_deputy_signed_for_it(self):
        found = answer("Ai là Chủ tịch UBND tỉnh?", ROWS)
        self.assertTrue(found.empty)
        self.assertIn("kho không cho biết ai giữ chức vụ này", _text(found))
        self.assertIn("Nguyễn Duy Em (Phó chủ tịch)", _text(found))

    def test_a_position_nobody_signed_as_is_left_to_the_search(self):
        self.assertIsNone(answer("Ai là Giám đốc Sở Tài chính tỉnh Hưng Yên?", ROWS))

    def test_one_persons_position(self):
        found = answer("Đỗ Văn Hòa có phải là chánh văn phòng không?", ROWS)
        self.assertEqual("person", found.kind)
        self.assertIn("không giữ chức vụ Chánh văn phòng", _text(found))

    def test_ong_ay_is_the_signer_the_last_answer_named(self):
        state = _state("Kế hoạch 105/KH-KTNS do ai ký?", "Kế hoạch do ông Trần Quốc Dũng ký.", [6])
        found = answer("Ông ấy giữ chức vụ gì?", ROWS, state)
        self.assertEqual("person", found.kind)
        self.assertIn("Trưởng ban", _text(found))

    def test_a_person_the_last_answer_named_who_signed_nothing_is_left_to_the_search(self):
        state = _state("Quyết định cử ai đi học?", "Đồng chí Trần Thị Lan được cử đi học.", [6])
        self.assertIsNone(answer("Bà ấy giữ chức vụ gì?", ROWS, state))

    def test_what_else_a_person_signed(self):
        state = _state("Kế hoạch 339 do ai ký?", "Kế hoạch 339-KH/ĐĐHĐND do Lê Văn An ký.", [1])
        found = answer("Ông ấy còn ký văn bản nào khác trong kho không?", ROWS, state)
        self.assertEqual("signer_documents", found.kind)
        self.assertEqual([2], found.docs)
        state = _state("Kế hoạch 166 do ai ký?", "Kế hoạch 166-KH/ĐĐHĐND do Phạm Văn Bình ký.", [3])
        found = answer("Ông ấy còn ký văn bản nào khác?", ROWS, state)
        self.assertTrue(found.empty)
        self.assertIn("không có văn bản nào khác do Phạm Văn Bình ký", _text(found))

    def test_a_short_title_question_after_a_document_asks_about_that_document(self):
        state = _state("Kế hoạch 105/KH-KTNS khảo sát những đơn vị nào?", "Các trường…", [6])
        self.assertIsNone(answer("Trưởng ban là ai?", ROWS, state))


class DocumentCountTests(unittest.TestCase):
    def test_issuers_naming_a_place_counted_by_type(self):
        found = answer("liệt kê các văn bản liên quan tới hưng yên gồm số lượng bao nhiêu văn bản", ROWS)
        self.assertEqual("documents", found.kind)
        text = _text(found)
        self.assertIn("KẾT QUẢ: 12 văn bản có cơ quan ban hành ghi Hưng Yên — theo loại: Công văn 6, Kế hoạch 4, Thông báo 1, Tờ trình 1 (kho có tổng cộng 14 văn bản).", text)
        self.assertEqual([12], found.totals)
        self.assertIn(("M2/TTr-UBND", ["M2/TTr-UBND"]), found.items)
        self.assertNotIn(5, found.docs)
        self.assertNotIn(13, found.docs)

    def test_the_longest_issuer_named_wins(self):
        # "Đảng đoàn HĐND tỉnh", not every HĐND document nor the "Đoàn ĐBQH
        # và HĐND tỉnh" supervision team.
        self.assertEqual([1, 3], answer("Đảng đoàn HĐND tỉnh ban hành bao nhiêu kế hoạch?", ROWS).docs)
        self.assertEqual([10, 14], answer("Liệt kê các công văn do Đoàn đại biểu Quốc hội tỉnh ban hành",
                                          ROWS).docs)
        self.assertEqual([25, 26], answer("Những văn bản nào do Văn phòng HĐND tỉnh ban hành?", ROWS).docs)

    def test_by_year_type_and_the_whole_store(self):
        self.assertEqual([11, 24, 25, 26], answer("Năm 2019 có bao nhiêu văn bản?", ROWS).docs)
        found = answer("Kho có bao nhiêu văn bản, mỗi loại bao nhiêu?", ROWS)
        self.assertIn("KẾT QUẢ: 14 văn bản — theo loại: Công văn 6, Kế hoạch 5", _text(found))
        self.assertEqual([1, 2, 3, 5, 6], answer("Liệt kê các kế hoạch", ROWS).docs)

    def test_among_the_documents_of_the_last_turn(self):
        found = answer("Văn bản nào trong số đó do Ban Pháp chế ban hành?", ROWS, within=[11, 24, 25, 26])
        self.assertEqual([24], found.docs)

    def test_questions_about_content_are_left_to_the_search(self):
        for question in ("Liệt kê các văn bản về Luật Thanh niên",
                         "Trong kho còn quyết định nào cử cán bộ đi học không?",
                         "Văn bản này do ai ký?", "kế hoạch đó do ai ký?",
                         "Bà Tạ Thị Cúc còn có tên trong văn bản nào khác?",
                         "Có văn bản nào về phòng, chống tham nhũng không?",
                         "tóm tắt tờ trình trong kho"):
            self.assertIsNone(answer(question, ROWS), question)


if __name__ == "__main__":
    unittest.main()
