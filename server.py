from fastapi import FastAPI, HTTPException, Query
from starlette.requests import Request
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator
import os
from playwright.async_api import async_playwright, Browser, Page
import zipfile
import io
import base64
from typing import List, Optional, Dict, Any, Literal
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

file_handler = RotatingFileHandler('Log.log', maxBytes=10*1024*1024, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
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

logger = logging.getLogger(__name__)

DEFAULT_PDF_OPTIONS = {
    "format": "A4",
    "margin": {"top": "2cm", "right": "2cm", "bottom": "2cm", "left": "2cm"},
    "print_background": True,
    "landscape": False,
    "scale": 1.0
}

MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "50"))
MAX_HTML_SIZE = int(os.getenv("MAX_HTML_SIZE", "10_000_000"))
CONVERSION_TIMEOUT = int(os.getenv("CONVERSION_TIMEOUT", "60"))
BROWSER_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "30"))

_browser: Optional[Browser] = None
_playwright = None
_browser_lock = asyncio.Lock()

BROWSER_ARGS = [
    "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
    "--disable-accelerated-2d-canvas", "--no-first-run", "--disable-gpu",
    "--disable-software-rasterizer"
]

FOOTER_TEMPLATE = '<div style="font-size: 10px; padding: 0; margin: 0; width: 100%; text-align: center; color: rgb(0, 0, 0); font-family: Arial, sans-serif;"><span>© 2025 LionOBytes — Innovating the future. All rights reserved.</span></div>'

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _browser, _playwright
    try:
        _playwright = await async_playwright().__aenter__()
        _browser = await _playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        logger.info("Browser initialized")
        yield
    finally:
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

app = FastAPI(
    title="HTML to PDF Microservice",
    version="1.0.0",
    description="Production-ready HTML to PDF conversion service with batch processing",
    lifespan=lifespan
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    for error in errors:
        if error.get("type") == "json_invalid":
            ctx = error.get("ctx", {})
            if "Invalid control character" in str(ctx.get("error", "")):
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [{
                            "type": "json_invalid",
                            "loc": error.get("loc", []),
                            "msg": "Invalid JSON: HTML content contains unescaped control characters. Make sure your HTML is properly escaped in JSON.",
                            "input": error.get("input", {})
                        }]
                    }
                )
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

def validate_html_size(v: str) -> str:
    if len(v.encode('utf-8')) > MAX_HTML_SIZE:
        raise ValueError(f"HTML content exceeds maximum size of {MAX_HTML_SIZE} bytes")
    return v

class HTMLRequest(BaseModel):
    html: str = Field(..., min_length=1)
    
    @field_validator('html')
    @classmethod
    def validate_size(cls, v: str) -> str:
        return validate_html_size(v)

class RawHTMLRequest(BaseModel):
    html_content: str = Field(..., min_length=1)
    
    @field_validator('html_content')
    @classmethod
    def validate_size(cls, v: str) -> str:
        return validate_html_size(v)

class BatchHTMLRequest(BaseModel):
    html_list: List[str] = Field(...)
    
    @field_validator('html_list')
    @classmethod
    def validate_batch_size(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("HTML list cannot be empty")
        if len(v) > MAX_BATCH_SIZE:
            raise ValueError(f"Batch size exceeds maximum of {MAX_BATCH_SIZE} items")
        return v
    
    @field_validator('html_list', mode='after')
    @classmethod
    def validate_html_items(cls, v: List[str]) -> List[str]:
        for i, html in enumerate(v):
            if not html or not html.strip():
                raise ValueError(f"HTML content at index {i} is empty")
            if len(html.encode('utf-8')) > MAX_HTML_SIZE:
                raise ValueError(f"HTML content at index {i} exceeds maximum size of {MAX_HTML_SIZE} bytes")
        return v

@app.get("/health", tags=["Health"])
async def health_check():
    logger.info("Health check requested")
    return {
        "status": "ok",
        "service": "html-to-pdf-microservice",
        "max_batch_size": MAX_BATCH_SIZE,
        "max_html_size": MAX_HTML_SIZE
    }

async def _get_browser() -> Browser:
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

def _build_pdf_options(pdf_options: dict) -> Dict[str, Any]:
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

async def _convert_html_to_pdf_bytes(html_content: str, pdf_options: dict, index: Optional[int] = None) -> tuple[int, bytes]:
    page: Optional[Page] = None
    prefix = f"[Index {index}]" if index is not None else ""
    try:
        logger.info(f"{prefix} Starting HTML to PDF conversion")
        browser = await _get_browser()
        page = await browser.new_page()
        page.set_default_timeout(BROWSER_TIMEOUT * 1000)
        logger.info(f"{prefix} Page created, loading HTML content")
        
        await asyncio.wait_for(page.set_content(html_content, wait_until="networkidle"), timeout=CONVERSION_TIMEOUT)
        logger.info(f"{prefix} HTML content loaded, generating PDF")
        
        pdf_bytes = await asyncio.wait_for(page.pdf(**_build_pdf_options(pdf_options)), timeout=CONVERSION_TIMEOUT)
        logger.info(f"{prefix} PDF generated successfully, size: {len(pdf_bytes)} bytes")
        return (index if index is not None else 0, pdf_bytes)
        
    except asyncio.TimeoutError:
        msg = f"Conversion timeout after {CONVERSION_TIMEOUT}s" + (f" for item at index {index}" if index is not None else "")
        logger.error(msg)
        raise TimeoutError(msg)
    except Exception as e:
        msg = "Failed to convert HTML to PDF" + (f" at index {index}" if index is not None else "")
        logger.error(f"{msg}: {str(e)}")
        raise
    finally:
        if page:
            try:
                await page.close()
            except Exception as e:
                logger.warning(f"Error closing page: {str(e)}")

async def _convert_html_to_pdf(html_content: str, pdf_options: dict) -> Response:
    logger.info("Converting HTML to PDF response")
    _, pdf_bytes = await _convert_html_to_pdf_bytes(html_content, pdf_options)
    logger.info(f"PDF conversion completed, returning response with {len(pdf_bytes)} bytes")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=document.pdf",
            "Content-Length": str(len(pdf_bytes))
        }
    )

