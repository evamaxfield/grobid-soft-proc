#!/usr/bin/env python

import os
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

import datasets
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from autotrain.trainers.text_classification.__main__ import train as ft_train
from dataclasses_json import DataClassJsonMixin
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    accuracy_score,
    precision_recall_fscore_support,
)
from sklearn.model_selection import train_test_split
from tabulate import tabulate
from tqdm import tqdm
from transformers import Pipeline, pipeline

# Models used for testing, both fine-tune and semantic logit
BASE_MODELS = {
    "deberta": "microsoft/deberta-v3-base",
    "mpnet": "sentence-transformers/all-mpnet-base-v2",
    "distilbert": "distilbert/distilbert-base-uncased",
    "scibert": "allenai/scibert_scivocab_uncased",
}

# Fine-tune default settings
DEFAULT_HF_DATASET_PATH = "evamxb/soft-cite-intent"
DEFAULT_FINE_TUNE_TEMP_STORAGE_PATH = Path("autotrain-text-classification-temp/")
DEFAULT_MODEL_MAX_SEQ_LENGTH = 128
FINE_TUNE_COMMAND_DICT = {
    "data_path": DEFAULT_HF_DATASET_PATH,
    "project_name": str(DEFAULT_FINE_TUNE_TEMP_STORAGE_PATH),
    "text_column": "text",
    "target_column": "label",
    "train_split": "train",
    "valid_split": "valid",
    "epochs": 2,
    "lr": 5e-5,
    "auto_find_batch_size": True,
    "seed": 12,
    "max_seq_length": DEFAULT_MODEL_MAX_SEQ_LENGTH,
}

# Evaluation storage path
EVAL_STORAGE_PATH = Path("model-eval-results/")


###############################################################################

# Load environment variables
load_dotenv()
FINE_TUNE_COMMAND_DICT["token"] = os.environ["HF_AUTH_TOKEN"]

# Delete prior results and then remake
shutil.rmtree(EVAL_STORAGE_PATH, ignore_errors=True)
EVAL_STORAGE_PATH.mkdir(exist_ok=True)

# Set seed
np.random.seed(12)
random.seed(12)

###############################################################################


@dataclass
class EvaluationResults(DataClassJsonMixin):
    model: str
    accuracy: float
    precision: float
    recall: float
    f1: float


def evaluate(
    model: Pipeline,
    x_test: list[np.ndarray] | list[str],
    y_test: list[str],
    model_name: str,
    df: pd.DataFrame,
) -> EvaluationResults:
    # Evaluate the model
    print("Evaluating model")

    # Predict
    y_pred = model.predict(x_test)

    # Get the actual predictions from Pipeline
    y_pred = [pred["label"] for pred in y_pred]

    # Metrics
    accuracy = accuracy_score(
        y_test,
        y_pred,
    )
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_test,
        y_pred,
        average="micro",
    )

    # Print results
    print(
        f"Accuracy: {accuracy}, "
        f"Precision: {precision}, "
        f"Recall: {recall}, "
        f"F1: {f1}, "
    )

    this_model_eval_storage = EVAL_STORAGE_PATH / model_name
    this_model_eval_storage.mkdir(exist_ok=True)

    # Create confusion matrix display
    cm = ConfusionMatrixDisplay.from_predictions(
        y_test,
        y_pred,
    )

    # Save confusion matrix
    cm.figure_.savefig(this_model_eval_storage / "confusion.png")

    # Add a "predicted" column
    df["predicted"] = y_pred

    # Find rows of misclassifications
    misclassifications = df[df["label"] != df["predicted"]]

    # Save misclassifications
    misclassifications.to_csv(
        this_model_eval_storage / "misclassifications.csv",
        index=False,
    )

    return EvaluationResults(
        model=this_iter_model_name,
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1=f1,
    )


###############################################################################

# Load the data
data = pd.read_csv("sci-impact-hack-soft-cite-intent.csv")

# Select down to just context and label columns
data = data[["sentence", "used", "mention", "created"]]

# Rename "mention" to "mentioned" and "sentence" to "text"
data = data.rename(columns={"mention": "mentioned", "sentence": "text"})

# Melt the data to have the sentence column and then the label column
data = pd.melt(
    data,
    id_vars=["text"],
    value_vars=["used", "mentioned", "created"],
    var_name="label",
)

