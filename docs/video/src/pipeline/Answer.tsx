import React from 'react';
import {Caption, Panel, SceneTitle, useVideo} from '../chrome';
import {P1} from '../doc';
import {Icon} from '../icons';
import {CHAT} from '../strings';
import {fade, lerp, prog, SANS, SERIF} from '../theme';
import {T} from './timeline';

const A = T.ans;
const CHECK_AT = [A + 86, A + 93, A + 100];
const CHIP_PRESS = A + 112;
const STREAM: [number, number] = [A + 42, A + 80];

/** The passage the answer rests on, lit on page 1 (chip [1] opens it). */
export const AnswerPageOverlay: React.FC<{frame: number}> = ({frame}) => {
  const {pal} = useVideo();
  if (frame < T.retr + 80 || frame > T.outro + 24) return null;
  const on = prog(frame, T.retr + 82, T.retr + 92) * (1 - prog(frame, T.outro, T.outro + 12));
  const press = prog(frame, CHIP_PRESS, CHIP_PRESS + 8);
  const r = P1.s2;
  return (
    <div
      style={{
        position: 'absolute',
        left: r.x - 8,
        top: r.y - 4,
        width: r.w + 16,
        height: r.h + 6,
        background: pal.paperMark,
        mixBlendMode: 'multiply',
        borderRadius: 3,
        opacity: on * (0.55 + 0.45 * press),
        boxShadow: `inset ${4 * press}px 0 0 ${pal.paperMarkEdge}`,
      }}
    />
  );
};

const InlineChip: React.FC<{n: number; pressed: number}> = ({n, pressed}) => {
  const {pal} = useVideo();
  return (
    <span
      style={{
        display: 'inline-block',
        fontFamily: SANS,
        fontSize: 16,
        fontWeight: 600,
        lineHeight: '24px',
        padding: '0 8px',
        borderRadius: 4,
        verticalAlign: 2,
        background: pressed > 0.5 ? pal.olive : pal.oliveSoft,
        color: pressed > 0.5 ? '#FFFFFF' : pal.oliveText,
      }}
    >
      {n}
    </span>
  );
};

