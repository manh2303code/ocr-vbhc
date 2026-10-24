"""A fictional corpus of Vietnamese administrative documents for README shots.

EVERYTHING HERE IS INVENTED. "Tỉnh Mẫu" ("sample province") does not exist;
its agencies, document numbers, figures and every person named below are
made up for screenshots of the app. No real document, no real official.

Each document is written once, as blocks, and rendered twice:

* print_html(doc)  -> A4 pages in the layout of Nghị định 30/2020/NĐ-CP
                      (two-column header, Times New Roman, page numbers at
                      the top from page 2), printed to PDF by build_demo.py
                      with Edge headless. The text runs on from page to
                      page; a landscape group (an annex table) starts its own.
* chandra_pages(doc, layout) -> what the OCR model (Chandra OCR 2) returns
                      for those pages: <div data-label data-bbox> blocks, one
                      HTML string per page, split where build_demo.py found
                      each block in the printed PDF.

metadata(doc) gives the 12 NĐ30 fields as a reviewer keeps them after
checking the parser's result.
"""
import html
import math
import random
import re

# ─── The fictional world ─────────────────────────────────────────────
PLACE = "Mẫu"  # the date line: "Mẫu, ngày ... tháng ... năm ..."

UBND = [("ỦY BAN NHÂN DÂN", True), ("TỈNH MẪU", True)]
HDND = [("HỘI ĐỒNG NHÂN DÂN", True), ("TỈNH MẪU", True)]
HDND_TT = [("HĐND TỈNH MẪU", False), ("THƯỜNG TRỰC", True)]
SO_TTTT = [("UBND TỈNH MẪU", False), ("SỞ THÔNG TIN", True), ("VÀ TRUYỀN THÔNG", True)]
VP_UBND = [("UBND TỈNH MẪU", False), ("VĂN PHÒNG", True)]

# Invented people (fictional province, fictional offices).
CT_UBND = "Nghiêm Văn Khải"      # Chủ tịch UBND tỉnh
PCT_UBND = "Lê Minh Khang"       # Phó Chủ tịch UBND tỉnh
GD_STTTT = "Phạm Thu Hà"         # Giám đốc Sở TT&TT
PGD_STTTT = "Đặng Quốc Huy"      # Phó Giám đốc Sở TT&TT
PCVP_UBND = "Hồ Sỹ Tuấn"         # Phó Chánh Văn phòng UBND tỉnh
CT_HDND = "Lương Thế Bảo"        # Chủ tịch HĐND tỉnh
CVP_HDND = "Vũ Thanh Nga"        # Chánh Văn phòng HĐND tỉnh


# ─── Blocks ──────────────────────────────────────────────────────────
def P(text):            # body paragraph, first line indented
    return ("p", text)


def H(text):            # bold heading line ("I. MỤC TIÊU", "1. Mục đích")
    return ("h", text)


def C(text):            # centred bold line ("QUYẾT ĐỊNH:")
    return ("c", text)


def D(label, text):     # article: bold "Điều 1." then text
    return ("d", label, text)


def KG(*lines):         # "Kính gửi:" one recipient, or a list
    return ("kg", lines)


def T(head, rows, widths=None, small=False):
    return ("t", head, rows, widths, small)


def CAP(*lines):        # centred title lines of an annex page
    return ("cap", lines)


FOOTER = ("footer",)


def page(*blocks, wide=False):
    """A group of blocks as first drafted on one page. Portrait groups run
    on in print; where each block lands is measured from the PDF."""
    return {"orient": "landscape" if wide else "portrait", "blocks": list(blocks)}


