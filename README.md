# GROBID Software (Mentions and Context) Processing

This repository contains the scripts for deploying and managing a
processing pipeline which takes a list of article DOIs,
attempts to download the PDF of each article, extracts all software mentions
using [GROBID](https://github.com/softcite/software-mentions) and then
classify each mention using it's context as a either "created", "used", "mentioned"
or "other."

**The pipeline is set up to run on a GCP VM (or a local Linux machine).**

## GCP Setup

1. Clone this repository: `git clone https://github.com/evamaxfield/grobid-soft-proc.git`
2. Install gcloud CLI: [https://cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install)
3. Install just: [https://just.systems/man/en/chapter_4.html](https://just.systems/man/en/chapter_4.html)
4. Login to gcloud: `just gcp-login`
5. **Optional:** Create a new GCP project:
    1. Edit the [Justfile](Justfile) and change the `default_project` variable to an entirely new GCP project name.
    2. Run `just gcp-project-create`
6. Enable VM and Compute Services: `just gcp-services-enable`

## VM Management

Note: this repository is currently setup to only allow a single VM to exist (started or stopped) at a time.

### Create and Setup

1. Create a new VM: `just gcp-vm-create` (with GPU: `just gcp-vm-create --gpu=true`)
2. Copy over the necessary files: `just gcp-vm-copy-all`
2. SSH into the VM: `just gcp-vm-connect`
3. While in an SSH session, run the very basic setup script: `. setup.sh`
4. While in an SSH session and after step three, install heavier dependencies and larger setup: `just gcp-vm-setup` (with GPU: `just gcp-vm-setup --gpu=true`)

### All VM Commands

```
gcp-vm-connect           # connect to existing gcp vm
gcp-vm-copy-all          # copy all (data and src)
gcp-vm-copy-data         # copy data directory to VM
gcp-vm-copy-src          # copy src, config, and scripts to VM
gcp-vm-create gpu="false" project=default_project # create new gcp vm
gcp-vm-delete            # delete the gcp vm
gcp-vm-setup gpu="false" # setup the newly created vm and start the grobid docker container
gcp-vm-start             # start the gcp vm
gcp-vm-stop              # stop the gcp vm
```

All Just commands must be done from outside the VM and disconnected from an SSH session.

## Docker Management

**Whether within GCP VM or locally on a Linux machine**, you can use the following commands to
manage the GROBID Docker container used for processing.

```
docker-delete            # delete the docker container
docker-logs              # get the command to run to watch the logs of the running container
docker-pull              # pull the docker image
docker-start gpu="false" # create and start the docker container
docker-stop              # stop the docker container
```

Note: this repository is currently setup to only allow a single Docker container to exist (started or stopped) at a time.