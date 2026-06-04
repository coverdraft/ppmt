const { chromium } = require('playwright');
const path = require('path');

async function main() {
  const browser = await chromium.launch();
  const outputDir = '/home/z/my-project/download';

  const diagrams = [
    {
      html: 'diagram1-full-flow.html',
      output: 'architecture-full-flow.png',
      width: 1400,
      height: 1800,
    },
    {
      html: 'diagram2-sde-pipeline.html',
      output: 'sde-pipeline-detail.png',
      width: 1400,
      height: 1500,
    },
    {
      html: 'diagram3-dependency-map.html',
      output: 'module-dependency-map.png',
      width: 1600,
      height: 960,
    },
    {
      html: 'diagram4-api-routes.html',
      output: 'api-routes-map.png',
      width: 1600,
      height: 1200,
    },
  ];

  for (const d of diagrams) {
    console.log(`Rendering ${d.html}...`);
    const page = await browser.newPage();
    const filePath = path.join(outputDir, d.html);
    
    await page.goto(`file://${filePath}`, { waitUntil: 'networkidle' });
    
    // Set viewport and device scale factor for high quality
    await page.setViewportSize({ width: d.width, height: d.height });
    
    // Wait for fonts to load
    await page.waitForTimeout(500);
    
    // Get the actual content height
    const bodyHeight = await page.evaluate(() => document.body.scrollHeight);
    const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
    const finalHeight = Math.max(bodyHeight + 40, d.height);
    const finalWidth = Math.max(bodyWidth + 20, d.width);
    
    await page.setViewportSize({ width: finalWidth, height: finalHeight });
    await page.waitForTimeout(200);

    await page.screenshot({
      path: path.join(outputDir, d.output),
      fullPage: false,
      clip: { x: 0, y: 0, width: finalWidth, height: finalHeight },
      deviceScaleFactor: 2,
    });

    console.log(`  -> Saved ${d.output} (${finalWidth}x${finalHeight} @2x)`);
    await page.close();
  }

  await browser.close();
  console.log('All diagrams rendered successfully!');
}

main().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
