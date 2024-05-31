FROM python:3.11.8-slim

# Allow statements and log messages to immediately appear in the Knative logs
ENV PYTHONUNBUFFERED True

# Configure extra apt packages
RUN apt-get update && apt-get install curl vim -y

# Configure space
WORKDIR /soft-proc
COPY resources/ /soft-proc/resources/

# Install Python dependencies
RUN pip install -r /soft-proc/resources/requirements.txt