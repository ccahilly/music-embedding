import os
import pandas as pd
from transformers import Wav2Vec2FeatureExtractor, T5Tokenizer, T5ForConditionalGeneration, AutoModel
from datasets import Dataset
from torch.utils.data import Dataset, DataLoader
import torch
import torchaudio
from tqdm import tqdm
import numpy as np
from scipy.io import wavfile
import torch.nn as nn
import torchaudio.transforms as T
from gcloud_helpers import upload_to_gcs

FROZEN = True
print(f"Frozen: {FROZEN}")

data_dir = "../data/splits"

# Hyperparameters
BATCH_SIZE = 8
EPOCHS = 4
LEARNING_RATE = 1e-4
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
NORMALIZING_INPUT = True  # Flag for normalization
DEBUG = False
MAX_TOKENS = 64

print("Device:", DEVICE)

# Save the fine-tuned model
if FROZEN:
    model_save_path = "../models/fine_tuned_mert_t5_frozen"
    gcloud_path = "models/fine_tuned_mert_t5_frozen"
else:
    model_save_path = "../models/fine_tuned_mert_t5_unfrozen"
    gcloud_path = "models/fine_tuned_mert_t5_unfrozen"
os.makedirs(model_save_path, exist_ok=True)

mert_model_name = "m-a-p/MERT-v1-95M"
t5_model_name = "t5-small"
last_epoch = 0

if last_epoch == 0:
    # Load pretrained models
    mert_processor = Wav2Vec2FeatureExtractor.from_pretrained("m-a-p/MERT-v1-95M",trust_remote_code=True)
    mert_model = AutoModel.from_pretrained("m-a-p/MERT-v1-95M", trust_remote_code=True).to(DEVICE)
    
    t5_tokenizer = T5Tokenizer.from_pretrained("t5-small")
    t5_model = T5ForConditionalGeneration.from_pretrained("t5-small").to(DEVICE)

    # Define the linear and aggregator layers
    aggregator = nn.Conv1d(in_channels=13, out_channels=1, kernel_size=1).to(DEVICE)
    reduce_layer = nn.Linear(768, t5_model.config.d_model).to(DEVICE)

else: # Using previously fine tuned
    old_model_save_path = "../models/fine_tuned_mert_t5"
    if FROZEN:
        old_model_save_path += "_frozen"
    else:
        old_model_save_path += "_unfrozen"
    
    old_model_save_path += f"/e{last_epoch}"

    mert_processor = Wav2Vec2FeatureExtractor.from_pretrained(old_model_save_path + "/mert")
    mert_model = AutoModel.from_pretrained(old_model_save_path + "/mert").to(DEVICE)
    
    t5_tokenizer = T5Tokenizer.from_pretrained(old_model_save_path + "/t5")
    t5_model = T5ForConditionalGeneration.from_pretrained(old_model_save_path + "/t5").to(DEVICE)

    aggregator = nn.Conv1d(in_channels=13, out_channels=1, kernel_size=1).to(DEVICE)
    aggregator.load_state_dict(torch.load(os.path.join(old_model_save_path + "/aggregator", "aggregator.pth")))

    reduce_layer = nn.Linear(768, t5_model.config.d_model).to(DEVICE)
    reduce_layer.load_state_dict(torch.load(os.path.join(old_model_save_path + "/linear", "reduce_layer.pth")))

def preprocess_audio(audio_path, processor):
    """
    Preprocess audio file to ensure it is mono and normalized.
    Args:
        audio_path (str): Path to the audio file.
    Returns:
        np.ndarray: Preprocessed audio data.
    """
    # Load the audio file
    waveform, sample_rate = torchaudio.load(audio_path)

    if sample_rate != processor.sampling_rate:
        # print(f"resampling from {sample_rate} to {processor.sampling_rate}")
        resampler = T.Resample(orig_freq=sample_rate, new_freq=processor.sampling_rate)
        waveform = resampler(waveform)

    # Convert stereo to mono if necessary
    if waveform.ndim == 2:  # Stereo audio
        waveform = waveform.mean(axis=0)  # Average the two channels

    # Normalize audio to the range [-1, 1] if required
    # if NORMALIZING_INPUT:
    #     waveform = waveform.astype(np.float32) / np.iinfo(np.int16).max

    waveform = waveform.squeeze().numpy()
    # print(f"Waveform type: {type(waveform)}, shape: {waveform.shape}")

    # print(f"mert {processor.sampling_rate}")
    return waveform, processor.sampling_rate

