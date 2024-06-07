#!/usr/bin/env python

import pyalex
import msgspec
from datetime import date
import backoff
import time
import os
from semanticscholar import SemanticScholar
from semanticscholar.ApiRequester import ObjectNotFoundException
from pathlib import Path
import shutil
from hashlib import sha256
from dotenv import load_dotenv
import json
import math
import requests
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
# APIs


class OpenAlexAPICallStatus(msgspec.Struct):
    call_count: int
    current_date: date


OPEN_ALEX_API_CALL_STATUS_FILEPATH = (
    Path("~/.open_alex_api_call_status.msgpack").expanduser().resolve()
)


@backoff.on_exception(backoff.expo, Exception, max_time=5)
def _write_open_alex_api_call_status(current_status: OpenAlexAPICallStatus) -> None:
    """Write the API call status to disk."""
    OPEN_ALEX_MSGSPEC_ENCODER = msgspec.msgpack.Encoder()

    # Make dirs if needed
    OPEN_ALEX_API_CALL_STATUS_FILEPATH.parent.mkdir(parents=True, exist_ok=True)

    with open(OPEN_ALEX_API_CALL_STATUS_FILEPATH, "wb") as f:
        f.write(OPEN_ALEX_MSGSPEC_ENCODER.encode(current_status))


@backoff.on_exception(backoff.expo, Exception, max_time=5)
def _read_open_alex_api_call_status() -> OpenAlexAPICallStatus:
    """Read the API call status from disk."""
    OPEN_ALEX_MSGSPEC_DECODER = msgspec.msgpack.Decoder(type=OpenAlexAPICallStatus)

    with open(OPEN_ALEX_API_CALL_STATUS_FILEPATH, "rb") as f:
        current_status = OPEN_ALEX_MSGSPEC_DECODER.decode(f.read())

    # Check if current API status is out of date
    if current_status.current_date != date.today():
        current_status = OpenAlexAPICallStatus(
            call_count=0,
            current_date=date.today(),
        )
        _write_open_alex_api_call_status(current_status=current_status)

    return current_status


@backoff.on_exception(backoff.expo, Exception, max_time=5)
def _create_open_alex_api_call_status_file() -> None:
    """Reset the API call status."""
    # Check and setup call status
    if not OPEN_ALEX_API_CALL_STATUS_FILEPATH.exists():
        _write_open_alex_api_call_status(
            OpenAlexAPICallStatus(
                call_count=0,
                current_date=date.today(),
            )
        )


@backoff.on_exception(backoff.expo, Exception, max_time=5)
def _setup_open_alex_api(email: str | None) -> pyalex.Works:
    """Set up a pool of polite workers for OpenAlex."""
    # Add email for polite pool
    if email is not None:
        pyalex.config.email = email

    # Add retries
    pyalex.config.max_retries = 3
    pyalex.config.retry_backoff_factor = 0.5
    pyalex.config.retry_http_codes = [429, 500, 503]

    _create_open_alex_api_call_status_file()

    return pyalex.Works()


def _increment_open_alex_call_count_and_check() -> None:
    """Increment the API call count and check if we need to sleep."""
    # Read latest
    current_status = _read_open_alex_api_call_status()

    # Temporary log
    if current_status.call_count % 1000 == 0:
        print(f"OpenAlex Daily API call count: {current_status.call_count}")

    # If we've made 80,000 calls in a single day
    # pause all processing until tomorrow
    if current_status.call_count >= 80_000:
        print("Sleeping until tomorrow to avoid OpenAlex API limit.")
        while date.today() == current_status.current_date:
            time.sleep(600)

        # Reset the call count
        current_status = OpenAlexAPICallStatus(
            call_count=0,
            current_date=date.today(),
        )

    # Increment count
    current_status.call_count += 1
    _write_open_alex_api_call_status(current_status)

    # Sleep for a second to avoid rate limiting
    time.sleep(1)


