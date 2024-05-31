# GROBID Software (Mentions and Context) Processing

This repository contains the scripts for deploying and managing a
processing pipeline which takes a list of article DOIs,
attempts to download the PDF of each article, extracts all software mentions
using [GROBID](https://github.com/softcite/software-mentions) and then
classify each mention using it's context as a either "created", "used", "mentioned"
or "other."

The pipeline is set up to run on a GCP VM (or a local Linux machine). It will not work
on Mac or Windows.

## Setup GCP

1. Clone this repository: `git clone https://github.com/evamaxfield/grobid-soft-proc.git`
2. Install gcloud CLI: [https://cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install)
3. Install just: [https://just.systems/man/en/chapter_4.html](https://just.systems/man/en/chapter_4.html)
4. Login to gcloud: `just gcp-login`
5. **Optional:** Create a new GCP project:
    1. Edit the [Justfile](Justfile) and change the `default_project` variable to an entirely new GCP project name.
    2. Run `just gcp-project-init`
6. Enable VM and Compute Services: `just gcp-enable-services`

## VM Management

### Create and Setup

1. Create a new VM: `just gcp-create-vm`
2. SSH into the VM: `just gcp-connect-vm`
3. Run the very basic setup script: `. setup.sh`
4. Install heavier dependencies and larger setup: `just gcp-setup-vm`

### Other Commands

* Start the VM: `just gcp-start-vm`
* Stop the VM: `just gcp-stop-vm`
* Delete the VM: `just gcp-delete-vm`

## Docker Management

**Whether within GCP VM or locally on a Linux machine**, you can use the following commands to
manage docker containers used for processing.

* Build containers for the first time and/or start: `just docker-up`
* Start existing containers: `just docker-start`
* Stop running containers: `just docker-stop`
* Remove containers: `just docker-down`