# Dataset class
class AudioCaptionDataset(Dataset):
    def __init__(self, data_path, processor, tokenizer):
        self.data = pd.read_csv(data_path)
        self.processor = processor
        self.tokenizer = tokenizer

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        row = self.data.iloc[idx]
        audio_path = row["file_path"]
        caption = row["caption"]

        # Load and preprocess audio
        processed_audio, sample_rate = preprocess_audio(audio_path, self.processor)
        # if sample_rate != self.processor.sampling_rate:
        #     print("Value error")
        #     print(sample_rate)

        #     sample_rate = self.processor.sampling_rate

        # print(f"Processed audio shape: {processed_audio.shape}")

        inputs = self.processor(processed_audio, sampling_rate = sample_rate, return_tensors="pt")
        input_values = torch.tensor(processed_audio)
        # print(input_values.shape)

        attention_mask = inputs.get("attention_mask", torch.ones_like(input_values))  # Default to ones if missing

        # Tokenize caption
        labels = self.tokenizer(caption, return_tensors="pt", padding="max_length", truncation=True, max_length=MAX_TOKENS)

        return {
            "inputs": input_values,
            "attention_mask": attention_mask,
            "labels": labels["input_ids"].squeeze(0),
            "decoder_attention_mask": labels["attention_mask"].squeeze(0)
        }

# Load data
train_dataset = AudioCaptionDataset(data_dir + "/train.csv", mert_processor, t5_tokenizer)
val_dataset = AudioCaptionDataset(data_dir + "/val.csv", mert_processor, t5_tokenizer)

train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, drop_last=True)
val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, drop_last=True)

