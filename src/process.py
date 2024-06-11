#!/usr/bin/env python

import pyalex
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

from dataclasses_json import DataClassJsonMixin
from gcsfs import GCSFileSystem
from software_mentions_client.client import software_mentions_client
import typer
import pandas as pd
from prefect import Flow, unmapped, task
from prefect.task_runners import SequentialTaskRunner
from prefect_dask.task_runners import DaskTaskRunner
from transformers import pipeline
from tqdm import tqdm

###############################################################################

GCP_STORAGE_BUCKET = "grobid-soft-proc-results"

###############################################################################

app = typer.Typer()

###############################################################################
# APIs


@dataclass
class OpenAlexAPICallStatus(DataClassJsonMixin):
    call_count: int
    current_date: str


OPEN_ALEX_API_CALL_STATUS_FILEPATH = (
    Path("~/.open_alex_api_call_status.json").expanduser().resolve()
)


@backoff.on_exception(backoff.expo, Exception, max_time=5)
def _write_open_alex_api_call_status(current_status: OpenAlexAPICallStatus) -> None:
    """Write the API call status to disk."""
    # Make dirs if needed
    OPEN_ALEX_API_CALL_STATUS_FILEPATH.parent.mkdir(parents=True, exist_ok=True)

    with open(OPEN_ALEX_API_CALL_STATUS_FILEPATH, "w") as f:
        json.dump(current_status.to_dict(), f)


