import os
import pandas as pd
import simpleaudio as sa
import json
from generate_pairs import NUM_PAIRS_PER_CAPTION

# Configurations
audio_dir = "../data/dpo-gen-output/"
caption_file = "../data/musiccaps-train-data.csv"
output_file = "./data/dpo-gen-output/human_labels.json"

# Load captions
captions_df = pd.read_csv(caption_file)

# Initialize labels
labels = []

# Helper function to play an audio file
def play_audio(file_path):
    try:
        wave_obj = sa.WaveObject.from_wave_file(file_path)
        play_obj = wave_obj.play()
        play_obj.wait_done()
    except Exception as e:
        print(f"Error playing audio: {e}")

for f in os.listdir(audio_dir):
    # Extract ytid and check if it's valid
    if not f.endswith(".wav"):
        continue
    ytid = "-".join(f.split("-")[:-2])

    for pair_idx in range(NUM_PAIRS_PER_CAPTION):  # Define NUM_PAIRS_PER_CAPTION
        file_0 = os.path.join(audio_dir, f"{ytid}-pair{pair_idx}-0-converted.wav")
        file_1 = os.path.join(audio_dir, f"{ytid}-pair{pair_idx}-1.wav")
        
        # Skip if files are missing
        if not os.path.exists(file_0) or not os.path.exists(file_1):
            print(f"Missing files for {ytid} pair {pair_idx}. Skipping.")
            continue

        # Display caption
        caption = captions_df[captions_df["ytid"] == ytid]["caption"].values[0]
        print(f"\nCaption: {caption}")
        # print(f"Pair {pair_idx}:")
        print(f"1. {file_0}")
        print(f"2. {file_1}")

        # Playback loop
        while True:
            print("Type '1' to play first audio, '2' to play second audio, or 'q' to move to rating.")
            choice = input("Play option (1/2/q): ").strip()
            if choice == "1":
                play_audio(file_0)
            elif choice == "2":
                play_audio(file_1)
            elif choice == "q":
                break
            else:
                print("Invalid input. Please try again.")

        # Collect human preference
        while True:
            preference = input("Enter your preference (0 for first, 1 for second, -1 for no preference): ").strip()
            if preference in {"0", "1", "-1"}:
                labels.append({"ytid": ytid, "pair_idx": pair_idx, "preference": int(preference)})
                break
            else:
                print("Invalid input. Please enter 0, 1, or -1.")

# Save labeled data
with open(output_file, "w") as f:
    json.dump(labels, f, indent=2)

print(f"Human labels saved to {output_file}")
