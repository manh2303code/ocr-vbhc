import unittest

from doc_resolver import (
    ConversationState,
    DocumentCatalog,
    TurnRecord,
    bare_numbers,
    conversation_block,
    is_meta_question,
    resolve,
)
from llm_client import estimate_text_tokens
from search_store import fold_vietnamese

# (doc_id, so_ki_hieu, loai_vb, ngay_bh, nd_chinh): numbers as OCR stored them.
ROWS = [
    (3, "166-KH/ĐĐHĐND", "Kế hoạch", "08/04/2022", "Quán triệt Chỉ thị số 25-CT/TU về phòng, chống tham nhũng"),
    (5, "", "Kế hoạch", "02/08/2022", "Giám sát đảng viên năm 2022"),
    (6, "105/KH-KTNS", "Kế hoạch", "05/04/2017", "Giám sát đơn vị sự nghiệp công lập"),
    (7, "336/KH-KTNS", "Kế hoạch", "02/08/2017", "Khảo sát Quỹ phát triển đất"),
    (8, "195/KH-HĐND", "Kế hoạch", "02/06/2017", "Tổ chức Hội nghị tiếp xúc cử tri"),
    (20, "23/KH-VP", "Kế hoạch", "04/01/2017", "Quản lý vũ khí, vật liệu nổ"),
    (22, "117/QĐ-HĐND", "Quyết định", "28/04/2017", "Cử đoàn công tác"),
    (23, "4243-QĐ/TU", "Quyết định", "25/07/2018", "Cử cán bộ đi học"),
    (28, "183/TTTr-VP", "Tờ trình", "29/07/2019", "Kiện toàn Tổ thư ký"),
    (29, "410/TTtr-HĐND", "Tờ trình", "29/06/2021", "Số lượng Ủy viên các Ban"),
    (30, "23/TTTr-CD", "Tờ trình", "14/11/2017", "Đề nghị khen thưởng"),
]


def _catalog():
    return DocumentCatalog(ROWS)


def _state(*turns, open_doc=None, scope="all"):
    return ConversationState(list(turns), open_doc=open_doc, scope=scope)


def _turn(question, docs, open_doc=None, status="answered", scope="all", how="cited"):
    return TurnRecord(question=question, docs=list(docs), open_doc=open_doc, scope=scope,
                      status=status, how=how)


class NumberTests(unittest.TestCase):
    def test_every_full_number_is_found_in_the_order_named(self):
        found = resolve("So sánh thông báo 336/KH-KTNS và 195-KH/HĐND, rồi 166/KH-ĐĐHĐND", _catalog())
        self.assertEqual([7, 8, 3], found.docs)
        self.assertEqual("named", found.how[7])

    def test_two_numbers_with_the_same_lead_are_both_named(self):
        self.assertEqual([8, 22], resolve(
            "Kế hoạch tiếp xúc cử tri 195/KH-HĐND và Quyết định 117/QĐ-HĐND do ai ký?", _catalog()).docs)

    def test_a_slip_in_the_letters_still_matches(self):
        self.assertEqual([7], resolve("kế hoạch 336/KH-KTSN do ai ký", _catalog()).docs)
        self.assertEqual([29], resolve("Tờ trình 410/TTr-HĐND đề nghị gì?", _catalog()).docs)

    def test_ocr_confused_leading_digits_match_when_the_letters_agree(self):
        # The store holds 4243-QĐ/TU for 1213-QĐ/TU and 117/QĐ-HĐND for 147/QĐ-HĐND.
        found = resolve("Quyết định 1213-QĐ/TU cử ai đi học?", _catalog())
        self.assertEqual([23], found.docs)
        self.assertEqual("ocr", found.how[23])
        self.assertEqual([22], resolve("Quyết định 147/QĐ-HĐND cử đoàn đi đâu?", _catalog()).docs)

    def test_unconfusable_digits_or_other_letters_do_not_match(self):
        self.assertEqual([], resolve("Kế hoạch 999/KH-HĐND về việc gì?", _catalog()).docs)
        self.assertEqual([], resolve("Quyết định 1213-QĐ/UBND cử ai?", _catalog()).docs)
        self.assertEqual([], resolve("Nghị định 30/2020/NĐ-CP quy định gì?", _catalog()).docs)
        self.assertEqual([], resolve("Họp ngày 10/01/2018 ở đâu?", _catalog()).docs)


