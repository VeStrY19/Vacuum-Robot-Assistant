"""
nexo_mcp_assistant.py
-----------------------
Même pipeline vocal que nexo_robot_assistant.py (Whisper/OpenVINO + Qwen +
Piper), mais le contrôle du robot passe maintenant par un serveur MCP
(robot_mcp_server.py) au lieu d'une classification JSON faite à la main.

Qwen reçoit la liste des tools exposés par le serveur MCP et décide
lui-même, via le tool-calling natif d'Ollama, s'il doit en appeler un ou
répondre normalement en conversation.
"""

import asyncio
import contextlib
import os
import re
import subprocess
import wave

import numpy as np
import ollama
from fastmcp import Client
from optimum.intel import OVModelForSpeechSeq2Seq
from transformers import AutoProcessor, pipeline

# ============================================================
# Config
# ============================================================
PIPER_VOICE = "en_US-lessac-medium"
TTS_OUTPUT_FILE = "reply.wav"
LLM_MODEL = "qwen2.5:7b"
MCP_SERVER_SCRIPT = "robot_mcp_server.py"

SYSTEM_PROMPT = """You are a helpful desktop assistant robot named Nexo.
Be friendly, concise and helpful. Always answer in English.
This is a voice assistant: your replies are read aloud, so never use Markdown,
bullet points, numbered lists, or special symbols like * or #. Write in plain,
natural spoken sentences only.
You have access to tools that control a real robot vacuum. Use them when the
user clearly asks for a vacuum action (starting, stopping, pausing, going
home, or going to a named place or person). For anything else, just chat
normally. If a tool returns an error, tell the user briefly what went wrong."""

conversation = []


# ============================================================
# Suppression des warnings ALSA/JACK (cosmétique)
# ============================================================
@contextlib.contextmanager
def suppress_native_stderr():
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_stderr_fd = os.dup(2)
    try:
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(saved_stderr_fd, 2)
        os.close(devnull_fd)
        os.close(saved_stderr_fd)


with suppress_native_stderr():
    import pyaudio

# ============================================================
# Whisper (OpenVINO)
# ============================================================
MODEL_ID = "openai/whisper-base"
OV_DEVICE = "GPU"

print(f"Loading Whisper on OpenVINO ({OV_DEVICE})...")
ov_model = OVModelForSpeechSeq2Seq.from_pretrained(MODEL_ID, export=True, device=OV_DEVICE)
processor = AutoProcessor.from_pretrained(MODEL_ID)

asr_pipeline = pipeline(
    "automatic-speech-recognition",
    model=ov_model,
    tokenizer=processor.tokenizer,
    feature_extractor=processor.feature_extractor,
    generate_kwargs={
        "language": "english",
        "task": "transcribe",
        "repetition_penalty": 1.3,
        "no_repeat_ngram_size": 3,
    },
)


# ============================================================
# Audio: enregistrement + transcription
# ============================================================
def record_until_silence(filename="temp.wav",
                          silence_threshold=200,
                          silence_duration=2,
                          min_speech_seconds=0.5):
    with suppress_native_stderr():
        p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                     input=True, frames_per_buffer=1024)
    frames = []
    speech_frames = 0
    silent_chunks = 0
    speaking = False
    silence_chunks_needed = int(16000 / 1024 * silence_duration)
    while True:
        data = stream.read(1024)
        frames.append(data)
        audio_data = np.frombuffer(data, dtype=np.int16)
        volume = np.abs(audio_data).mean()
        if volume > silence_threshold:
            if not speaking:
                print("Listening...")
                speaking = True
            speech_frames += 1
            silent_chunks = 0
        else:
            if speaking:
                silent_chunks += 1
                if silent_chunks > silence_chunks_needed:
                    print("Processing...")
                    break
    stream.stop_stream()
    stream.close()
    p.terminate()

    min_speech_frames = int(16000 / 1024 * min_speech_seconds)
    if speech_frames < min_speech_frames:
        return False

    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
        wf.setframerate(16000)
        wf.writeframes(b''.join(frames))
    return True


def transcribe(filename="temp.wav"):
    result = asr_pipeline(filename)
    return result["text"].strip()


# ============================================================
# TTS (Piper)
# ============================================================
def strip_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"#{1,6}\s*", "", text)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    return text.strip()


def speak(text):
    clean_text = strip_markdown(text)
    try:
        subprocess.run(
            ["piper", "--model", PIPER_VOICE, "--output_file", TTS_OUTPUT_FILE],
            input=clean_text.encode("utf-8"),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with suppress_native_stderr():
            subprocess.run(["aplay", "-q", TTS_OUTPUT_FILE], check=True, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print("[TTS] 'piper' or 'aplay' not found.")
    except subprocess.CalledProcessError as e:
        print(f"[TTS] Error during synthesis/playback: {e}")


# ============================================================
# Pont MCP -> format "tools" attendu par Ollama
# ============================================================
def mcp_tools_to_ollama_format(mcp_tools):
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in mcp_tools
    ]


async def ask_llama_with_tools(mcp_client, ollama_tools, text):
    """
    Envoie le message au LLM avec la liste des tools MCP disponibles.
    Si le LLM décide d'appeler un tool, on l'exécute via le client MCP,
    on renvoie le résultat au LLM, et on récupère sa réponse finale.
    """
    print("Thinking...")
    conversation.append({"role": "user", "content": text})

    response = ollama.chat(
        model=LLM_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + conversation,
        tools=ollama_tools,
        keep_alive="30m",
    )
    message = response["message"]

    # Le LLM a décidé d'appeler un ou plusieurs tools
    if message.get("tool_calls"):
        conversation.append(message)
        for call in message["tool_calls"]:
            tool_name = call["function"]["name"]
            tool_args = call["function"]["arguments"]
            print(f"[MCP tool call: {tool_name}({tool_args})]")
            try:
                result = await mcp_client.call_tool(tool_name, tool_args)
                tool_result_text = result.data if hasattr(result, "data") else str(result)
            except Exception as e:
                # Une erreur robot (ex: "robot is busy") ne doit pas planter l'assistant
                tool_result_text = f"Error: {e}"
            conversation.append({"role": "tool", "content": str(tool_result_text)})

        # Deuxième appel pour que le LLM formule une réponse naturelle
        follow_up = ollama.chat(
            model=LLM_MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + conversation,
            keep_alive="30m",
        )
        reply = follow_up["message"]["content"]
        conversation.append({"role": "assistant", "content": reply})
        return reply

    # Pas de tool call, réponse conversationnelle normale
    reply = message["content"]
    conversation.append({"role": "assistant", "content": reply})
    return reply


# ============================================================
# Boucle principale
# ============================================================
async def main():
    print("Connecting to robot MCP server...")
    async with Client(MCP_SERVER_SCRIPT) as mcp_client:
        mcp_tools = await mcp_client.list_tools()
        ollama_tools = mcp_tools_to_ollama_format(mcp_tools)
        print(f"MCP tools available: {[t.name for t in mcp_tools]}")

        print("Nexo Assistant started. Speak anytime.\n")

        while True:
            typed = input("\n[Press Enter to speak, or type your message]: ").strip()

            if typed:
                text = typed
            else:
                got_audio = record_until_silence()
                if not got_audio:
                    continue
                text = transcribe()
                if not text:
                    continue
                print(f"You : {text}")
            if "quit" in text.lower():
                print("Goodbye!")
                speak("Goodbye!")
                break

            reply = await ask_llama_with_tools(mcp_client, ollama_tools, text)
            print(f"Nexo : {reply}\n")
            speak(reply)


if __name__ == "__main__":
    asyncio.run(main())