# ─── The documents (in the order they were issued) ───────────────────
DOCS = [
    dict(
        key="nq15",
        file="nq-15-2025-muc-chi-so-hoa.pdf",
        agency=HDND,
        so="15/2025/NQ-HĐND",
        date=(12, 12, 2025),
        loai="Nghị quyết",
        title="NGHỊ QUYẾT",
        subject="Quy định mức chi cho công tác số hóa tài liệu lưu trữ\ntrên địa bàn tỉnh Mẫu",
        noi_nhan=["Thường trực Tỉnh ủy;", "UBND tỉnh;", "Đại biểu HĐND tỉnh;",
                  "Các sở, ban, ngành tỉnh;", "Lưu: VT."],
        chuc_vu=["CHỦ TỊCH"],
        signer=CT_HDND,
        pages=[
            page(
                C("HỘI ĐỒNG NHÂN DÂN TỈNH MẪU\nKHÓA XVII, KỲ HỌP THỨ MƯỜI"),
                P("Căn cứ Luật Tổ chức chính quyền địa phương;"),
                P("Căn cứ Luật Ngân sách nhà nước;"),
                P("Căn cứ Luật Lưu trữ năm 2024;"),
                P("Xét Tờ trình số 302/TTr-UBND ngày 25 tháng 11 năm 2025 của Ủy ban nhân dân "
                  "tỉnh về việc ban hành Nghị quyết quy định mức chi cho công tác số hóa tài liệu "
                  "lưu trữ trên địa bàn tỉnh Mẫu; Báo cáo thẩm tra của Ban Kinh tế – Ngân sách "
                  "Hội đồng nhân dân tỉnh; ý kiến thảo luận của đại biểu Hội đồng nhân dân tỉnh "
                  "tại kỳ họp."),
                C("QUYẾT NGHỊ:"),
                D("Điều 1.", "Phạm vi điều chỉnh và đối tượng áp dụng"),
                P("1. Nghị quyết này quy định mức chi cho công tác số hóa tài liệu lưu trữ của các "
                  "cơ quan, tổ chức sử dụng ngân sách nhà nước trên địa bàn tỉnh Mẫu."),
                P("2. Đối tượng áp dụng là các cơ quan, tổ chức cấp tỉnh, cấp xã và cá nhân có liên "
                  "quan đến việc lập dự toán, quản lý, sử dụng kinh phí số hóa tài liệu lưu trữ."),
                D("Điều 2.", "Mức chi số hóa tài liệu lưu trữ"),
                T(["STT", "Nội dung công việc", "Đơn vị tính", "Mức chi (đồng)"],
                  [["1", "Chuẩn bị tài liệu, bóc tách, làm phẳng", "Trang A4", "450"],
                   ["2", "Quét tài liệu khổ A4, độ phân giải 300 dpi", "Trang A4", "1.200"],
                   ["3", "Quét tài liệu khổ A3", "Trang A3", "2.100"],
                   ["4", "Nhận dạng ký tự, kiểm tra và hiệu đính", "Trang", "950"],
                   ["5", "Nhập siêu dữ liệu hồ sơ", "Hồ sơ", "18.000"],
                   ["6", "Kiểm tra chất lượng bản số, nộp lưu", "Trang", "300"]],
                  widths=[8, 56, 16, 20]),
            ),
            page(
                D("Điều 3.", "Nguồn kinh phí"),
                P("Ngân sách tỉnh bảo đảm kinh phí số hóa cho các cơ quan, tổ chức cấp tỉnh; ngân "
                  "sách cấp xã bảo đảm cho các cơ quan, tổ chức cấp xã theo phân cấp hiện hành."),
                D("Điều 4.", "Tổ chức thực hiện"),
                P("1. Giao Ủy ban nhân dân tỉnh tổ chức thực hiện Nghị quyết này."),
                P("2. Giao Thường trực Hội đồng nhân dân tỉnh, các Ban của Hội đồng nhân dân tỉnh, "
                  "các Tổ đại biểu và đại biểu Hội đồng nhân dân tỉnh giám sát việc thực hiện "
                  "Nghị quyết."),
                P("Nghị quyết này đã được Hội đồng nhân dân tỉnh Mẫu khóa XVII, kỳ họp thứ mười "
                  "thông qua ngày 12 tháng 12 năm 2025 và có hiệu lực từ ngày 01 tháng 01 năm "
                  "2026./."),
                FOOTER,
            ),
        ],
    ),
    dict(
        key="ttr47",
        file="ttr-47-de-an-luu-tru-so.pdf",
        agency=SO_TTTT,
        so="47/TTr-STTTT",
        date=(2, 2, 2026),
        loai="Tờ trình",
        title="TỜ TRÌNH",
        subject="Về việc phê duyệt Đề án Nền tảng lưu trữ số dùng chung\n"
                "tỉnh Mẫu giai đoạn 2026 – 2028",
        noi_nhan=["Như trên;", "Chủ tịch, các PCT UBND tỉnh;", "Sở Nội vụ, Sở Tài chính;",
                  "Lưu: VT, CĐS."],
        chuc_vu=["GIÁM ĐỐC"],
        signer=GD_STTTT,
        pages=[
            page(
                KG("Ủy ban nhân dân tỉnh Mẫu."),
                P("Căn cứ Luật Lưu trữ năm 2024;"),
                P("Căn cứ Nghị quyết số 15/2025/NQ-HĐND ngày 12 tháng 12 năm 2025 của Hội đồng "
                  "nhân dân tỉnh quy định mức chi cho công tác số hóa tài liệu lưu trữ trên địa bàn "
                  "tỉnh Mẫu;"),
                P("Sở Thông tin và Truyền thông kính trình Ủy ban nhân dân tỉnh xem xét, phê duyệt "
                  "Đề án Nền tảng lưu trữ số dùng chung tỉnh Mẫu giai đoạn 2026 – 2028 (sau đây gọi "
                  "tắt là Đề án) với các nội dung chính như sau:"),
                H("I. SỰ CẦN THIẾT"),
                P("Đến hết năm 2025, các cơ quan, tổ chức cấp tỉnh đang lưu giữ khoảng 4.200 mét giá "
                  "tài liệu giấy, trong đó 1.350 mét giá có thời hạn bảo quản vĩnh viễn. Tỷ lệ tài "
                  "liệu đã được số hóa mới đạt 12%, chủ yếu thực hiện riêng lẻ tại từng cơ quan, "
                  "định dạng không thống nhất và chưa kết nối với hệ thống quản lý văn bản của tỉnh."),
                H("II. MỤC TIÊU"),
                P("1. Xây dựng một nền tảng lưu trữ số dùng chung cho các cơ quan, tổ chức cấp tỉnh, "
                  "bảo đảm lưu trữ an toàn, lâu dài và khai thác thuận tiện tài liệu đã số hóa."),
                P("2. Số hóa 40% tài liệu có thời hạn bảo quản vĩnh viễn trong năm 2026 và hoàn "
                  "thành 100% trước ngày 31 tháng 12 năm 2028."),
                H("III. KINH PHÍ VÀ TIẾN ĐỘ"),
                P("Tổng kinh phí dự kiến 21,4 tỷ đồng từ ngân sách tỉnh, trong đó năm 2026 là 9,8 "
                  "tỷ đồng, năm 2027 là 7,1 tỷ đồng và năm 2028 là 4,5 tỷ đồng; gồm đầu tư phần mềm "
                  "nền tảng, thuê trung tâm dữ liệu dự phòng, số hóa tài liệu theo định mức và đào "
                  "tạo, tập huấn."),
            ),
            page(
                H("IV. TỔ CHỨC THỰC HIỆN"),
                P("1. Sở Thông tin và Truyền thông chủ trì xây dựng, vận hành nền tảng; hướng dẫn kỹ "
                  "thuật số hóa và kết nối với hệ thống quản lý văn bản và điều hành của tỉnh."),
                P("2. Sở Nội vụ hướng dẫn nghiệp vụ lưu trữ, thẩm định danh mục tài liệu cần số hóa; "
                  "kiểm tra việc thực hiện tại các cơ quan, tổ chức."),
                P("3. Sở Tài chính tham mưu bố trí kinh phí hằng năm theo tiến độ của Đề án."),
                P("4. Các sở, ban, ngành tỉnh rà soát, chỉnh lý tài liệu và lập kế hoạch số hóa của "
                  "cơ quan, đơn vị mình."),
                P("(Có dự thảo Đề án và dự thảo Quyết định phê duyệt kèm theo)."),
                P("Sở Thông tin và Truyền thông kính trình Ủy ban nhân dân tỉnh xem xét, quyết "
                  "định./."),
                FOOTER,
            ),
        ],
    ),
    dict(
        key="qd210",
        file="qd-210-phe-duyet-de-an.pdf",
        agency=UBND,
        so="210/QĐ-UBND",
        date=(20, 2, 2026),
        loai="Quyết định",
        title="QUYẾT ĐỊNH",
        subject="Phê duyệt Đề án Nền tảng lưu trữ số dùng chung\ntỉnh Mẫu giai đoạn 2026 – 2027",
        noi_nhan=["Như Điều 3;", "TT Tỉnh ủy, TT HĐND tỉnh;", "Chủ tịch, các PCT UBND tỉnh;",
                  "Lưu: VT, KGVX."],
        chuc_vu=["TM. ỦY BAN NHÂN DÂN", "CHỦ TỊCH"],
        signer=CT_UBND,
        pages=[
            page(
                C("ỦY BAN NHÂN DÂN TỈNH MẪU"),
                P("Căn cứ Luật Tổ chức chính quyền địa phương;"),
                P("Căn cứ Luật Lưu trữ năm 2024;"),
                P("Căn cứ Nghị quyết số 15/2025/NQ-HĐND ngày 12 tháng 12 năm 2025 của Hội đồng nhân "
                  "dân tỉnh quy định mức chi cho công tác số hóa tài liệu lưu trữ trên địa bàn tỉnh "
                  "Mẫu;"),
                P("Theo đề nghị của Giám đốc Sở Thông tin và Truyền thông tại Tờ trình số "
                  "47/TTr-STTTT ngày 02 tháng 02 năm 2026."),
                C("QUYẾT ĐỊNH:"),
                D("Điều 1.", "Phê duyệt Đề án Nền tảng lưu trữ số dùng chung tỉnh Mẫu giai đoạn "
                  "2026 – 2027 với các nội dung chủ yếu sau:"),
                P("1. Mục tiêu: số hóa 60% tài liệu có thời hạn bảo quản vĩnh viễn của các cơ quan, "
                  "tổ chức cấp tỉnh trong năm 2026; hoàn thành 100% trước ngày 31 tháng 12 năm 2027."),
                P("2. Hạ tầng: nền tảng được triển khai trên hạ tầng của Trung tâm dữ liệu tỉnh hiện "
                  "có; không thuê trung tâm dữ liệu dự phòng, sao lưu định kỳ sang hệ thống lưu trữ "
                  "ngoại tuyến của tỉnh."),
                P("3. Kinh phí: 18,6 tỷ đồng từ ngân sách tỉnh (năm 2026: 11,2 tỷ đồng; năm 2027: "
                  "7,4 tỷ đồng)."),
            ),
            page(
                D("Điều 2.", "Giao Sở Thông tin và Truyền thông chủ trì, phối hợp với Sở Nội vụ, "
                  "Sở Tài chính và các cơ quan liên quan tổ chức triển khai Đề án; tham mưu Ủy ban "
                  "nhân dân tỉnh ban hành kế hoạch thực hiện trong tháng 3 năm 2026."),
                D("Điều 3.", "Quyết định này có hiệu lực kể từ ngày ký. Chánh Văn phòng Ủy ban nhân "
                  "dân tỉnh, Giám đốc các sở: Thông tin và Truyền thông, Nội vụ, Tài chính; Thủ "
                  "trưởng các cơ quan, đơn vị có liên quan chịu trách nhiệm thi hành Quyết định "
                  "này./."),
                FOOTER,
            ),
        ],
    ),
    dict(
        key="kh125",
        file="kh-125-so-hoa-tai-lieu-luu-tru.pdf",
        agency=UBND,
        so="125/KH-UBND",
        date=(14, 3, 2026),
        loai="Kế hoạch",
        title="KẾ HOẠCH",
        subject="Số hóa hồ sơ, tài liệu lưu trữ của các cơ quan, tổ chức cấp tỉnh\n"
                "giai đoạn 2026 – 2027",
        noi_nhan=["Thường trực Tỉnh ủy;", "Thường trực HĐND tỉnh;", "Chủ tịch, các PCT UBND tỉnh;",
                  "Các sở, ban, ngành tỉnh;", "Lưu: VT, KGVX."],
        chuc_vu=["KT. CHỦ TỊCH", "PHÓ CHỦ TỊCH"],
        signer=PCT_UBND,
        pages=[
            page(
                P("Thực hiện Quyết định số 210/QĐ-UBND ngày 20 tháng 02 năm 2026 của Ủy ban nhân dân "
                  "tỉnh phê duyệt Đề án Nền tảng lưu trữ số dùng chung tỉnh Mẫu giai đoạn 2026 – "
                  "2027, Ủy ban nhân dân tỉnh ban hành Kế hoạch số hóa hồ sơ, tài liệu lưu trữ của "
                  "các cơ quan, tổ chức cấp tỉnh như sau:"),
                H("I. MỤC ĐÍCH, YÊU CẦU"),
                H("1. Mục đích"),
                P("- Bảo quản an toàn, lâu dài tài liệu lưu trữ có giá trị; hạn chế sử dụng bản gốc, "
                  "giảm nguy cơ hư hỏng tài liệu giấy."),
                P("- Phục vụ tra cứu, khai thác tài liệu nhanh chóng trên môi trường mạng, góp phần "
                  "xây dựng chính quyền số của tỉnh."),
                H("2. Yêu cầu"),
                P("- Tài liệu được chỉnh lý hoàn chỉnh, xác định thời hạn bảo quản trước khi số hóa; "
                  "bản số bảo đảm tính toàn vẹn, xác thực và đúng hướng dẫn kỹ thuật của Sở Thông "
                  "tin và Truyền thông."),
                P("- Dữ liệu số hóa được lưu trữ tập trung trên Nền tảng lưu trữ số dùng chung tỉnh "
                  "Mẫu, không lưu trữ phân tán tại máy tính cá nhân."),
                H("II. MỤC TIÊU"),
                P("1. Năm 2026: số hóa 60% hồ sơ, tài liệu có thời hạn bảo quản vĩnh viễn của các cơ "
                  "quan, tổ chức cấp tỉnh (khoảng 810 mét giá)."),
                P("2. Đến ngày 31 tháng 12 năm 2027: hoàn thành số hóa 100% hồ sơ, tài liệu có thời "
                  "hạn bảo quản vĩnh viễn; 100% hồ sơ điện tử hình thành mới được nộp lưu vào Nền "
                  "tảng."),
            ),
            page(
                H("III. NHIỆM VỤ, GIẢI PHÁP"),
                P("1. Rà soát, chỉnh lý tài liệu tồn đọng; lập danh mục hồ sơ cần số hóa theo thứ tự "
                  "ưu tiên: hồ sơ đất đai, xây dựng, nhân sự và tài liệu có tần suất khai thác cao."),
                P("2. Số hóa theo quy trình thống nhất: bóc tách, làm phẳng, quét ở độ phân giải tối "
                  "thiểu 300 dpi, lưu định dạng PDF/A, nhận dạng ký tự và kiểm tra chất lượng toàn bộ "
                  "bản số trước khi nộp lưu."),
                P("3. Bảo đảm an toàn thông tin: phân quyền truy cập theo vai trò, ghi nhật ký khai "
                  "thác, sao lưu định kỳ hằng tuần."),
                H("IV. KINH PHÍ THỰC HIỆN"),
                P("Tổng kinh phí 18,6 tỷ đồng từ ngân sách tỉnh, trong đó năm 2026 là 11,2 tỷ đồng, "
                  "năm 2027 là 7,4 tỷ đồng. Nền tảng sử dụng hạ tầng Trung tâm dữ liệu tỉnh hiện có, "
                  "không thuê trung tâm dữ liệu dự phòng. Định mức chi số hóa áp dụng theo Nghị quyết "
                  "số 15/2025/NQ-HĐND ngày 12 tháng 12 năm 2025 của Hội đồng nhân dân tỉnh."),
                H("V. TỔ CHỨC THỰC HIỆN"),
                P("1. Sở Thông tin và Truyền thông chủ trì vận hành Nền tảng lưu trữ số dùng chung; "
                  "tổng hợp, báo cáo Ủy ban nhân dân tỉnh kết quả thực hiện trước ngày 05 của tháng "
                  "đầu mỗi quý."),
                P("2. Sở Nội vụ hướng dẫn nghiệp vụ lưu trữ, thẩm định danh mục tài liệu cần số hóa."),
                P("3. Sở Tài chính bố trí kinh phí theo tiến độ; các cơ quan, tổ chức cấp tỉnh thực "
                  "hiện nhiệm vụ được giao tại Phụ lục kèm theo Kế hoạch này./."),
                FOOTER,
            ),
            page(
                CAP("Phụ lục", "PHÂN CÔNG NHIỆM VỤ THỰC HIỆN KẾ HOẠCH SỐ HÓA HỒ SƠ, TÀI LIỆU LƯU TRỮ",
                    "(Kèm theo Kế hoạch số 125/KH-UBND ngày 14 tháng 3 năm 2026 của Ủy ban nhân dân "
                    "tỉnh Mẫu)"),
                T(["STT", "Nhiệm vụ", "Cơ quan chủ trì", "Cơ quan phối hợp", "Thời gian hoàn thành",
                   "Sản phẩm"],
                  [["1", "Ban hành hướng dẫn kỹ thuật số hóa tài liệu lưu trữ",
                    "Sở Thông tin và Truyền thông", "Sở Nội vụ", "Tháng 3/2026", "Văn bản hướng dẫn"],
                   ["2", "Rà soát, lập danh mục hồ sơ cần số hóa", "Các cơ quan, tổ chức cấp tỉnh",
                    "Sở Nội vụ", "Tháng 4/2026", "Danh mục hồ sơ"],
                   ["3", "Đưa Nền tảng lưu trữ số dùng chung vào vận hành",
                    "Sở Thông tin và Truyền thông", "Văn phòng UBND tỉnh", "Tháng 6/2026",
                    "Nền tảng vận hành chính thức"],
                   ["4", "Số hóa 60% tài liệu có thời hạn bảo quản vĩnh viễn",
                    "Các cơ quan, tổ chức cấp tỉnh", "Sở Thông tin và Truyền thông", "Tháng 12/2026",
                    "Bản số nộp lưu"],
                   ["5", "Kiểm tra, đánh giá kết quả năm 2026", "Sở Nội vụ",
                    "Sở Thông tin và Truyền thông", "Tháng 01/2027", "Báo cáo kiểm tra"],
                   ["6", "Hoàn thành số hóa 100% tài liệu có thời hạn bảo quản vĩnh viễn",
                    "Các cơ quan, tổ chức cấp tỉnh", "Sở Thông tin và Truyền thông", "Tháng 12/2027",
                    "Bản số nộp lưu"],
                   ["7", "Tổng kết, đánh giá việc thực hiện Kế hoạch", "Sở Thông tin và Truyền thông",
                    "Sở Nội vụ, Sở Tài chính", "Tháng 01/2028", "Báo cáo tổng kết"]],
                  widths=[5, 31, 18, 18, 12, 16], small=True),
                wide=True,
            ),
        ],
    ),
    dict(
        key="hd12",
        file="hd-12-ky-thuat-so-hoa.pdf",
        agency=SO_TTTT,
        so="12/HD-STTTT",
        date=(25, 3, 2026),
        loai="Hướng dẫn",
        title="HƯỚNG DẪN",
        subject="Kỹ thuật số hóa tài liệu lưu trữ và kiểm tra chất lượng bản số",
        noi_nhan=["UBND tỉnh (để b/c);", "Các sở, ban, ngành tỉnh;", "Sở Nội vụ;", "Lưu: VT, CĐS."],
        chuc_vu=["KT. GIÁM ĐỐC", "PHÓ GIÁM ĐỐC"],
        signer=PGD_STTTT,
        pages=[
            page(
                P("Thực hiện Kế hoạch số 125/KH-UBND ngày 14 tháng 3 năm 2026 của Ủy ban nhân dân "
                  "tỉnh về số hóa hồ sơ, tài liệu lưu trữ của các cơ quan, tổ chức cấp tỉnh giai đoạn "
                  "2026 – 2027, Sở Thông tin và Truyền thông hướng dẫn kỹ thuật số hóa như sau:"),
                H("1. Yêu cầu chung"),
                P("Chỉ số hóa tài liệu đã được chỉnh lý, có trong danh mục được Sở Nội vụ thẩm định. "
                  "Bản số phải đầy đủ trang, đúng thứ tự, rõ nét và giữ nguyên dấu, chữ ký, bút phê "
                  "trên bản gốc."),
                H("2. Thông số kỹ thuật bản số"),
                T(["Thông số", "Yêu cầu"],
                  [["Độ phân giải", "Tối thiểu 300 dpi; tài liệu chữ nhỏ, bản vẽ: 400 dpi"],
                   ["Chế độ màu", "Màu 24 bit đối với trang có dấu, chữ ký màu; thang xám 8 bit đối "
                                  "với các trang còn lại"],
                   ["Định dạng lưu trữ", "PDF/A-2b, mỗi hồ sơ một tệp"],
                   ["Nhận dạng ký tự", "Lớp văn bản tiếng Việt Unicode, độ chính xác tối thiểu 98%"],
                   ["Đặt tên tệp", "Theo mã hồ sơ trong danh mục, không dấu, không khoảng trắng"],
                   ["Siêu dữ liệu", "Đủ các trường thông tin của văn bản: số, ký hiệu, ngày ban hành, "
                                    "trích yếu, người ký, cơ quan ban hành"]],
                  widths=[28, 72]),
            ),
            page(
                H("3. Kiểm tra chất lượng"),
                P("Kiểm tra toàn bộ bản số về độ rõ nét, số trang và thứ tự trang; đối chiếu ngẫu "
                  "nhiên 10% bản số với bản gốc. Bản số không đạt yêu cầu phải được quét lại trước "
                  "khi nộp lưu vào Nền tảng lưu trữ số dùng chung tỉnh Mẫu."),
                H("4. Tổ chức thực hiện"),
                P("Các cơ quan, tổ chức cấp tỉnh phân công công chức phụ trách số hóa, báo cáo kết "
                  "quả về Sở Thông tin và Truyền thông trước ngày 30 hằng tháng. Trong quá trình thực "
                  "hiện, nếu có vướng mắc đề nghị liên hệ Phòng Chuyển đổi số để được hướng dẫn./."),
                FOOTER,
            ),
        ],
    ),
    dict(
        key="cv33",
        file="cv-33-bao-cao-tien-do-quy-1.pdf",
        agency=VP_UBND,
        so="33/CV-VP",
        vv="V/v báo cáo tiến độ số hóa tài liệu\nlưu trữ quý I năm 2026",
        date=(8, 4, 2026),
        do_khan="Khẩn",
        loai="Công văn",
        noi_nhan=["Như trên;", "PCT UBND tỉnh Lê Minh Khang (để b/c);", "Sở Thông tin và Truyền thông;",
                  "Lưu: VT, KGVX."],
        chuc_vu=["KT. CHÁNH VĂN PHÒNG", "PHÓ CHÁNH VĂN PHÒNG"],
        signer=PCVP_UBND,
        pages=[
            page(
                KG("Các sở, ban, ngành tỉnh;", "Ủy ban nhân dân các xã, phường."),
                P("Thực hiện Kế hoạch số 125/KH-UBND ngày 14 tháng 3 năm 2026 của Ủy ban nhân dân "
                  "tỉnh về số hóa hồ sơ, tài liệu lưu trữ của các cơ quan, tổ chức cấp tỉnh giai đoạn "
                  "2026 – 2027; theo chỉ đạo của đồng chí Lê Minh Khang, Phó Chủ tịch Ủy ban nhân dân "
                  "tỉnh, Văn phòng Ủy ban nhân dân tỉnh đề nghị các cơ quan, đơn vị:"),
                P("1. Báo cáo kết quả rà soát, lập danh mục hồ sơ cần số hóa và khối lượng tài liệu "
                  "đã số hóa đến hết ngày 31 tháng 3 năm 2026 (theo mẫu kèm theo)."),
                P("2. Nêu rõ khó khăn, vướng mắc về nhân lực, thiết bị quét và kinh phí; đề xuất "
                  "giải pháp tháo gỡ."),
                P("Báo cáo gửi về Sở Thông tin và Truyền thông trước ngày 15 tháng 4 năm 2026 để tổng "
                  "hợp, báo cáo Ủy ban nhân dân tỉnh. Đề nghị các cơ quan, đơn vị khẩn trương thực "
                  "hiện./."),
                FOOTER,
            ),
        ],
    ),
    dict(
        key="ct07",
        file="ct-07-an-toan-thong-tin-luu-tru-so.pdf",
        agency=UBND,
        so="07/CT-UBND",
        date=(22, 4, 2026),
        loai="Chỉ thị",
        title="CHỈ THỊ",
        subject="Về tăng cường bảo đảm an toàn thông tin trong số hóa\nvà lưu trữ hồ sơ điện tử",
        noi_nhan=["Thường trực Tỉnh ủy;", "Thường trực HĐND tỉnh;", "Các sở, ban, ngành tỉnh;",
                  "UBND các xã, phường;", "Lưu: VT, KGVX."],
        chuc_vu=["CHỦ TỊCH"],
        signer=CT_UBND,
        pages=[
            page(
                P("Thời gian qua, việc số hóa hồ sơ, tài liệu lưu trữ trên địa bàn tỉnh đã có chuyển "
                  "biến tích cực; Nền tảng lưu trữ số dùng chung tỉnh Mẫu đang được triển khai theo "
                  "Kế hoạch số 125/KH-UBND ngày 14 tháng 3 năm 2026 của Ủy ban nhân dân tỉnh. Tuy "
                  "nhiên, một số cơ quan còn lưu bản số trên máy tính cá nhân, sử dụng thiết bị lưu "
                  "trữ di động không được quản lý, chưa phân quyền truy cập theo vai trò."),
                P("Để bảo đảm an toàn thông tin trong quá trình số hóa và lưu trữ hồ sơ điện tử, Chủ "
                  "tịch Ủy ban nhân dân tỉnh chỉ thị:"),
                P("1. Thủ trưởng các sở, ban, ngành tỉnh chịu trách nhiệm toàn diện về an toàn dữ "
                  "liệu số hóa của cơ quan mình; không lưu trữ bản số trên máy tính cá nhân, thiết bị "
                  "di động; chỉ nộp lưu, khai thác qua Nền tảng lưu trữ số dùng chung."),
                P("2. Sở Thông tin và Truyền thông phân quyền truy cập theo vai trò, ghi nhật ký khai "
                  "thác, sao lưu dữ liệu hằng tuần sang hệ thống lưu trữ ngoại tuyến; kiểm tra, đánh "
                  "giá an toàn thông tin của Nền tảng ít nhất 06 tháng một lần."),
            ),
            page(
                P("3. Sở Nội vụ phối hợp với Sở Thông tin và Truyền thông kiểm tra việc chấp hành "
                  "quy định về lưu trữ và an toàn thông tin tại các cơ quan, tổ chức; kịp thời chấn "
                  "chỉnh các trường hợp vi phạm."),
                P("4. Công an tỉnh hướng dẫn các cơ quan bảo vệ bí mật nhà nước trong hồ sơ, tài liệu "
                  "số hóa; không đưa tài liệu có nội dung bí mật nhà nước lên Nền tảng."),
                P("Yêu cầu Thủ trưởng các cơ quan, đơn vị nghiêm túc thực hiện Chỉ thị này. Sở Thông "
                  "tin và Truyền thông theo dõi, tổng hợp kết quả, báo cáo Ủy ban nhân dân tỉnh trước "
                  "ngày 15 tháng 12 năm 2026./."),
                FOOTER,
            ),
        ],
    ),
    dict(
        key="bc52",
        file="bc-52-ket-qua-6-thang.pdf",
        agency=SO_TTTT,
        so="52/BC-STTTT",
        date=(6, 7, 2026),
        loai="Báo cáo",
        title="BÁO CÁO",
        subject="Kết quả thực hiện Kế hoạch số hóa hồ sơ, tài liệu lưu trữ\n6 tháng đầu năm 2026",
        noi_nhan=["Như trên;", "Sở Nội vụ;", "Lưu: VT, CĐS."],
        chuc_vu=["GIÁM ĐỐC"],
        signer=GD_STTTT,
        pages=[
            page(
                KG("Ủy ban nhân dân tỉnh Mẫu."),
                P("Thực hiện Kế hoạch số 125/KH-UBND ngày 14 tháng 3 năm 2026 của Ủy ban nhân dân "
                  "tỉnh, Sở Thông tin và Truyền thông báo cáo kết quả 6 tháng đầu năm 2026 như sau:"),
                H("I. KẾT QUẢ THỰC HIỆN"),
                P("1. Nền tảng lưu trữ số dùng chung tỉnh Mẫu đưa vào vận hành chính thức từ ngày 15 "
                  "tháng 6 năm 2026; 32 cơ quan, tổ chức cấp tỉnh đã được cấp tài khoản và kết nối "
                  "với hệ thống quản lý văn bản và điều hành."),
                P("2. Đến ngày 30 tháng 6 năm 2026 đã số hóa 486 mét giá tài liệu, đạt 36% khối "
                  "lượng tài liệu có thời hạn bảo quản vĩnh viễn và 60% chỉ tiêu năm 2026. Kết quả "
                  "tại một số cơ quan:"),
                T(["STT", "Cơ quan", "Khối lượng (mét giá)", "Đã số hóa (mét giá)", "Tỷ lệ (%)"],
                  [["1", "Văn phòng UBND tỉnh", "96", "61", "63,5"],
                   ["2", "Sở Nội vụ", "74", "40", "54,1"],
                   ["3", "Sở Tài chính", "88", "29", "33,0"],
                   ["4", "Sở Nông nghiệp và Môi trường", "152", "47", "30,9"],
                   ["5", "Sở Xây dựng", "131", "35", "26,7"],
                   ["", "Toàn tỉnh", "1.350", "486", "36,0"]],
                  widths=[8, 44, 16, 16, 16]),
            ),
            page(
                H("II. TỒN TẠI, HẠN CHẾ"),
                P("- Một số cơ quan chưa hoàn thành chỉnh lý tài liệu tồn đọng nên chưa thể số hóa."),
                P("- Thiết bị quét khổ A3 còn thiếu; 4/32 cơ quan chưa bố trí công chức phụ trách."),
                H("III. NHIỆM VỤ 6 THÁNG CUỐI NĂM 2026"),
                P("1. Tập trung số hóa 324 mét giá còn lại để hoàn thành chỉ tiêu năm 2026."),
                P("2. Kiến nghị Ủy ban nhân dân tỉnh chỉ đạo các cơ quan có tỷ lệ số hóa dưới 30% xây "
                  "dựng kế hoạch bổ sung, hoàn thành chỉnh lý tài liệu trong quý III năm 2026."),
                P("Sở Thông tin và Truyền thông kính báo cáo Ủy ban nhân dân tỉnh./."),
                FOOTER,
            ),
        ],
    ),
    dict(
        key="tb18",
        file="tb-18-ket-luan-giam-sat.pdf",
        agency=HDND_TT,
        so="18/TB-HĐND",
        date=(28, 7, 2026),
        loai="Thông báo",
        title="THÔNG BÁO",
        subject="Kết luận của Thường trực Hội đồng nhân dân tỉnh tại phiên giám sát\n"
                "chuyên đề về số hóa tài liệu lưu trữ",
        noi_nhan=["Thường trực Tỉnh ủy;", "UBND tỉnh;", "Các Ban HĐND tỉnh;",
                  "Sở Thông tin và Truyền thông;", "Lưu: VT."],
        chuc_vu=["TL. THƯỜNG TRỰC HĐND", "CHÁNH VĂN PHÒNG"],
        signer=CVP_HDND,
        pages=[
            page(
                P("Ngày 21 tháng 7 năm 2026, Thường trực Hội đồng nhân dân tỉnh tổ chức phiên giám sát "
                  "chuyên đề về việc thực hiện Kế hoạch số 125/KH-UBND ngày 14 tháng 3 năm 2026 của Ủy "
                  "ban nhân dân tỉnh. Sau khi nghe Sở Thông tin và Truyền thông báo cáo tại Báo cáo số "
                  "52/BC-STTTT ngày 06 tháng 7 năm 2026 và ý kiến của các cơ quan liên quan, đồng chí "
                  "Lương Thế Bảo, Chủ tịch Hội đồng nhân dân tỉnh kết luận như sau:"),
                P("1. Ghi nhận Nền tảng lưu trữ số dùng chung đã vận hành đúng tiến độ; kết quả số hóa "
                  "6 tháng đầu năm đạt 60% chỉ tiêu năm 2026."),
                P("2. Đề nghị Ủy ban nhân dân tỉnh chỉ đạo các cơ quan có tỷ lệ số hóa dưới 30% hoàn "
                  "thành chỉnh lý tài liệu trước ngày 30 tháng 9 năm 2026; bố trí bổ sung thiết bị "
                  "quét khổ A3; báo cáo kết quả tại kỳ họp cuối năm 2026 của Hội đồng nhân dân tỉnh."),
                P("3. Giao Ban Kinh tế – Ngân sách Hội đồng nhân dân tỉnh theo dõi việc thực hiện các "
                  "nội dung kết luận này."),
                P("Thường trực Hội đồng nhân dân tỉnh thông báo để các cơ quan, đơn vị biết, thực "
                  "hiện./."),
                FOOTER,
            ),
        ],
    ),
    dict(
        key="cv1475",
        file="cv-1475-gop-y-quy-che.pdf",
        agency=UBND,
        so="1475/UBND-KGVX",
        vv="V/v góp ý dự thảo Quy chế quản lý, khai thác\nNền tảng lưu trữ số dùng chung",
        date=(2, 10, 2026),
        loai="Công văn",
        noi_nhan=["Như trên;", "Chủ tịch, các PCT UBND tỉnh;", "Lưu: VT, KGVX."],
        chuc_vu=["KT. CHỦ TỊCH", "PHÓ CHỦ TỊCH"],
        signer=PCT_UBND,
        pages=[
            page(
                KG("Các sở, ban, ngành tỉnh."),
                P("Sở Thông tin và Truyền thông đã xây dựng dự thảo Quy chế quản lý, khai thác Nền "
                  "tảng lưu trữ số dùng chung tỉnh Mẫu, quy định việc nộp lưu, phân quyền truy cập, "
                  "khai thác và sao lưu dữ liệu số hóa theo Chỉ thị số 07/CT-UBND ngày 22 tháng 4 năm "
                  "2026 của Chủ tịch Ủy ban nhân dân tỉnh."),
                P("Ủy ban nhân dân tỉnh đề nghị các cơ quan, đơn vị nghiên cứu, góp ý dự thảo (đăng "
                  "tải trên Cổng thông tin điện tử tỉnh) và gửi ý kiến bằng văn bản về Sở Thông tin và "
                  "Truyền thông trước ngày 16 tháng 10 năm 2026. Quá thời hạn trên, cơ quan, đơn vị "
                  "không có ý kiến được hiểu là thống nhất với dự thảo./."),
                FOOTER,
            ),
        ],
    ),
]

