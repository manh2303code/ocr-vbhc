import {existsSync} from 'node:fs';
import {Config} from '@remotion/cli/config';

// Render with an installed Chromium browser when one is available, so that no
// headless browser has to be downloaded. REMOTION_BROWSER overrides the
// lookup; without a browser Remotion downloads its own Chrome Headless Shell.
const candidates = [
  process.env.REMOTION_BROWSER,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
].filter((p): p is string => Boolean(p));

const browser = candidates.find((p) => existsSync(p));
if (browser) {
  Config.setBrowserExecutable(browser);
  Config.setChromeMode('chrome-for-testing');
}

Config.setVideoImageFormat('png');
Config.setPixelFormat('yuv420p');
Config.setCodec('h264');
Config.setOverwriteOutput(true);
