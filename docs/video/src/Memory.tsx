import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {Caption, Lockup, useVideo, VideoProvider} from './chrome';
import {Icon} from './icons';
import {VideoProps} from './Pipeline';
import {COPY, MEM_TURNS} from './strings';
import {DARK, fade, LIGHT, lerp, prog, SANS, SERIF} from './theme';

export const MEMORY_FRAMES = 360;

// Frames of each turn: question in, phrase resolved, answer streams.
const TURNS = [
  {q: 16, ref: 0, a: 34, aEnd: 66},
  {q: 104, ref: 116, a: 134, aEnd: 162},
  {q: 202, ref: 214, a: 232, aEnd: 250},
];
// Conversation state changes.
const STATE = {
  open1: 54,
  cited1: 58,
  cited2: 64,
  turn1: 68,
  open2: 166,
  turn2: 170,
  turn3: 256,
};
const CHAT = {x: 96, y: 232, w: 1060, h: 692};
const SIDE = {x: 1196, y: 232, w: 628, h: 692};

const glowAt = (frame: number, a: number, hold = 30) => prog(frame, a, a + 6) * (1 - prog(frame, a + hold, a + hold + 12));

const Num: React.FC<{children: React.ReactNode; size?: number}> = ({children, size = 21}) => {
  const {pal} = useVideo();
  return (
    <span style={{fontFamily: SERIF, fontWeight: 700, fontSize: size, color: pal.gold, whiteSpace: 'nowrap'}}>{children}</span>
  );
};

const Chip: React.FC<{n: string}> = ({n}) => {
  const {pal} = useVideo();
  return (
    <span
      style={{
        display: 'inline-block',
        fontFamily: SANS,
        fontSize: 15,
        fontWeight: 600,
        lineHeight: '23px',
        padding: '0 7px',
        borderRadius: 4,
        verticalAlign: 2,
        background: pal.oliveSoft,
        color: pal.oliveText,
      }}
    >
      {n}
    </span>
  );
};

/** Answer text streamed character by character, with [n] rendered as chips. */
const Streamed: React.FC<{text: string; shown: number}> = ({text, shown}) => {
  const parts = text.split(/(\[\d\])/).filter(Boolean);
  let budget = shown;
  return (
    <>
      {parts.map((p, i) => {
        if (budget <= 0) return null;
        const chip = /^\[(\d)\]$/.exec(p);
        if (chip) {
          budget -= 1;
          return <Chip key={i} n={chip[1]} />;
        }
        const s = p.slice(0, budget);
        budget -= p.length;
        return <span key={i}>{s}</span>;
      })}
    </>
  );
};

const streamLen = (text: string) => text.replace(/\[\d\]/g, '#').length;

