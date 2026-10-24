import React from 'react';
import {SERIF} from './theme';

// A fictional administrative document laid out after Nghị định 30/2020
// (Times New Roman, agency top left, national motto top right, number and
// date, type and subject, body, recipients and signature). "Tỉnh Mẫu" means
// "sample province"; the agency, numbers and people are invented.

export const PAGE_W = 614;
export const PAGE_H = 868;

export type Rect = {x: number; y: number; w: number; h: number};

const INK = '#18211B';
const BODY: React.CSSProperties = {
  fontFamily: SERIF,
  fontSize: 16.5,
  lineHeight: '25px',
  color: INK,
  textAlign: 'justify',
};

// Page 1 geometry (page coordinates). Used both to lay out the page and to
// draw the overlays that point at it.
export const P1 = {
  agency: {x: 36, y: 48, w: 248, h: 58},
  agencyTop: {x: 72, y: 50, w: 176, h: 22},
  agencyBottom: {x: 42, y: 72, w: 236, h: 22},
  motto: {x: 300, y: 48, w: 302, h: 58},
  number: {x: 80, y: 112, w: 160, h: 24},
  urgency: {x: 120, y: 148, w: 80, h: 30},
  date: {x: 330, y: 112, w: 242, h: 24},
  title: {x: 60, y: 192, w: 494, h: 64},
  titleType: {x: 252, y: 192, w: 110, h: 26},
  titleSubject: {x: 80, y: 218, w: 454, h: 26},
  intro: {x: 60, y: 268, w: 518, h: 78},
  s1: {x: 60, y: 352, w: 518, h: 128},
  s2: {x: 60, y: 486, w: 518, h: 78},
  s3: {x: 60, y: 570, w: 518, h: 230},
} satisfies Record<string, Rect>;

export const P3 = {
  pageNo: {x: 287, y: 26, w: 40, h: 20},
  body: {x: 60, y: 58, w: 518, h: 178},
  recipients: {x: 36, y: 262, w: 252, h: 118},
  sigTitle: {x: 330, y: 258, w: 240, h: 52},
  sigName: {x: 350, y: 420, w: 200, h: 28},
} satisfies Record<string, Rect>;

const Abs: React.FC<{r: Rect; style?: React.CSSProperties; children: React.ReactNode}> = ({
  r,
  style,
  children,
}) => (
  <div style={{position: 'absolute', left: r.x, top: r.y, width: r.w, ...style}}>{children}</div>
);

const P: React.FC<{children: React.ReactNode; bold?: boolean; indent?: boolean}> = ({
  children,
  bold,
  indent = true,
}) => (
  <div style={{textIndent: indent ? 34 : 0, fontWeight: bold ? 700 : 400}}>{children}</div>
);

