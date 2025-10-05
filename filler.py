import os
from pathlib import Path
from dotenv import load_dotenv
import openai

# Load environment variables
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

if not api_key:
    raise ValueError("❌ OPENAI_API_KEY not found in .env file")

# Initialize client
client = openai.OpenAI(api_key=api_key)

# Folder to store generated filler audio
output_dir = Path("audio")
output_dir.mkdir(parents=True, exist_ok=True)

# Expanded filler phrases
filler_phrases = [
    # --- Short ---
    "Hmm.",
    "Okay.",
    "Alright.",
    "Got it.",
    "Sure thing.",
    "Right.",
    "I see.",
    "Uh-huh.",
    "Yep, one sec.",

    # --- Medium ---
    "Hmm, let's see.",
    "Let me check that real quick.",
    "Sure, I can look into this for you.",
    "Just a moment, I'm pulling that up.",
    "Okay, give me a second to think.",
    "Alright, let me process that.",
    "Good question, let’s work through it.",
    "Hold on, I want to make sure I get this right.",
    "Interesting… let me consider this.",
    "One second, I’m checking the details.",
    "I hear you, let me pull that up.",
    "Alright, I need a second here.",
    "Hang on just a bit.",
    "Let me quickly run through that.",
    "Got it, let’s dive in.",

    # --- Longer, natural ---
    "Hmm, that’s an interesting one… give me a second to think.",
    "Let me go over that carefully so I can give you the right info.",
    "Alright, I’m running through the details right now.",
    "Hold tight, I just want to make sure I don’t miss anything.",
    "Okay, let me organize my thoughts for a moment.",
    "I’m checking a couple of things in the background for you.",
    "That’s a good point—let me pull the details together.",
    "Just a moment, I want to be thorough with this.",
    " ",
]

# Generate TTS audio files for each filler phrase
for i, phrase in enumerate(filler_phrases, start=1):
    filename = output_dir / f"filler_{i}.wav"  # Save as WAV
    print(f"🎙️ Generating {filename}...")

    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",  # Fast & natural TTS
        voice="cedar",            # Cedar voice (natural tone)
        input=phrase
    ) as response:
        response.stream_to_file(filename)

print("✅ All filler audios generated as WAV!")
