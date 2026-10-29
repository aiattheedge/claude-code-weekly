// Renders each job in jobs.json to a PNG at its exact size.
// Uses RENDER_DIR when set, otherwise the newest render/<date>/ folder.
// jobs.json: [{"html": "cover.html", "out": "images/2026-11-02/cover.png", "width": 1200, "height": 627}, ...]
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

function jobDir() {
  if (process.env.RENDER_DIR) return process.env.RENDER_DIR;
  const dirs = fs.readdirSync('render').filter((d) => fs.existsSync(path.join('render', d, 'jobs.json'))).sort();
  if (!dirs.length) throw new Error('No render/<date>/jobs.json found');
  return path.join('render', dirs[dirs.length - 1]);
}

async function main() {
  const dir = jobDir();
  const jobs = JSON.parse(fs.readFileSync(path.join(dir, 'jobs.json'), 'utf8'));
  const browser = await chromium.launch();
  try {
    for (const job of jobs) {
      const page = await browser.newPage({ viewport: { width: job.width, height: job.height } });
      await page.goto('file://' + path.resolve(dir, job.html), { waitUntil: 'networkidle' });
      await page.evaluate(() => document.fonts.ready);
      fs.mkdirSync(path.dirname(job.out), { recursive: true });
      await page.screenshot({ path: job.out, clip: { x: 0, y: 0, width: job.width, height: job.height } });
      await page.close();
      console.log('rendered', job.out, job.width + 'x' + job.height);
    }
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
