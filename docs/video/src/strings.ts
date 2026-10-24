// Interface copy in Vietnamese and English. The sample document and the chat
// stay in Vietnamese in both variants, as in the app (the assistant answers in
// Vietnamese); the English variant adds a muted translation under the chat.

export type Lang = 'vi' | 'en';

const VI = {
  appName: 'OCR VBHC',
  tagline: 'Số hóa văn bản hành chính và hỏi đáp ngay trên máy của bạn',
  stages: ['Đọc trang', 'Kiểm tra trang', 'Trích trường', 'Lập chỉ mục', 'Tìm nguồn', 'Trả lời'],

  ocrTitle: 'Chandra OCR 2 đọc bố cục và chữ',
  ocrSub: 'Mô hình thị giác và ngôn ngữ, chạy cục bộ bằng llama.cpp',
  ocrPanel: 'Toàn văn, trang 1',
  ocrCaption: 'Lỗi ký tự 0,74% trên 7 trang đối chiếu',
  blockTags: {
    agency: 'Cơ quan',
    motto: 'Quốc hiệu',
    number: 'Số hiệu',
    urgency: 'Độ khẩn',
    date: 'Địa danh, ngày',
    title: 'Tên loại, trích yếu',
    body: 'Nội dung',
  },

  gateTitle: 'Mỗi trang được kiểm tra trước khi dùng',
  gateSub: 'Trang bị cắt ngắn, lặp chữ hoặc thiếu bố cục được đọc lại một lần',
  gateLine: 'Kiểm tra trang',
  page: (n: number) => `Trang ${n}`,
  gateLoop: 'Lặp chữ',
  gateRetry: 'Đọc lại',
  gatePassed: 'Đạt',
  gateNext: 'Sang bước trích trường',

  fieldsTitle: '12 trường theo Nghị định 30/2020',
  fieldsSub: 'Sửa được trong ứng dụng, mỗi lần sửa lưu một phiên bản',
  fieldsPanel: 'Thông tin',
  fieldLabels: [
    'Cơ quan chủ quản',
    'Cơ quan ban hành',
    'Số hiệu',
    'Ngày ban hành',
    'Độ khẩn',
    'Độ mật',
    'Loại văn bản',
    'Nội dung chính',
    'Nơi nhận',
    'Chức vụ',
    'Người ký',
    'Toàn văn',
  ],
  none: 'Không',
  pages3: '3 trang',
  fromPage: (n: number) => `từ trang ${n}`,

  indexTitle: 'Chia đoạn, nhúng vector, lập chỉ mục',
  indexSub: 'bge-m3 tiếng Việt, 1024 chiều, lưu cạnh chỉ mục toàn văn trong SQLite',
  chunk: (n: number) => `Đoạn ${n}`,
  pageShort: (n: number | string) => `trang ${n}`,
  vecHead: 'bge-m3, 1024 chiều',
  storeFts: 'FTS5, có bản không dấu',
  storeVec: 'sqlite-vec',

  retrTitle: 'Tìm đoạn liên quan',
  retrSub: 'Khớp từ khóa, ý nghĩa và chữ không dấu, gộp bằng RRF, rồi ViRanker xếp lại',
  listFts: 'Từ khóa',
  listVec: 'Ý nghĩa',
  listFold: 'Không dấu',
  listRrf: 'Gộp thứ hạng RRF',
  listRerank: 'ViRanker xếp lại',

  ansTitle: 'Qwen3 trả lời và dẫn nguồn',
  ansSub: 'Mỗi số, ngày và số hiệu được đối chiếu với nguồn trước khi hiển thị',
  stepSearch: 'Tìm văn bản: 3 đoạn phù hợp',
  stepRead: 'Đọc mục III, trang 2',
  srcLabel: (n: number, p: number) => `[${n}]  125/KH-SKHCN, trang ${p}`,
  checkTitle: 'Kiểm tra câu trả lời',
  checkDone: '3/3 số liệu có trong nguồn',
  capTurns: '187/199 lượt hỏi đáp nhiều lượt trả lời đúng',
  capFigures: 'Câu trả lời có số liệu không có trong nguồn: từ 17 xuống 0',
  inputPlaceholder: 'Hỏi về văn bản',

  outroLine: 'Chạy hoàn toàn trên máy, không gửi dữ liệu ra ngoài.',
  credit: 'Phát triển bởi Trần Hậu Mạnh',
  footnote:
    'Số liệu đo trên máy của nhà phát triển: RTX 5090 Laptop 24 GB, Qwen3-8B, bộ thử cục bộ.',

  // Conversation memory composition
  memTitle: 'Nhớ cuộc hội thoại',
  memSub: 'Mỗi lượt lưu văn bản đang mở và các văn bản đã dẫn, gói gọn thành trạng thái hội thoại',
  memState: 'Trạng thái hội thoại',
  memOpen: 'Đang mở',
  memCited: 'Đã dẫn',
  memTurns: 'Các lượt',
  memTurn: (n: number) => `Lượt ${n}`,
  memResolved: 'hiểu là',
  memFromState: 'Trả lời từ trạng thái hội thoại, không cần tìm lại',
  memAsked: ['hỏi nơi tổ chức', 'hỏi kinh phí', 'hỏi về chính cuộc hội thoại'],
  memSeconds: '0,8 giây',
  memCaption: 'Nhớ văn bản của lượt đầu khi đã sang lượt thứ 4 trở đi: 16/16 hội thoại, trước đây 3/16',
};

