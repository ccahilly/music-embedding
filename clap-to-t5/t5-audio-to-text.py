import torch
from transformers import T5ForConditionalGeneration, T5Tokenizer
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
import numpy as np
import matplotlib.pyplot as plt
from google.cloud import storage

# Check if GPU is available, otherwise use CPU
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

class AudioToTextSmallModel(nn.Module):
    def __init__(self):
        super(AudioToTextModel, self).__init__()
        # Initialize T5 model and tokenizer
        self.t5 = T5ForConditionalGeneration.from_pretrained("t5-small")

    def forward(self, audio_embeddings, labels=None):
        # Ensure correct shape for inputs_embeds: (batch_size, seq_length, embedding_dim)
        # T5 expects the shape (batch_size, seq_length, embedding_dim)
        projected_embeddings = audio_embeddings.unsqueeze(1)  # Add seq_length dimension (usually 1 for this case)

        # Generate outputs with T5
        outputs = self.t5(
            inputs_embeds=projected_embeddings,
            labels=labels
        )
        return outputs

class AudioToTextBaseModel(nn.Module):
    def __init__(self):
        super(AudioToTextBaseModel, self).__init__()
        # Initialize T5 model and tokenizer with t5-large
        self.t5 = T5ForConditionalGeneration.from_pretrained("t5-base")
        # Linear layer to project 512-dimensional CLAP embeddings to 1024-dimensional embeddings
        self.projection_layer = nn.Linear(512, 768)

    def forward(self, audio_embeddings, labels=None):
        # Project audio embeddings from 512 to 1024 dimensions
        projected_embeddings = self.projection_layer(audio_embeddings)
        
        # Add seq_length dimension (usually 1 for this case)
        projected_embeddings = projected_embeddings.unsqueeze(1)

        # Generate outputs with T5
        outputs = self.t5(
            inputs_embeds=projected_embeddings,
            labels=labels
        )
        return outputs



# Load the training data
train_data = torch.load('../data/train_data.pt')

train_embeddings = torch.tensor(np.array(train_data["embeddings"])).to(device)  # Move to GPU
train_labels = [str(label) for label in train_data["labels"]]

# Ensure all labels are strings
for label in train_labels:
    if label is None or not isinstance(label, str):
        print("Label has an error or is not a string")

tokenizer = T5Tokenizer.from_pretrained("t5-base")

# Tokenize the labels (convert them into token IDs) just once
tokenized_labels = tokenizer(train_labels, padding=True, truncation=True, return_tensors="pt").input_ids.to(device)  # Move to GPU

# Create a DataLoader for your train data
train_dataset = TensorDataset(train_embeddings, tokenized_labels)
train_loader = DataLoader(train_dataset, batch_size=8, shuffle=True)

# Initialize the model and tokenizer
model = AudioToTextBaseModel().to(device)  # Move the model to GPU
model.train()  # Set the model to training mode

# Set the optimizer
optimizer = optim.AdamW(model.parameters(), lr=1e-5)  # You can adjust the learning rate

num_epochs = 10  # You can adjust this depending on your dataset and model size

# List to store the loss for each epoch
epoch_losses = []

# Training loop
for epoch in range(num_epochs):
    total_loss = 0
    for i, batch in enumerate(train_loader):
        audio_embeddings, labels = batch
        
        # Move data to GPU
        audio_embeddings = audio_embeddings.to(device)
        labels = labels.to(device)
        
        # Zero the gradients
        optimizer.zero_grad()
        
        # Forward pass
        outputs = model(audio_embeddings, labels=labels)
        
        # Calculate loss
        loss = outputs.loss
        total_loss += loss.item()
        
        # Backward pass
        loss.backward()
        
        # Update model parameters
        optimizer.step()

        if i % 62 == 0:
            print(f"Done with first {8 * i} examples")

    # Calculate and print the loss for this epoch
    avg_loss = total_loss / len(train_loader)
    epoch_losses.append(avg_loss)  # Store the average loss for this epoch
    print(f"Epoch {epoch + 1}/{num_epochs}, Train Loss: {avg_loss}")

torch.save(model.state_dict(), "weights_10_base.pth")

test_data = torch.load('../data/test_data.pt')

# +
test_embeddings = torch.tensor(np.array(test_data["embeddings"])).to(device)  # Move to GPU
test_labels = [str(label) for label in test_data["labels"]]

tokenized_test_labels = tokenizer(test_labels, padding=True, truncation=True, return_tensors="pt").input_ids.to(device)
# Create a DataLoader for your train data
test_dataset = TensorDataset(test_embeddings, tokenized_test_labels)
test_loader = DataLoader(test_dataset, batch_size=8, shuffle=True)


# -

def evaluate_final_loss(model, data_loader):
    total_loss = 0
    for i, batch in enumerate(data_loader):
        audio_embeddings, labels = batch

        # Move data to GPU
        audio_embeddings = audio_embeddings.to(device)
        labels = labels.to(device)

        # Zero the gradients
        optimizer.zero_grad()

        # Forward pass
        outputs = model(audio_embeddings, labels=labels)

        # Calculate loss
        loss = outputs.loss
        total_loss += loss.item()

    # Calculate and print the loss for this epoch
    avg_loss = total_loss / len(train_loader)
    return avg_loss


evaluate_final_loss(model, test_loader)

model.eval()


def inference(example_embedding):
    print(example_embedding.size())
    with torch.no_grad():
        t5_input = model.projection_layer(example_embedding)
        print(t5_input.size())
        generated_ids = model.t5.generate(
            inputs_embeds=t5_input.view(1, 1, t5_input.size()[-1]),
            max_length=50,  # Adjust as needed
            early_stopping=True
        )
    return tokenizer.decode(generated_ids[0], skip_special_tokens=False)


inference(test_embeddings[0])

train_data["filenames"][:5]



print(type(test_data))

# Plotting the training loss over epochs
plt.figure(figsize=(10, 6))
plt.plot(range(1, num_epochs + 1), epoch_losses, marker='o', color='b', label='Train Loss')
plt.title('Training Loss Over Epochs')
plt.xlabel('Epochs')
plt.ylabel('Loss')
plt.grid(True)
plt.legend()

# Save the plot as a .jpg file
plt.savefig('audio_to_text_training_loss_10.jpg', format='jpg')

# Optionally show the plot
plt.show()

# save to google cloud
bucket_name = "musiccaps-wav-16khz"
storage_client = storage.Client()
bucket = storage_client.bucket(bucket_name)
blob = bucket.blob("trained_audio_to_text_model.pth")

# # Upload the temporary file to GCS
# blob.upload_from_filename('../models/trained_audio_to_text_model.pth')
# print(f"Embedding dictionary saved to GCS")
