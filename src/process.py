#!/usr/bin/env python

from pathlib import Path
from hashlib import sha256
import json
import math
from datetime import datetime
from dataclasses import dataclass

from software_mentions_client.client import software_mentions_client
import typer
import pandas as pd
from prefect import Flow, unmapped, task
from prefect.task_runners import SequentialTaskRunner
from prefect_dask.task_runners import DaskTaskRunner
from tqdm import tqdm

###############################################################################

app = typer.Typer()

###############################################################################

DEFAULT_CONFIG_PATH = Path("software-mentions.config.json")
TEMP_DIR = Path("grobid-annotations-temp-dir/")
RESULTS_DIR = Path("grobid-annotations-results-dir/")

###############################################################################


@dataclass
class ErrorResult:
    doi: str
    doi_hash: str
    step: str
    error: str


@dataclass
class PDFURLResult:
    doi: str
    doi_hash: str
    pdf_url: str


@task
def _get_pdf_url_for_doi(doi: str) -> PDFURLResult | ErrorResult:
    try:
        # TODO: Implement this function
        return PDFURLResult(doi=doi, doi_hash="", pdf_url="")

    except Exception as e:
        return ErrorResult(
            doi=doi,
            doi_hash="",
            step="get-pdf-url-for-doi",
            error=str(e),
        )


@dataclass
class PDFDownloadResult:
    doi: str
    doi_hash: str
    pdf_url: str
    pdf_local_path: str


@task
def _download_pdf(data: PDFURLResult | ErrorResult) -> PDFDownloadResult | ErrorResult:
    if isinstance(data, ErrorResult):
        return data

    try:
        # TODO: Implement this function
        return PDFDownloadResult(
            doi=data.doi,
            doi_hash=data.doi_hash,
            pdf_url=data.pdf_url,
            pdf_local_path="",
        )

    except Exception as e:
        return ErrorResult(
            doi=data.doi,
            doi_hash="",
            step="download-pdf",
            error=str(e),
        )


@dataclass
class PDFAnnotationResult:
    doi: str
    doi_hash: str
    pdf_url: str
    annotation_storage_path: str


@task
def _annotate_pdf(
    data: PDFDownloadResult | ErrorResult,
    temp_working_dir: Path,
    config_path: Path,
) -> PDFAnnotationResult | ErrorResult:
    if isinstance(data, ErrorResult):
        return data

    try:
        # Init client
        client = software_mentions_client(config_path=config_path)

        # Create temporary output path for this PDF
        tmp_output_path = temp_working_dir / f"{data.doi_hash}.json"

        # Annotate PDF
        client.annotate(
            file_in=data.pdf_local_path,
            file_out=tmp_output_path,
            full_record=None,
        )

        # TODO: copy to GCP storage

        # Store the path to the annotation
        return PDFAnnotationResult(
            doi=data.doi,
            doi_hash=data.doi_hash,
            pdf_url=data.pdf_url,
            annotation_storage_path=str(tmp_output_path),
        )

    except Exception as e:
        return ErrorResult(
            doi=data.doi,
            doi_hash=data.doi_hash,
            step="annotate-pdf",
            error=str(e),
        )

    finally:
        # Clean up the temporary output path
        if tmp_output_path.exists():
            tmp_output_path.unlink()


def _store_batch_results(
    results: list[PDFAnnotationResult | ErrorResult],
    batch_id: int,
    results_dir: Path,
) -> None:
    # Separate the results into successful and failed
    successful_results = pd.DataFrame(
        [r for r in results if not isinstance(r, ErrorResult)]
    )
    failed_results = pd.DataFrame([r for r in results if isinstance(r, ErrorResult)])

    # Store the results
    successful_results.to_csv(
        results_dir / f"successful-results-{batch_id}.csv", index=False
    )
    failed_results.to_csv(results_dir / f"failed-results-{batch_id}.csv", index=False)


