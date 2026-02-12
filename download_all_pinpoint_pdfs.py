import re
from pathlib import Path
from playwright.sync_api import sync_playwright

# Input Data Path
COLLECTION_URL = "https://journaliststudio.google.com/pinpoint/search?collection=828502a5fcab2dc3"
STATE_FILE = "pinpoint_state.json"

# Output File Path: The downloaded files will be compiled inside a folder titled "israeli_state_archives_pdfs" within the folder that is code is located in. 
OUT_DIR = Path("israeli_state_archives_pdfs")
OUT_DIR.mkdir(parents=True, exist_ok=True)
FAILED_LOG = OUT_DIR / "failed.txt"

# Initialized timeouts and page numbers 
TOTAL_PAGES = 29
START_PAGE = 1
SCROLL_PASSES_PER_PAGE = 90
WAIT_BETWEEN_DOCS_MS = 450
WAIT_BETWEEN_PAGES_MS = 900
MENU_TIMEOUT_MS = 15000
DOWNLOAD_TIMEOUT_MS = 90000

# Removes illegal characters (whitespace, ..., and \ / : * ? " < > |) which would break the filesystem naming convention
def sanitize_filename(name: str) -> str:
    name = name.strip().replace("…", "")
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name or "download.pdf"


# Scrolls down the results page repeatedly to avoid lazy-loading, so that all document links become visible in the DOM (Document Object Model).
def load_results_page(page):
    for _ in range(SCROLL_PASSES_PER_PAGE):
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(140)
    page.wait_for_timeout(900)

# Ensures the PDF viewer is scrolled to the top, so the download menu is accessible.
def nudge_pdf_viewer(page):
    try:
        page.mouse.click(500, 300)
        page.wait_for_timeout(120)
    except Exception:
        pass
    for key in ["Home", "PageUp", "PageUp", "PageUp"]:
        try:
            page.keyboard.press(key)
            page.wait_for_timeout(90)
        except Exception:
            pass

# Opens the top-bar menu inside the document viewer and clicks "Download original file" to trigger the PDF download.
def click_download_original(page):
    page.get_by_role("button", name="Top bar menu").wait_for(state="visible", timeout=MENU_TIMEOUT_MS)
    page.get_by_role("button", name="Top bar menu").click(timeout=MENU_TIMEOUT_MS)
    page.wait_for_timeout(150)
    try:
        page.get_by_role("menuitem", name="Download original file").click(timeout=5000)
    except Exception:
        page.get_by_text("Download original file").click(timeout=5000)

# Navigates back from the document viewer to the search results list.
def go_back_to_results(page):
    try:
        page.get_by_role("button", name="Back", exact=True).click(timeout=8000)
    except Exception:
        try:
            page.go_back()
        except Exception:
            pass
    page.wait_for_timeout(450)

# Clicks the "next page" button to move to the next results page.
def click_next_results_page(page):
    page.get_by_role("button").filter(has_text="").first.click(timeout=7000)
    page.wait_for_timeout(1400)

# Skips forward through results pages to reach the desired START_PAGE ( when resuming a partially completed download run).
def advance_to_start_page(page, start_page: int):
    for _ in range(start_page - 1):
        click_next_results_page(page)

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
        page.wait_for_timeout(2200)

        # If resuming from a later page, skip ahead
        if START_PAGE > 1:
            advance_to_start_page(page, START_PAGE)

        # Open the failure log in append mode so previous entries are kept
        with open(FAILED_LOG, "a", encoding="utf-8") as flog:
            for results_page in range(START_PAGE, TOTAL_PAGES + 1):
                print(f"\n====================")
                print(f"RESULTS PAGE {results_page} / {TOTAL_PAGES}")
                print(f"====================")

                # Scroll to load all document links on this page
                load_results_page(page)

                # Find all elements whose text contains ".pdf"
                docs = page.locator("text=.pdf")
                total = docs.count()
                if total == 0:
                    # Retry once in case the page was slow to load
                    load_results_page(page)
                    docs = page.locator("text=.pdf")
                    total = docs.count()

                print(f"Visible docs: {total}")
                if total == 0:
                    msg = f"No docs visible on results page {results_page}. Stopping."
                    print("STOPPED:", msg)
                    flog.write(f"results_page={results_page}\t{msg}\n")
                    break

                # Iterate through each document on the current results page
                for i in range(total):
                    docs = page.locator("text=.pdf")
                    # Get the document title shown in the UI
                    try:
                        ui_title = sanitize_filename(docs.nth(i).inner_text())
                    except Exception:
                        ui_title = f"results_page_{results_page}_doc_{i}.pdf"

                    # Skip files that have already been downloaded, handles duplicates here
                    if (OUT_DIR / ui_title).exists():
                        print(f"Skip (exists): {ui_title}")
                        continue

                    print(f"[{results_page}:{i+1}/{total}] {ui_title}")

                    try:
                        # Click the document to open the PDF viewer
                        docs.nth(i).click()
                        page.wait_for_timeout(1250)

                        nudge_pdf_viewer(page)

                        # Wait for the download to start, then save with the original filename from the server
                        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
                            click_download_original(page)

                        download = dl_info.value
                        suggested = sanitize_filename(download.suggested_filename or ui_title)
                        save_path = OUT_DIR / suggested
                        download.save_as(str(save_path))
                        print(f"FINISHED Saved: {save_path.name}")

                    except Exception as e:
                        print(f"STOPPED Failed: {ui_title} -> {e}")
                        flog.write(f"results_page={results_page}\t{ui_title}\t{repr(e)}\n")

                    # Return to the results list before processing the next doc
                    go_back_to_results(page)
                    page.wait_for_timeout(WAIT_BETWEEN_DOCS_MS)

                # Move to the next results page (unless we're on the last one)
                if results_page < TOTAL_PAGES:
                    try:
                        click_next_results_page(page)
                        page.wait_for_timeout(WAIT_BETWEEN_PAGES_MS)
                    except Exception as e:
                        print(f"STOPPED Could not go to next results page from page {results_page}: {e}")
                        flog.write(f"results_page={results_page}\tnext_failed\t{repr(e)}\n")
                        break

        context.close()
        browser.close()
        print(f"\nDone. PDFs saved to: {OUT_DIR.resolve()}")
        print(f"Failures logged to: {FAILED_LOG.resolve()}")

if __name__ == "__main__":
    main()