class BareNumberTests(unittest.TestCase):
    def test_bare_numbers_after_a_type_word(self):
        self.assertEqual([8, 3], resolve("văn bản 195 và văn bản 166 khác nhau thế nào", _catalog()).docs)
        self.assertEqual([28, 29], resolve("So sánh ngày ban hành của hai tờ trình 183 và 410",
                                           _catalog()).docs)
        self.assertEqual([7, 6], resolve("so sánh văn bản 336 và văn bản 105", _catalog()).docs)
        self.assertEqual([6], resolve("Còn kế hoạch số 105 thì sao?", _catalog()).docs)
        self.assertEqual([3], resolve("không phải, tôi hỏi kế hoạch 166 cơ", _catalog()).docs)

    def test_lists_of_bare_numbers(self):
        self.assertEqual([(339, "ke hoach"), (850, "ke hoach"), (166, "ke hoach")],
                         bare_numbers(fold_vietnamese("ba kế hoạch 339, 850 và 166")))

    def test_numbers_that_are_not_document_numbers(self):
        for question in ("Điều 166 quy định gì?", "năm 2024 có bao nhiêu kế hoạch",
                         "đoàn có 15 người", "thiết bị lớp 2, lớp 6",
                         "Kế hoạch 336/KH-KTNS về việc gì?"):
            self.assertEqual([], bare_numbers(fold_vietnamese(question)), question)

    def test_a_type_word_picks_between_two_documents_numbered_23(self):
        self.assertEqual([20], resolve("kế hoạch 23 về việc gì", _catalog()).docs)
        self.assertEqual([30], resolve("tờ trình 23 đề nghị gì", _catalog()).docs)
        both = resolve("văn bản 23 về việc gì", _catalog())
        self.assertEqual([20, 30], both.docs)

    def test_an_ambiguous_number_prefers_the_document_already_discussed(self):
        state = _state(_turn("Tờ trình đề nghị khen thưởng?", [30]))
        self.assertEqual([30], resolve("văn bản 23 ban hành ngày nào", _catalog(), state).docs)

    def test_abbreviated_type(self):
        self.assertEqual([20], resolve("KH 23 VP đoàn đi đâu", _catalog()).docs)