const Turn: React.FC<{i: number; frame: number}> = ({i, frame}) => {
  const {pal, copy, lang} = useVideo();
  const t = TURNS[i];
  const turn = MEM_TURNS[i];
  if (frame < t.q) return null;
  const qIn = prog(frame, t.q, t.q + 10);
  const refOn = t.ref ? prog(frame, t.ref, t.ref + 6) : 0;
  const total = streamLen(turn.a);
  const shown = Math.round(lerp(0, total, prog(frame, t.a, t.aEnd, (x) => x)));
  const en = lang === 'en';
  return (
    <div style={{marginBottom: 20}}>
      {/* Question */}
      <div style={{textAlign: 'right', opacity: qIn, transform: `translateY(${(1 - qIn) * 10}px)`}}>
        <div
          style={{
            display: 'inline-block',
            background: pal.oliveSoft,
            borderRadius: 6,
            padding: '9px 18px',
            fontFamily: SANS,
            fontSize: 23,
            lineHeight: '32px',
            color: pal.text,
            textAlign: 'left',
          }}
        >
          {turn.q.map((s, k) => (
            <span
              key={k}
              style={
                s.ref
                  ? {
                      boxShadow: `inset 0 ${-3 * refOn}px 0 ${pal.gold}`,
                      background: `color-mix(in srgb, ${pal.goldSoft} ${Math.round(refOn * 100)}%, transparent)`,
                    }
                  : undefined
              }
            >
              {s.t}
            </span>
          ))}
        </div>
        {en ? (
          <div style={{fontFamily: SANS, fontSize: 16, lineHeight: '24px', color: pal.muted, marginTop: 4}}>{turn.qGloss}</div>
        ) : null}
        {turn.resolved ? (
          <div
            style={{
              marginTop: 8,
              display: 'flex',
              justifyContent: 'flex-end',
              alignItems: 'baseline',
              gap: 10,
              fontFamily: SANS,
              fontSize: 18,
              color: pal.text2,
              opacity: prog(frame, t.ref + 4, t.ref + 12),
            }}
          >
            <span style={{fontWeight: 600, color: pal.text}}>{turn.q.find((s) => s.ref)?.t.toLowerCase()}</span>
            <span>{copy.memResolved}</span>
            {turn.resolved === 'turn1' ? (
              <>
                <span style={{fontWeight: 600, color: pal.text}}>{copy.memTurn(1)}</span>
                <Num size={19}>125/KH-SKHCN</Num>
              </>
            ) : (
              <Num size={19}>{turn.resolved}</Num>
            )}
          </div>
        ) : null}
      </div>

      {/* Answer */}
      {shown > 0 ? (
        <div style={{marginTop: 14, maxWidth: 900}}>
          <div style={{fontFamily: SANS, fontSize: 23, lineHeight: '36px', color: pal.text}}>
            <Streamed text={turn.a} shown={shown} />
          </div>
          {en ? (
            <div
              style={{
                fontFamily: SANS,
                fontSize: 16,
                lineHeight: '24px',
                color: pal.muted,
                marginTop: 4,
                opacity: prog(frame, t.aEnd, t.aEnd + 8),
              }}
            >
              {turn.aGloss}
            </div>
          ) : null}
          {i === 2 ? (
            <div
              style={{
                marginTop: 10,
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                fontFamily: SANS,
                fontSize: 17,
                color: pal.text2,
                opacity: prog(frame, t.aEnd, t.aEnd + 8),
              }}
            >
              <Icon name="chat" size={20} color={pal.oliveText} />
              {copy.memFromState}
              <span
                style={{
                  padding: '0 9px',
                  lineHeight: '26px',
                  borderRadius: 4,
                  boxShadow: `0 0 0 1px ${pal.border}`,
                  color: pal.text,
                  fontWeight: 500,
                }}
              >
                {copy.memSeconds}
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

const StateRow: React.FC<{label: string; children: React.ReactNode}> = ({label, children}) => {
  const {pal} = useVideo();
  return (
    <div style={{padding: '18px 28px', borderBottom: `1px solid ${pal.border}`}}>
      <div style={{fontFamily: SANS, fontSize: 17, color: pal.muted, marginBottom: 10}}>{label}</div>
      {children}
    </div>
  );
};

const DocTag: React.FC<{n: string; opacity: number; glow?: number}> = ({n, opacity, glow = 0}) => {
  const {pal} = useVideo();
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '4px 12px',
        marginRight: 10,
        borderRadius: 4,
        background: `color-mix(in srgb, ${pal.goldSoft} ${Math.round(glow * 100)}%, ${pal.surface})`,
        boxShadow: `0 0 0 ${1 + glow}px ${glow > 0.05 ? pal.gold : pal.border}`,
        opacity,
      }}
    >
      <Num>{n}</Num>
    </span>
  );
};

export const Memory: React.FC<VideoProps> = ({lang, theme}) => {
  const frame = useCurrentFrame();
  const pal = theme === 'dark' ? DARK : LIGHT;
  const copy = COPY[lang];
  const intro = prog(frame, 0, 14);
  const panels = prog(frame, 6, 18);

  const open2 = prog(frame, STATE.open2, STATE.open2 + 10);
  const citedGlow = glowAt(frame, TURNS[1].ref, 34);
  const turn1Glow = glowAt(frame, TURNS[2].ref, 34);
  const rows = [
    {at: STATE.turn1, doc: '125/KH-SKHCN', glow: turn1Glow},
    {at: STATE.turn2, doc: '48/TTr-SKHCN', glow: 0},
    {at: STATE.turn3, doc: null, glow: 0},
  ];

  return (
    <VideoProvider value={{pal, copy, lang}}>
      <AbsoluteFill style={{background: pal.bg, overflow: 'hidden'}}>
        <Lockup t={1} opacity={intro} />

        <div style={{position: 'absolute', left: 96, top: 100, opacity: intro}}>
          <div style={{fontFamily: SANS, fontWeight: 600, fontSize: 42, lineHeight: '54px', letterSpacing: '-0.015em', color: pal.text}}>
            {copy.memTitle}
          </div>
          <div style={{fontFamily: SANS, fontSize: 22, lineHeight: '32px', color: pal.text2, marginTop: 8}}>{copy.memSub}</div>
        </div>

        {/* Chat */}
        <div
          style={{
            position: 'absolute',
            left: CHAT.x,
            top: CHAT.y,
            width: CHAT.w,
            height: CHAT.h,
            background: pal.surface,
            boxShadow: `0 0 0 1px ${pal.border}`,
            borderRadius: 6,
            overflow: 'hidden',
            opacity: panels,
          }}
        >
          <div
            style={{
              position: 'absolute',
              inset: 0,
              padding: '30px 32px 8px',
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'flex-end',
              WebkitMaskImage: 'linear-gradient(to bottom, transparent 0px, black 30px)',
              maskImage: 'linear-gradient(to bottom, transparent 0px, black 30px)',
            }}
          >
            <div style={{minHeight: '100%', flex: 'none'}}>
              {[0, 1, 2].map((i) => (
                <Turn key={i} i={i} frame={frame} />
              ))}
            </div>
          </div>
        </div>

        {/* Conversation state */}
        <div
          style={{
            position: 'absolute',
            left: SIDE.x,
            top: SIDE.y,
            width: SIDE.w,
            height: SIDE.h,
            background: pal.surfaceAlt,
            boxShadow: `0 0 0 1px ${pal.border}`,
            borderRadius: 6,
            overflow: 'hidden',
            opacity: panels,
          }}
        >
          <div
            style={{
              height: 56,
              display: 'flex',
              alignItems: 'center',
              padding: '0 28px',
              borderBottom: `1px solid ${pal.border}`,
              fontFamily: SANS,
              fontSize: 19,
              fontWeight: 600,
              color: pal.text,
            }}
          >
            {copy.memState}
          </div>
          <StateRow label={copy.memOpen}>
            <div style={{height: 36, position: 'relative'}}>
              <span style={{position: 'absolute', left: 0, top: 0, opacity: fade(frame, STATE.open1, 8) * (1 - open2)}}>
                <DocTag n="125/KH-SKHCN" opacity={1} />
              </span>
              <span style={{position: 'absolute', left: 0, top: 0, opacity: open2}}>
                <DocTag n="48/TTr-SKHCN" opacity={1} />
              </span>
            </div>
          </StateRow>
          <StateRow label={copy.memCited}>
            <div style={{height: 36}}>
              <DocTag n="125/KH-SKHCN" opacity={fade(frame, STATE.cited1, 8)} />
              <DocTag n="48/TTr-SKHCN" opacity={fade(frame, STATE.cited2, 8)} glow={citedGlow} />
            </div>
          </StateRow>
          <div style={{padding: '18px 28px'}}>
            <div style={{fontFamily: SANS, fontSize: 17, color: pal.muted, marginBottom: 6}}>{copy.memTurns}</div>
            {rows.map((r, k) => (
              <div
                key={k}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 16,
                  height: 50,
                  margin: '0 -12px',
                  padding: '0 12px',
                  borderRadius: 4,
                  background: `color-mix(in srgb, ${pal.goldSoft} ${Math.round(r.glow * 100)}%, transparent)`,
                  boxShadow: r.glow > 0.05 ? `inset 3px 0 0 ${pal.gold}` : 'none',
                  opacity: fade(frame, r.at, 8),
                }}
              >
                <span style={{width: 74, fontFamily: SANS, fontSize: 18, fontWeight: 600, color: pal.text}}>{copy.memTurn(k + 1)}</span>
                {r.doc ? <Num size={20}>{r.doc}</Num> : null}
                <span style={{fontFamily: SANS, fontSize: 17, color: pal.text2}}>{copy.memAsked[k]}</span>
              </div>
            ))}
          </div>
        </div>

        <Caption frame={frame} start={266} end={MEMORY_FRAMES + 20} lines={[copy.memCaption]} x={CHAT.x} y={952} />
      </AbsoluteFill>
    </VideoProvider>
  );
};
