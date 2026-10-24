import React, {createContext, useContext} from 'react';
import {Img, staticFile} from 'remotion';
import {COPY, Copy, Lang} from './strings';
import {fade, LIGHT, Palette, prog, SANS, lerp} from './theme';

export const W = 1920;
export const H = 1080;

// Layout grid (px, 1920 x 1080).
export const GRID = {
  margin: 96,
  pageX: 112,
  pageY: 104,
  workX: 812,
  workW: 1012,
  titleY: 100,
  panelY: 232,
  panelH: 640,
  captionY: 900,
  railY: 1000,
};

type Ctx = {pal: Palette; copy: Copy; lang: Lang};
const VideoCtx = createContext<Ctx>({pal: LIGHT, copy: COPY.vi, lang: 'vi'});
export const VideoProvider = VideoCtx.Provider;
export const useVideo = () => useContext(VideoCtx);

/**
 * The app mark and name. `t` = 0 is the large centred lockup, 1 the small
 * header lockup in the top left corner.
 */
export const Lockup: React.FC<{t: number; centreY?: number; opacity?: number}> = ({
  t,
  centreY = 410,
  opacity = 1,
}) => {
  const {pal, copy} = useVideo();
  const big = {markH: 150, font: 104, gap: 34, width: 752};
  const scale = lerp(1, 40 / big.markH, t);
  const x = lerp((W - big.width) / 2, GRID.margin, t);
  const y = lerp(centreY, 30, t);
  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        top: y,
        transform: `scale(${scale})`,
        transformOrigin: '0 0',
        display: 'flex',
        alignItems: 'flex-end',
        gap: big.gap,
        opacity,
      }}
    >
      <Img src={staticFile('app_mark.png')} style={{height: big.markH, width: 'auto', display: 'block'}} />
      <div
        style={{
          fontFamily: SANS,
          fontWeight: 600,
          fontSize: big.font,
          lineHeight: 1,
          letterSpacing: '-0.02em',
          color: pal.text,
          whiteSpace: 'nowrap',
          marginBottom: -8,
        }}
      >
        {copy.appName}
      </div>
    </div>
  );
};

/** The sequence of stages along the bottom; a small gold mark shows where we are. */
export const Rail: React.FC<{frame: number; starts: number[]; opacity: number}> = ({
  frame,
  starts,
  opacity,
}) => {
  const {pal, copy} = useVideo();
  const colW = (W - 2 * GRID.margin) / copy.stages.length;
  // Fractional position of the mark: eases from stage i-1 to i at each start.
  let pos = 0;
  starts.forEach((s, i) => {
    if (i === 0) return;
    pos += prog(frame, s - 2, s + 14);
  });
  const active = Math.round(pos);
  return (
    <div style={{position: 'absolute', left: 0, top: GRID.railY, width: W, opacity}}>
      <div
        style={{
          position: 'absolute',
          left: GRID.margin,
          width: W - 2 * GRID.margin,
          top: 0,
          height: 1,
          background: pal.border,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: GRID.margin + pos * colW,
          top: -1,
          width: 44,
          height: 3,
          background: pal.gold,
        }}
      />
      {copy.stages.map((s, i) => (
        <div
          key={s}
          style={{
            position: 'absolute',
            left: GRID.margin + i * colW,
            top: 18,
            fontFamily: SANS,
            fontSize: 20,
            fontWeight: i === active ? 500 : 400,
            color: i === active ? pal.text : i < active ? pal.text2 : pal.muted,
            opacity: i <= active ? 1 : 0.8,
          }}
        >
          {s}
        </div>
      ))}
    </div>
  );
};

/** Scene title and one supporting line, at the top of the work area. */
export const SceneTitle: React.FC<{
  frame: number;
  start: number;
  end: number;
  title: string;
  sub: string;
}> = ({frame, start, end, title, sub}) => {
  const {pal} = useVideo();
  const inT = prog(frame, start, start + 14);
  const outT = prog(frame, end - 10, end);
  const opacity = inT * (1 - outT);
  if (opacity <= 0) return null;
  const dy = (1 - inT) * 14 - outT * 10;
  return (
    <div
      style={{
        position: 'absolute',
        left: GRID.workX,
        top: GRID.titleY + dy,
        width: GRID.workW,
        opacity,
      }}
    >
      <div
        style={{
          fontFamily: SANS,
          fontWeight: 600,
          fontSize: 42,
          lineHeight: '54px',
          letterSpacing: '-0.015em',
          color: pal.text,
        }}
      >
        {title}
      </div>
      <div
        style={{
          fontFamily: SANS,
          fontSize: 22,
          lineHeight: '32px',
          color: pal.text2,
          marginTop: 8,
        }}
      >
        {sub}
      </div>
    </div>
  );
};

/** A quiet caption with a measured number. */
export const Caption: React.FC<{
  frame: number;
  start: number;
  end: number;
  lines: string[];
  x?: number;
  y?: number;
}> = ({frame, start, end, lines, x = GRID.workX, y = GRID.captionY}) => {
  const {pal} = useVideo();
  const opacity = fade(frame, start, 12, end - 8, 8);
  if (opacity <= 0) return null;
  return (
    <div style={{position: 'absolute', left: x, top: y, width: W - x - GRID.margin, opacity}}>
      {lines.map((l) => (
        <div
          key={l}
          style={{
            fontFamily: SANS,
            fontSize: 20,
            lineHeight: '30px',
            color: pal.text2,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <span style={{width: 8, height: 8, background: pal.gold, flex: 'none'}} />
          {l}
        </div>
      ))}
    </div>
  );
};

/** A white panel like the app's editor and chat dock. */
export const Panel: React.FC<{
  opacity: number;
  label?: string;
  children: React.ReactNode;
  style?: React.CSSProperties;
}> = ({opacity, label, children, style}) => {
  const {pal} = useVideo();
  return (
    <div
      style={{
        position: 'absolute',
        left: GRID.workX,
        top: GRID.panelY,
        width: GRID.workW,
        height: GRID.panelH,
        background: pal.surface,
        boxShadow: `0 0 0 1px ${pal.border}`,
        borderRadius: 6,
        overflow: 'hidden',
        opacity,
        ...style,
      }}
    >
      {label ? (
        <div
          style={{
            height: 52,
            display: 'flex',
            alignItems: 'center',
            padding: '0 28px',
            borderBottom: `1px solid ${pal.border}`,
            fontFamily: SANS,
            fontSize: 19,
            fontWeight: 500,
            color: pal.text2,
          }}
        >
          {label}
        </div>
      ) : null}
      {children}
    </div>
  );
};