BY_KEY = {doc["key"]: doc for doc in DOCS}


# ─── NĐ30 fields ─────────────────────────────────────────────────────
def date_line(doc):
    day, month, year = doc["date"]
    # NĐ30: a leading zero for days below 10 and for months 1 and 2.
    return "{}, ngày {:02d} tháng {} năm {}".format(
        PLACE, day, "{:02d}".format(month) if month < 3 else str(month), year)


def subject_text(doc):
    if doc.get("vv"):  # a reviewer keeps "V/v góp ý ..." as "Góp ý ..."
        text = doc["vv"].replace("\n", " ")
        text = text[4:] if text.startswith("V/v ") else text
        return text[:1].upper() + text[1:]
    return doc["subject"].replace("\n", " ")


def metadata(doc):
    """The 12 NĐ30 fields as a reviewer keeps them (PascalCase, add_document)."""
    names = [name for name, _bold in doc["agency"]]
    tren, duoi = names[0], "\n".join(names[1:])
    if doc["agency"] is SO_TTTT:  # one agency on two lines
        duoi = " ".join(names[1:])
    return {
        "CQBH_tren": tren,
        "CQBH_duoi": duoi,
        "So_Ki_Hieu": doc["so"],
        "Ngay_BH": "{:02d}/{:02d}/{}".format(*doc["date"]),
        "Do_Khan": doc.get("do_khan", ""),
        "Do_Mat": "",
        "Loai_VB": doc["loai"],
        "ND_Chinh": subject_text(doc),
        "Noi_Nhan": "\n".join(doc["noi_nhan"]),  # list items, as the parser keeps them
        "Chuc_Vu": "\n".join(doc["chuc_vu"]),
        "Chu_Ky": doc["signer"],
    }