class ConversationReferenceTests(unittest.TestCase):
    def setUp(self):
        # The user's session: 166 open, then 195 open, then a comparison.
        self.state = _state(
            _turn("tóm tắt về văn bản tôi đang mở", [3], open_doc=3, scope="open", how="scope"),
            _turn("tóm tắt về văn bản đang mở", [8], open_doc=8, scope="open", how="scope"),
            open_doc=8,
        )

    def test_the_open_document(self):
        found = resolve("tóm tắt văn bản đang mở", _catalog(), self.state)
        self.assertEqual([8], found.docs)
        self.assertEqual("open", found.how[8])

    def test_the_first_document_and_the_first_one_opened(self):
        self.assertEqual([3], resolve("văn bản đầu tiên tôi hỏi ban hành ngày nào?",
                                      _catalog(), self.state).docs)
        self.assertEqual([3], resolve("Văn bản tôi mở đầu tiên khảo sát vào những ngày nào?",
                                      _catalog(), self.state).docs)
        self.assertEqual([3], resolve("Văn bản tôi hỏi ở câu đầu tiên do ai ký?",
                                      _catalog(), self.state).docs)

    def test_previous_is_the_one_before_the_current(self):
        found = resolve("văn bản trước được ban hành ngày nào?", _catalog(), self.state)
        self.assertEqual([3], found.docs)
        self.assertEqual("previous", found.how[3])

    def test_just_said_and_the_one_before_it(self):
        state = _state(_turn("Tờ trình 410/TTr-HĐND đề nghị gì?", [29], how="named"),
                       _turn("Tờ trình 183/TTr-VP về việc gì?", [28], how="named"))
        found = resolve("So sánh văn bản vừa nói với văn bản trước đó: ngày ban hành", _catalog(), state)
        self.assertEqual([28, 29], found.docs)
        self.assertEqual({28: "last", 29: "previous"}, found.how)

    def test_a_report_due_before_a_date_is_not_the_previous_document(self):
        state = _state(_turn("Tóm tắt", [7], open_doc=7), open_doc=7, scope="open")
        found = resolve("Văn bản này yêu cầu gửi báo cáo trước ngày nào?", _catalog(), state)
        self.assertEqual([7], found.docs)

    def test_that_document_is_the_last_one(self):
        state = _state(_turn("Đỗ Xuân Chương giữ chức vụ gì?", [5]),
                       _turn("Ông ấy bị giám sát từ ngày nào?", [5]))
        self.assertEqual([5], resolve("Ai ký kế hoạch đó?", _catalog(), state).docs)
        # "do ai ký" (by whom) is not "đó" (that), "bà ấy" is a person.
        self.assertEqual([], resolve("Kế hoạch do ai ký năm 2017?", _catalog(), state).docs)
        self.assertEqual([], resolve("Kế hoạch giám sát do bà ấy ký giám sát ai?", _catalog(), state).docs)

    def test_this_document_without_an_open_one_is_the_last(self):
        state = _state(_turn("Kế hoạch 23/KH-VP về việc gì?", [20], how="named"))
        self.assertEqual([20], resolve("Người ký kế hoạch này là ai?", _catalog(), state).docs)

    def test_both_and_the_three_of_them(self):
        both = resolve("Hai văn bản này có cùng người ký không?", _catalog(), self.state)
        self.assertEqual([8, 3], both.docs)
        self.assertEqual("plural", both.how[3])
        three = _state(_turn("So sánh ba kế hoạch", [3, 6, 7], how="named"))
        self.assertEqual([3, 6, 7], resolve("Ba văn bản đó có chung người ký không?",
                                            _catalog(), three).docs)
        self.assertEqual([3, 6], resolve("Cả hai đều do cùng một người ký à?", _catalog(),
                                         _state(_turn("so sánh", [3, 6]))).docs)

    def test_both_after_one_named_and_a_second_cited(self):
        turn = _turn("Tổng dự toán theo 336/KH-KTNS và tổng mức đầu tư dự án?", [7], how="named")
        turn.cited = [7, 6]
        self.assertEqual([7, 6], resolve("Văn bản nào trong hai văn bản đó do UBND trình?",
                                         _catalog(), _state(turn)).docs)

    def test_both_after_a_comparison_that_cited_one_of_the_two_shown(self):
        turn = _turn("So với lịch tiếp xúc cử tri năm 2008 thì sao?", [8])
        turn.cited, turn.shown = [8], [7, 8]
        self.assertEqual([8, 7], resolve("Hai văn bản đó do cơ quan nào ban hành?",
                                         _catalog(), _state(turn)).docs)

    def test_pronouns_are_marked(self):
        state = _state(_turn("Đỗ Xuân Chương?", [5]))
        self.assertTrue(resolve("Ông ấy phải nộp báo cáo trước ngày nào?", _catalog(), state).anaphora)
        self.assertTrue(resolve("Tổng dự toán của nó là bao nhiêu?", _catalog(), state).anaphora)
        self.assertFalse(resolve("Còn hạn đăng ký thì sao?", _catalog(), state).anaphora)

    def test_which_of_the_last_documents(self):
        state = _state(_turn("so sánh văn bản 336 và văn bản 105", [7, 6], how="number"))
        self.assertEqual([7, 6], resolve("văn bản nào ban hành trước?", _catalog(), state).docs)
        self.assertEqual([7, 6], resolve("cái nào do Trần Quốc Toàn ký?", _catalog(), state).docs)
        # One document last: "nào" asks the store.
        single = _state(_turn("Kế hoạch 336", [7]))
        self.assertEqual([], resolve("Có văn bản nào về tham nhũng không?", _catalog(), single).docs)

    def test_a_named_document_wins_over_a_pointer(self):
        found = resolve("so sánh văn bản đang mở với kế hoạch 105", _catalog(), self.state)
        self.assertEqual([6, 8], found.docs)

    def test_pointers_need_a_conversation(self):
        self.assertEqual([], resolve("văn bản trước được ban hành ngày nào?", _catalog()).docs)
        # Without a state a one-document scope is the open document.
        self.assertEqual([7], resolve("Văn bản này do ai ký?", _catalog(), open_doc=7).docs)