# Training function
def train(model, train_loader, val_loader, epochs):
    for param in mert_model.parameters():
        param.requires_grad = not FROZEN # true when frozen is false
    for param in aggregator.parameters():
        param.requires_grad = True
    for param in reduce_layer.parameters():
        param.requires_grad = True
    for param in model.parameters():
        param.requires_grad = True
    
    if not FROZEN:
        mert_model.train()
    else:
        mert_model.eval()

    aggregator.train()
    reduce_layer.train()
    model.train()

    if FROZEN:
        optimizer = torch.optim.AdamW(
        list(aggregator.parameters()) + list(reduce_layer.parameters()) + list(model.parameters()),
        lr=LEARNING_RATE
        )
    else:
        optimizer = torch.optim.AdamW(
        list(mert_model.parameters()) + list(aggregator.parameters()) + list(reduce_layer.parameters()) + list(model.parameters()),
        lr=LEARNING_RATE
        )

    for epoch in range(epochs):
        train_loss = 0
        for batch in tqdm(train_loader, desc=f"Epoch {epoch + 1}/{epochs}"):
            inputs = batch["inputs"].to(DEVICE)
            attention_mask = batch["attention_mask"].to(DEVICE)
            labels = batch["labels"].to(DEVICE)
            decoder_attention_mask = batch["decoder_attention_mask"].to(DEVICE)

            if DEBUG:
                print(f"inputs shape: {inputs.shape}")
                print(f"attention_mask shape: {attention_mask.shape}")
                print(f"labels shape: {labels.shape}")
                print(f"decoder_attention_mask shape: {decoder_attention_mask.shape}")

            # Extract embeddings
            if FROZEN:
                with torch.no_grad():
                    mert_outputs = mert_model(inputs, output_hidden_states=True)
            else:
                mert_outputs = mert_model(inputs, output_hidden_states=True)
            
            all_layer_hidden_states = torch.stack(mert_outputs.hidden_states).squeeze()
            if DEBUG:
                print(f"all_layer_hidden_states shape: {all_layer_hidden_states.shape}")
                
            combined_dim = all_layer_hidden_states.view(BATCH_SIZE, 13, -1)  # [batch_size, layers, time_steps * features]

            if DEBUG:
                print(f"combined_dim shape: {combined_dim.shape}")

            # Apply Conv1d for learnable aggregation
            aggregated_embedding = aggregator(combined_dim)  # [batch_size, 1, time_steps * features]

            if DEBUG:
                print(f"aggregated_embedding shape: {aggregated_embedding.shape}")

            # Uncombine the last dimension back into time_steps and features
            aggregated_embedding = aggregated_embedding.view(BATCH_SIZE, 749, 768)  # [batch_size, time_steps, features]

            if DEBUG:
                print(f"aggregated_embedding shape: {aggregated_embedding.shape}")

            # Reduce Wav2Vec2 embeddings
            reduced_embeddings = reduce_layer(aggregated_embedding)

            if DEBUG:
                print(f"reduced_embeddings shape: {reduced_embeddings.shape}")

            # Feed embeddings to T5
            outputs = model(
                inputs_embeds=reduced_embeddings,
                labels=labels,
                decoder_attention_mask=decoder_attention_mask,
            )

            loss = outputs.loss
            train_loss += loss.item()

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        avg_train_loss = train_loss / len(train_loader)
        print(f"Epoch {epoch + 1}: Train Loss = {avg_train_loss}")

        # Evaluate
        avg_val_loss = evaluate(model, val_loader)

        checkpoint_path = model_save_path + f"/e{last_epoch + epoch + 1}"
        gcloud_checkpoint_path = gcloud_path + f"/e{last_epoch + epoch + 1}"
        os.makedirs(checkpoint_path, exist_ok=True)

        # Save the loss
        with open(checkpoint_path + "/loss.txt", "w") as f:
            f.write(f"Epoch {last_epoch + epoch + 1}: Train Loss = {avg_train_loss:.4f}, Validation Loss = {avg_val_loss:.4f}\n")
        upload_to_gcs(checkpoint_path + "/loss.txt", gcloud_checkpoint_path + "/loss.txt")

        # Save the T5 model
        os.makedirs(checkpoint_path + "/t5", exist_ok=True)
        t5_tokenizer.save_pretrained(checkpoint_path + "/t5")
        model.save_pretrained(checkpoint_path + "/t5")
        upload_to_gcs(checkpoint_path + "/t5", gcloud_checkpoint_path + "/t5")

        # Save the MERT model
        os.makedirs(checkpoint_path + "/mert", exist_ok=True)
        mert_processor.save_pretrained(checkpoint_path + "/mert")
        mert_model.save_pretrained(checkpoint_path + "/mert")
        upload_to_gcs(checkpoint_path + "/mert", gcloud_checkpoint_path + "/mert")

        # Save the linear layer
        os.makedirs(checkpoint_path + "/linear", exist_ok=True)
        torch.save(reduce_layer.state_dict(), checkpoint_path + "/linear" + "/reduce_layer.pth")
        upload_to_gcs(checkpoint_path + "/linear", gcloud_checkpoint_path + "/linear")

        # Save the aggregator layer
        os.makedirs(checkpoint_path + "/aggregator", exist_ok=True)
        torch.save(aggregator.state_dict(), os.path.join(model_save_path + "/aggregator", "aggregator.pth"))
        upload_to_gcs(checkpoint_path + "/aggregator", gcloud_checkpoint_path + "/aggregator")

# Evaluation function
def evaluate(model, val_loader):
    model.eval()
    reduce_layer.eval()
    aggregator.eval()
    if not FROZEN:
        mert_model.eval()

    val_loss = 0

    with torch.no_grad():
        for batch in tqdm(val_loader, desc="Evaluating"):
            inputs = batch["inputs"].to(DEVICE)
            attention_mask = batch["attention_mask"].to(DEVICE)
            labels = batch["labels"].to(DEVICE)
            decoder_attention_mask = batch["decoder_attention_mask"].to(DEVICE)

            # Extract embeddings
            mert_outputs = mert_model(inputs, output_hidden_states=True)
            all_layer_hidden_states = torch.stack(mert_outputs.hidden_states).squeeze()
            combined_dim = all_layer_hidden_states.view(BATCH_SIZE, 13, -1)
            aggregated_embedding = aggregator(combined_dim)  # [batch_size, 1, time_steps * features]
            aggregated_embedding = aggregated_embedding.view(BATCH_SIZE, 749, 768)
            reduced_embeddings = reduce_layer(aggregated_embedding)

            # Feed embeddings to T5
            outputs = model(
                inputs_embeds=reduced_embeddings,
                labels=labels,
                decoder_attention_mask=decoder_attention_mask,
            )

            val_loss += outputs.loss.item()

    avg_val_loss = val_loss / len(val_loader)
    print(f"Validation Loss = {avg_val_loss}")
    
    model.train()
    reduce_layer.train()
    aggregator.train()
    if not FROZEN:
        mert_model.train()

    return avg_val_loss

if __name__ == "__main__":
    train(t5_model, train_loader, val_loader, EPOCHS)