# ─── Print layout (HTML → PDF) ───────────────────────────────────────
PRINT_CSS = """
@page { size: A4 portrait; margin: 20mm 15mm 18mm 30mm;
        @top-center { content: counter(page); font-family: 'Times New Roman', serif;
                      font-size: 13pt; vertical-align: bottom; padding-bottom: 5mm; } }
@page :first { @top-center { content: none; } }
@page wide { size: A4 landscape; margin: 18mm 18mm 15mm 20mm;
        @top-center { content: counter(page); font-family: 'Times New Roman', serif;
                      font-size: 13pt; vertical-align: bottom; padding-bottom: 4mm; } }
html, body { margin: 0; padding: 0; }
body { font-family: 'Times New Roman', Times, serif; font-size: 13.5pt; line-height: 1.22;
       color: #000; background: #fff; }
section.page { break-after: page; }
section.page:last-child { break-after: auto; }
section.wide { page: wide; }
table.hdr, table.ftr { width: 100%; border-collapse: collapse; table-layout: fixed; }
table.hdr td, table.ftr td { vertical-align: top; padding: 0; text-align: center; }
table.hdr td.l { width: 38%; } table.hdr td.r { width: 62%; }
.ag0 { font-size: 12.5pt; text-transform: uppercase; }
.ag1 { font-size: 12.5pt; font-weight: bold; }
.rule-s { width: 32%; margin: 1px auto 0; border-top: 1px solid #000; }
.qh { font-size: 12.5pt; font-weight: bold; white-space: nowrap; }
.tn { font-size: 13.5pt; font-weight: bold; }
.tn span { border-bottom: 1px solid #000; padding-bottom: 1px; }
.so { font-size: 13pt; margin-top: 7pt; }
.vv { font-size: 12pt; margin-top: 3pt; }
.dt { font-size: 13.5pt; font-style: italic; margin-top: 7pt; }
.stamp { display: inline-block; margin-top: 8pt; padding: 1pt 8pt; border: 1.6px solid #b3261e;
         color: #b3261e; font-weight: bold; font-size: 12.5pt; letter-spacing: 1px; }
.title { text-align: center; font-weight: bold; font-size: 14pt; margin-top: 16pt; }
.subject { text-align: center; font-weight: bold; font-size: 14pt; }
.subject + .rule-s { width: 22%; margin: 5pt auto 10pt; }
.kg { text-align: center; margin: 10pt 0 8pt; }
table.kgl { margin: 10pt auto 8pt; border-collapse: collapse; }
table.kgl td { padding: 0 0 0 4pt; vertical-align: top; }
p { margin: 0 0 5pt; text-indent: 12mm; text-align: justify; }
p.h { font-weight: bold; break-after: avoid; }
p.c { text-indent: 0; text-align: center; font-weight: bold; margin: 8pt 0 6pt; }
.cap { text-align: center; margin-bottom: 6pt; }
.cap .k { font-weight: bold; } .cap .t { font-weight: bold; } .cap .n { font-style: italic; }
table.data { width: 100%; border-collapse: collapse; margin: 4pt 0 8pt; font-size: 12.5pt; }
table.data.small { font-size: 12pt; }
table.data th, table.data td { border: 1px solid #000; padding: 3pt 5pt; vertical-align: top; }
table.data th { font-weight: bold; text-align: center; }
table.data td.num { text-align: center; }
table.ftr { margin-top: 12pt; break-inside: avoid; }
table.data tr { break-inside: avoid; }
table.ftr td.l { width: 46%; text-align: left; }
table.ftr td.r { width: 54%; }
.nn { font-size: 12pt; font-weight: bold; font-style: italic; }
.nnl { font-size: 11pt; line-height: 1.25; }
.cv { font-weight: bold; font-size: 13.5pt; }
.sig { height: 26mm; display: flex; align-items: center; justify-content: center; }
.name { font-weight: bold; font-size: 13.5pt; }
"""