export type Copy = typeof VI;

const EN: Copy = {
  appName: 'OCR VBHC',
  tagline: 'Digitises Vietnamese administrative documents and answers questions about them, on your own machine',
  stages: ['Read', 'Check', 'Extract', 'Index', 'Retrieve', 'Answer'],

  ocrTitle: 'Chandra OCR 2 reads the layout and the text',
  ocrSub: 'A vision-language model, run locally by llama.cpp',
  ocrPanel: 'Full text, page 1',
  ocrCaption: 'Character error rate 0.74% on 7 reference pages',
  blockTags: {
    agency: 'Agency',
    motto: 'National motto',
    number: 'Number',
    urgency: 'Urgency',
    date: 'Place, date',
    title: 'Type, subject',
    body: 'Body',
  },

  gateTitle: 'Every page is checked before it is used',
  gateSub: 'Truncated, looping or layout-less pages are read once more',
  gateLine: 'Page check',
  page: (n: number) => `Page ${n}`,
  gateLoop: 'Looping text',
  gateRetry: 'Read again',
  gatePassed: 'Passed',
  gateNext: 'On to field extraction',

  fieldsTitle: 'Twelve fields from Decree 30/2020',
  fieldsSub: 'Editable in the app, every edit saved as a version',
  fieldsPanel: 'Details',
  fieldLabels: [
    'Parent body',
    'Issuing body',
    'Number',
    'Date issued',
    'Urgency',
    'Confidentiality',
    'Document type',
    'Subject',
    'Recipients',
    'Signer’s title',
    'Signed by',
    'Full text',
  ],
  none: 'None',
  pages3: '3 pages',
  fromPage: (n: number) => `from page ${n}`,

  indexTitle: 'Chunked, embedded and indexed',
  indexSub: 'Vietnamese bge-m3, 1024 dimensions, stored beside a full-text index in SQLite',
  chunk: (n: number) => `Chunk ${n}`,
  pageShort: (n: number | string) => `page ${n}`,
  vecHead: 'bge-m3, 1024 dimensions',
  storeFts: 'FTS5, with an accent-free copy',
  storeVec: 'sqlite-vec',

  retrTitle: 'Finding the right passage',
  retrSub: 'Keyword, meaning and accent-free matches are fused with RRF, then ViRanker re-ranks them',
  listFts: 'Keywords',
  listVec: 'Meaning',
  listFold: 'Accent-free',
  listRrf: 'Rank fusion, RRF',
  listRerank: 'ViRanker re-ranks',

  ansTitle: 'Qwen3 answers and cites its sources',
  ansSub: 'Every number, date and document number is checked against the sources first',
  stepSearch: 'Search documents: 3 matching chunks',
  stepRead: 'Read section III, page 2',
  srcLabel: (n: number, p: number) => `[${n}]  125/KH-SKHCN, page ${p}`,
  checkTitle: 'Answer check',
  checkDone: '3 of 3 figures found in the sources',
  capTurns: '187 of 199 multi-turn answers correct',
  capFigures: 'Answers with a figure missing from the sources: 17, now 0',
  inputPlaceholder: 'Ask about your documents',

  outroLine: 'Runs entirely on your machine and sends no data out.',
  credit: 'Developed by Trần Hậu Mạnh',
  footnote:
    'Measured on the developer’s machine: RTX 5090 Laptop 24 GB, Qwen3-8B, local test sets.',

  memTitle: 'It remembers the conversation',
  memSub: 'Each turn keeps the open document and the documents cited, in a compact conversation state',
  memState: 'Conversation state',
  memOpen: 'Open',
  memCited: 'Cited',
  memTurns: 'Turns',
  memTurn: (n: number) => `Turn ${n}`,
  memResolved: 'means',
  memFromState: 'Answered from the conversation state, no new search',
  memAsked: ['asked where', 'asked the cost', 'asked about the chat itself'],
  memSeconds: '0.8 s',
  memCaption: 'First-turn document still recalled at turn 4 and later: 16 of 16 conversations, was 3 of 16',
};

