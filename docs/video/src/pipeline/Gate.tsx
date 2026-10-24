import React from 'react';
import {SceneTitle, useVideo} from '../chrome';
import {Icon} from '../icons';
import {alpha, EASE_IN_OUT, fade, lerp, prog, SANS, SERIF} from '../theme';
import {T} from './timeline';

// OCR output of each page, as it would come back from the model.
const OUT_TEXT = {
  1: 'UBND TỈNH MẪU SỞ KHOA HỌC VÀ CÔNG NGHỆ Số: 125/KH-SKHCN KẾ HOẠCH Tổ chức hội nghị tổng kết công tác chuyển đổi số năm 2025. Thực hiện Chương trình công tác năm 2026 của Ủy ban nhân dân tỉnh Mẫu, Sở Khoa học và Công nghệ ban hành Kế hoạch… I. MỤC ĐÍCH, YÊU CẦU 1. Đánh giá kết quả chuyển đổi số năm 2025 của các cơ quan, đơn vị…',
  2: 'khoảng 150 đại biểu. IV. NỘI DUNG 1. Báo cáo tổng kết công tác chuyển đổi số năm 2025 và phương hướng năm 2026. 2. Tham luận của 05 cơ quan, đơn vị có kết quả nổi bật. 3. Khen thưởng tập thể, cá nhân có thành tích xuất sắc. V. KINH PHÍ Kinh phí tổ chức hội nghị từ nguồn ngân sách…',
  3: '2. Phòng Chuyển đổi số tổng hợp báo cáo, xây dựng chương trình hội nghị, hoàn thành trước ngày 20/3/2026. Trên đây là Kế hoạch tổ chức hội nghị… Nơi nhận: UBND tỉnh (để báo cáo); Các sở, ban, ngành; Lưu: VT, CĐS. KT. GIÁM ĐỐC PHÓ GIÁM ĐỐC Lê Minh Khoa',
  loop: Array.from({length: 9}, () => 'khoảng 150 đại biểu.').join(' '),
};

const SLIP_W = 160;
const SLIP_H = 220;
const ROW_Y = 352;
const QUEUE_X = 836;
const GATE_X = 1150;
const SLOTS = [1200, 1376, 1552];
const RETRY = {x: 972, y: 652};
const STOP_X = GATE_X - SLIP_W - 18;

type Keyframe = [number, number, number]; // frame, x, y

const along = (f: number, keys: Keyframe[]) => {
  if (f <= keys[0][0]) return {x: keys[0][1], y: keys[0][2]};
  for (let i = 1; i < keys.length; i++) {
    const [f1, x1, y1] = keys[i];
    const [f0, x0, y0] = keys[i - 1];
    if (f <= f1) {
      const t = prog(f, f0, f1, EASE_IN_OUT);
      return {x: lerp(x0, x1, t), y: lerp(y0, y1, t)};
    }
  }
  const last = keys[keys.length - 1];
  return {x: last[1], y: last[2]};
};

const Slip: React.FC<{
  x: number;
  y: number;
  opacity: number;
  label: string;
  text: string;
  bad?: boolean;
  passed: number;
  labelOpacity: number;
  flag?: React.ReactNode;
}> = ({x, y, opacity, label, text, bad, passed, labelOpacity, flag}) => {
  const {pal} = useVideo();
  return (
    <div style={{position: 'absolute', left: x, top: y, width: SLIP_W, opacity}}>
      <div
        style={{
          position: 'absolute',
          top: -32,
          left: 0,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontFamily: SANS,
          fontSize: 18,
          fontWeight: 500,
          color: pal.text2,
          whiteSpace: 'nowrap',
          opacity: labelOpacity,
        }}
      >
        {label}
        <span style={{opacity: passed, transform: `scale(${0.6 + 0.4 * passed})`}}>
          <Icon name="statusOk" size={20} color={pal.success} />
        </span>
      </div>
      <div
        style={{
          width: SLIP_W,
          height: SLIP_H,
          boxSizing: 'border-box',
          background: pal.paper,
          boxShadow: `0 0 0 ${bad ? 2 : 1}px ${bad ? pal.red : pal.paperEdge}`,
          padding: '12px 12px',
          overflow: 'hidden',
          fontFamily: SERIF,
          fontSize: 11.5,
          lineHeight: '15px',
          color: bad ? pal.red : '#18211B',
          textAlign: bad ? 'left' : 'justify',
        }}
      >
        {text}
      </div>
      {flag}
    </div>
  );
};