# Drop rows where the value is False
data = data[data["value"] is True].copy()

# Drop the value column
data = data.drop(columns=["value"])

# Strip the text column
data["text"] = data["text"].str.strip()

# Drop any duplicates
data = data.drop_duplicates()

# Create a train and test split
train, test_and_valid = train_test_split(
    data, test_size=0.2, random_state=42, stratify=data["label"], shuffle=True
)
test, valid = train_test_split(
    test_and_valid,
    test_size=0.25,
    random_state=42,
    stratify=test_and_valid["label"],
    shuffle=True,
)

# Print proporation of each label in the train set
print(train["label"].value_counts(normalize=True))

# Store class details required for feature construction
num_classes = data["label"].nunique()
class_labels = list(data["label"].unique())

# Construct features for the dataset
features = datasets.Features(
    text=datasets.Value("string"),
    label=datasets.ClassLabel(
        num_classes=num_classes,
        names=class_labels,
    ),
)
# Create a dataframe where the rows are the different splits
# and there are three columns one column is the split name,
# the other columns are the counts of label
split_counts = []
for split_name, split_df in [
    ("train", train),
    ("valid", valid),
    ("test", test),
]:
    split_counts.append(
        {
            "split": split_name,
            **split_df["label"].value_counts().to_dict(),
        }
    )
split_counts_df = pd.DataFrame(split_counts)
print("Split counts:")
print(split_counts_df)
print()

# Create the dataset
train_ds = datasets.Dataset.from_pandas(
    train,
    features=features,
    preserve_index=False,
)
valid_ds = datasets.Dataset.from_pandas(
    valid,
    features=features,
    preserve_index=False,
)
test_ds = datasets.Dataset.from_pandas(
    test,
    features=features,
    preserve_index=False,
)

# Store as dataset dict
ds_dict = datasets.DatasetDict(
    {
        "train": train_ds,
        "valid": valid_ds,
        "test": test_ds,
    }
)

# Push to hub
load_dotenv()
ds_dict.push_to_hub(
    DEFAULT_HF_DATASET_PATH,
    private=True,
    token=os.environ["HF_AUTH_TOKEN"],
)
print()
print()

# Iter through fieldsets
results = []
# Train each fine-tuned model
for model_short_name, hf_model_path in tqdm(
    BASE_MODELS.items(),
    desc="Fine-tune models",
    leave=False,
):
    # Set seed
    np.random.seed(12)
    random.seed(12)

    this_iter_model_name = f"fine-tune-{model_short_name}"
    print()
    print(f"Working on: {this_iter_model_name}")
    try:
        # Delete existing temp storage if exists
        if DEFAULT_FINE_TUNE_TEMP_STORAGE_PATH.exists():
            shutil.rmtree(DEFAULT_FINE_TUNE_TEMP_STORAGE_PATH)

        # Update the fine-tune command dict
        this_iter_command_dict = FINE_TUNE_COMMAND_DICT.copy()
        this_iter_command_dict["model"] = hf_model_path

        # Train the model
        ft_train(
            this_iter_command_dict,
        )

        # Evaluate the model
        ft_transformer_pipe = pipeline(
            task="text-classification",
            model=str(DEFAULT_FINE_TUNE_TEMP_STORAGE_PATH),
            tokenizer=str(DEFAULT_FINE_TUNE_TEMP_STORAGE_PATH),
            padding=True,
            truncation=True,
            max_length=DEFAULT_MODEL_MAX_SEQ_LENGTH,
        )
        results.append(
            evaluate(
                ft_transformer_pipe,
                test["text"].tolist(),
                test["label"].tolist(),
                this_iter_model_name,
                test,
            ).to_dict(),
        )

    except Exception as e:
        print(f"Error during: {this_iter_model_name}, Error: {e}")
        results.append(
            {
                "model": this_iter_model_name,
                "error": str(e),
            }
        )

# Print results
results_df = pd.DataFrame(results)
results_df = results_df.sort_values(by="f1", ascending=False).reset_index(drop=True)
results_df.to_csv("all-model-results.csv", index=False)
print("Current standings")
print(
    tabulate(
        results_df.head(10),
        headers="keys",
        tablefmt="psql",
        showindex=False,
    )
)