export const AnswerScene: React.FC<{frame: number}> = ({frame}) => {
  const {pal, copy, lang} = useVideo();
  if (frame < A - 2 || frame > T.outro + 2) return null;
  const f = frame - A;
  const panelOpacity = fade(f, 2, 10, 126, 9);
  const press = prog(frame, CHIP_PRESS, CHIP_PRESS + 4);

  // Stream the answer character by character, chips appear in place.
  const total = CHAT.answer.reduce((s, seg) => s + (seg.t ? seg.t.length : 0), 0);
  let budget = Math.round(lerp(0, total, prog(frame, STREAM[0], STREAM[1], (t) => t)));
  const streaming = budget < total;

  const checked = CHECK_AT.map((c) => prog(frame, c, c + 6));
  const allChecked = prog(frame, A + 106, A + 112);
  const en = lang === 'en';

  return (
    <>
      <SceneTitle frame={frame} start={A} end={T.outro} title={copy.ansTitle} sub={copy.ansSub} />
      <Panel opacity={panelOpacity}>
        {/* The question */}
        <div style={{position: 'absolute', top: 28, right: 32, textAlign: 'right', opacity: fade(f, 4, 8)}}>
          <div
            style={{
              display: 'inline-block',
              background: pal.oliveSoft,
              borderRadius: 6,
              padding: '10px 18px',
              fontFamily: SANS,
              fontSize: 23,
              lineHeight: '32px',
              color: pal.text,
            }}
          >
            {CHAT.question}
          </div>
          {en ? (
            <div style={{fontFamily: SANS, fontSize: 17, color: pal.muted, marginTop: 6}}>{CHAT.questionGloss}</div>
          ) : null}
        </div>

        {/* The assistant at work, then its answer */}
        <div
          style={{
            position: 'absolute',
            top: en ? 126 : 104,
            left: 32,
            right: 32,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          <div style={{display: 'flex', alignItems: 'center', gap: 10, opacity: fade(f, 8, 8)}}>
            <Icon name="sparkles" size={22} color={pal.oliveText} />
            <span style={{fontFamily: SANS, fontSize: 18, fontWeight: 600, color: pal.text2}}>Qwen3-8B</span>
          </div>
          {[
            {icon: 'search' as const, text: copy.stepSearch, a: 12, done: 20},
            {icon: 'docSearch' as const, text: copy.stepRead, a: 20, done: 30},
          ].map((s) => (
            <div
              key={s.text}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                fontFamily: SANS,
                fontSize: 18,
                color: pal.text2,
                opacity: fade(f, s.a, 6),
              }}
            >
              <Icon name={s.icon} size={20} color={pal.muted} />
              {s.text}
              <span style={{opacity: prog(f, s.done, s.done + 5)}}>
                <Icon name="check" size={20} color={pal.success} />
              </span>
            </div>
          ))}
          <div style={{display: 'flex', gap: 10, marginTop: 6}}>
            {[1, 2].map((n) => (
              <div
                key={n}
                style={{
                  fontFamily: SANS,
                  fontSize: 16,
                  lineHeight: '30px',
                  padding: '0 12px',
                  borderRadius: 4,
                  background: n === 1 && press > 0.5 ? pal.olive : pal.surfaceAlt,
                  color: n === 1 && press > 0.5 ? '#FFFFFF' : pal.text2,
                  boxShadow: `0 0 0 1px ${n === 1 && press > 0.5 ? pal.olive : pal.border}`,
                  opacity: fade(f, 32 + n * 4, 6),
                  whiteSpace: 'pre',
                }}
              >
                {copy.srcLabel(n, n)}
              </div>
            ))}
          </div>

          <div
            style={{
              marginTop: 10,
              fontFamily: SANS,
              fontSize: 25,
              lineHeight: '42px',
              color: pal.text,
              maxWidth: 920,
              minHeight: 84,
            }}
          >
            {CHAT.answer.map((seg, i) => {
              if (seg.chip) {
                if (budget <= 0 && streaming) return null;
                return <InlineChip key={i} n={seg.chip} pressed={seg.chip === 1 ? press : 0} />;
              }
              const text = seg.t ?? '';
              if (budget <= 0) return null;
              const shown = text.slice(0, budget);
              budget -= text.length;
              const c = seg.check !== undefined ? checked[seg.check] : 0;
              return (
                <span
                  key={i}
                  style={{
                    boxShadow: c > 0 ? `inset 0 ${-3 * c}px 0 ${pal.gold}` : undefined,
                    background: c > 0 ? `color-mix(in srgb, ${pal.goldSoft} ${Math.round(c * 70)}%, transparent)` : undefined,
                  }}
                >
                  {shown}
                </span>
              );
            })}
          </div>
          {en ? (
            <div style={{fontFamily: SANS, fontSize: 17, lineHeight: '26px', color: pal.muted, maxWidth: 920, opacity: fade(f, 80, 8)}}>
              {CHAT.answerGloss}
            </div>
          ) : null}

          {/* Answer check: each figure is found in a source passage */}
          <div
            style={{
              marginTop: 12,
              paddingTop: 14,
              borderTop: `1px solid ${pal.border}`,
              fontFamily: SANS,
              opacity: fade(f, 82, 6),
            }}
          >
            <div style={{display: 'flex', alignItems: 'center', fontSize: 18, color: pal.text2, marginBottom: 6}}>
              <span style={{fontWeight: 600}}>{copy.checkTitle}</span>
              <span style={{marginLeft: 'auto', color: pal.success, fontWeight: 500, opacity: allChecked}}>
                {copy.checkDone}
              </span>
            </div>
            {CHAT.evidence.map((e, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  height: 40,
                  gap: 16,
                  opacity: 0.25 + 0.75 * checked[i],
                }}
              >
                <span style={{width: 132, flex: 'none', fontSize: 19, fontWeight: 600, color: pal.text}}>
                  {CHAT.checks[i]}
                </span>
                <InlineChip n={e.src} pressed={0} />
                <span style={{fontFamily: SERIF, fontSize: 19.5, color: pal.text2, whiteSpace: 'nowrap'}}>
                  {e.pre}
                  <span style={{color: pal.text, fontWeight: 700}}>{e.hit}</span>
                  {e.post}
                </span>
                <span style={{marginLeft: 'auto', opacity: checked[i], transform: `scale(${0.5 + 0.5 * checked[i]})`}}>
                  <Icon name="check" size={22} color={pal.success} />
                </span>
              </div>
            ))}
          </div>
        </div>
      </Panel>
      <Caption frame={frame} start={A + 100} end={T.outro} lines={[copy.capTurns, copy.capFigures]} />
    </>
  );
};