def _get_pdf_url_from_open_alex(doi: str) -> str | None:
    """Get the PDF URL for a given DOI."""
    prepped_doi = f"https://doi.org/{doi}"

    # Get API
    OpenAlexWorksAPI = _setup_open_alex_api(email=os.environ["OPENALEX_EMAIL"])

    # Increment call count
    _increment_open_alex_call_count_and_check()

    # Get publication
    publication = OpenAlexWorksAPI[prepped_doi]

    # Check for "best_oa_location"
    if publication["best_oa_location"] is not None:
        # PDF is better
        if publication["best_oa_location"]["pdf_url"] is not None:
            return publication["best_oa_location"]["pdf_url"]

    return None


def _get_pdf_url_from_semantic_scholar(doi: str) -> str | None:
    """Get the PDF URL for a given DOI."""
    try:
        # Sleep to avoid rate limiting
        time.sleep(1)

        # Create API
        S2API = SemanticScholar(api_key=os.environ["S2_API_KEY"])

        # Request paper
        paper = S2API.get_paper(
            f"DOI:{doi}",
            fields=["openAccessPdf"],
        )

        if paper.openAccessPdf is not None:
            return paper.openAccessPdf["url"]

        return None

    except ObjectNotFoundException:
        return None


def _get_arxiv_pdf_url(doi: str) -> str | None:
    """Get the PDF URL for a given DOI."""
    if "arxiv" in doi:
        arxiv_id = doi.split("arxiv.")[1]
        return f"https://arxiv.org/pdf/{arxiv_id}"

    return None


@dataclass
class OpenAccessPDFResults:
    doi: str
    pdf_url: str
    api_source: str


def _get_open_access_pdf(doi: str) -> OpenAccessPDFResults | None:
    """Get the Open Access PDF for a given DOI."""
    # Ensure DOI only includes the DOI
    if "doi.org" in doi:
        doi = doi.split("doi.org/")[1]

    # Check for ArXiv DOI and short circuit
    pdf_url_or_none = _get_arxiv_pdf_url(doi=doi)
    if pdf_url_or_none is not None:
        return OpenAccessPDFResults(
            doi=doi,
            pdf_url=pdf_url_or_none,
            api_source="ArXiv",
        )

    # Check OpenAlex
    pdf_url_or_none = _get_pdf_url_from_open_alex(doi=doi)
    if pdf_url_or_none is not None:
        return OpenAccessPDFResults(
            doi=doi,
            pdf_url=pdf_url_or_none,
            api_source="OpenAlex",
        )

    # If None, check Semantic Scholar
    pdf_url_or_none = _get_pdf_url_from_semantic_scholar(doi=doi)
    if pdf_url_or_none is not None:
        return OpenAccessPDFResults(
            doi=doi,
            pdf_url=pdf_url_or_none,
            api_source="Semantic Scholar",
        )

    return None


###############################################################################
# Pipeline

DEFAULT_CONFIG_PATH = Path("software-mentions.config.json")
TEMP_DIR = Path("grobid-annotations-temp-dir/")
RESULTS_DIR = Path("grobid-annotations-results-dir/")


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
    api_source: str


@task
def _get_pdf_url_for_doi(doi: str) -> PDFURLResult | ErrorResult:
    # Create doi hash
    doi_hash = sha256(doi.encode()).hexdigest()

    try:
        # Get the Open Access PDF
        open_access_pdf_results = _get_open_access_pdf(doi=doi)
        if open_access_pdf_results is not None:
            return PDFURLResult(
                doi=open_access_pdf_results.doi,
                doi_hash=doi_hash,
                pdf_url=open_access_pdf_results.pdf_url,
                api_source=open_access_pdf_results.api_source,
            )

        return ErrorResult(
            doi=doi,
            doi_hash=doi_hash,
            step="get-pdf-url-for-doi",
            error="No PDF URL found.",
        )

    except Exception as e:
        return ErrorResult(
            doi=doi,
            doi_hash=doi_hash,
            step="get-pdf-url-for-doi",
            error=str(e),
        )


@dataclass
class PDFDownloadResult:
    doi: str
    doi_hash: str
    pdf_url: str
    api_source: str
    pdf_local_path: str


