import re
from pathlib import Path
from playwright.sync_api import sync_playwright

# Input Data Path
COLLECTION_URL = "https://journaliststudio.google.com/pinpoint/search?collection=828502a5fcab2dc3"
STATE_FILE = "pinpoint_state.json"  # optional

# Output File Path: The downloaded files will be compiled inside a folder titled "israeli_state_archives_pdfs_smoketest" within the folder that this code is located in.
OUT_DIR = Path("israeli_state_archives_pdfs_smoketest")
OUT_DIR.mkdir(parents=True, exist_ok=True)
FAILED_LOG = OUT_DIR / "failed.txt"

# Initialized timeouts and page numbers
TOTAL_PAGES = 29
START_PAGE = 1
SCROLL_PASSES = 40
MENU_TIMEOUT_MS = 15000
DOWNLOAD_TIMEOUT_MS = 60000
WAIT_BETWEEN_PAGES = 0.6

# Removes illegal characters (whitespace, ..., and \ / : * ? " < > |) which would break the filesystem naming convention
def sanitize_filename(name: str) -> str:
    name = name.strip().replace("…", "")
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name or "download.pdf"

# Scrolls down the results page repeatedly to avoid lazy-loading, so that all document links become visible in the DOM (Document Object Model).
def load_page_list(page):
    for _ in range(SCROLL_PASSES):
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(120)
    page.wait_for_timeout(700)

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
    except Exception:
        page.get_by_text("Download original file").click(timeout=5000)

# Navigates back from the document viewer to the search results list.
def go_back_to_list(page):
    try:
        page.get_by_role("button", name="Back", exact=True).click(timeout=8000)
    except Exception:
        try:
            page.go_back()
        except Exception:
            pass
    page.wait_for_timeout(400)

# Clicks the "next page" button to move to the next results page.
def click_next_results_page(page):
    page.get_by_role("button").filter(has_text="").first.click(timeout=5000)
    page.wait_for_timeout(1400)

# Skips forward through results pages to reach the desired START_PAGE ( when resuming a partially completed download run).
def jump_to_start_page(page, start_page: int):
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
        page.wait_for_timeout(2000)

        # If resuming from a later page, skip ahead
        if START_PAGE > 1:
            jump_to_start_page(page, START_PAGE)

        # Open the failure log in append mode so previous entries are kept
        with open(FAILED_LOG, "a", encoding="utf-8") as flog:
            for results_page in range(START_PAGE, TOTAL_PAGES + 1):
                print(f"\n====================")
                print(f"SMOKE TEST: RESULTS PAGE {results_page} / {TOTAL_PAGES}")
                print(f"====================")

                # Scroll to load all document links on this page
                load_page_list(page)

                # Find all elements whose text contains ".pdf"
                docs = page.locator("text=.pdf")
                total = docs.count()
                if total == 0:
                    # Retry once in case the page was slow to load
                    load_page_list(page)
                    docs = page.locator("text=.pdf")
                    total = docs.count()

                if total == 0:
                    msg = f"No docs visible on results page {results_page}; stopping."
                    print("STOPPED:", msg)
                    flog.write(f"results_page={results_page}\t{msg}\n")
                    break

                # Download only the FIRST doc on this results page (one per page)
                # Get the document title shown in the UI
                try:
                    title_ui = sanitize_filename(docs.first.inner_text())
                except Exception:
                    title_ui = f"results_page_{results_page}_first.pdf"

                # Use a marker file to track which pages have been completed,
                # so re-runs can skip already-downloaded pages.
                marker = OUT_DIR / f"_page_{results_page:02d}_done.txt"
                if marker.exists():
                    print(f"Skip download (page already done): results page {results_page}")
                else:
                    try:
                        print(f"Downloading first doc: {title_ui}")

                        # Click the document to open the PDF viewer
                        docs.first.click()
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

                        # Mark this results page as completed
                        marker.write_text(f"completed results page {results_page}\n", encoding="utf-8")

                    except Exception as e:
                        print(f"STOPPED Failed on results page {results_page}: {e}")
                        flog.write(f"results_page={results_page}\t{repr(e)}\n")

                    # Return to the results list before processing the next doc
                    go_back_to_list(page)

                # Move to the next results page (unless we're on the last one)
                if results_page < TOTAL_PAGES:
                    try:
                        click_next_results_page(page)
                        page.wait_for_timeout(int(WAIT_BETWEEN_PAGES * 1000))
                    except Exception as e:
                        print(f"STOPPED Could not go to next results page from page {results_page}: {e}")
                        flog.write(f"results_page={results_page}\tnext_failed\t{repr(e)}\n")
                        break

        context.close()
        browser.close()
        print(f"\nSmoke test complete. Saved PDFs to: {OUT_DIR.resolve()}")
        print(f"Failures logged to: {FAILED_LOG.resolve()}")

if __name__ == "__main__":
    main()
