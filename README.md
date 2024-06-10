# GROBID Software (Mentions and Context) Processing

This repository contains the scripts for deploying and managing a
processing pipeline which takes a list of article DOIs,
attempts to download the PDF of each article, and extracts all software mentions
using [GROBID](https://github.com/softcite/software-mentions) and finally,
while GROBID provides it's own prediction as to the intent of the mention, 
we also predict the intent of the mention based off of the CZI Soft Intent model.

**The pipeline is set up to run on a GCP VM (or a local Linux machine).**

## Project Setup

1. Clone this repository: `git clone https://github.com/evamaxfield/grobid-soft-proc.git`
2. Create a `.env` file with "OPENALEX_EMAIL" and "S2_API_KEY" environment variables.
    1. `OPENALEX_EMAIL` is a .edu email address which allows for more API requests to the OpenAlex API.
    2. `S2_API_KEY` is the API key for the Semantic Scholar API.
    3. This file will be copied over to the GCP VM and used by the processing scripts but is by default ignored by git.
3. Install gcloud CLI: [https://cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install)
4. Install just: [https://just.systems/man/en/chapter_4.html](https://just.systems/man/en/chapter_4.html)
5. Login to gcloud: `just gcp-login`
6. Edit the [Justfile](Justfile) and change the `default_project` and `default_storage_bucket_name` variables to the names you would like to use. You can use existing projects and buckets but it is recommended to create new ones.
    1. Additionally, update the `GCP_STORAGE_BUCKET` variable in the `src/process.py` file to match the bucket name you would like to use.
7. **Optional:** If creating a new project, run: `just gcp-project-create`
8. Enable VM, Compute, and Storage Services: `just gcp-services-enable`

## Create and Setup the VM

Note: this repository is currently setup to only allow a single VM to exist (start or stopped) at a time.

1. Create a new VM: `just gcp-vm-create` (with GPU: `just gcp-vm-create --gpu=true`)
2. Copy over the necessary files: `just gcp-vm-copy-files`
3. SSH into the VM: `just gcp-vm-connect`
4. While in an SSH session, run the very basic setup script: `. setup.sh`
5. While in an SSH session and after step four, install heavier dependencies and larger setup: `just gcp-vm-setup` (with GPU: `just gcp-vm-setup --gpu=true`)

## Process Articles

1. While in an SSH session to the VM, activate the created micromamba environment: `micromamba activate soft-proc`
2. Run the processing script: `python process.py --csv-path data/example-dois.csv`

This will try and find open access PDFs for each DOI, download those PDFs to the VM, extract software mentions using GROBID, and push all of the results (successful and unsuccessful) to a Google Cloud Storage bucket.

To copy a new CSV file to the VM, add it to your `data/` directory and run `just gcp-vm-copy-files`, it will be copied over for use in the processing script.

The results of the processing will be stored in the Google Cloud Storage bucket you specified in the `Justfile` and `src/process.py` file. The bucket structure will have the following structure:

```
.
├── a-different-csv-file-name  # the name of the CSV file used for processing
│   ├── failed-results
│   │   └── batch-0.parquet
│   └── successful-results
│       └── batch-0.parquet
└── csv-file-name
    ├── failed-results
    │   └── batch-0.parquet
    └── successful-results
        └── batch-0.parquet
```

The `successful-results` directory will contain CSV files with the following columns:

- doi
- doi_hash
- pdf_url
- api_source
- context
- mention_type
- software_type
- software_name_raw
- software_name_normalized
- software_name_offset_start
- software_name_offset_end
- grobid_intent_cls
- grobid_intent_score
- czi_soft_cite_intent_cls
- czi_soft_cite_intent_score

The `failed-results` directory will contain CSV files with the following columns:

- doi
- doi_hash
- step
- error

Possible error values can include simple filtering tasks such as not finding an open access PDF to download or a Python error that occurred during one of the processing steps.

## Stopping and Restarting the VM (and Docker)

1. Stop the VM: `just gcp-vm-stop`
2. Start the VM: `just gcp-vm-start`
3. Start the Docker container: `just docker-start`

## All Commands

### VM Commands

```
gcp-vm-connect           # connect to existing gcp vm
gcp-vm-copy-files        # copy src, config, data, and scripts to VM
gcp-vm-create gpu="false" project=default_project # create new gcp vm
gcp-vm-delete            # delete the gcp vm
gcp-vm-setup gpu="false" # setup the newly created vm and start the grobid docker container
gcp-vm-start             # start the gcp vm
gcp-vm-stop              # stop the gcp vm
```

All Just commands must be done from outside the VM and disconnected from an SSH session.

## Docker Commands

**Whether within GCP VM or locally on a Linux machine**, you can use the following commands to
manage the GROBID Docker container used for processing.

```
docker-check-services    # hidden command to sleep and check services
docker-delete            # delete the docker container
docker-init gpu="false"  # create and start the docker container
docker-logs              # get the command to run to watch the logs of the running container
docker-pull              # pull the docker image
docker-start             # start the stopped docker container
docker-stop              # stop the docker container
```

Note: this repository is currently setup to only allow a single Docker container to exist (started or stopped) at a time.