def _e(text):
    return html.escape(text, quote=False)


def _lines(text):
    return "<br/>".join(_e(line) for line in text.split("\n"))


_SIGNATURE_STROKES = (
    "M8 44 C 14 12, 34 6, 30 30 C 27 50, 12 48, 20 34 C 28 20, 44 28, 50 38 "
    "C 55 46, 60 26, 68 30 C 76 34, 78 44, 86 36 C 94 28, 100 30, 106 38 C 112 46, 124 32, 142 20",
    "M10 40 C 20 8, 40 10, 36 28 C 33 42, 22 44, 26 32 L 34 14 C 38 4, 44 30, 48 40 "
    "C 52 48, 58 22, 66 24 C 72 26, 70 40, 78 38 C 88 36, 92 18, 100 22 C 108 26, 104 42, 116 40 "
    "C 128 38, 136 26, 150 24",
    "M12 30 C 26 6, 48 8, 44 26 C 42 38, 28 42, 30 30 C 34 16, 58 22, 60 34 C 62 44, 70 28, 80 30 "
    "C 90 32, 86 46, 98 42 C 110 38, 112 22, 124 26 C 134 30, 132 44, 146 36",
)


def signature_svg(name, seed):
    """An invented pen stroke where the signature goes: one of a few
    cursive shapes, each number nudged a little per document."""
    rnd = random.Random(seed)
    stroke = _SIGNATURE_STROKES[rnd.randrange(len(_SIGNATURE_STROKES))]

    def nudge(match):
        return str(max(2, int(match.group(0)) + rnd.randint(-3, 3)))

    stroke = re.sub(r"\d+", nudge, stroke)
    flourish = "M{} 54 C {} 46, {} 46, {} {}".format(
        rnd.randint(14, 26), rnd.randint(50, 70), rnd.randint(100, 120), rnd.randint(150, 162),
        rnd.randint(34, 42))
    return ('<svg width="170" height="62" viewBox="0 0 170 62" aria-label="Chữ ký">'
            '<path d="{} {}" fill="none" stroke="#1f2f7a" stroke-width="1.9" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>').format(stroke, flourish)


