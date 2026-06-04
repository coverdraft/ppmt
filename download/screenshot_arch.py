import asyncio
from playwright.async_api import async_playwright

async def main():
    html_path = "/home/z/my-project/download/cryptoquant-arch.html"
    output_path = "/home/z/my-project/download/cryptoquant-architecture.png"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={'width': 1600, 'height': 1200},
            device_scale_factor=2
        )
        await page.goto(f'file://{html_path}', wait_until='networkidle')
        await page.wait_for_timeout(800)

        # Get the actual content size and adjust viewport
        el = page.locator('#root')
        bbox = await el.bounding_box()
        if bbox:
            fit_w = max(1600, int(bbox['width'] + 80))
            fit_h = int(bbox['height'] + 80)
            await page.set_viewport_size({'width': fit_w, 'height': fit_h})
            await page.wait_for_timeout(300)

        await el.screenshot(path=output_path)
        await browser.close()

    import os
    size_kb = os.path.getsize(output_path) / 1024
    print(f"✅ Saved: {output_path} ({size_kb:.0f} KB)")

asyncio.run(main())