async def _handle_conversion(request_data, html_field: str):
    logger.info(f"Received {request_data.__class__.__name__} request")
    try:
        result = await _convert_html_to_pdf(getattr(request_data, html_field), DEFAULT_PDF_OPTIONS)
        logger.info(f"Successfully processed {request_data.__class__.__name__} request")
        return result
    except HTTPException:
        raise
    except TimeoutError as e:
        logger.error(f"Conversion timeout: {str(e)}")
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"Conversion error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to convert HTML to PDF: {str(e)}")

@app.post("/convert", response_class=Response, tags=["PDF"])
async def convert_html_to_pdf(request_data: HTMLRequest):
    return await _handle_conversion(request_data, "html")

@app.post("/convert-raw", response_class=Response, tags=["PDF"])
async def convert_html_to_pdf_raw(request_data: RawHTMLRequest):
    return await _handle_conversion(request_data, "html_content")

@app.post("/convert-batch", tags=["PDF"])
async def convert_batch_html_to_pdf(
    request_data: BatchHTMLRequest,
    return_format: Literal["zip", "json"] = Query(default="zip", description="Return format: 'zip' for ZIP file, 'json' for JSON with base64 PDFs")
):
    html_list = request_data.html_list
    total_items = len(html_list)
    
    logger.info(f"Received /convert-batch request: {total_items} documents, format: {return_format}")
    
    try:
        logger.info(f"Creating {total_items} conversion tasks")
        tasks = [_convert_html_to_pdf_bytes(html, DEFAULT_PDF_OPTIONS, index=i) for i, html in enumerate(html_list)]
        
        logger.info("Executing batch conversions concurrently")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("All batch conversions completed")
        
        successful_pdfs: Dict[int, bytes] = {}
        errors: List[Dict[str, Any]] = []
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                errors.append({
                    "index": i,
                    "error": str(result),
                    "error_type": "timeout" if isinstance(result, TimeoutError) else type(result).__name__
                })
                logger.warning(f"Failed to convert HTML at index {i}: {str(result)}")
            else:
                successful_pdfs[result[0]] = result[1]
        
        success_count = len(successful_pdfs)
        error_count = len(errors)
        logger.info(f"Batch processing complete: {success_count} succeeded, {error_count} failed")
        
        if success_count == 0:
            raise HTTPException(
                status_code=500,
                detail={"message": "All PDF conversions failed", "total": total_items, "errors": errors}
            )
        
        if return_format == "zip":
            logger.info(f"Creating ZIP file with {success_count} PDFs")
            zip_buffer = io.BytesIO()
            try:
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for idx in sorted(successful_pdfs.keys()):
                        zip_file.writestr(f"document_{idx+1}.pdf", successful_pdfs[idx])
                
                zip_data = zip_buffer.getvalue()
                logger.info(f"ZIP file created, size: {len(zip_data)} bytes")
                
                headers = {
                    "Content-Disposition": "attachment; filename=documents.zip",
                    "Content-Length": str(len(zip_data))
                }
                if errors:
                    headers["X-Conversion-Errors"] = str(error_count)
                    headers["X-Conversion-Success"] = str(success_count)
                
                logger.info("Returning ZIP response")
                return Response(content=zip_data, media_type="application/zip", headers=headers)
            finally:
                zip_buffer.close()
        else:
            logger.info(f"Encoding {success_count} PDFs to base64")
            pdfs_base64 = {idx: base64.b64encode(pdf_bytes).decode('utf-8') for idx, pdf_bytes in successful_pdfs.items()}
            
            response_data: Dict[str, Any] = {
                "success_count": success_count,
                "error_count": error_count,
                "total": total_items,
                "pdfs": pdfs_base64,
                "format": "base64"
            }
            if errors:
                response_data["errors"] = errors
            
            logger.info("Returning JSON response with base64 PDFs")
            return JSONResponse(content=response_data)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch conversion error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process batch conversion: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    
    uvicorn_log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            }
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout"
            },
            "file": {
                "formatter": "default",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": "Log.log",
                "maxBytes": 10*1024*1024,
                "backupCount": 5
            }
        },
        "root": {"level": "INFO", "handlers": ["default", "file"]},
        "loggers": {
            "uvicorn": {"level": "INFO", "handlers": ["default", "file"], "propagate": False},
            "uvicorn.error": {"level": "INFO", "handlers": ["default", "file"], "propagate": False},
            "uvicorn.access": {"level": "INFO", "handlers": ["default", "file"], "propagate": False}
        }
    }
    
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=uvicorn_log_config)