def _download_annotate_for_software_from_doi_pipeline(
    pdf_download_results: list[PDFDownloadResult],
    results_dir: Path,
    temp_working_dir: Path,
    config_path: Path,
) -> None:
    # Read config and get batch size
    with open(config_path, "r") as f:
        config = json.load(f)
        batch_size = config.get("batch_size", 50)

    # Create chunks of batch_size of the results to process
    n_batches = math.ceil(len(pdf_download_results) / batch_size)
    for i in tqdm(
        range(0, len(pdf_download_results), batch_size),
        desc="Batches",
        total=n_batches,
    ):
        chunk = pdf_download_results[i : i + batch_size]

        # Handle any timeouts and such
        try:
            # Annotate PDFs
            pdf_annotation_results = _annotate_pdf.map(
                data=chunk,
                temp_working_dir=unmapped(temp_working_dir),
                config_path=unmapped(config_path),
            )

            # Store batch results
            _store_batch_results(
                results=[f.result() for f in pdf_annotation_results],
                batch_id=i,
                results_dir=results_dir,
            )

        except Exception as e:
            print(f"Error processing batch {i}: {e}")


###############################################################################


@app.command()
def process(
    csv_path: Path = typer.Argument(..., help="Path to the CSV file for processing."),
    results_dir: Path = typer.Argument(
        RESULTS_DIR,
        help=(
            "Path to the directory to store results. "
            "We will always create a subdirectory from this parent "
            "with the same name as the input CSV file."
        ),
    ),
    temp_working_dir: Path = typer.Argument(
        TEMP_DIR, help="Path to the temporary working directory."
    ),
    use_dask: bool = typer.Option(False, help="Use Dask for parallel processing."),
    config_path: Path = typer.Argument(
        DEFAULT_CONFIG_PATH, help="Path to the config file."
    ),
):
    # TODO: check that the data file has a unique name compared to existing in storage

    # Make temp storage directory
    temp_working_dir.mkdir(exist_ok=True)

    # Make results storage directory
    this_run_results_dir = results_dir / csv_path.stem
    this_run_results_dir.mkdir(exist_ok=True, parents=True)

    # Init client
    print("Initializing Software Annotation Client...")
    client = software_mentions_client(config_path=config_path)

    # Check that the service is alive
    is_alive = client.service_isalive()
    if not is_alive:
        raise RuntimeError("Software Annotation Service is not alive.")
    else:
        print("Software Annotation Service is alive and ready.")

    csv_path = Path(csv_path).resolve(strict=True)
    print(f"Reading '{csv_path}'...")
    df = pd.read_csv(csv_path)

    # Construct pairs to pass to construct the pipeline
    pdf_url_results = [
        PDFDownloadResult(
            doi=row["doi"],
            doi_hash=sha256(row["doi"].encode()).hexdigest(),
            pdf_url="",
            pdf_local_path=str(Path(row["pdf_path"]).resolve(strict=True).absolute()),
        )
        for _, row in df.iterrows()
    ]

    # If using dask, use DaskTaskRunner
    if use_dask:
        task_runner = DaskTaskRunner(
            cluster_class="distributed.LocalCluster",
            cluster_kwargs={
                "threads_per_worker": 1,
                "processes": False,
            },
        )
    else:
        task_runner = SequentialTaskRunner()

    # Construct the pipeline
    pipeline = Flow(
        _download_annotate_for_software_from_doi_pipeline,
        name="download-annotate-for-software-from-doi-pipeline",
        task_runner=task_runner,
        log_prints=True,
    )

    # Keep track of duration
    start_dt = datetime.now()
    start_dt = start_dt.replace(microsecond=0)

    # Start the flow
    pipeline(
        pdf_download_results=pdf_url_results,
        results_dir=this_run_results_dir,
        temp_working_dir=temp_working_dir,
        config_path=config_path,
    )

    # End duration
    end_dt = datetime.now()
    end_dt = end_dt.replace(microsecond=0)

    # Log time taken
    print(f"Total Processing Duration: {end_dt - start_dt}")


###############################################################################

if __name__ == "__main__":
    app()
