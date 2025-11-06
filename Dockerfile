# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Create necessary directories for data, logs, and outputs within the image.
RUN mkdir -p /app/data /app/logs

# Copy the requirements file into the container
# This is done first to leverage Docker's layer caching.
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# --no-cache-dir reduces image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container
COPY ./socialosintagent ./socialosintagent

# Set the entrypoint to run the main module
CMD ["python", "-m", "socialosintagent.main"]