def _listed(question, shown):
    """A turn that listed documents (a metadata table): none named, the
    listed ones shown in their order."""
    turn = _turn(question, [], how="cited")
    turn.shown = list(shown)
    return turn


class TypeReferenceTests(unittest.TestCase):
    """The user's session: a list of documents, then "văn bản tờ trình" and
    "tờ trình đó", which were taken for the last công văn cited."""

    def test_a_type_picks_among_the_documents_the_last_turn_listed(self):
        state = _state(_listed("liệt kê các văn bản …", [3, 8, 29, 22, 23]))
        found = resolve("vậy trong số các văn bản đó thì tóm tắt văn bản tờ trình đi", _catalog(), state)
        self.assertEqual([29], found.docs)
        self.assertEqual("type", found.how[29])
        self.assertEqual("to trinh", found.kind)

    def test_that_type_after_a_document_of_another_type_is_never_that_document(self):
        state = _state(_turn("Quyết định 117/QĐ-HĐND cử ai?", [22], how="named"))
        found = resolve("tờ trình đó về nội dung gì", _catalog(), state)
        self.assertEqual([], found.docs)  # three tờ trình in the store: none chosen
        self.assertEqual([], found.focus)
        # A matching type is the last document, as before.
        self.assertEqual({22: "last"}, resolve("quyết định đó do ai ký", _catalog(), state).how)

    def test_the_only_document_of_a_type_in_the_store(self):
        catalog = DocumentCatalog(ROWS + [(12, "641/HĐND-VP", "Thông báo", "03/10/2022", "Đính chính")])
        state = _state()
        self.assertEqual([12], resolve("Thông báo trong kho nói về việc gì?", catalog, state).docs)
        self.assertEqual([], resolve("tóm tắt tờ trình trong kho", catalog, state).docs)

    def test_an_ordinal_in_the_last_list(self):
        state = _state(_listed("Liệt kê các kế hoạch", [3, 5, 6, 7, 8]))
        self.assertEqual([6], resolve("kế hoạch thứ ba trong danh sách ban hành ngày nào?",
                                      _catalog(), state).docs)
        self.assertEqual([8], resolve("kế hoạch cuối cùng trong danh sách do ai ký?", _catalog(), state).docs)
        # The list two turns back, after a turn about one of its documents.
        state.turns.append(_turn("kế hoạch đầu tiên do ai ký?", [3], how="ordinal"))
        self.assertEqual([8], resolve("kế hoạch cuối cùng trong danh sách do ai ký?", _catalog(), state).docs)
        # "đầu tiên tôi hỏi" is the conversation's first turn.
        self.assertEqual("first", resolve("văn bản đầu tiên tôi hỏi do ai ký?", _catalog(), state).how.get(3))

    def test_several_of_a_type_are_pinned_or_searched_within(self):
        state = _state(_listed("Liệt kê", [3, 22, 28, 29, 30]))
        found = resolve("trong các tờ trình đó, tờ trình nào về khen thưởng?", _catalog(), state)
        self.assertEqual([28, 29, 30], found.docs)
        many = _state(_listed("Liệt kê", [3, 5, 6, 7, 8, 20, 22]))
        found = resolve("trong các kế hoạch đó, kế hoạch nào về tiếp xúc cử tri?", _catalog(), many)
        self.assertEqual([], found.docs)
        self.assertEqual([3, 5, 6, 7, 8, 20], found.within)
        found = resolve("Văn bản nào trong số đó do Ban Pháp chế ban hành?", _catalog(), many)
        self.assertEqual([3, 5, 6, 7, 8, 20, 22], found.within)

    def test_counted_documents_and_questions_about_the_type_are_left_as_they_were(self):
        two = _state(_turn("so sánh", [6, 7], how="named"))
        self.assertEqual([6, 7], resolve("Hai kế hoạch đó ban hành năm nào?", _catalog(), two).docs)
        last = _state(_turn("Công văn 22?", [22], how="named"))
        found = resolve("Văn bản đó có phải tờ trình không?", _catalog(), last)
        self.assertEqual({22: "last"}, found.how)

    def test_switched_off(self):
        import doc_resolver

        doc_resolver.TYPE_REFERENCES = False
        try:
            state = _state(_turn("Quyết định 117/QĐ-HĐND cử ai?", [22], how="named"))
            self.assertEqual([22], resolve("tờ trình đó về nội dung gì", _catalog(), state).docs)
        finally:
            doc_resolver.TYPE_REFERENCES = True


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        self.state = _state(_turn("Kế hoạch 195/KH-HĐND căn cứ vào những văn bản nào?", [8], how="named"),
                            _turn("cảm ơn", [], status="conversation"))

    def test_follow_ups_lean_on_the_last_documents(self):
        for question in ("Ông ấy phải nộp báo cáo trước ngày nào?", "Còn hạn đăng ký thì sao?",
                         "Tổng dự toán của nó là bao nhiêu?", "Trưởng đoàn của đoàn công tác đó là ai?"):
            found = resolve(question, _catalog(), self.state)
            self.assertEqual([], found.docs, question)
            self.assertTrue(found.follow_up, question)
            self.assertEqual([8], found.focus, question)

    def test_short_questions_are_marked_short(self):
        found = resolve("Họp ở đâu?", _catalog(), self.state)
        self.assertTrue(found.short)
        self.assertFalse(resolve("Hội đồng nhân dân tỉnh là cơ quan gì vậy bạn?", _catalog(),
                                 self.state).short)

    def test_asking_for_other_documents(self):
        for question in ("Bà ấy còn ký văn bản nào khác trong kho không?",
                         "Ông ấy ký những văn bản nào trong kho?",
                         "Kho còn kế hoạch giám sát nào khác không?"):
            self.assertTrue(resolve(question, _catalog(), self.state).other_documents, question)


