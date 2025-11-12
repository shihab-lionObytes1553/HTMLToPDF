from fastapi import FastAPI, HTTPException, Query
from starlette.requests import Request
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from typing import List, Literal, Dict, Any
from contextlib import asynccontextmanager
import os
import asyncio
import uvicorn
import zipfile
import io
import base64

from helper import (
    logger,
    DEFAULT_PDF_OPTIONS,
    MAX_BATCH_SIZE,
    MAX_HTML_SIZE,
    init_browser,
    close_browser,
    convert_html_to_pdf_bytes
)
from models import HTMLRequest, BatchHTMLRequest


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_browser()
    yield
    await close_browser()

app = FastAPI(
    title="HTML to PDF Microservice",
    version="1.0.0",
    description="HTML to PDF conversion service with batch processing",
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


@app.get("/health", tags=["Health"])
async def health_check():
    logger.info("Health check requested")
    return {
        "status": "ok",
        "service": "html-to-pdf-microservice",
        "max_batch_size": MAX_BATCH_SIZE,
        "max_html_size": MAX_HTML_SIZE
    }

async def convert_html_to_pdf_response(html_content: str, pdf_options: dict) -> Response:
    """Convert HTML to PDF and return Response object."""
    logger.info("Converting HTML to PDF response")
    _, pdf_bytes = await convert_html_to_pdf_bytes(html_content, pdf_options)
    logger.info(f"PDF conversion completed, returning response with {len(pdf_bytes)} bytes")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=document.pdf",
            "Content-Length": str(len(pdf_bytes))
        }
    )


async def handle_conversion(request_data, html_field: str):
    """Handle HTML to PDF conversion request."""
    logger.info(f"Received {request_data.__class__.__name__} request")
    try:
        result = await convert_html_to_pdf_response(
            getattr(request_data, html_field),
            DEFAULT_PDF_OPTIONS
        )
        logger.info(f"Successfully processed {request_data.__class__.__name__} request")
        return result
    except HTTPException:
        raise
    except TimeoutError as e:
        logger.error(f"Conversion timeout: {str(e)}")
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"Conversion error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert HTML to PDF: {str(e)}"
        )

@app.post("/convert", response_class=Response, tags=["PDF"])
async def convert_html_to_pdf(request_data: HTMLRequest):
    """Convert HTML content to PDF."""
    return await handle_conversion(request_data, "html")

@app.post("/convert-batch", tags=["PDF"])
async def convert_batch_html_to_pdf(
    request_data: BatchHTMLRequest,
    return_format: Literal["zip", "json"] = Query(
        default="zip",
        description="Return format: 'zip' for ZIP file, 'json' for JSON with base64 PDFs"
    )
):
    """Convert multiple HTML documents to PDF in batch."""
    html_list = request_data.html_list
    total_items = len(html_list)
    
    logger.info(f"Received /convert-batch request: {total_items} documents, format: {return_format}")
    
    try:
        logger.info(f"Creating {total_items} conversion tasks")
        tasks = [
            convert_html_to_pdf_bytes(html, DEFAULT_PDF_OPTIONS, index=i)
            for i, html in enumerate(html_list)
        ]
        
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
                    "error_type": (
                        "timeout" if isinstance(result, TimeoutError)
                        else type(result).__name__
                    )
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
                detail={
                    "message": "All PDF conversions failed",
                    "total": total_items,
                    "errors": errors
                }
            )
        
        if return_format == "zip":
            return _create_zip_response(successful_pdfs, error_count, success_count)
        else:
            return _create_json_response(successful_pdfs, errors, total_items, error_count, success_count)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch conversion error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process batch conversion: {str(e)}"
        )


def _create_zip_response(
    successful_pdfs: Dict[int, bytes],
    error_count: int,
    success_count: int
) -> Response:
    """Create ZIP file response from successful PDFs."""
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
        if error_count > 0:
            headers["X-Conversion-Errors"] = str(error_count)
            headers["X-Conversion-Success"] = str(success_count)
        
        logger.info("Returning ZIP response")
        return Response(content=zip_data, media_type="application/zip", headers=headers)
    finally:
        zip_buffer.close()


def _create_json_response(
    successful_pdfs: Dict[int, bytes],
    errors: List[Dict[str, Any]],
    total_items: int,
    error_count: int,
    success_count: int
) -> JSONResponse:
    """Create JSON response with base64 encoded PDFs."""
    logger.info(f"Encoding {success_count} PDFs to base64")
    pdfs_base64 = {
        idx: base64.b64encode(pdf_bytes).decode('utf-8')
        for idx, pdf_bytes in successful_pdfs.items()
    }
    
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

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
