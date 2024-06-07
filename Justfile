# list all available commands
default:
  just --list

# install deps for development
install:
    pip install -r {{justfile_directory()}}/src/requirements.txt
    pip install -r {{justfile_directory()}}/requirements-dev.txt

# lint, format, and check all files
lint:
	pre-commit run --all-files

###############################################################################
# Infra

# Default region for infrastructures
key_guid := replace_regex(uuid(), "([a-z0-9]{8})(.*)", "$1")
default_key := justfile_directory() + "/.keys/dev.json"

# Edit this for changing the project name
default_project := "sci-software-graph"

# run gcloud login
gcp-login:
  gcloud auth login
  gcloud auth application-default login

# generate a service account JSON
gcp-gen-key project=default_project:
	mkdir -p {{justfile_directory()}}/.keys/
	gcloud iam service-accounts create {{project}}-{{key_guid}} \
		--description="Dev Service Account {{key_guid}}" \
		--display-name="{{project}}-{{key_guid}}"
	gcloud projects add-iam-policy-binding {{project}} \
		--member="serviceAccount:{{project}}-{{key_guid}}@{{project}}.iam.gserviceaccount.com" \
		--role="roles/owner"
	gcloud iam service-accounts keys create {{justfile_directory()}}/.keys/{{project}}-{{key_guid}}.json \
		--iam-account "{{project}}-{{key_guid}}@{{project}}.iam.gserviceaccount.com"
	cp -rf {{justfile_directory()}}/.keys/{{project}}-{{key_guid}}.json {{default_key}}
	@ echo "----------------------------------------------------------------------------"
	@ echo "Sleeping for thirty seconds while resources set up"
	@ echo "----------------------------------------------------------------------------"
	sleep 30
	@ echo "Be sure to update the GOOGLE_APPLICATION_CREDENTIALS environment variable."
	@ echo "----------------------------------------------------------------------------"

# create a new gcloud project and generate a key
gcp-project-create project=default_project:
	gcloud projects create {{project}} --set-as-default
	echo "----------------------------------------------------------------------------"
	echo "Follow the link to setup billing for the created GCloud account."
	echo "https://console.cloud.google.com/billing/linkedaccount?project={{project}}"
	echo "----------------------------------------------------------------------------"
	just gen-key {{project}}

# switch active gcloud project
gcp-project-switch project=default_project:
	gcloud config set project {{project}}

# enable gcloud services
gcp-services-enable project=default_project:
    gcloud services enable cloudresourcemanager.googleapis.com
    gcloud services enable \
        compute.googleapis.com \
        logging.googleapis.com \
        monitoring.googleapis.com \

vm_uuid := replace_regex(uuid(), "([a-z0-9]{8})(.*)", "$1")

# create new gcp vm
gcp-vm-create gpu="false" project=default_project:
    {{ if path_exists(".gcp-vm-id") == "true" { error("there may already be an existing gcp vm") } else { "" } }}
    gcloud compute instances create grobid-soft-proc-{{vm_uuid}} \
        --project={{project}} \
        --zone=us-west1-b \
        --machine-type=n1-standard-8 \
        --network-interface=network-tier=PREMIUM,stack-type=IPV4_ONLY,subnet=default \
        --maintenance-policy=TERMINATE \
        --provisioning-model=STANDARD \
        --scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/trace.append \
        {{ if gpu == "true" { "--accelerator=count=1,type=nvidia-tesla-t4" } else { "" } }} \
        --create-disk=auto-delete=yes,boot=yes,device-name=grobid-soft-proc-{{vm_uuid}},image=projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20240523a,mode=rw,size=128,type=projects/{{project}}/zones/us-west1-b/diskTypes/pd-balanced \
        --no-shielded-secure-boot \
        --shielded-vtpm \
        --shielded-integrity-monitoring \
        --labels=goog-ec-src=vm_add-gcloud \
        --reservation-affinity=any
    sleep 15
    echo "grobid-soft-proc-{{vm_uuid}}" >> .gcp-vm-id

