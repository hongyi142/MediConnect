# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your microservice code and the credentials
COPY . .

# Expose the port your Flask app runs on (from your code)
EXPOSE 5005

# Command to run your application
CMD ["python", "inventory.py"]