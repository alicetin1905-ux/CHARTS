// Launch screens for the iOS home-screen app: the start page's sphere where the page will draw it, so opening the
// app goes straight from the launch screen into the start animation. Draws with the page's own orb() code.
//
//     node scripts/glasssphere_splash.js          (needs Playwright, and Python with Pillow)   writes icons/splash/*.png
// and prints the <link rel="apple-touch-startup-image"> tags for index.html.
const { chromium } = require(process.env.PW || 'playwright');
const fs = require('fs'), path = require('path');
const ROOT = path.join(__dirname, '..'), OUT = path.join(ROOT, 'icons', 'splash');
// iPhones: CSS width x height and pixel ratio, portrait
const DEVICES = [[440, 956, 3], [430, 932, 3], [428, 926, 3], [414, 896, 3], [414, 896, 2], [402, 874, 3], [393, 852, 3],
  [390, 844, 3], [375, 812, 3], [414, 736, 3], [375, 667, 2], [320, 568, 2]];

(async () => {
  const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const a = html.indexOf('  function orb('), b = html.indexOf('\n  function frame(');
  if (a < 0 || b < 0) throw new Error('orb() not found in index.html');
  const orbSrc = html.slice(a, b);
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch(), tags = [];
  for (const [w, h, dpr] of DEVICES) {
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: dpr });
    await page.setContent(`<body style="margin:0;background:radial-gradient(70% 55% at 50% 50%,rgba(53,255,176,.07),transparent 70%),#070a09">
      <canvas id="c" width="${w * dpr}" height="${h * dpr}" style="width:${w}px;height:${h}px;display:block"></canvas></body>`);
    await page.evaluate(([src, w, h, dpr]) => {
      const g = document.getElementById('c').getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
      const VC = { up: [53, 255, 176], down: [255, 77, 106] };
      const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`, mixc = (a, b, k) => a.map((v, j) => v + (b[j] - v) * k);
      const orb = new Function('g', 'VC', 'rgba', 'mixc', src + '\nreturn orb;')(g, VC, rgba, mixc);
      const R = Math.max(56, Math.min(140, Math.min(w, h) * 0.16));     // as geo() in index.html
      orb(w / 2, h / 2 + 4, R, 0, 0);
    }, [orbSrc, w, h, dpr]);
    const name = `splash-${w * dpr}x${h * dpr}.png`;
    await page.screenshot({ path: path.join(OUT, name) });
    tags.push(`<link rel="apple-touch-startup-image" href="icons/splash/${name}" media="(device-width: ${w}px) and (device-height: ${h}px) and (-webkit-device-pixel-ratio: ${dpr}) and (orientation: portrait)">`);
    await page.close();
  }
  await browser.close();
  // 256 colours with dithering: the soft glow keeps its look at about a tenth of the size
  require('child_process').execFileSync('python3', ['-c', `import glob
from PIL import Image
for f in glob.glob('${OUT}/*.png'):
    Image.open(f).convert('RGB').quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG).save(f, optimize=True)`]);
  console.log(tags.join('\n'));
})();
