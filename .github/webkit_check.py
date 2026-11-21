"""Opens the downloaded handbook (with its bible folder) in WebKit, the engine inside Safari, and uses the Bible reader
and the word study. Run by the Mac test on GitHub. Usage: python webkit_check.py <path to Apologetics_Handbook.html>"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

page_file = pathlib.Path(sys.argv[1]).resolve()
errors = []
with sync_playwright() as p:
    b = p.webkit.launch()
    for w, h, tag in ((1280, 860, "desktop"), (390, 844, "phone")):
        pg = b.new_page(viewport={"width": w, "height": h})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(page_file.as_uri())
        pg.wait_for_timeout(1500)
        pg.evaluate("localStorage.setItem('hb-tour','done')")
        assert "Apologetics Handbook" in pg.title()
        pg.evaluate("document.querySelector('#nav-bible').click()")
        pg.wait_for_selector("#bible .bv", timeout=30000)
        pg.select_option("#br-book", "John")
        pg.select_option("#br-ch", "8")
        pg.select_option("#br-t2", "TR")
        pg.wait_for_selector("#bible .bvt.b2", timeout=30000)
        row = pg.text_content("#bv-58")
        assert "Abraham" in row, row
        print(tag, "reader ok:", row[:120])
        pg.screenshot(path=f"webkit-reader-{tag}.png")
        pg.evaluate("document.querySelector('.br-x').click()")
        pg.keyboard.press("Meta+k")
        try:
            pg.wait_for_selector("dialog[open] #pal-q", timeout=3000)
        except Exception:  # noqa: BLE001
            pg.keyboard.press("Control+k")
            pg.wait_for_selector("dialog[open] #pal-q", timeout=5000)
        pg.keyboard.type("Deuteronomy 22:28")
        pg.wait_for_timeout(300)
        pg.click("dialog[open] [role=option]:has-text('Hebrew words')")
        pg.wait_for_selector("#words-dlg[open] .wd-w[data-s='H8610']", timeout=30000)
        pg.click("#words-dlg .wd-w[data-s='H8610']")
        entry = pg.text_content("#words-dlg .wd-entry")
        assert "H8610" in entry, entry
        print(tag, "word study ok:", " ".join(entry.split())[:140])
        pg.screenshot(path=f"webkit-words-{tag}.png")
        pg.close()
    b.close()
print("page errors:", errors)
sys.exit(1 if errors else 0)
