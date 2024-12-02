import random
from transformers import SpeechT5Processor, SpeechT5ForSpeechToText
import torch
from scipy.io import wavfile
import os
import pandas as pd
import evaluate
from tqdm import tqdm
import numpy as np
from torch.utils.data import Dataset
from speecht5_train import SAMPLE_RATE, SpeechDataset, NORMALIZING_INPUT

wer = evaluate.load("wer")

# Paths
split_save_path = "../data/splits"
model_path = "../models/speecht5-model-e20"  # Path to the saved model

# Load the preprocessed data splits
train_data = pd.read_csv(os.path.join(split_save_path, "train.csv"))
val_data = pd.read_csv(os.path.join(split_save_path, "val.csv"))
test_data = pd.read_csv(os.path.join(split_save_path, "test.csv"))

# Print the number of examples in each dataset
print(f"Number of training examples: {len(train_data)}")
print(f"Number of validation examples: {len(val_data)}")
print(f"Number of test examples: {len(test_data)}")

def check_overlap(train_data, val_data, test_data):
    """
    Check if there are overlapping datapoints between train, val, and test datasets.
    
    Args:
        train_data (pd.DataFrame): Training dataset.
        val_data (pd.DataFrame): Validation dataset.
        test_data (pd.DataFrame): Test dataset.
    
    Returns:
        None. Prints the results of the overlap check.
    """
    # Convert file paths to sets
    train_set = set(train_data["file_path"])
    val_set = set(val_data["file_path"])
    test_set = set(test_data["file_path"])
    
    # Check for overlaps
    train_val_overlap = train_set.intersection(val_set)
    train_test_overlap = train_set.intersection(test_set)
    val_test_overlap = val_set.intersection(test_set)
    
    # Print results
    if train_val_overlap:
        print(f"Overlap found between train and val: {len(train_val_overlap)} examples")
    else:
        print("No overlap between train and val.")
    
    if train_test_overlap:
        print(f"Overlap found between train and test: {len(train_test_overlap)} examples")
    else:
        print("No overlap between train and test.")
    
    if val_test_overlap:
        print(f"Overlap found between val and test: {len(val_test_overlap)} examples")
    else:
        print("No overlap between val and test.")

check_overlap(train_data, val_data, test_data)

# Load the processor and model
processor = SpeechT5Processor.from_pretrained(model_path)
model = SpeechT5ForSpeechToText.from_pretrained(model_path)

# Move the model to GPU (if available)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)
print(f"Device: {device}")

# Prepare DataLoader (though not needed for inference, but we'll use it to sample random examples)
train_dataset = SpeechDataset(train_data, processor)
val_dataset = SpeechDataset(val_data, processor)
test_dataset = SpeechDataset(test_data, processor)

def run_inference(dataset, dataset_name, n_samples=3):
    model.eval()  # Set model to evaluation mode
    total_wer = 0
    total_samples = len(dataset)
    random_indices = random.sample(range(total_samples), n_samples)
    
    s = ""

    # For computing WER across all data points
    for idx in tqdm(range(total_samples), desc=f"Processing {dataset_name}"):
        # Load the audio file and caption from the dataset
        sample = dataset[idx]
        input_values = sample["input_values"].unsqueeze(0).to(device)  # Add batch dimension and move to device
        true_caption = sample["labels"]
        
        # Tokenize the labels (captions)
        labels = processor(text_target=true_caption, padding=True, truncation=True, return_tensors="pt").input_ids.to(device)

        # Run inference
        with torch.no_grad():
            outputs = model(input_values=input_values, labels=labels)  # Using labels for supervised inference

        # Get the predicted text
        predictions = outputs.logits.argmax(dim=-1)  # Get the predicted tokens
        predicted_texts = processor.batch_decode(predictions, skip_special_tokens=True)
        
        # Decode the true labels to text
        decoded_references = processor.batch_decode(labels, skip_special_tokens=True)

        # Compute WER (Word Error Rate)
        total_wer += wer.compute(predictions=predicted_texts, references=decoded_references)

        # Print results for random samples
        if idx in random_indices:
            s += f"\nSample {idx + 1} from {dataset_name}:"
            s += f"True Caption: {true_caption}"
            s += f"Generated Caption: {predicted_texts[0]}"
            s += "-" * 50
    
    print(s)

    # Calculate and print overall WER for the dataset
    avg_wer = total_wer / total_samples
    print(f"\nOverall WER for {dataset_name}: {avg_wer:.4f}")

# Compute WER for train, val, and test datasets
# print("Processing Train Dataset:")
# run_inference(train_dataset, "Train")

# print("\nProcessing Validation Dataset:")
# run_inference(val_dataset, "Validation")

print("\nProcessing Test Dataset:")
run_inference(test_dataset, "Test")