export const COPY: Record<Lang, Copy> = {vi: VI, en: EN};

// The three-turn conversation of the Memory composition. `ref` marks the
// phrase the document resolver has to work out from the conversation state.
export const MEM_TURNS = [
  {
    q: [{t: 'Kế hoạch 125/KH-SKHCN tổ chức hội nghị ở đâu?'}],
    qGloss: 'Where does plan 125/KH-SKHCN hold the conference?',
    a: 'Tại Hội trường A, Trung tâm Hội nghị tỉnh Mẫu [1]. Kinh phí theo Tờ trình số 48/TTr-SKHCN [2].',
    aGloss: 'In Hall A of the Mẫu Province Conference Centre [1]. Funding follows proposal 48/TTr-SKHCN [2].',
  },
  {
    q: [{t: 'Tờ trình đó', ref: true}, {t: ' đề xuất kinh phí bao nhiêu?'}],
    qGloss: 'How much funding does that proposal request?',
    resolved: '48/TTr-SKHCN',
    a: 'Tờ trình 48/TTr-SKHCN đề xuất 120.000.000 đồng [1].',
    aGloss: 'Proposal 48/TTr-SKHCN requests VND 120,000,000 [1].',
  },
  {
    q: [{t: 'Ở '}, {t: 'đầu đoạn chat', ref: true}, {t: ' tôi hỏi về văn bản nào?'}],
    qGloss: 'Which document did I ask about at the start of this chat?',
    resolved: 'turn1',
    a: 'Ở lượt đầu, bạn hỏi về Kế hoạch 125/KH-SKHCN của Sở Khoa học và Công nghệ tỉnh Mẫu.',
    aGloss: 'In the first turn you asked about plan 125/KH-SKHCN of the Mẫu Province Department of Science and Technology.',
  },
] as Array<{
  q: Array<{t: string; ref?: boolean}>;
  qGloss: string;
  resolved?: string;
  a: string;
  aGloss: string;
}>;

// Chat content (Vietnamese, as the assistant answers) and English glosses.
export const CHAT = {
  question: 'Hội nghị tổ chức vào ngày nào?',
  questionGloss: 'When is the conference held?',
  // Answer as segments so the checked figures and chips can be styled.
  answer: [
    {t: 'Hội nghị tổ chức lúc '},
    {t: '08 giờ 00', check: 0},
    {t: ', thứ Năm, ngày '},
    {t: '26/3/2026', check: 1},
    {t: ', tại Hội trường A, Trung tâm Hội nghị tỉnh Mẫu '},
    {chip: 1},
    {t: '. Dự kiến khoảng '},
    {t: '150', check: 2},
    {t: ' đại biểu tham dự '},
    {chip: 2},
    {t: '.'},
  ] as Array<{t?: string; check?: number; chip?: number}>,
  answerGloss:
    'At 08:00 on Thursday, 26 March 2026, in Hall A of the Mẫu Province Conference Centre [1]. About 150 delegates are expected [2].',
  checks: ['08 giờ 00', '26/3/2026', '150'],
  // Where the answer check finds each figure in the sources.
  evidence: [
    {src: 1, pre: '1. Thời gian: ', hit: '08 giờ 00', post: ', thứ Năm, ngày 26 tháng 3 năm 2026.'},
    {src: 1, pre: '1. Thời gian: 08 giờ 00, thứ Năm, ', hit: 'ngày 26 tháng 3 năm 2026', post: '.'},
    {src: 2, pre: '… Tổng số khoảng ', hit: '150', post: ' đại biểu.'},
  ],
};