class MetaQuestionTests(unittest.TestCase):
    def test_questions_about_the_conversation(self):
        for question in ("ở đầu đoạn chat tôi đã hỏi bạn về văn bản nào",
                         "văn bản thứ nhất tôi mở là văn bản nào?",
                         "câu trước tôi hỏi gì?", "còn câu đầu tiên?",
                         "Tóm tắt lại những gì chúng ta đã trao đổi từ đầu đến giờ",
                         "câu hỏi thứ hai của tôi là gì?",
                         "nhắc lại ngắn gọn câu trả lời vừa rồi",
                         "câu đầu tiên tôi hỏi là về văn bản nào?",
                         "Lúc nãy tôi hỏi đoàn đi đâu là của văn bản số mấy?",
                         "o dau doan chat toi da hoi ban ve van ban nao"):
            self.assertTrue(is_meta_question(question), question)

    def test_questions_about_documents(self):
        for question in ("Văn bản tôi mở đầu tiên khảo sát vào những ngày nào?",
                         "văn bản đầu tiên tôi hỏi ban hành ngày nào?",
                         "Văn bản tôi hỏi ở câu đầu tiên do ai ký?",
                         "Tờ trình tôi hỏi ở đầu cuộc trò chuyện ban hành ngày nào?",
                         "Văn bản này nói về gì?", "Văn bản này có nói gì về bảo hiểm xã hội không?",
                         "bạn vừa trả lời sai, sửa lại", "tóm tắt lại văn bản 166",
                         "Họp ở đâu?", "Trong kho có những văn bản nào?"):
            self.assertFalse(is_meta_question(question), question)