def _print_header(doc):
    left = []
    for name, bold in doc["agency"]:
        left.append('<div class="{}">{}</div>'.format("ag1" if bold else "ag0", _e(name)))
    left.append('<div class="rule-s"></div>')
    right = ['<div class="qh">CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM</div>',
             '<div class="tn"><span>Độc lập - Tự do - Hạnh phúc</span></div>']
    so = ['<div class="so">Số: {}</div>'.format(_e(doc["so"]))]
    if doc.get("vv"):
        so.append('<div class="vv">{}</div>'.format(_lines(doc["vv"])))
    if doc.get("do_khan"):
        so.append('<div class="stamp">{}</div>'.format(_e(doc["do_khan"].upper())))
    out = ['<table class="hdr"><tr><td class="l">{}</td><td class="r">{}</td></tr>'.format(
        "".join(left), "".join(right)),
        '<tr><td class="l">{}</td><td class="r"><div class="dt">{}</div></td></tr></table>'.format(
        "".join(so), _e(date_line(doc)))]
    if doc.get("title"):
        out.append('<div class="title">{}</div>'.format(_e(doc["title"])))
        out.append('<div class="subject">{}</div><div class="rule-s"></div>'.format(
            _lines(doc["subject"])))
    return "\n".join(out)


def _print_footer(doc, seed):
    items = "<br/>".join("- " + _e(item) for item in doc["noi_nhan"])
    return ('<table class="ftr"><tr><td class="l"><div class="nn">Nơi nhận:</div>'
            '<div class="nnl">{}</div></td><td class="r"><div class="cv">{}</div>'
            '<div class="sig">{}</div><div class="name">{}</div></td></tr></table>').format(
        items, "<br/>".join(_e(line) for line in doc["chuc_vu"]),
        signature_svg(doc["signer"], seed), _e(doc["signer"]))