export const Page1: React.FC = () => (
  <>
    <Abs r={P1.agency} style={{textAlign: 'center', fontFamily: SERIF, color: INK}}>
      <div style={{fontSize: 14.5, lineHeight: '22px'}}>UBND TỈNH MẪU</div>
      <div style={{fontSize: 14.5, lineHeight: '22px', fontWeight: 700}}>SỞ KHOA HỌC VÀ CÔNG NGHỆ</div>
      <div style={{width: 84, height: 1, background: INK, margin: '3px auto 0'}} />
    </Abs>
    <Abs r={P1.motto} style={{textAlign: 'center', fontFamily: SERIF, color: INK}}>
      <div style={{fontSize: 13.5, lineHeight: '22px', fontWeight: 700, whiteSpace: 'nowrap'}}>
        CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
      </div>
      <div style={{fontSize: 15, lineHeight: '22px', fontWeight: 700}}>
        <span style={{borderBottom: `1px solid ${INK}`, paddingBottom: 2}}>Độc lập - Tự do - Hạnh phúc</span>
      </div>
    </Abs>
    <Abs r={P1.number} style={{textAlign: 'center', fontFamily: SERIF, fontSize: 15.5, lineHeight: '24px', color: INK, whiteSpace: 'nowrap'}}>
      Số: 125/KH-SKHCN
    </Abs>
    <Abs
      r={P1.urgency}
      style={{
        height: P1.urgency.h,
        boxSizing: 'border-box',
        border: '2px solid #B3263E',
        color: '#B3263E',
        fontFamily: SERIF,
        fontWeight: 700,
        fontSize: 16,
        lineHeight: '26px',
        textAlign: 'center',
        letterSpacing: '0.04em',
      }}
    >
      KHẨN
    </Abs>
    <Abs
      r={P1.date}
      style={{textAlign: 'center', fontFamily: SERIF, fontSize: 15.5, lineHeight: '24px', fontStyle: 'italic', color: INK, whiteSpace: 'nowrap'}}
    >
      Mẫu, ngày 12 tháng 3 năm 2026
    </Abs>
    <Abs r={P1.title} style={{textAlign: 'center', fontFamily: SERIF, color: INK, fontWeight: 700}}>
      <div style={{fontSize: 17, lineHeight: '26px'}}>KẾ HOẠCH</div>
      <div style={{fontSize: 16, lineHeight: '26px'}}>
        Tổ chức hội nghị tổng kết công tác chuyển đổi số năm 2025
      </div>
      <div style={{width: 120, height: 1, background: INK, margin: '5px auto 0'}} />
    </Abs>
    <Abs r={P1.intro} style={BODY}>
      <P>
        Thực hiện Chương trình công tác năm 2026 của Ủy ban nhân dân tỉnh Mẫu, Sở Khoa học và
        Công nghệ ban hành Kế hoạch tổ chức hội nghị tổng kết công tác chuyển đổi số năm 2025
        như sau:
      </P>
    </Abs>
    <Abs r={P1.s1} style={BODY}>
      <P bold>I. MỤC ĐÍCH, YÊU CẦU</P>
      <P>
        1. Đánh giá kết quả chuyển đổi số năm 2025 của các cơ quan, đơn vị; biểu dương tập thể,
        cá nhân tiêu biểu.
      </P>
      <P>2. Thống nhất nhiệm vụ trọng tâm năm 2026.</P>
      <P>3. Hội nghị bảo đảm thiết thực, tiết kiệm, đúng thành phần.</P>
    </Abs>
    <Abs r={P1.s2} style={BODY}>
      <P bold>II. THỜI GIAN, ĐỊA ĐIỂM</P>
      <P>1. Thời gian: 08 giờ 00, thứ Năm, ngày 26 tháng 3 năm 2026.</P>
      <P>2. Địa điểm: Hội trường A, Trung tâm Hội nghị tỉnh Mẫu.</P>
    </Abs>
    <Abs r={P1.s3} style={BODY}>
      <P bold>III. THÀNH PHẦN</P>
      <P>
        1. Đại biểu tỉnh: lãnh đạo Ủy ban nhân dân tỉnh; lãnh đạo Văn phòng Ủy ban nhân dân
        tỉnh.
      </P>
      <P>
        2. Lãnh đạo và công chức phụ trách chuyển đổi số của các sở, ban, ngành thuộc tỉnh.
      </P>
      <P>
        3. Lãnh đạo Ủy ban nhân dân các huyện, thị xã, thành phố; đại diện các doanh nghiệp
        công nghệ thông tin, viễn thông trên địa bàn tỉnh; phóng viên Báo và Phát thanh, Truyền
        hình tỉnh Mẫu đến dự và đưa tin về hội nghị. Tổng số
      </P>
    </Abs>
  </>
);

