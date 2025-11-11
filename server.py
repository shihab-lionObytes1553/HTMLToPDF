from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
import os
from playwright.async_api import async_playwright
import json

app = FastAPI(title="HTML to PDF Microservice", version="1.0.0")

DEFAULT_PDF_OPTIONS = {
    "format": "A4",
    "margin": {
        "top": "2cm",
        "right": "2cm",
        "bottom": "2cm",
        "left": "2cm"
    },
    "print_background": True,
    "landscape": False,
    "scale": 1.0
}

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
                        "detail": [
                            {
                                "type": "json_invalid",
                                "loc": error.get("loc", []),
                                "msg": "Invalid JSON: HTML content contains unescaped control characters. "
                                       "Make sure your HTML is properly escaped in JSON.",
                                "input": error.get("input", {})
                            }
                        ]
                    }
                )
    
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(errors)}
    )

class HTMLRequest(BaseModel):
    html: str = Field(..., description="HTML content to convert to PDF")

class RawHTMLRequest(BaseModel):
    html_content: str = Field(..., description="Raw HTML content to convert to PDF")

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": "html-to-pdf-microservice"}

async def _convert_html_to_pdf(html_content: str, pdf_options: dict):
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--no-first-run",
                    "--disable-gpu"
                ]
            )
            
            page = await browser.new_page()
            await page.set_content(html_content, wait_until="networkidle")
            
            playwright_pdf_options = {
                "format": pdf_options["format"],
                "margin": pdf_options["margin"],
                "print_background": pdf_options["print_background"],
                "scale": pdf_options["scale"],
                "display_header_footer": True,
                "header_template": '<div></div>',
                "footer_template": '<div style="font-size: 10px; padding: 0; margin: 0; width: 100%; text-align: center; color: rgb(0, 0, 0); font-family: Arial, sans-serif;"><span>© 2025 LionOBytes — Innovating the future. All rights reserved.</span></div>'
            }
            
            if pdf_options["landscape"]:
                playwright_pdf_options["landscape"] = True
            
            pdf_bytes = await page.pdf(**playwright_pdf_options)
            await browser.close()

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": "attachment; filename=document.pdf",
                "Content-Length": str(len(pdf_bytes))
            }
        )
    except Exception as error:
        if browser:
            try:
                await browser.close()
            except:
                pass
        raise error

@app.post("/convert", response_class=Response, tags=["PDF"])
async def convert_html_to_pdf(request_data: HTMLRequest):
    try:
        html_content = request_data.html
        
        if not html_content or not html_content.strip():
            raise HTTPException(status_code=400, detail="HTML content is required")
        
        return await _convert_html_to_pdf(html_content, DEFAULT_PDF_OPTIONS)
            
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert HTML to PDF: {str(error)}"
        )

@app.post("/convert-raw", response_class=Response, tags=["PDF"])
async def convert_html_to_pdf_raw(request_data: RawHTMLRequest):
    try:
        html_content = request_data.html_content
        
        if not html_content or not html_content.strip():
            raise HTTPException(status_code=400, detail="HTML content is required")
        
        return await _convert_html_to_pdf(html_content, DEFAULT_PDF_OPTIONS)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert HTML to PDF: {str(error)}"
        )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
