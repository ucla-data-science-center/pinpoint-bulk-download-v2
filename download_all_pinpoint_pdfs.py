import re
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

# Input Data Path
COLLECTION_URL = "https://journaliststudio.google.com/pinpoint/search?collection=828502a5fcab2dc3"
STATE_FILE = "pinpoint_state.json"

# Output File Path: The downloaded files will be compiled inside a folder titled "israeli_state_archives_pdfs" within the folder that is code is located in. 
OUT_DIR = Path("israeli_state_archives_pdfs")
OUT_DIR.mkdir(parents=True, exist_ok=True)
FAILED_LOG = OUT_DIR / "failed.txt"
DOWNLOAD_STATE_FILE = OUT_DIR / "download_state.json"
MISSING_REPORT_FILE = OUT_DIR / "missing_files_report.txt"

# Initialized timeouts and page numbers 
TOTAL_PAGES = 31
START_PAGE = 1
EXPECTED_TOTAL_DOCS = 3005
DOCS_PER_FULL_PAGE = 100
SCROLL_PASSES_PER_PAGE = 90
WAIT_BETWEEN_DOCS_MS = 450
WAIT_BETWEEN_PAGES_MS = 900
MENU_TIMEOUT_MS = 15000
DOWNLOAD_TIMEOUT_MS = 90000

