# HTML to PDF Microservice

A modern Python microservice for converting HTML content to PDF documents using Playwright (headless Chrome).

## Features

- Convert HTML strings to PDF bytes
- Support for custom PDF options (format, margins, landscape, etc.)
- RESTful API with JSON and raw HTML input support
- Health check endpoint
- FastAPI with automatic API documentation
- Async/await support for high performance
- Error handling

## Installation

### Prerequisites

- Python 3.11 or higher
- pip (Python package manager)

### Setup

1. **Install Python dependencies:**
```bash
pip install -r requirements.txt
```

2. **Install Playwright browsers:**
```bash
playwright install chromium
```

## Usage

### Start the server

```bash
python server.py
```

Or using uvicorn directly:

```bash
uvicorn server:app --host 0.0.0.0 --port 3000
```

The server will start on port 3000 (or the port specified in the `PORT` environment variable).

### API Documentation

FastAPI provides automatic interactive API documentation:
- Swagger UI: `http://localhost:3000/docs`
- ReDoc: `http://localhost:3000/redoc`

### API Endpoints

#### Health Check
```
GET /health
```

Returns service status.

#### Convert HTML to PDF
```
POST /convert
```

**Request Body Options:**

1. **JSON with HTML field:**
```json
{
  "html": "<html><body><h1>Hello World</h1></body></html>",
  "format": "A4",
  "margin": {
    "top": "1cm",
    "right": "1cm",
    "bottom": "1cm",
    "left": "1cm"
  },
  "print_background": true,
  "landscape": false,
  "scale": 1.0
}
```

2. **Raw HTML string:**
```
Content-Type: text/html

<html><body><h1>Hello World</h1></body></html>
```

**Response:**
- Content-Type: `application/pdf`
- Body: PDF file bytes

**PDF Options:**
- `format`: Paper format (A4, Letter, etc.) - default: "A4"
- `margin`: Object with top, right, bottom, left margins - default: 1cm each
- `print_background`: Include background graphics - default: true
- `landscape`: Landscape orientation - default: false
- `scale`: Scale factor - default: 1.0

## Example Usage

### Using cURL

```bash
# JSON format
curl -X POST http://localhost:3000/convert \
  -H "Content-Type: application/json" \
  -d '{"html":"<html><body><h1>Hello PDF</h1></body></html>"}' \
  --output output.pdf

# Raw HTML
curl -X POST http://localhost:3000/convert \
  -H "Content-Type: text/html" \
  -d "<html><body><h1>Hello PDF</h1></body></html>" \
  --output output.pdf
```

### Using JavaScript (fetch)

```javascript
const html = '<html><body><h1>Hello World</h1></body></html>';

const response = await fetch('http://localhost:3000/convert', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ html })
});

const pdfBytes = await response.arrayBuffer();
// Use pdfBytes as needed
```

### Using Python (requests)

```python
import requests

html = '<html><body><h1>Hello World</h1></body></html>'

response = requests.post(
    'http://localhost:3000/convert',
    json={'html': html}
)

with open('output.pdf', 'wb') as f:
    f.write(response.content)
```

## Docker

A Dockerfile is included for containerization:

```bash
docker build -t html-to-pdf-service .
docker run -p 3000:3000 html-to-pdf-service
```

The Dockerfile automatically installs Playwright and all required dependencies.

## Environment Variables

- `PORT`: Server port (default: 3000)

## Error Handling

The service returns appropriate HTTP status codes:
- `200`: Success
- `400`: Bad request (missing or invalid HTML)
- `500`: Internal server error

## Development

### Running in Development Mode

```bash
uvicorn server:app --reload --host 0.0.0.0 --port 3000
```

### Testing

You can test the API using the interactive Swagger documentation at `http://localhost:3000/docs`.

## License

MIT
