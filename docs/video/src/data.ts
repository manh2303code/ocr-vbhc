import {P1, P3, Rect} from './doc';

// Everything below describes the fictional sample document (see doc.tsx).

type TagKey = 'agency' | 'motto' | 'number' | 'urgency' | 'date' | 'title' | 'body';

export type OcrLine = {text: string; bold?: boolean; italic?: boolean; centre?: boolean};
export type OcrBlock = {rect: Rect; tag: TagKey; lines: OcrLine[]};

export const OCR_BLOCKS: OcrBlock[] = [
  {
    rect: P1.agency,
    tag: 'agency',
    lines: [{text: 'UBND TỈNH MẪU'}, {text: 'SỞ KHOA HỌC VÀ CÔNG NGHỆ', bold: true}],
  },
  {
    rect: P1.motto,
    tag: 'motto',
    lines: [
      {text: 'CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM', bold: true},
      {text: 'Độc lập - Tự do - Hạnh phúc', bold: true},
    ],
  },
  {rect: P1.number, tag: 'number', lines: [{text: 'Số: 125/KH-SKHCN'}]},
  {rect: P1.urgency, tag: 'urgency', lines: [{text: 'KHẨN', bold: true}]},
  {rect: P1.date, tag: 'date', lines: [{text: 'Mẫu, ngày 12 tháng 3 năm 2026', italic: true}]},
  {
    rect: P1.title,
    tag: 'title',
    lines: [
      {text: 'KẾ HOẠCH', bold: true},
      {text: 'Tổ chức hội nghị tổng kết công tác chuyển đổi số năm 2025', bold: true},
    ],
  },
  {
    rect: P1.intro,
    tag: 'body',
    lines: [
      {
        text: 'Thực hiện Chương trình công tác năm 2026 của Ủy ban nhân dân tỉnh Mẫu, Sở Khoa học và Công nghệ ban hành Kế hoạch tổ chức hội nghị tổng kết công tác chuyển đổi số năm 2025 như sau:',
      },
    ],
  },
  {
    rect: P1.s1,
    tag: 'body',
    lines: [
      {text: 'I. MỤC ĐÍCH, YÊU CẦU', bold: true},
      {
        text: '1. Đánh giá kết quả chuyển đổi số năm 2025 của các cơ quan, đơn vị; biểu dương tập thể, cá nhân tiêu biểu.',
      },
      {text: '2. Thống nhất nhiệm vụ trọng tâm năm 2026.'},
      {text: '3. Hội nghị bảo đảm thiết thực, tiết kiệm, đúng thành phần.'},
    ],
  },
  {
    rect: P1.s2,
    tag: 'body',
    lines: [
      {text: 'II. THỜI GIAN, ĐỊA ĐIỂM', bold: true},
      {text: '1. Thời gian: 08 giờ 00, thứ Năm, ngày 26 tháng 3 năm 2026.'},
      {text: '2. Địa điểm: Hội trường A, Trung tâm Hội nghị tỉnh Mẫu.'},
    ],
  },
  {
    rect: P1.s3,
    tag: 'body',
    lines: [
      {text: 'III. THÀNH PHẦN', bold: true},
      {text: '1. Đại biểu tỉnh: lãnh đạo Ủy ban nhân dân tỉnh; lãnh đạo Văn phòng Ủy ban nhân dân tỉnh.'},
      {text: '2. Lãnh đạo và công chức phụ trách chuyển đổi số của các sở, ban, ngành thuộc tỉnh.'},
      {text: '3. Lãnh đạo Ủy ban nhân dân các huyện, thị xã, thành phố; đại diện các doanh nghiệp…'},
    ],
  },
];

// Field values (document content, so Vietnamese in both variants). `src`
// says which page region the value was read from; null = not on the page.
export type FieldDef = {
  value: string | null; // null: use the copy's "none" / page count
  page: 1 | 3 | null;
  rect: Rect | null;
  gold?: boolean;
  lines?: number;
};

export const FIELDS: FieldDef[] = [
  {value: 'UBND TỈNH MẪU', page: 1, rect: P1.agencyTop},
  {value: 'SỞ KHOA HỌC VÀ CÔNG NGHỆ', page: 1, rect: P1.agencyBottom},
  {value: '125/KH-SKHCN', page: 1, rect: P1.number, gold: true},
  {value: '12/03/2026', page: 1, rect: P1.date},
  {value: 'Khẩn', page: 1, rect: P1.urgency},
  {value: null, page: null, rect: null},
  {value: 'Kế hoạch', page: 1, rect: P1.titleType},
  {value: 'Tổ chức hội nghị tổng kết công tác chuyển đổi số năm 2025', page: 1, rect: P1.titleSubject},
  {
    value: 'UBND tỉnh (để báo cáo); Các sở, ban, ngành; UBND các huyện, thị xã, thành phố; Lưu: VT, CĐS.',
    page: 3,
    rect: P3.recipients,
    lines: 2,
  },
  {value: 'KT. GIÁM ĐỐC, PHÓ GIÁM ĐỐC', page: 3, rect: P3.sigTitle},
  {value: 'Lê Minh Khoa', page: 3, rect: P3.sigName},
  {value: null, page: null, rect: null},
];

export type Chunk = {id: number; page: string; text: string};

export const CHUNKS: Chunk[] = [
  {id: 1, page: '1', text: 'Thực hiện Chương trình công tác năm 2026 của Ủy ban nhân dân tỉnh Mẫu, Sở Khoa học và Công nghệ…'},
  {id: 2, page: '1', text: 'I. Mục đích, yêu cầu: đánh giá kết quả chuyển đổi số năm 2025 của các cơ quan…'},
  {id: 3, page: '1', text: 'II. Thời gian: 08 giờ 00, thứ Năm, ngày 26 tháng 3 năm 2026. Địa điểm: Hội trường A…'},
  {id: 4, page: '1–2', text: 'III. Thành phần: đại biểu tỉnh, các sở, ban, ngành… Tổng số khoảng 150 đại biểu.'},
  {id: 5, page: '2', text: 'V. Kinh phí: chi thường xuyên năm 2026 của Sở, theo Tờ trình số 48/TTr-SKHCN.'},
  {id: 6, page: '3', text: 'VI. Tổ chức thực hiện: Văn phòng Sở chuẩn bị hội trường, tài liệu…'},
];

// Where the chunks of page 1 start and end (page y coordinates).
export const P1_CUTS = [P1.s1.y - 3, P1.s2.y - 3, P1.s3.y - 3];

// Ranked chunk ids returned by each retriever for the question.
export const LISTS = {
  fts: [3, 1, 6],
  vec: [3, 4, 2],
  fold: [3, 1, 4],
};
// Reciprocal rank fusion (k = 60) of the three lists, top four.
export const FUSED = [3, 1, 4, 2];
// ViRanker cross-encoder scores, best first.
export const RERANKED: Array<{id: number; score: number}> = [
  {id: 3, score: 0.94},
  {id: 4, score: 0.37},
  {id: 1, score: 0.21},
  {id: 2, score: 0.06},
];