# Minimal sanitization: trim surrounding whitespace and remove unsafe filename chars.
def sanitize_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[\\/:*?"<>|]+', "", name)
    name = re.sub(r"[\x00-\x1f]", "", name)
    return name or "download.pdf"

def expected_docs_for_page(page_number: int) -> int:
    if page_number < TOTAL_PAGES:
        return DOCS_PER_FULL_PAGE
    remaining = EXPECTED_TOTAL_DOCS - (DOCS_PER_FULL_PAGE * (TOTAL_PAGES - 1))
    return max(1, remaining)

def write_download_state(state: dict):
    DOWNLOAD_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def write_missing_report(state: dict):
    lines = []
    run_counters = state.get("run_counters", {})
    lines.append("Pinpoint missing files report")
    lines.append(f"Collection: {state.get('collection_url', '')}")
    lines.append(
        "Totals: attempted={attempted}, downloaded={downloaded_new}, "
        "downloaded_or_existing={downloaded_or_existing}, failed={failed}".format(
            attempted=run_counters.get("attempted", 0),
            downloaded_new=run_counters.get("downloaded_new", 0),
            downloaded_or_existing=run_counters.get("downloaded_or_existing", 0),
            failed=run_counters.get("failed", 0),
        )
    )
    lines.append("")

    pages = state.get("pages", {})
    missing_any = False
    for page_num in range(1, int(state.get("total_pages", TOTAL_PAGES)) + 1):
        page_data = pages.get(str(page_num))
        if not page_data:
            lines.append(f"Page {page_num}: NOT PROCESSED")
            missing_any = True
            continue
        expected = int(page_data.get("expected_docs", 0))
        downloaded_or_existing = int(page_data.get("downloaded_or_existing", 0))
        failed_docs = list(page_data.get("not_downloaded_files", []))
        shortfall = max(0, expected - downloaded_or_existing)
        if shortfall > 0 or failed_docs:
            missing_any = True
            lines.append(
                f"Page {page_num}: MISSING {shortfall} "
                f"(downloaded_or_existing={downloaded_or_existing}, expected={expected})"
            )
            if failed_docs:
                for name in failed_docs:
                    lines.append(f"  - {name}")
            else:
                lines.append("  - No failed-title capture; review this page manually.")
        else:
            lines.append(f"Page {page_num}: OK ({downloaded_or_existing}/{expected})")

    if not missing_any:
        lines.append("")
        lines.append("All pages look complete based on current run state.")

    MISSING_REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

def next_available_path(base_path: Path) -> Path:
    if not base_path.exists():
        return base_path
    stem, suffix = base_path.stem, base_path.suffix
    first_retry = base_path.with_name(f"{stem}_downloaded{suffix}")
    if not first_retry.exists():
        return first_retry
    idx = 2
    while True:
        candidate = base_path.with_name(f"{stem}_downloaded_{idx}{suffix}")
        if not candidate.exists():
            return candidate
        idx += 1


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

        pages_completed = 0
        docs_attempted = 0
        docs_downloaded = 0
        docs_failed = 0
        docs_counted_as_downloaded = 0
        download_state = {
            "collection_url": COLLECTION_URL,
            "total_pages": TOTAL_PAGES,
            "expected_total_docs": EXPECTED_TOTAL_DOCS,
            "expected_docs_per_full_page": DOCS_PER_FULL_PAGE,
            "expected_docs_last_page": expected_docs_for_page(TOTAL_PAGES),
            "run_counters": {
                "attempted": 0,
                "downloaded_new": 0,
                "downloaded_or_existing": 0,
                "failed": 0,
            },
            "pages": {},
        }
        write_download_state(download_state)

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
                expected = expected_docs_for_page(results_page)
                page_titles = []
                page_not_downloaded = []
                page_downloaded = 0
                page_failed = 0
                if total != expected:
                    warn = (
                        f"Expected {expected} docs on results page {results_page}, "
                        f"but found {total}. Retrying page load once."
                    )
                    print("WARNING:", warn)
                    flog.write(f"results_page={results_page}\tcount_mismatch\t{warn}\n")
                    load_results_page(page)
                    docs = page.locator("text=.pdf")
                    total = docs.count()
                    print(f"Visible docs after retry: {total}")
                    if total != expected:
                        mismatch = (
                            f"Persistent count mismatch on results page {results_page}: "
                            f"expected {expected}, got {total}."
                        )
                        print("WARNING:", mismatch)
                        flog.write(f"results_page={results_page}\tcount_mismatch_persistent\t{mismatch}\n")

                if total == 0:
                    msg = f"No docs visible on results page {results_page}. Stopping."
                    print("STOPPED:", msg)
                    flog.write(f"results_page={results_page}\t{msg}\n")
                    break

                # Iterate through each document on the current results page
                for i in range(total):
                    docs_attempted += 1
                    download_state["run_counters"]["attempted"] = docs_attempted
                    docs = page.locator("text=.pdf")
                    # Get the document title shown in the UI
                    try:
                        raw_ui_title = docs.nth(i).inner_text().strip()
                        ui_title = sanitize_filename(raw_ui_title)
                    except Exception:
                        raw_ui_title = f"results_page_{results_page}_doc_{i}.pdf"
                        ui_title = f"results_page_{results_page}_doc_{i}.pdf"
                    page_titles.append(raw_ui_title)

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
                        raw_suggested = (download.suggested_filename or ui_title).strip() or "download.pdf"

                        # Prefer preserving the exact server-provided filename.
                        preferred_path = next_available_path(OUT_DIR / raw_suggested)
                        try:
                            download.save_as(str(preferred_path))
                            save_path = preferred_path
                        except Exception as save_error:
                            fallback_name = sanitize_filename(raw_suggested)
                            fallback_path = next_available_path(OUT_DIR / fallback_name)
                            download.save_as(str(fallback_path))
                            save_path = fallback_path
                            note = (
                                f"results_page={results_page}\tfallback_name\t"
                                f"{raw_suggested}\t{fallback_name}\t{repr(save_error)}\n"
                            )
                            flog.write(note)
                            print(
                                "WARNING: Saved with sanitized fallback name "
                                f"({fallback_path.name}) after raw-name save failed."
                            )

                        print(f"FINISHED Saved: {save_path.name}")
                        docs_downloaded += 1
                        docs_counted_as_downloaded += 1
                        page_downloaded += 1
                        download_state["run_counters"]["downloaded_new"] = docs_downloaded
                        download_state["run_counters"]["downloaded_or_existing"] = docs_counted_as_downloaded

                    except Exception as e:
                        print(f"STOPPED Failed: {ui_title} -> {e}")
                        flog.write(f"results_page={results_page}\t{ui_title}\t{repr(e)}\n")
                        docs_failed += 1
                        page_failed += 1
                        page_not_downloaded.append(raw_ui_title)
                        download_state["run_counters"]["failed"] = docs_failed

                    # Return to the results list before processing the next doc
                    go_back_to_results(page)
                    page.wait_for_timeout(WAIT_BETWEEN_DOCS_MS)

                pages_completed += 1
                if page_downloaded < expected:
                    shortfall = expected - page_downloaded
                    msg = (
                        f"results_page={results_page}\tpage_shortfall\t"
                        f"downloaded_or_existing={page_downloaded}\texpected={expected}\tmissing={shortfall}"
                    )
                    print("WARNING:", msg)
                    flog.write(f"{msg}\n")

                download_state["pages"][str(results_page)] = {
                    "expected_docs": expected,
                    "visible_docs": total,
                    "attempted_docs": total,
                    "downloaded_new": page_downloaded,
                    "downloaded_or_existing": page_downloaded,
                    "failed_docs": page_failed,
                    "not_downloaded_files": page_not_downloaded,
                    "all_visible_files": page_titles,
                }
                write_download_state(download_state)
                write_missing_report(download_state)

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
        existing_pdfs = len(list(OUT_DIR.glob("*.pdf")))
        print("\nRun summary:")
        print(f"- Pages completed: {pages_completed}")
        print(f"- Docs iterated: {docs_attempted}")
        print(f"- Downloaded: {docs_downloaded}")
        print(f"- Downloaded or existing metric: {docs_counted_as_downloaded}")
        print(f"- Failed downloads: {docs_failed}")
        print(f"- PDFs currently in output folder: {existing_pdfs}")
        if existing_pdfs < EXPECTED_TOTAL_DOCS:
            missing = EXPECTED_TOTAL_DOCS - existing_pdfs
            msg = (
                f"WARNING: Output folder has {existing_pdfs}/{EXPECTED_TOTAL_DOCS} PDFs; "
                f"still missing at least {missing} files."
            )
            print(msg)
            with open(FAILED_LOG, "a", encoding="utf-8") as flog:
                flog.write(f"post_run\tincomplete\t{msg}\n")
        else:
            print(f"Verification passed: at least {EXPECTED_TOTAL_DOCS} PDFs are present.")
        download_state["run_counters"] = {
            "attempted": docs_attempted,
            "downloaded_new": docs_downloaded,
            "downloaded_or_existing": docs_counted_as_downloaded,
            "failed": docs_failed,
        }
        download_state["pages_completed"] = pages_completed
        download_state["pdfs_currently_in_output_folder"] = existing_pdfs
        write_download_state(download_state)
        write_missing_report(download_state)
        print(f"\nDone. PDFs saved to: {OUT_DIR.resolve()}")
        print(f"Failures logged to: {FAILED_LOG.resolve()}")
        print(f"Download state saved to: {DOWNLOAD_STATE_FILE.resolve()}")
        print(f"Missing report saved to: {MISSING_REPORT_FILE.resolve()}")

if __name__ == "__main__":
    main()
