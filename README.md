# Pinpoint Bulk PDF Downloader

Bulk-downloads PDF documents from a [Google Pinpoint](https://journaliststudio.google.com/pinpoint) collection using Playwright browser automation.

## Prerequisites

- Python 3.9+

## Setup

1. **Download the files**

   Download the repository from [GitHub](https://github.com/ucla-data-science-center/pinpoint-bulk-download) and navigate into the folder using the `cd` command:

   ```bash
   cd path/to/pinpoint-bulk-download
   ```

2. **Create and activate a virtual environment**

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Install the Playwright browser**

   ```bash
   playwright install chromium
   ```

## Usage

### Save your login state (optional)

If the Pinpoint collection requires a Google login, run the state-saver script first to log in once and cache your session:

```bash
python save_state.py
```

This opens a browser window. Log into your Google account, then come back to the terminal and press **Enter**. Your session is saved to `pinpoint_state.json` so subsequent runs don't require re-authentication.

> **Note:** If the collection is publicly accessible, you can skip this step.

### Download all PDFs

```bash
python download_all_pinpoint_pdfs.py
```

PDFs are saved to `israeli_state_archives_pdfs/`. Failed downloads are logged to `israeli_state_archives_pdfs/failed.txt`.

### Configuration

Edit the constants at the top of `download_all_pinpoint_pdfs.py` to adjust behavior:

| Variable | Default | Description |
|---|---|---|
| `COLLECTION_URL` | *(set)* | URL of the Pinpoint collection |
| `TOTAL_PAGES` | `29` | Number of results pages to iterate |
| `START_PAGE` | `1` | Page to start from (for resuming) |
| `SCROLL_PASSES_PER_PAGE` | `90` | Scroll iterations to load all results on a page |
| `DOWNLOAD_TIMEOUT_MS` | `90000` | Max wait time per PDF download (ms) |

### Resuming

If the script is interrupted, set `START_PAGE` to the last completed page + 1. Already-downloaded files are automatically skipped.

## Other Scripts

| Script | Description |
|---|---|
| `save_state.py` | Save Google login session to `pinpoint_state.json` |
| `test_download_first_10_docs.py` | Test: download first 10 docs |
| `test_one_pdf_per_results_page_29_pages.py` | Test: one PDF per results page |
