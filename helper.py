import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, Page
from contextlib import asynccontextmanager

# Logging setup
def setup_logging():
    """Configure logging for the application."""
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler = RotatingFileHandler('Log.log', maxBytes=10*1024*1024, backupCount=5)
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    for name in ["uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"]:
        logging.getLogger(name).setLevel(logging.INFO)
    
    return logging.getLogger(__name__)

logger = setup_logging()

# Configuration
DEFAULT_PDF_OPTIONS = {
    "format": "A4",
    "margin": {"top": "2cm", "right": "2cm", "bottom": "2cm", "left": "2cm"},
    "print_background": True,
    "landscape": False,
    "scale": 1.0
}

MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "1000"))
MAX_HTML_SIZE = int(os.getenv("MAX_HTML_SIZE", "10_000_000"))
CONVERSION_TIMEOUT = int(os.getenv("CONVERSION_TIMEOUT", "60"))
BROWSER_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "30"))

BROWSER_ARGS = [
    "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
    "--disable-accelerated-2d-canvas", "--no-first-run", "--disable-gpu",
    "--disable-software-rasterizer"
]

FOOTER_TEMPLATE = (
    '<div style="font-size: 10px; padding: 0; margin: 0; width: 100%; '
    'text-align: center; color: rgb(0, 0, 0); font-family: Arial, sans-serif;">'
    '<span>© 2025 LionOBytes — Innovating the future. All rights reserved.</span></div>'
)

# Browser management
_browser: Optional[Browser] = None
_playwright = None
_browser_lock = asyncio.Lock()


async def get_browser() -> Browser:
    """Get or create a browser instance."""
    global _browser, _playwright
    if _browser is None or not _browser.is_connected():
        logger.info("Creating new browser instance")
        async with _browser_lock:
            if _browser is None or not _browser.is_connected():
                if _playwright is None:
                    _playwright = await async_playwright().__aenter__()
                _browser = await _playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
                logger.info("Browser instance created successfully")
    return _browser


async def init_browser():
    """Initialize browser on startup."""
    global _browser, _playwright
    _playwright = await async_playwright().__aenter__()
    _browser = await _playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
    logger.info("Browser initialized")


async def close_browser():
    """Close browser on shutdown."""
    global _browser, _playwright
    if _browser:
        try:
            await _browser.close()
        except Exception as e:
            logger.warning(f"Error closing browser: {str(e)}")
        _browser = None
    if _playwright:
        try:
            await _playwright.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"Error closing playwright: {str(e)}")
        _playwright = None
    logger.info("Browser closed")


def build_pdf_options(pdf_options: dict) -> Dict[str, Any]:
    """Build PDF options dictionary for Playwright."""
    options: Dict[str, Any] = {
        "format": pdf_options["format"],
        "margin": pdf_options["margin"],
        "print_background": pdf_options["print_background"],
        "scale": pdf_options["scale"],
        "display_header_footer": True,
        "header_template": '<div></div>',
        "footer_template": FOOTER_TEMPLATE
    }
    if pdf_options.get("landscape"):
        options["landscape"] = True
    return options


async def convert_html_to_pdf_bytes(
    html_content: str,
    pdf_options: dict,
    index: Optional[int] = None
) -> tuple[int, bytes]:
    """Convert HTML content to PDF bytes."""
    page: Optional[Page] = None
    prefix = f"[Index {index}]" if index is not None else ""
    
    try:
        logger.info(f"{prefix} Starting HTML to PDF conversion")
        browser = await get_browser()
        page = await browser.new_page()
        page.set_default_timeout(BROWSER_TIMEOUT * 1000)
        logger.info(f"{prefix} Page created, loading HTML content")
        
        await asyncio.wait_for(
            page.set_content(html_content, wait_until="networkidle"),
            timeout=CONVERSION_TIMEOUT
        )
        logger.info(f"{prefix} HTML content loaded, generating PDF")
        
        pdf_bytes = await asyncio.wait_for(
            page.pdf(**build_pdf_options(pdf_options)),
            timeout=CONVERSION_TIMEOUT
        )
        logger.info(f"{prefix} PDF generated successfully, size: {len(pdf_bytes)} bytes")
        return (index if index is not None else 0, pdf_bytes)
        
    except asyncio.TimeoutError:
        msg = (
            f"Conversion timeout after {CONVERSION_TIMEOUT}s"
            + (f" for item at index {index}" if index is not None else "")
        )
        logger.error(msg)
        raise TimeoutError(msg)
    except Exception as e:
        msg = (
            "Failed to convert HTML to PDF"
            + (f" at index {index}" if index is not None else "")
        )
        logger.error(f"{msg}: {str(e)}")
        raise
    finally:
        if page:
            try:
                await page.close()
            except Exception as e:
                logger.warning(f"Error closing page: {str(e)}")

