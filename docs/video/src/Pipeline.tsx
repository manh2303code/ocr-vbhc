import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {Lockup, Rail, VideoProvider, W} from './chrome';
import {AnswerScene} from './pipeline/Answer';
import {FieldsScene} from './pipeline/Fields';
import {GateScene} from './pipeline/Gate';
import {IndexScene} from './pipeline/Index';
import {OcrScene} from './pipeline/Ocr';
import {RetrieveScene} from './pipeline/Retrieve';
import {DocStack} from './pipeline/Stack';
import {STAGE_STARTS, T} from './pipeline/timeline';
import {COPY, Lang} from './strings';
import {DARK, fade, LIGHT, prog, SANS} from './theme';

export type VideoProps = {lang: Lang; theme: 'light' | 'dark'};

export const PIPELINE_FRAMES = T.end;

const Centred: React.FC<{y: number; opacity: number; children: React.ReactNode; style?: React.CSSProperties}> = ({
  y,
  opacity,
  children,
  style,
}) => (
  <div
    style={{
      position: 'absolute',
      left: 160,
      width: W - 320,
      top: y,
      textAlign: 'center',
      fontFamily: SANS,
      opacity,
      ...style,
    }}
  >
    {children}
  </div>
);

export const Pipeline: React.FC<VideoProps> = ({lang, theme}) => {
  const frame = useCurrentFrame();
  const pal = theme === 'dark' ? DARK : LIGHT;
  const copy = COPY[lang];

  const toHeader = prog(frame, 50, 74);
  const toCentre = prog(frame, T.outro + 6, T.outro + 30);
  const lockT = toHeader - toCentre;
  const introIn = prog(frame, 0, 20);
  const o = T.outro;

  return (
    <VideoProvider value={{pal, copy, lang}}>
      <AbsoluteFill style={{background: pal.bg, overflow: 'hidden'}}>
        <DocStack frame={frame} />
        <OcrScene frame={frame} />
        <GateScene frame={frame} />
        <FieldsScene frame={frame} />
        <IndexScene frame={frame} />
        <RetrieveScene frame={frame} />
        <AnswerScene frame={frame} />

        <Rail
          frame={frame}
          starts={STAGE_STARTS}
          opacity={fade(frame, T.ocr - 6, 14) * (1 - prog(frame, o, o + 12))}
        />

        <div style={{position: 'absolute', inset: 0, transform: `translateY(${(1 - introIn) * 16}px)`}}>
          <Lockup t={lockT} centreY={frame > o ? 300 : 404} opacity={introIn} />
        </div>

        {/* Opening line */}
        <Centred y={602} opacity={fade(frame, 14, 16, 46, 10)}>
          <div style={{fontSize: 30, lineHeight: '42px', color: pal.text2}}>{copy.tagline}</div>
        </Centred>

        {/* Closing */}
        <Centred y={520} opacity={fade(frame, o + 24, 14)}>
          <div style={{fontSize: 38, lineHeight: '52px', color: pal.text}}>{copy.outroLine}</div>
        </Centred>
        <Centred y={598} opacity={fade(frame, o + 32, 14)}>
          <div style={{fontSize: 25, lineHeight: '36px', color: pal.text2}}>{copy.credit}</div>
        </Centred>
        <Centred y={968} opacity={fade(frame, o + 40, 14)}>
          <div style={{fontSize: 17, lineHeight: '26px', color: pal.muted}}>{copy.footnote}</div>
        </Centred>
      </AbsoluteFill>
    </VideoProvider>
  );
};
