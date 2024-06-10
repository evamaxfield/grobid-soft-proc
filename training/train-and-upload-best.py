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
from transformers import Pipeline, pipeline
from sklearn.model_selection import train_test_split

###############################################################################

load_dotenv()

# Base model we fine-tune on top of
BASE_MODEL = "microsoft/deberta-v3-base"
FT_MODEL_HUB_NAME = "evamxb/soft-cite-intent-cls"

# Fine-tune default settings
HF_DATASET_PATH = "evamxb/soft-cite-intent"
MODEL_LOCAL_STORAGE_PATH = Path("final-model/")
DEFAULT_MODEL_MAX_SEQ_LENGTH = 128
FINE_TUNE_COMMAND_DICT = {
    "data_path": HF_DATASET_PATH,
    "project_name": str(MODEL_LOCAL_STORAGE_PATH),
    "text_column": "text",
    "target_column": "label",
    "train_split": "train",
    # "valid_split": "valid",
    "epochs": 3,
    "lr": 5e-5,
    "auto_find_batch_size": True,
    "seed": 12,
    "max_seq_length": DEFAULT_MODEL_MAX_SEQ_LENGTH,
    "model": BASE_MODEL,
    "token": os.environ["HF_AUTH_TOKEN"],
}

# Evaluation storage path
EVAL_STORAGE_PATH = Path("final-model-eval-results/")


###############################################################################

# Delete prior results and then remake
shutil.rmtree(EVAL_STORAGE_PATH, ignore_errors=True)
EVAL_STORAGE_PATH.mkdir(exist_ok=True)

# Set seed
np.random.seed(12)
random.seed(12)

###############################################################################


@dataclass
class EvaluationResults(DataClassJsonMixin):
    accuracy: float
    precision: float
    recall: float
    f1: float

def evaluate(
    model: Pipeline,
    x_test: list[np.ndarray] | list[str],
    y_test: list[str],
    df: pd.DataFrame,
) -> EvaluationResults:
    # Evaluate the model
    print("Evaluating model")

    print

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

    EVAL_STORAGE_PATH.mkdir(exist_ok=True)

    # Create confusion matrix display
    cm = ConfusionMatrixDisplay.from_predictions(
        y_test,
        y_pred,
    )

    # Save confusion matrix
    cm.figure_.savefig(EVAL_STORAGE_PATH / "confusion.png")

    # Add a "predicted" column
    df["predicted"] = y_pred

    # Find rows of misclassifications
    misclassifications = df[df["label"] != df["predicted"]]

    # Save misclassifications
    misclassifications.to_csv(
        EVAL_STORAGE_PATH / "misclassifications.csv",
        index=False,
    )

    return EvaluationResults(
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

# Create a new column called "other" which is True when "used", "mentioned", and "created" are all False
data["other"] = ~data[["used", "mentioned", "created"]].any(axis=1)

# Melt the data to have the sentence column and then the label column
data = pd.melt(data, id_vars=["text"], value_vars=["used", "mentioned", "created", "other"], var_name="label")

# Drop rows where the value is False
data = data[data["value"] == True].copy()

# Drop the value column
data = data.drop(columns=["value"])

# Strip the text column
data["text"] = data["text"].str.strip()

# Drop any duplicates
data = data.drop_duplicates()

# Create a train and test split
train, test = train_test_split(data, test_size=0.1, random_state=42, stratify=data["label"], shuffle=True)
# test, valid = train_test_split(test_and_valid, test_size=0.25, random_state=42, stratify=test_and_valid["label"], shuffle=True)

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
    # ("valid", valid),
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
# valid_ds = datasets.Dataset.from_pandas(
#     valid,
#     features=features,
#     preserve_index=False,
# )
test_ds = datasets.Dataset.from_pandas(
    test,
    features=features,
    preserve_index=False,
)

# Store as dataset dict
ds_dict = datasets.DatasetDict(
    {
        "train": train_ds,
        # "valid": valid_ds,
        "test": test_ds,
    }
)

# Push to hub
ds_dict.push_to_hub(
    HF_DATASET_PATH,
    private=True,
    token=os.environ["HF_AUTH_TOKEN"],
)
print()
print()

# Set seed
np.random.seed(12)
random.seed(12)

# Delete existing temp storage if exists
if MODEL_LOCAL_STORAGE_PATH.exists():
    shutil.rmtree(MODEL_LOCAL_STORAGE_PATH)

# Train the model
ft_train(
    FINE_TUNE_COMMAND_DICT,
)

# Evaluate the model
ft_transformer_pipe = pipeline(
    task="text-classification",
    model=str(MODEL_LOCAL_STORAGE_PATH),
    tokenizer=str(MODEL_LOCAL_STORAGE_PATH),
    padding=True,
    truncation=True,
    max_length=DEFAULT_MODEL_MAX_SEQ_LENGTH,
)

results = evaluate(
    ft_transformer_pipe,
    test["text"].tolist(),
    test["label"].tolist(),
    test,
)

print(results)

# Upload the model
ft_transformer_pipe.push_to_hub(
    FT_MODEL_HUB_NAME,
    private=False,
    token=os.environ["HF_AUTH_TOKEN"],
)