export const GateScene: React.FC<{frame: number}> = ({frame}) => {
  const {pal, copy} = useVideo();
  if (frame < T.gate - 2 || frame > T.fields + 2) return null;
  const f = frame - T.gate;
  const sceneOut = 1 - prog(f, 93, 100);

  // Page 1 passes; page 2 loops, is read again in the lower lane and passes
  // through the gate there; page 3 passes meanwhile.
  const p1 = along(f, [
    [10, QUEUE_X, ROW_Y],
    [28, SLOTS[0], ROW_Y],
  ]);
  const p2 = along(f, [
    [18, QUEUE_X + 10, ROW_Y + 10],
    [32, STOP_X, ROW_Y],
    [42, STOP_X, ROW_Y],
    [52, RETRY.x, RETRY.y],
    [62, RETRY.x, RETRY.y],
    [73, SLOTS[1], RETRY.y],
    [84, SLOTS[1], ROW_Y],
  ]);
  const p3 = along(f, [
    [46, QUEUE_X + 20, ROW_Y + 20],
    [64, SLOTS[2], ROW_Y],
  ]);

  const rejected = f >= 32 && f < 58;
  const rejectFlash = prog(f, 32, 36) * (1 - prog(f, 42, 50));
  const retrySpin = prog(f, 50, 64);
  const fixed = f >= 58;
  const gateColor = rejectFlash > 0.05 ? pal.red : pal.olive;

  return (
    <>
      <SceneTitle frame={frame} start={T.gate} end={T.fields} title={copy.gateTitle} sub={copy.gateSub} />
      <div style={{position: 'absolute', inset: 0, opacity: sceneOut}}>
        {/* The gate */}
        <div style={{position: 'absolute', left: GATE_X, top: 300, opacity: fade(f, 2, 10)}}>
          <div
            style={{
              position: 'absolute',
              left: -1 - rejectFlash * 1.5,
              top: 0,
              width: 2 + rejectFlash * 3,
              height: 580,
              background: gateColor,
            }}
          />
          <div
            style={{
              position: 'absolute',
              left: 0,
              top: -36,
              transform: 'translateX(-50%)',
              whiteSpace: 'nowrap',
              fontFamily: SANS,
              fontSize: 18,
              fontWeight: 500,
              color: pal.oliveText,
            }}
          >
            {copy.gateLine}
          </div>
        </div>

        {/* Retry lane */}
        <div
          style={{
            position: 'absolute',
            left: RETRY.x - 150,
            top: RETRY.y + 96,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            fontFamily: SANS,
            fontSize: 18,
            color: pal.text2,
            opacity: fade(f, 46, 8, 70, 8),
          }}
        >
          <span style={{transform: `rotate(${retrySpin * 360}deg)`}}>
            <Icon name="rotate" size={24} color={pal.oliveText} />
          </span>
          {copy.gateRetry}
        </div>

        <Slip
          x={p3.x}
          y={p3.y}
          opacity={fade(f, 14, 8)}
          label={copy.page(3)}
          labelOpacity={fade(f, 46, 6)}
          text={OUT_TEXT[3]}
          passed={prog(f, 60, 66)}
        />
        <Slip
          x={p2.x}
          y={p2.y}
          opacity={fade(f, 8, 8)}
          label={copy.page(2)}
          labelOpacity={fade(f, 18, 6)}
          text={fixed ? OUT_TEXT[2] : OUT_TEXT.loop}
          bad={rejected && !fixed}
          passed={prog(f, 80, 86)}
          flag={
            <div
              style={{
                position: 'absolute',
                top: SLIP_H + 10,
                left: 0,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontFamily: SANS,
                fontSize: 17,
                fontWeight: 500,
                color: pal.red,
                whiteSpace: 'nowrap',
                opacity: fade(f, 34, 6, 50, 6),
              }}
            >
              <Icon name="alert" size={20} color={pal.red} />
              {copy.gateLoop}
            </div>
          }
        />
        <Slip
          x={p1.x}
          y={p1.y}
          opacity={fade(f, 4, 8)}
          label={copy.page(1)}
          labelOpacity={fade(f, 10, 6)}
          text={OUT_TEXT[1]}
          passed={prog(f, 22, 28)}
        />

        {/* Accepted pages carry on to field extraction */}
        <div
          style={{
            position: 'absolute',
            left: SLOTS[0],
            top: ROW_Y + SLIP_H + 22,
            width: SLOTS[2] + SLIP_W - SLOTS[0],
            borderTop: `1px solid ${alpha(pal.success, 0.5)}`,
            paddingTop: 10,
            fontFamily: SANS,
            fontSize: 17,
            color: pal.success,
            opacity: fade(f, 86, 6),
          }}
        >
          {copy.gateNext}
        </div>
      </div>
    </>
  );
};