def _print_table(head, rows, widths, small):
    cols = ""
    if widths:
        cols = "<colgroup>{}</colgroup>".format(
            "".join('<col style="width:{}%">'.format(w) for w in widths))
    body = ["<tr>{}</tr>".format("".join("<th>{}</th>".format(_e(h)) for h in head))]
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            numeric = index == 0 or cell.replace(".", "").replace(",", "").isdigit()
            cls = ' class="num"' if numeric else ""
            text = _e(cell)
            if not row[0] and index == 1:  # the total row
                text = "<b>{}</b>".format(text)
            cells.append("<td{}>{}</td>".format(cls, text))
        body.append("<tr>{}</tr>".format("".join(cells)))
    return '<table class="data{}">{}{}</table>'.format(" small" if small else "", cols, "".join(body))


def flow(doc):
    """[(orientation, block)] in reading order. Portrait groups of a
    document run on as one text (the browser breaks the pages); a landscape
    group (an annex table) starts a page of its own."""
    return [(pg["orient"], block) for pg in doc["pages"] for block in pg["blocks"]]


def probe(block):
    """A piece of text that finds the block on its PDF page."""
    kind = block[0]
    if kind in ("p", "h"):
        text = block[1]
    elif kind == "c":
        text = block[1].split("\n")[0]
    elif kind == "d":
        text = "{} {}".format(block[1], block[2])
    elif kind == "kg":
        text = "Kính gửi"
    elif kind == "t":
        row = block[2][0]
        text = row[1] if len(row) > 1 else row[0]
    elif kind == "cap":
        text = block[1][1]
    else:  # footer
        text = "Nơi nhận"
    return re.sub(r"\s+", " ", text).strip()[:32]


def _groups(doc):
    groups = []
    for orient, block in flow(doc):
        if not groups or groups[-1][0] != orient:
            groups.append((orient, []))
        groups[-1][1].append(block)
    return groups


def print_html(doc):
    sections = []
    for number, (orient, blocks) in enumerate(_groups(doc), 1):
        parts = []
        if number == 1:
            parts.append(_print_header(doc))
        for block in blocks:
            kind = block[0]
            if kind == "p":
                parts.append("<p>{}</p>".format(_e(block[1])))
            elif kind == "h":
                parts.append('<p class="h">{}</p>'.format(_e(block[1])))
            elif kind == "c":
                parts.append('<p class="c">{}</p>'.format(_lines(block[1])))
            elif kind == "d":
                parts.append("<p><b>{}</b> {}</p>".format(_e(block[1]), _e(block[2])))
            elif kind == "kg":
                lines = block[1]
                if len(lines) == 1:
                    parts.append('<div class="kg">Kính gửi: {}</div>'.format(_e(lines[0])))
                else:
                    parts.append('<table class="kgl"><tr><td>Kính gửi:</td><td>{}</td></tr>'
                                 '</table>'.format("<br/>".join("- " + _e(x) for x in lines)))
            elif kind == "t":
                parts.append(_print_table(*block[1:]))
            elif kind == "cap":
                lines = block[1]
                parts.append('<div class="cap"><div class="k">{}</div><div class="t">{}</div>'
                             '<div class="n">{}</div></div>'.format(*(_e(x) for x in lines)))
            elif kind == "footer":
                parts.append(_print_footer(doc, doc["key"]))
        cls = "page wide" if orient == "landscape" else "page"
        sections.append('<section class="{}">\n{}\n</section>'.format(cls, "\n".join(parts)))
    return ("<!doctype html>\n<html lang=\"vi\"><head><meta charset=\"utf-8\">"
            "<title>{} (văn bản mẫu, hư cấu)</title><style>{}</style></head><body>\n"
            "<!-- Văn bản hư cấu dùng cho ảnh minh họa; tỉnh Mẫu không có thật. -->\n{}\n"
            "</body></html>\n").format(_e(doc["so"]), PRINT_CSS, "\n".join(sections))


# ─── What Chandra returns for those pages ────────────────────────────
PORTRAIT = (1240, 1754)   # A4 at 150 dpi, as the app renders it
LANDSCAPE = (1754, 1240)
LINE = 34                 # one line of 13.5 pt text at 150 dpi


class _Cursor:
    """Lays blocks down a page and hands out plausible data-bbox values."""

    def __init__(self, orient):
        self.width, self.height = PORTRAIT if orient == "portrait" else LANDSCAPE
        self.left = 177 if orient == "portrait" else 118
        self.right = self.width - (89 if orient == "portrait" else 106)
        self.y = 118 if orient == "portrait" else 106

    def take(self, text_len, x1=None, x2=None, gap=10, extra=0):
        x1 = self.left if x1 is None else x1
        x2 = self.right if x2 is None else x2
        per_line = max(10, int((x2 - x1) / 12.4))
        lines = max(1, math.ceil(text_len / per_line))
        y1, y2 = self.y, self.y + lines * LINE + extra
        self.y = y2 + gap
        return "{} {} {} {}".format(x1, y1, x2, y2)


def _div(label, bbox, inner):
    return '<div data-label="{}" data-bbox="{}">{}</div>'.format(label, bbox, inner)


def _chandra_table(head, rows):
    out = ["<table>", "<tr>{}</tr>".format("".join("<th>{}</th>".format(_e(h)) for h in head))]
    for row in rows:
        out.append("<tr>{}</tr>".format("".join("<td>{}</td>".format(_e(c)) for c in row)))
    out.append("</table>")
    return "".join(out)


