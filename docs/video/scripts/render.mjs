// Renders the README videos: MP4 (H.264), GIF (palette optimised with
// ffmpeg) and a PNG poster, for each composition and language.
//
//   node scripts/render.mjs                    everything
//   node scripts/render.mjs --only=mp4         one kind: mp4 | gif | poster | stills
//   node scripts/render.mjs --lang=en --comp=Memory
//   node scripts/render.mjs --only=stills --frames=120,400   frames for review
//
// GIFs are made from the MP4s, so render the MP4s first. ffmpeg must be on
// PATH for the GIF and poster steps.

import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = path.resolve(ROOT, '..', 'media', 'video');
const STILLS = path.join(ROOT, 'out', 'stills');

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, v] = a.replace(/^--/, '').split('=');
    return [k, v ?? 'true'];
  }),
);
const only = args.only ? args.only.split(',') : ['mp4', 'gif', 'poster'];
const langs = args.lang ? args.lang.split(',') : ['vi', 'en'];
const theme = args.theme ?? 'light';

// Per composition: file stem, MP4 quality, GIF window and poster frame.
const JOBS = {
  Pipeline: {stem: 'pipeline', crf: 18, gif: {start: 0, duration: null, fps: 12}, poster: 808},
  Memory: {stem: 'memory', crf: 18, gif: {start: 0, duration: null, fps: 12}, poster: null},
};
const comps = args.comp ? args.comp.split(',') : Object.keys(JOBS);

const BROWSERS = [
  process.env.REMOTION_BROWSER,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
].filter(Boolean);
const browserExecutable = BROWSERS.find((p) => fs.existsSync(p)) ?? null;
const chromeMode = browserExecutable ? 'chrome-for-testing' : 'headless-shell';

const mb = (file) => (fs.statSync(file).size / 1024 / 1024).toFixed(2) + ' MB';
const ffmpeg = (argv) => execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...argv], {stdio: 'inherit'});

fs.mkdirSync(OUT, {recursive: true});
console.log(`Browser: ${browserExecutable ?? 'Remotion Chrome Headless Shell'}`);
console.log('Bundling...');
const serveUrl = await bundle({
  entryPoint: path.join(ROOT, 'src', 'index.ts'),
  rootDir: ROOT,
  publicDir: path.join(ROOT, 'public'),
});

for (const compId of comps) {
  const job = JOBS[compId];
  const suffix = theme === 'dark' ? '_dark' : '';
  for (const lang of langs) {
    const inputProps = {lang, theme};
    const composition = await selectComposition({serveUrl, id: compId, inputProps, browserExecutable, chromeMode});
    const base = path.join(OUT, `${job.stem}_${lang}${suffix}`);

    if (only.includes('stills')) {
      fs.mkdirSync(STILLS, {recursive: true});
      const frames = (args.frames ?? '100').split(',').map(Number);
      for (const frame of frames) {
        const output = path.join(STILLS, `${job.stem}_${lang}${suffix}_${String(frame).padStart(4, '0')}.png`);
        await renderStill({composition, serveUrl, output, frame, inputProps, browserExecutable, chromeMode, scale: Number(args.scale ?? 1)});
        console.log(`still ${output}`);
      }
    }

    if (only.includes('mp4')) {
      const output = `${base}.mp4`;
      await renderMedia({
        composition,
        serveUrl,
        codec: 'h264',
        crf: Number(args.crf ?? job.crf),
        x264Preset: 'slow',
        pixelFormat: 'yuv420p',
        imageFormat: 'png',
        outputLocation: output,
        inputProps,
        browserExecutable,
        chromeMode,
        onProgress: ({progress}) => process.stdout.write(`\r${compId} ${lang} mp4 ${(progress * 100).toFixed(0)}%   `),
      });
      console.log(`\nmp4 ${output} ${mb(output)}`);
    }

    if (only.includes('gif')) {
      const input = `${base}.mp4`;
      const output = `${base}.gif`;
      const {start, duration, fps} = job.gif;
      const trim = ['-ss', String(start), ...(duration ? ['-t', String(duration)] : [])];
      const palette = path.join(ROOT, 'out', `palette_${job.stem}_${lang}${suffix}.png`);
      fs.mkdirSync(path.dirname(palette), {recursive: true});
      const scale = `fps=${fps},scale=960:-1:flags=lanczos`;
      ffmpeg([...trim, '-i', input, '-vf', `${scale},palettegen=max_colors=256:stats_mode=full`, palette]);
      ffmpeg([
        ...trim,
        '-i', input,
        '-i', palette,
        '-lavfi', `${scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle`,
        '-loop', '0',
        output,
      ]);
      console.log(`gif ${output} ${mb(output)}`);
    }

    if (only.includes('poster') && job.poster !== null) {
      const output = `${base}.png`;
      const tmp = path.join(ROOT, 'out', `poster_${job.stem}_${lang}${suffix}.png`);
      fs.mkdirSync(path.dirname(tmp), {recursive: true});
      await renderStill({composition, serveUrl, output: tmp, frame: job.poster, inputProps, browserExecutable, chromeMode});
      // 1280 px wide, lanczos down-scale, 8-bit palette keeps it small.
      ffmpeg(['-i', tmp, '-vf', 'scale=1280:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=256[p];[b][p]paletteuse=dither=none', output]);
      console.log(`poster ${output} ${mb(output)}`);
    }
  }
}
