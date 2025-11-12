# Use Playwright's official Python image which includes all system dependencies
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies (Playwright is already installed in base image)
RUN pip install --no-cache-dir -r requirements.txt

# Chromium is already installed in the base image, but ensure it's up to date
RUN playwright install chromium

# Copy application files
COPY . .

# Expose port
EXPOSE 3000

# Set environment variable
ENV PORT=3000

# Start the application
CMD ["python", "server.py"]