export const Page2: React.FC = () => (
  <>
    <Abs r={{x: 287, y: 26, w: 40, h: 20}} style={{textAlign: 'center', fontFamily: SERIF, fontSize: 14, color: INK}}>
      2
    </Abs>
    <Abs r={{x: 60, y: 58, w: 518, h: 700}} style={BODY}>
      <P indent={false}>khoảng 150 đại biểu.</P>
      <P bold>IV. NỘI DUNG</P>
      <P>1. Báo cáo tổng kết công tác chuyển đổi số năm 2025 và phương hướng năm 2026.</P>
      <P>2. Tham luận của 05 cơ quan, đơn vị có kết quả nổi bật.</P>
      <P>3. Khen thưởng tập thể, cá nhân có thành tích xuất sắc.</P>
      <P bold>V. KINH PHÍ</P>
      <P>
        Kinh phí tổ chức hội nghị từ nguồn ngân sách chi thường xuyên năm 2026 của Sở Khoa học và
        Công nghệ, theo Tờ trình số 48/TTr-SKHCN ngày 05/3/2026 của Sở.
      </P>
      <P bold>VI. TỔ CHỨC THỰC HIỆN</P>
      <P>
        1. Văn phòng Sở chủ trì chuẩn bị hội trường, tài liệu và các điều kiện phục vụ hội nghị;
        gửi giấy mời đến các đại biểu trước ngày 20/3/2026.
      </P>
    </Abs>
  </>
);

export const Page3: React.FC = () => (
  <>
    <Abs r={P3.pageNo} style={{textAlign: 'center', fontFamily: SERIF, fontSize: 14, color: INK}}>
      3
    </Abs>
    <Abs r={P3.body} style={BODY}>
      <P>
        2. Phòng Chuyển đổi số tổng hợp báo cáo, xây dựng chương trình hội nghị, hoàn thành
        trước ngày 20/3/2026.
      </P>
      <P>
        Trên đây là Kế hoạch tổ chức hội nghị tổng kết công tác chuyển đổi số năm 2025 của Sở
        Khoa học và Công nghệ./.
      </P>
    </Abs>
    <Abs r={P3.recipients} style={{fontFamily: SERIF, color: INK, fontSize: 13, lineHeight: '19px'}}>
      <div style={{fontWeight: 700, fontStyle: 'italic', fontSize: 14}}>Nơi nhận:</div>
      <div>- UBND tỉnh (để báo cáo);</div>
      <div>- Các sở, ban, ngành;</div>
      <div>- UBND các huyện, thị xã, thành phố;</div>
      <div>- Lưu: VT, CĐS.</div>
    </Abs>
    <Abs r={P3.sigTitle} style={{fontFamily: SERIF, color: INK, fontWeight: 700, fontSize: 15.5, lineHeight: '24px', textAlign: 'center'}}>
      <div>KT. GIÁM ĐỐC</div>
      <div>PHÓ GIÁM ĐỐC</div>
    </Abs>
    {/* An invented signature stroke. */}
    <svg
      style={{position: 'absolute', left: 372, top: 326}}
      width={160}
      height={70}
      viewBox="0 0 160 70"
      fill="none"
    >
      <path
        d="M8 46c14-22 26-34 30-30s-10 34-4 36 18-30 26-30-2 24 6 24 14-18 22-20 4 16 12 16 20-12 32-22"
        stroke="#2C3E7A"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M30 58c30-6 70-8 104-10" stroke="#2C3E7A" strokeWidth={1.6} strokeLinecap="round" />
    </svg>
    <Abs r={P3.sigName} style={{fontFamily: SERIF, color: INK, fontWeight: 700, fontSize: 16, lineHeight: '26px', textAlign: 'center'}}>
      Lê Minh Khoa
    </Abs>
  </>
);

/** A sheet of paper with one of the three pages printed on it. */
export const Sheet: React.FC<{
  page: 1 | 2 | 3;
  paper: string;
  edge: string;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}> = ({page, paper, edge, children, style}) => (
  <div
    style={{
      position: 'absolute',
      width: PAGE_W,
      height: PAGE_H,
      background: paper,
      boxShadow: `0 0 0 1px ${edge}`,
      overflow: 'hidden',
      ...style,
    }}
  >
    {page === 1 ? <Page1 /> : page === 2 ? <Page2 /> : <Page3 />}
    {children}
  </div>
);