# check for .gcp-vm-id file
gcp-vm-check-exists:
    {{ if path_exists(".gcp-vm-id") != "true" { error("there may not be an existing gcp vm") } else { "" } }}

# stop the gcp vm
gcp-vm-stop:
    just gcp-vm-check-exists
    gcp_vm_id=$(cat .gcp-vm-id) && gcloud compute instances stop $gcp_vm_id

# start the gcp vm
gcp-vm-start:
    just gcp-vm-check-exists
    gcp_vm_id=$(cat .gcp-vm-id) && gcloud compute instances start $gcp_vm_id
    sleep 15

# delete the gcp vm
gcp-vm-delete:
    just gcp-vm-check-exists
    gcp_vm_id=$(cat .gcp-vm-id) && gcloud compute instances delete $gcp_vm_id
    rm .gcp-vm-id

# copy src, config, data, and scripts to VM
gcp-vm-copy-files:
    just gcp-vm-check-exists
    gcp_vm_id=$(cat .gcp-vm-id) \
        && gcloud compute scp --recurse ./src/* $gcp_vm_id:~/ \
        && gcloud compute scp Justfile $gcp_vm_id:~/ \
        && gcloud compute scp .env $gcp_vm_id:~/ \
        && gcloud compute scp --recurse ./data/ $gcp_vm_id:~/

# connect to existing gcp vm
gcp-vm-connect:
    just gcp-vm-check-exists
    gcp_vm_id=$(cat .gcp-vm-id) && gcloud compute ssh $gcp_vm_id

# setup the newly created vm and start the grobid docker container
gcp-vm-setup gpu="false":
    # docker setup
    sudo apt-get update
    sudo apt-get install ca-certificates curl bzip2 -y
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update
    sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin -y

    # python setup
    curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj bin/micromamba
    mkdir -p /usr/local/bin
    sudo mv bin/micromamba /usr/local/bin && rm -rf bin
    /usr/local/bin/micromamba shell init -s bash -p ~/micromamba
    micromamba env create -f environment.yml -y
    
    # init docker grobid image
    just docker-pull
    just docker-init --gpu={{gpu}}

###############################################################################
# Docker management

# pull the docker image
docker-pull:
    sudo docker pull grobid/software-mentions:0.8.0

# check for .docker-container-id file
docker-check-exists:
    {{ if path_exists(".docker-container-id") != "true" { error("there may not be an existing docker container") } else { "" } }}

# hidden command to sleep and check services
docker-check-services:
    curl http://localhost:8060/service/isalive
    @ echo "Processing example PDF to ensure GROBID service is warm. Process won't release until response."
    curl --form input=@./example.pdf --form disambiguate=1 http://localhost:8060/service/annotateSoftwarePDF

# create and start the docker container
docker-init gpu="false":
    {{ if path_exists(".docker-container-id") == "true" { error("there may already be an existing docker container") } else { "" } }}
    {{ if gpu == "true" { `sudo docker run --gpus all -d --ulimit core=0 -p 8060:8060 grobid/software-mentions:0.8.0 >> .docker-container-id` } else { `sudo docker run -d --ulimit core=0 -p 8060:8060 grobid/software-mentions:0.8.0 >> .docker-container-id` } }}
    sleep 10
    just docker-check-services

# start the stopped docker container
docker-start:
    just docker-check-exists
    docker_container_id=$(cat .docker-container-id) && sudo docker start $docker_container_id
    sleep 10
    just docker-check-services

# stop the docker container
docker-stop:
    just docker-check-exists
    docker_container_id=$(cat .docker-container-id) && sudo docker stop $docker_container_id

# delete the docker container
docker-delete:
    just docker-check-exists
    just docker-stop
    docker_container_id=$(cat .docker-container-id) && sudo docker rm $docker_container_id
    rm .docker-container-id

# get the command to run to watch the logs of the running container
docker-logs:
    just docker-check-exists
    @docker_container_id=$(cat .docker-container-id) && echo "Watch logs with: sudo docker logs -f $docker_container_id"