def _chandra_header(doc, cur):
    mid = int(cur.left + (cur.right - cur.left) * 0.38)
    divs = []
    bold_agency = "<br/>".join(
        "<b>{}</b>".format(_e(name)) if bold else _e(name) for name, bold in doc["agency"])
    top = cur.y
    divs.append(_div("Text", "{} {} {} {}".format(cur.left + 20, top, mid - 20,
                                                   top + LINE * len(doc["agency"])),
                     "<p>{}</p>".format(bold_agency)))
    divs.append(_div("Text", "{} {} {} {}".format(mid + 10, top, cur.right, top + 2 * LINE),
                     "<p><b>CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM</b><br/>"
                     "<b>Độc lập - Tự do - Hạnh phúc</b></p>"))
    so_top = top + LINE * len(doc["agency"]) + 14
    so_lines = 1 + (doc["vv"].count("\n") + 1 if doc.get("vv") else 0)
    so_inner = "Số: {}".format(_e(doc["so"]))
    if doc.get("vv"):
        so_inner += "<br/>" + _lines(doc["vv"])
    divs.append(_div("Text", "{} {} {} {}".format(cur.left + 10, so_top, mid - 10,
                                                   so_top + LINE * so_lines),
                     "<p>{}</p>".format(so_inner)))
    divs.append(_div("Text", "{} {} {} {}".format(mid + 40, top + 2 * LINE + 14, cur.right - 20,
                                                   top + 3 * LINE + 14),
                     "<p><i>{}</i></p>".format(_e(date_line(doc)))))
    cur.y = so_top + LINE * so_lines + 16
    if doc.get("do_khan"):
        divs.append(_div("Text", "{} {} {} {}".format(cur.left + 120, cur.y, cur.left + 250,
                                                       cur.y + 40),
                         "<p><b>{}</b></p>".format(_e(doc["do_khan"].upper()))))
        cur.y += 56
    if doc.get("title"):
        cur.y += 14
        width = 40 * len(doc["title"])
        x1 = (cur.left + cur.right - width) // 2
        divs.append(_div("Section-Header", cur.take(len(doc["title"]), x1, x1 + width, gap=4),
                         "<p><b>{}</b></p>".format(_e(doc["title"]))))
        subject = doc["subject"].replace("\n", " ")
        divs.append(_div("Section-Header", cur.take(len(subject), cur.left + 60, cur.right - 60,
                                                    gap=24),
                         "<p><b>{}</b></p>".format(_e(subject))))
    return divs


def _chandra_footer(doc, cur):
    mid = int(cur.left + (cur.right - cur.left) * 0.46)
    top = cur.y + 10
    items = "".join("<li>{}</li>".format(_e(item)) for item in doc["noi_nhan"])
    nn = _div("List-Group", "{} {} {} {}".format(cur.left, top, mid - 20,
                                                 top + 30 + 26 * len(doc["noi_nhan"])),
              "<p><b><i>Nơi nhận:</i></b></p><ul>{}</ul>".format(items))
    cv_h = LINE * len(doc["chuc_vu"])
    cv = _div("Text", "{} {} {} {}".format(mid + 60, top, cur.right - 40, top + cv_h),
              "<p><b>{}</b></p>".format("<br/>".join(_e(x) for x in doc["chuc_vu"])))
    img_top = top + cv_h + 10
    img = _div("Image", "{} {} {} {}".format(mid + 140, img_top, cur.right - 120, img_top + 120),
               '<img alt="Chữ ký"/>')
    name_top = img_top + 132
    name = _div("Text", "{} {} {} {}".format(mid + 110, name_top, cur.right - 90, name_top + LINE),
                "<p><b>{}</b></p>".format(_e(doc["signer"])))
    cur.y = max(name_top + LINE, top + 30 + 26 * len(doc["noi_nhan"])) + 10
    return [nn, cv, img, name]


def chandra_pages(doc, layout=None):
    """(page HTML list, page widths, orientations) as the OCR step returns them.

    layout: the PDF page (1-based) each block of flow(doc) starts on, as
    build_demo.py measured it in the printed PDF; without it every group of
    doc["pages"] is taken as one page."""
    blocks = flow(doc)
    if layout is None:
        layout = [number for number, pg in enumerate(doc["pages"], 1) for _b in pg["blocks"]]
    by_page = {}
    for (orient, block), number in zip(blocks, layout):
        by_page.setdefault(number, (orient, []))[1].append(block)
    pages, widths, orients = [], [], []
    for number in range(1, max(layout) + 1):
        orient, page_blocks = by_page.get(number, (orients[-1] if orients else "portrait", []))
        cur = _Cursor(orient)
        divs = []
        if number > 1:
            x1 = cur.width // 2 - 12
            divs.append(_div("Page-Header", "{} {} {} {}".format(x1, cur.y - 50, x1 + 24,
                                                                  cur.y - 20),
                             "<p>{}</p>".format(number)))
        else:
            divs.extend(_chandra_header(doc, cur))
        for block in page_blocks:
            kind = block[0]
            if kind == "p":
                divs.append(_div("Text", cur.take(len(block[1]) + 10), "<p>{}</p>".format(_e(block[1]))))
            elif kind == "h":
                divs.append(_div("Text", cur.take(len(block[1]) + 10),
                                 "<p><b>{}</b></p>".format(_e(block[1]))))
            elif kind == "c":
                text = block[1]
                width = min(cur.right - cur.left, 26 * max(len(x) for x in text.split("\n")))
                x1 = (cur.left + cur.right - width) // 2
                divs.append(_div("Section-Header" if text.endswith(":") else "Text",
                                 cur.take(len(text), x1, x1 + width, extra=LINE * text.count("\n")),
                                 "<p><b>{}</b></p>".format(_lines(text))))
            elif kind == "d":
                divs.append(_div("Text", cur.take(len(block[1]) + len(block[2]) + 11),
                                 "<p><b>{}</b> {}</p>".format(_e(block[1]), _e(block[2]))))
            elif kind == "kg":
                lines = block[1]
                if len(lines) == 1:
                    inner = "<p>Kính gửi: {}</p>".format(_e(lines[0]))
                    divs.append(_div("Text", cur.take(len(lines[0]) + 10, cur.left + 200,
                                                      cur.right - 200), inner))
                else:
                    inner = "<p>Kính gửi:</p><ul>{}</ul>".format(
                        "".join("<li>{}</li>".format(_e(x)) for x in lines))
                    divs.append(_div("List-Group", cur.take(10, cur.left + 280, cur.right - 120,
                                                            extra=LINE * len(lines)), inner))
            elif kind == "t":
                head, rows = block[1], block[2]
                extra = LINE * (len(rows) + 1) + sum(
                    LINE * (max(len(c) for c in row) // 40) for row in rows)
                divs.append(_div("Table", cur.take(1, extra=extra), _chandra_table(head, rows)))
            elif kind == "cap":
                key, title, note = block[1]
                divs.append(_div("Section-Header", cur.take(len(key), cur.left + 700, cur.right - 700,
                                                            gap=2),
                                 "<p><b>{}</b></p>".format(_e(key))))
                divs.append(_div("Section-Header", cur.take(len(title), cur.left + 160,
                                                            cur.right - 160, gap=2),
                                 "<p><b>{}</b></p>".format(_e(title))))
                divs.append(_div("Text", cur.take(len(note), cur.left + 220, cur.right - 220),
                                 "<p><i>{}</i></p>".format(_e(note))))
            elif kind == "footer":
                divs.extend(_chandra_footer(doc, cur))
        pages.append("\n".join(divs))
        widths.append(cur.width)
        orients.append(orient)
    return pages, widths, orients