@backoff.on_exception(backoff.expo, Exception, max_time=5)
def _read_open_alex_api_call_status() -> OpenAlexAPICallStatus:
    """Read the API call status from disk."""
    with open(OPEN_ALEX_API_CALL_STATUS_FILEPATH, "r") as f:
        current_status = OpenAlexAPICallStatus.from_dict(json.load(f))

    # Check if current API status is out of date
    if current_status.current_date != date.today().isoformat():
        current_status = OpenAlexAPICallStatus(
            call_count=0,
            current_date=date.today().isoformat(),
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
                current_date=date.today().isoformat(),
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
            current_date=date.today().isoformat(),
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
class ErrorResult(DataClassJsonMixin):
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
class SoftwareMentionResult:
    doi: str
    doi_hash: str
    pdf_url: str
    api_source: str
    context: str
    mention_type: str
    software_type: str
    software_name_raw: str
    software_name_normalized: str
    software_name_offset_start: int
    software_name_offset_end: int
    grobid_intent_cls: str
    grobid_intent_score: float


@task(timeout_seconds=180, retries=3, retry_delay_seconds=5)
def _annotate_pdf(
    data: PDFDownloadResult | ErrorResult,
    temp_working_dir: Path,
    config_path: Path,
) -> list[SoftwareMentionResult] | ErrorResult:
    if isinstance(data, ErrorResult):
        return data

    # Create temporary output path for this JSON
    tmp_output_path = temp_working_dir / f"{data.doi_hash}.json"

    try:
        # Init client
        client = software_mentions_client(config_path=config_path)

        # Annotate PDF
        client.annotate(
            file_in=data.pdf_local_path,
            file_out=tmp_output_path,
            full_record=None,
        )

        # Read the JSON
        with open(tmp_output_path, "r") as f:
            annotations = json.load(f)

        # Parse the annotations
        results = []
        for mention in annotations["mentions"]:
            # Find cls with max value
            grobid_intent_details = mention["mentionContextAttributes"]
            best_cls = "other"
            best_score = 0.0
            for intent_cls in ["used", "created", "shared"]:
                if (
                    grobid_intent_details[intent_cls]["value"]
                    and grobid_intent_details[intent_cls]["score"] > best_score
                ):
                    best_cls = intent_cls
                    best_score = grobid_intent_details[intent_cls]["score"]

            # Add to results
            results.append(
                SoftwareMentionResult(
                    doi=data.doi,
                    doi_hash=data.doi_hash,
                    pdf_url=data.pdf_url,
                    api_source=data.api_source,
                    context=mention["context"],
                    mention_type=mention["type"],
                    software_type=mention["software-type"],
                    software_name_raw=mention["software-name"]["rawForm"],
                    software_name_normalized=mention["software-name"]["normalizedForm"],
                    software_name_offset_start=mention["software-name"]["offsetStart"],
                    software_name_offset_end=mention["software-name"]["offsetEnd"],
                    grobid_intent_cls=best_cls,
                    grobid_intent_score=best_score,
                )
            )

        return results

    except Exception as e:
        return ErrorResult(
            doi=data.doi,
            doi_hash=data.doi_hash,
            step="annotate-pdf",
            error=str(e),
        )

    finally:
        # Remove the temp files
        tmp_output_path.unlink(missing_ok=True)
        Path(data.pdf_local_path).unlink(missing_ok=True)


@task
def _flatten_annotation_results(
    data: list[list[SoftwareMentionResult] | ErrorResult],
) -> list[SoftwareMentionResult | ErrorResult]:
    flat_results: list[SoftwareMentionResult | ErrorResult] = []
    for result in data:
        if isinstance(result, ErrorResult):
            flat_results.append(result)

        else:
            flat_results.extend(result)

    return flat_results


@dataclass
class SoftCiteIntentResult(DataClassJsonMixin):
    doi: str
    doi_hash: str
    pdf_url: str
    api_source: str
    context: str
    mention_type: str
    software_type: str
    software_name_raw: str
    software_name_normalized: str
    software_name_offset_start: int
    software_name_offset_end: int
    grobid_intent_cls: str
    grobid_intent_score: float
    czi_soft_cite_intent_cls: str
    czi_soft_cite_intent_score: float


@task
def _classify_annotation_with_soft_cite(
    data: list[SoftwareMentionResult | ErrorResult],
) -> list[SoftCiteIntentResult | ErrorResult]:
    # Filter out errors
    errors = [result for result in data if isinstance(result, ErrorResult)]
    mention_results = [
        result for result in data if isinstance(result, SoftwareMentionResult)
    ]

    try:
        # Init pipeline
        model = pipeline(
            task="text-classification",
            model="evamxb/soft-cite-intent-cls",
            tokenizer="evamxb/soft-cite-intent-cls",
            padding=True,
            truncation=True,
            max_length=128,
        )

        # Predict all
        predictions = model(
            [mention.context for mention in mention_results],
        )

        # Zip results
        new_results: list[SoftCiteIntentResult | ErrorResult] = []
        for mention, prediction in zip(mention_results, predictions, strict=True):
            new_results.append(
                SoftCiteIntentResult(
                    doi=mention.doi,
                    doi_hash=mention.doi_hash,
                    pdf_url=mention.pdf_url,
                    api_source=mention.api_source,
                    context=mention.context,
                    mention_type=mention.mention_type,
                    software_type=mention.software_type,
                    software_name_raw=mention.software_name_raw,
                    software_name_normalized=mention.software_name_normalized,
                    software_name_offset_start=mention.software_name_offset_start,
                    software_name_offset_end=mention.software_name_offset_end,
                    grobid_intent_cls=mention.grobid_intent_cls,
                    grobid_intent_score=mention.grobid_intent_score,
                    czi_soft_cite_intent_cls=prediction["label"],
                    czi_soft_cite_intent_score=prediction["score"],
                )
            )

        # Extend with errors
        return new_results + errors

    except Exception as e:
        return errors + [
            ErrorResult(
                doi="",
                doi_hash="",
                step="classify-annotation-with-soft-cite",
                error=str(e),
            )
        ]


@task
def _store_batch_results(
    results: list[SoftCiteIntentResult | ErrorResult],
    batch_id: int,
    results_storage_dir_name: str,
) -> None:
    # Create prepended storage dir
    storage_dir = f"{GCP_STORAGE_BUCKET}/{results_storage_dir_name}"

    # Store the results
    successful_results_df = pd.DataFrame(
        [r.to_dict() for r in results if not isinstance(r, ErrorResult)]
    )
    failed_results_df = pd.DataFrame(
        [r.to_dict() for r in results if isinstance(r, ErrorResult)]
    )

    # Write the results to CSV
    successful_results_df.to_parquet(
        f"gs://{storage_dir}/successful-results/batch-{batch_id}.parquet",
        index=False,
    )
    failed_results_df.to_parquet(
        f"gs://{storage_dir}/failed-results/batch-{batch_id}.parquet",
        index=False,
    )


def _download_annotate_for_software_from_doi_pipeline(
    doi_df: pd.DataFrame,
    gcp_results_storage_dir_name: str,
    temp_working_dir: Path,
    config_path: Path,
) -> None:
    # Read config and get batch size
    with open(config_path, "r") as f:
        config = json.load(f)
        batch_size = config.get("batch_size", 50)

    # Make temp storage directory
    temp_working_dir.mkdir(exist_ok=True)

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

            # Flatten results
            flat_results = _flatten_annotation_results(
                [f.result() for f in pdf_annotation_results],
            )

            # Classify with SoftCite
            soft_cite_results = _classify_annotation_with_soft_cite(
                data=flat_results,
            )

            # Store batch results
            _store_batch_results(
                results=soft_cite_results,
                batch_id=i // batch_size,
                results_storage_dir_name=gcp_results_storage_dir_name,
            )

        except Exception as e:
            print(f"Error processing batch {i}: {e}")


###############################################################################


@app.command()
def process(
    csv_path: Path = typer.Argument(..., help="Path to the CSV file for processing."),
    temp_working_dir: Path = typer.Argument(
        TEMP_DIR, help="Path to the temporary working directory."
    ),
    parallel: bool = typer.Option(True, help="Use Dask for parallel processing."),
    config_path: Path = typer.Argument(
        DEFAULT_CONFIG_PATH, help="Path to the config file."
    ),
    force: bool = typer.Option(
        False, help="Force overwrite (or add to) existing results."
    ),
):
    # Load dotenv and check for OPENALEX_EMAIL and S2_API_KEY
    load_dotenv()
    if "OPENALEX_EMAIL" not in os.environ or "S2_API_KEY" not in os.environ:
        raise ValueError(
            "Please set OPENALEX_EMAIL "
            "and S2_API_KEY in your environment or in the .env file."
        )

    # Make results storage directory
    this_results_storage_dir_name = csv_path.stem

    # Check that the data file has a unique name compared to existing in storage
    # Init GCSFS
    fs = GCSFileSystem()
    if fs.exists(f"{GCP_STORAGE_BUCKET}/{this_results_storage_dir_name}") and not force:
        raise ValueError(
            "Results storage directory already exists and --force option not set. "
            "Please choose a unique name for your data file."
        )

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
        gcp_results_storage_dir_name=this_results_storage_dir_name,
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
