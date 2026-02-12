import re
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# Input Data Path
COLLECTION_URL = "https://journaliststudio.google.com/pinpoint/search?collection=828502a5fcab2dc3"
STATE_FILE = "pinpoint_state.json"  # optional

# Output File Path: The downloaded files will be compiled inside a folder titled "israeli_state_archives_pdfs_test" within the folder that this code is located in.
OUT_DIR = Path("israeli_state_archives_pdfs_test")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Initialized timeouts and page numbers
MENU_TIMEOUT_MS = 15000
DOWNLOAD_TIMEOUT_MS = 60000

# Removes illegal characters (whitespace, ..., and \ / : * ? " < > |) which would break the filesystem naming convention
def sanitize_filename(name: str) -> str:
    name = name.strip().replace("…", "")
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name or "download.pdf"

# Ensures the PDF viewer is scrolled to the top, so the download menu is accessible.
def nudge_off_last_page(page):
    try:
        page.mouse.click(500, 300)
        page.wait_for_timeout(120)
    except Exception:
        pass
    for key in ["Home", "PageUp", "PageUp"]:
        try:
            page.keyboard.press(key)
            page.wait_for_timeout(100)
        except Exception:
            pass

# Opens the top-bar menu inside the document viewer and clicks "Download original file" to trigger the PDF download.
def click_download_original(page):
    page.get_by_role("button", name="Top bar menu").wait_for(state="visible", timeout=MENU_TIMEOUT_MS)
    page.get_by_role("button", name="Top bar menu").click(timeout=MENU_TIMEOUT_MS)
    page.wait_for_timeout(150)
    try:
        page.get_by_role("menuitem", name="Download original file").click(timeout=5000)
    except PWTimeout:
        page.get_by_text("Download original file").click(timeout=5000)

# Navigates back from the document viewer to the search results list.
def go_back_to_list(page):
    try:
        page.get_by_role("button", name="Back", exact=True).click(timeout=8000)
    except Exception:
        page.go_back()
    page.wait_for_timeout(400)

def main():
    with sync_playwright() as p:
        # Launch a visible browser so the user can observe progress and manually intervene if needed.
        browser = p.chromium.launch(headless=False)

        # Load saved login session if necessary
        ctx_kwargs = {"accept_downloads": True}
        if Path(STATE_FILE).exists():
            ctx_kwargs["storage_state"] = STATE_FILE

        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        page.goto(COLLECTION_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Scrolls down the results page repeatedly to avoid lazy-loading, so that all document links become visible in the DOM (Document Object Model).
        for _ in range(30):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(150)
        page.wait_for_timeout(800)

        # Find all elements whose text contains ".pdf"
        docs = page.locator("text=.pdf")
        total = docs.count()
        print(f"Visible docs on page 1: {total}")

        # Download up to 10 documents from the first results page
        limit = min(10, total)
        for i in range(limit):
            docs = page.locator("text=.pdf")  # re-acquire after DOM changes

            # Get the document title shown in the UI
            title_ui = sanitize_filename(docs.nth(i).inner_text())

            # Skip files that have already been downloaded, handles duplicates here
            if (OUT_DIR / title_ui).exists():
                print(f"Skip (exists): {title_ui}")
                continue

            print(f"[1:{i+1}/{limit}] {title_ui}")

            # Click the document to open the PDF viewer
            docs.nth(i).click()
            page.wait_for_timeout(1200)

            nudge_off_last_page(page)

            # Wait for the download to start, then save with the original filename from the server
            with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
                click_download_original(page)

            download = dl_info.value
            suggested = sanitize_filename(download.suggested_filename or title_ui)
            save_path = OUT_DIR / suggested
            download.save_as(str(save_path))
            print(f"FINISHED Saved: {save_path.name}")

            # Return to the results list before processing the next doc
            go_back_to_list(page)

        context.close()
        browser.close()
        print(f"Done. Saved to: {OUT_DIR.resolve()}")

if __name__ == "__main__":
    main()
