# list all available commands
default:
  just --list

###############################################################################
# Infra

# Default region for infrastructures
default_region := "us-central1"
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
gcp-project-init project=default_project:
	gcloud projects create {{project}} --set-as-default
	echo "----------------------------------------------------------------------------"
	echo "Follow the link to setup billing for the created GCloud account."
	echo "https://console.cloud.google.com/billing/linkedaccount?project={{project}}"
	echo "----------------------------------------------------------------------------"
	just gen-key {{project}}

# switch active gcloud project
gcp-switch-project project=default_project:
	gcloud config set project {{project}}

# enable gcloud services
gcp-enable-services project=default_project:
	gcloud services enable cloudresourcemanager.googleapis.com
	gcloud services enable \
		compute.googleapis.com \
		logging.googleapis.com \
		monitoring.googleapis.com

# TODO: figure out if needed
# --service-account=488424689517-compute@developer.gserviceaccount.com \

vm_uuid := replace_regex(uuid(), "([a-z0-9]{8})(.*)", "$1")

# create new gcp vm
gcp-create-vm gpu="true":
    {{ if path_exists(".gcp-vm-id") == "true" { error("there may already be an existing gcp vm") } else { "" } }}
    gcloud compute instances create grobid-soft-proc-{{vm_uuid}} \
        --project=sci-software-graph \
        --zone=us-west1-b \
        --machine-type=n1-standard-8 \
        --network-interface=network-tier=PREMIUM,stack-type=IPV4_ONLY,subnet=default \
        --maintenance-policy=TERMINATE \
        --provisioning-model=STANDARD \
        --scopes=https://www.googleapis.com/auth/devstorage.read_only,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/trace.append \
        {{ if gpu == "true" { "--accelerator=count=1,type=nvidia-tesla-t4" } else { "" } }} \
        --create-disk=auto-delete=yes,boot=yes,device-name=instance-20240530-220503,image=projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20240523a,mode=rw,size=64,type=projects/sci-software-graph/zones/us-west1-b/diskTypes/pd-balanced \
        --no-shielded-secure-boot \
        --shielded-vtpm \
        --shielded-integrity-monitoring \
        --labels=goog-ec-src=vm_add-gcloud \
        --reservation-affinity=any
    sleep 30
    echo "grobid-soft-proc-{{vm_uuid}}" >> .gcp-vm-id

# stop the gcp vm
gcp-stop-vm:
    {{ if path_exists(".gcp-vm-id") != "true" { error("there may not be an existing gcp vm") } else { "" } }}
    gcp_vm_id=$(cat .gcp-vm-id) && gcloud compute instances stop $gcp_vm_id

# start the gcp vm
gcp-start-vm:
    {{ if path_exists(".gcp-vm-id") != "true" { error("there may not be an existing gcp vm") } else { "" } }}
    gcp_vm_id=$(cat .gcp-vm-id) && gcloud compute instances start $gcp_vm_id

# delete the gcp vm
gcp-delete-vm:
    {{ if path_exists(".gcp-vm-id") != "true" { error("there may not be an existing gcp vm") } else { "" } }}
    gcp_vm_id=$(cat .gcp-vm-id) && gcloud compute instances delete $gcp_vm_id
    rm .gcp-vm-id

# connect to existing gcp vm
gcp-connect-vm:
    {{ if path_exists(".gcp-vm-id") != "true" { error("there may not be an existing gcp vm") } else { "" } }}
    gcp_vm_id=$(cat .gcp-vm-id) && gcloud compute scp --recurse ./ $gcp_vm_id:~/ 
    gcp_vm_id=$(cat .gcp-vm-id) && gcloud compute ssh $gcp_vm_id

# setup the newly created vm
gcp-setup-vm:
    sudo apt-get update
    sudo apt-get install ca-certificates curl -y
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update

    sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin -y

###############################################################################
# Docker management

# create and start docker services
docker-up:
    sudo docker compose up

# start docker services
docker-start:
    sudo docker compose start

# stop docker services
docker-stop:
    sudo docker compose stop

# remove docker services
docker-down:
    sudo dockercompose down

# connect to processing container
docker-exec:
    sudo docker exec -it software-mentions-processing /bin/bash