class ConversationBlockTests(unittest.TestCase):
    def test_names_the_open_document_each_turn_and_the_current_question(self):
        state = _state(
            _turn("tóm tắt về văn bản tôi đang mở", [3], open_doc=3, scope="open", how="scope"),
            _turn("so sánh hai văn bản 195 và văn bản 166", [8, 3], status="not_found", how="number"),
            _turn("cảm ơn", [], status="conversation"),
            open_doc=8,
        )
        block = conversation_block(state, _catalog(), [3], {3: "first"})

        self.assertIn("TRẠNG THÁI HỘI THOẠI", block)
        self.assertIn("Văn bản đang mở bây giờ: 195/KH-HĐND", block)
        # A document is described where it is first named, then by number.
        self.assertIn("Lượt 1: \"tóm tắt về văn bản tôi đang mở\" → 166-KH/ĐĐHĐND (Kế hoạch, "
                      "08/04/2022, Quán triệt", block)
        self.assertIn("(văn bản đang mở lúc đó)", block)
        self.assertIn("→ 195/KH-HĐND, 166-KH/ĐĐHĐND; trợ lý chưa trả lời được", block)
        self.assertNotIn("cảm ơn", block)
        self.assertIn("Câu hỏi hiện tại được hiểu là về: 166-KH/ĐĐHĐND (văn bản của lượt đầu)", block)

    def test_a_document_without_a_number_is_named_by_kind_and_date(self):
        block = conversation_block(_state(_turn("Đỗ Xuân Chương giữ chức vụ gì?", [5])), _catalog())
        self.assertIn("Kế hoạch (không số) ngày 02/08/2022", block)

    def test_long_conversations_keep_the_first_and_newest_turns_under_the_cap(self):
        turns = [_turn("Câu hỏi số {} về một kế hoạch khá dài để chiếm chỗ".format(index),
                       [ROWS[index % len(ROWS)][0]]) for index in range(1, 13)]
        block = conversation_block(_state(*turns), _catalog())

        # 450 estimated tokens are about 350 of the model's.
        self.assertLessEqual(estimate_text_tokens(block), 450)
        self.assertIn("Lượt 1:", block)
        self.assertIn("Lượt 12:", block)
        self.assertNotIn("Lượt 2:", block)

    def test_answers_are_added_for_questions_about_the_conversation(self):
        turn = _turn("Kế hoạch 336/KH-KTNS về việc gì?", [7])
        turn.answer = "Kế hoạch khảo sát Quỹ phát triển đất. Chi tiết khác."
        block = conversation_block(_state(turn), _catalog(), answers=True)
        self.assertIn("đã trả lời: \"Kế hoạch khảo sát Quỹ phát triển đất.\"", block)
        self.assertNotIn("đã trả lời", conversation_block(_state(turn), _catalog()))

    def test_a_deleted_document_keeps_its_saved_label(self):
        turn = _turn("Hỏi", [99])
        turn.labels = {99: "77/QĐ-UBND"}
        self.assertIn("77/QĐ-UBND", conversation_block(_state(turn), _catalog()))

    def test_nothing_to_say_before_the_first_answer(self):
        self.assertEqual("", conversation_block(None, _catalog()))
        self.assertEqual("", conversation_block(_state(), _catalog()))
        self.assertEqual("", conversation_block(_state(open_doc=8), _catalog(), [8], {8: "open"}))


if __name__ == "__main__":
    unittest.main()