@task
def _download_pdf(
    data: PDFURLResult | ErrorResult, temp_working_dir: Path
) -> PDFDownloadResult | ErrorResult:
    if isinstance(data, ErrorResult):
        return data

    try:
        # Create temporary output path for this PDF
        tmp_output_path = temp_working_dir / f"{data.doi_hash}.pdf"

        # Resource copy
        with requests.get(data.pdf_url, stream=True, timeout=180) as response:
            response.raise_for_status()
            with open(tmp_output_path, "wb") as open_dst:
                shutil.copyfileobj(
                    response.raw, open_dst, length=64 * 1024 * 1024  # 64MB chunks
                )

        return PDFDownloadResult(
            doi=data.doi,
            doi_hash=data.doi_hash,
            pdf_url=data.pdf_url,
            api_source=data.api_source,
            pdf_local_path=str(tmp_output_path),
        )

    except Exception as e:
        return ErrorResult(
            doi=data.doi,
            doi_hash=data.doi_hash,
            step="download-pdf",
            error=str(e),
        )


@dataclass
class PDFAnnotationResult:
    doi: str
    doi_hash: str
    pdf_url: str
    api_source: str
    annotation_storage_path: str


@task(timeout_seconds=120, retries=3, retry_delay_seconds=60)
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

        # Create temporary output path for this JSON
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
            api_source=data.api_source,
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
        # Clean up the temporary output JSON
        if tmp_output_path.exists():
            tmp_output_path.unlink()

        # Clean up the temporary PDF
        if Path(data.pdf_local_path).exists():
            Path(data.pdf_local_path).unlink()


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
    doi_df: pd.DataFrame,
    results_dir: Path,
    temp_working_dir: Path,
    config_path: Path,
) -> None:
    # Read config and get batch size
    with open(config_path, "r") as f:
        config = json.load(f)
        batch_size = config.get("batch_size", 50)

    # Create chunks of batch_size of the results to process
    n_batches = math.ceil(len(doi_df) / batch_size)
    for i in tqdm(
        range(0, len(doi_df), batch_size),
        desc="Batches",
        total=n_batches,
    ):
        # Get chunk
        chunk = doi_df.iloc[i : i + batch_size]

        # Handle any timeouts and such
        try:
            # Get PDF URLs
            pdf_url_results = _get_pdf_url_for_doi.map(doi=chunk.doi.tolist())

            # Download PDFs
            pdf_download_results = _download_pdf.map(
                data=pdf_url_results,
                temp_working_dir=unmapped(temp_working_dir),
            )

            # Annotate PDFs
            pdf_annotation_results = _annotate_pdf.map(
                data=pdf_download_results,
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
    parallel: bool = typer.Option(True, help="Use Dask for parallel processing."),
    config_path: Path = typer.Argument(
        DEFAULT_CONFIG_PATH, help="Path to the config file."
    ),
):
    # TODO: check that the data file has a unique name compared to existing in storage
    # Load dotenv and check for OPENALEX_EMAIL and S2_API_KEY
    load_dotenv()
    if "OPENALEX_EMAIL" not in os.environ or "S2_API_KEY" not in os.environ:
        raise ValueError(
            "Please set OPENALEX_EMAIL "
            "and S2_API_KEY in your environment or in the .env file."
        )

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

    # Read and check CSV
    csv_path = Path(csv_path).resolve(strict=True)
    print(f"Reading '{csv_path}'...")
    doi_df = pd.read_csv(csv_path)

    # Ensure that the CSV has a 'doi' column
    if "doi" not in doi_df.columns:
        raise ValueError("CSV must have a 'doi' column.")

    # If using dask, use DaskTaskRunner
    if parallel:
        # Read config and get concurrency
        with open(config_path, "r") as f:
            config = json.load(f)
            n_workers = config.get("concurrency", 4)

        task_runner = DaskTaskRunner(
            cluster_class="distributed.LocalCluster",
            cluster_kwargs={
                "n_workers": n_workers,
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
        doi_df=doi_df,
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
