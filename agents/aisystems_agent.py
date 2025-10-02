import logging
import asyncio
import json
import os
from pathlib import Path
import random
from openai import OpenAI
from openai import AsyncOpenAI
import requests
from dotenv import load_dotenv
from urllib.parse import urlencode
from livekit.agents import (
    Agent,
    RunContext,
    function_tool,
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    AutoSubscribe,
    RoomInputOptions,
)
from livekit.agents import metrics
from livekit.plugins import openai, silero
from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip
from livekit.agents.llm import ChatMessage
from context import CONTEXT
from datetime import datetime
from livekit import rtc
import re
from pydantic import BaseModel, Field, EmailStr
from typing import Optional
import re

logger = logging.getLogger("mayfairtech-voice-agent")
load_dotenv(dotenv_path=".env")

if not hasattr(RunContext, "session_data"):
    RunContext.session_data = {}

CONTACT_INFO = {
    "address": "123 Innovation Avenue, Karachi, Pakistan",
    "phone": "+92 21 3567 8910",
    "email": "support@mayfairtech.ai",
    "office_hours": "Mon–Fri: 9:00 AM – 6:00 PM, Sat: 10:00 AM – 2:00 PM, Sun: Closed",
}


AMBIENT_AUDIO_FILES = [
    "audio/ambience1.mp3",
    "audio/ambience2.mp3",
    "audio/ambience3.mp3",
    "audio/ambience4.mp3",
    "audio/ambience5.mp3",
    "audio/ambience6.mp3",
    "audio/ambience7.mp3",
    # ... up to 10–15
]

LOG_FILE = "session_summary.json"


# ====== Pydantic models for the Agent ======

class ContactRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str | None = None
    subject: str
    message: str

    # --- Validators ---
    @field_validator("name")
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be empty.")
        if len(v) < 2:
            raise ValueError("Name must be at least 2 characters long.")
        return v.strip()

    @field_validator("phone")
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # Basic international phone regex
        pattern = re.compile(r"^\+?[0-9\s\-]{7,15}$")
        if not pattern.match(v):
            raise ValueError("Invalid phone number format.")
        return v.strip()

    @field_validator("subject")
    def validate_subject(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Subject cannot be empty.")
        if len(v) < 3:
            raise ValueError("Subject must be at least 3 characters long.")
        return v.strip()

    @field_validator("message")
    def validate_message(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Message cannot be empty.")
        if len(v) < 10:
            raise ValueError("Message must be at least 10 characters long.")
        return v.strip()


#  ------------------------- Helper functions ----------------------------------
def send_email(to_email: str, subject: str, body: str) -> bool:
    """
    Simple SMTP-based sender. Uses EMAIL_USER and EMAIL_APP_PASSWORD from env (.env).
    Returns True if send succeeded, False otherwise.
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    sender = os.getenv("EMAIL_USER")
    pwd = os.getenv("EMAIL_APP_PASSWORD")
    if not sender or not pwd:
        logger.warning("Email credentials not set; skipping email send.")
        return False

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, pwd)
            server.send_message(msg)
        logger.info(f"Email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False

async def rotate_ambience(background_audio, interval=180):
    """Randomly rotate ambience every `interval` seconds."""
    while True:
        new_file = random.choice(AMBIENT_AUDIO_FILES)
        print(f"🔊 Switching ambience to {new_file}")
        await background_audio.set_ambient(AudioConfig(new_file, volume=0.6))
        await asyncio.sleep(interval)

# ----------------------------------- AGENT CLASS -----------------------------------


class MayfairTechAgent(Agent):
    def __init__(self, voice: str = "cedar") -> None:
        stt = openai.STT(
            model="gpt-4o-transcribe",
            language="en",
            prompt="ALways transcribe in English or Urdu",
        )
        llm_inst = openai.LLM(model="gpt-4o")
        tts = openai.TTS(model="gpt-4o-mini-tts", voice=voice)
        silero_vad = silero.VAD.load()

        super().__init__(
            instructions=(f""" {CONTEXT}"""),
            stt=stt,
            llm=llm_inst,
            tts=tts,
            vad=silero_vad,
            allow_interruptions=True,
        )

    # ------------------ FLOW 1: Company Info ------------------
    @function_tool()
    async def get_company_info(self, query: str, context: RunContext) -> str:
        """
        Retrieves only the relevant company information section from about_company.md
        by letting the LLM select the best-matching Question, then returning the full Q/A.

        Args:
            query (str): The user's query related to the company.
            context (RunContext): The current run context for the agent.

        Returns:
            str: The most relevant Q/A pair from the company information markdown file.
        """

        fileloc = Path("info/")
        filenam = "about_company.md"

        with open(fileloc / filenam, "r", encoding="utf-8") as f:
            markdown_text = f.read()

        # --- Split into Q/A pairs ---
        sections = re.split(r"(## Q\d+:.*)", markdown_text)
        qa_pairs = {}
        for i in range(1, len(sections), 2):
            question = sections[i].strip()
            answer = sections[i + 1].strip() if i + 1 < len(sections) else ""
            qa_pairs[question] = answer

        # --- Just give questions to the LLM ---
        questions_only = "\n".join(list(qa_pairs.keys()))
        logger.info(f"Question list: {questions_only}")

        llm_prompt = f"""
        A user asked: "{query}"

        Here are the possible questions from the company info:

        {questions_only}

        Please return ONLY the most relevant question from the list above. 
        Do not return the answer, just the question as written.
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a selector system."},
                {"role": "user", "content": llm_prompt},
            ],
            max_tokens=100,
        )

        selected_question = response.choices[0].message.content.strip()

        # --- Get the matching answer ---
        answer = qa_pairs.get(
            selected_question, "Sorry, I couldn’t find relevant company info."
        )

        result = f"{selected_question}\n{answer}"
        logger.info(f"Selected QA Pair: {result}")

        return result

    # ------------------ FLOW 2: Leadership Team ------------------
    @function_tool()
    async def get_leadership_team(self, context: RunContext) -> str:
        """
        Retrieves information about the leadership team from a markdown file.

        This function reads the contents of 'LeaderShipTeam.md' located in the 'Knowledge repo' directory and returns it as a string.

        Args:
            context (RunContext): The current run context for the agent.

        Returns:
            str: The contents of the leadership team markdown file.
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (Get Leader Info):")
        logger.info("-------------------------------------")
        fileloc = Path("info/")
        filenam = "leadership_team.md"
        with open(fileloc / filenam, "r", encoding="utf-8") as f:
            markdown_text = f.read()
        return markdown_text

    # ------------------ FLOW 3: Customer Support ------------------
    @function_tool()
    async def get_contact_info(
        self, context: RunContext, field: Optional[str] = None
    ) -> str:
        """
        Retrieves contact information.
        Args:
            field (str): 'phone', 'email', 'address', or 'office_hours'
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (Get Contact Info):")
        logger.info("-------------------------------------")

        if field and field in CONTACT_INFO:
            logger.info(CONTACT_INFO[field])
            return CONTACT_INFO[field]
        return CONTACT_INFO

    # ------------------ FLOW 4: Contact Company ------------------
    @function_tool()
    async def contact_company(self, context: RunContext, contact: ContactRequest) -> dict:
        """
        Situation:
            Called when the user provides details to contact the company.
            Returns a preview of the contact request and requires explicit confirmation.
        Args:
            context (RunContext): conversation context
            contact (ContactRequest): validated contact request
        Returns:
            dict: {
                "contact_id": str,
                "summary": str,
                "requires_confirmation": True
            }
        """
        logger.info(f"Creating contact request preview for {contact.name} <{contact.email}>")

        contact_id = f"CTC{random.randint(10000, 99999)}"

        summary_lines = [
            f"Contact Request Preview (ID: {contact_id})",
            f"Name: {contact.name}",
            f"Email: {contact.email}",
            f"Phone: {contact.phone or 'Not provided'}",
            f"Subject: {contact.subject}",
            f"Message: {contact.message}",
            "",
            "Please confirm to finalize submitting this contact request."
        ]
        summary = "\n".join(summary_lines)

        # save pending contact in session (until user confirms)
        context.session_data["pending_contact"] = {
            "id": contact_id,
            "request": contact.model_dump(),
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }

        logger.info(f"Pending contact request saved: {contact_id}")

        return {
            "contact_id": contact_id,
            "summary": summary,
            "requires_confirmation": True,
        }

    # ------------------ FLOW 5: Confirm Contact Request Email ------------------
    @function_tool()
    async def confirm_contact_request(self, context: RunContext) -> dict:
        """Confirm and finalize the pending contact request, then send emails."""
        pending = context.session_data.get("pending_contact")
        if not pending:
            return {"error": "No pending contact request to confirm."}

        pending["status"] = "confirmed"
        context.session_data["last_contact"] = pending
        context.session_data.pop("pending_contact", None)

        # --- Email Sending ---
        request = pending["request"]
        user_email = request["email"]
        company_email = os.getenv("COMPANY_EMAIL", "company@example.com")

        # Email to user
        user_subject = "✅ Your Contact Request Has Been Received"
        user_body = (
            f"Hi {request['name']},\n\n"
            f"Thank you for contacting us regarding '{request['subject']}'. "
            "Our team will review your message and get back to you shortly.\n\n"
            "Best regards,\nAI Solutions Company"
        )
        send_email(user_email, user_subject, user_body)

        # Email to company
        company_subject = f"📩 New Contact Request from {request['name']}"
        company_body = (
            f"New contact request submitted:\n\n"
            f"Name: {request['name']}\n"
            f"Email: {request['email']}\n"
            f"Phone: {request['phone']}\n"
            f"Subject: {request['subject']}\n"
            f"Message:\n{request['message']}\n\n"
            f"Request ID: {pending['id']}"
        )
        send_email(company_email, company_subject, company_body)

        logger.info(f"Contact request confirmed and emails sent: {pending['id']}")

        return {
            "contact_id": pending["id"],
            "status": "confirmed",
            "message": "Your contact request has been submitted and a confirmation email has been sent."
        }

    # ------------------ FLOW 6: Products ------------------
    @function_tool()
    async def get_products(self, context: RunContext) -> str:
        """
        Retrieves information about the company's products from a markdown file.

        This function reads the contents of 'products.md' located in the 'info' directory
        and returns it as a string.

        Args:
            context (RunContext): The current run context for the agent.

        Returns:
            str: The contents of the products markdown file.
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (Get Products):")
        logger.info("-------------------------------------")
        fileloc = Path("info/")
        filenam = "products.md"
        with open(fileloc / filenam, "r", encoding="utf-8") as f:
            markdown_text = f.read()
        return markdown_text


    # ------------------ FLOW 7: Services ------------------
    @function_tool()
    async def get_services(self, context: RunContext) -> str:
        """
        Retrieves information about the company's services from a markdown file.

        This function reads the contents of 'services.md' located in the 'info' directory
        and returns it as a string.

        Args:
            context (RunContext): The current run context for the agent.

        Returns:
            str: The contents of the services markdown file.
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (Get Services):")
        logger.info("-------------------------------------")
        fileloc = Path("info/")
        filenam = "services.md"
        with open(fileloc / filenam, "r", encoding="utf-8") as f:
            markdown_text = f.read()
        return markdown_text


# ------------------ AGENT LIFECYCLE ------------------
def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Wait for the first participant
    participant = await ctx.wait_for_participant()
    logger.info(f"starting voice assistant for participant {participant.identity}")

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        min_endpointing_delay=0.9,
        max_endpointing_delay=5.0,
    )

    agent = MayfairTechAgent()
    usage_collector = metrics.UsageCollector()

    # Store conversation in memory
    conversation_log = []

    # --- Collect from session (AgentMetrics)
    @session.on("metrics_collected")
    def on_agent_metrics(agent_metrics: metrics.AgentMetrics):
        usage_collector.collect(agent_metrics)

    # --- Collect directly from engines
    @agent.llm.on("metrics_collected")
    def on_llm_metrics(llm_metrics: metrics.LLMMetrics):
        usage_collector.collect(llm_metrics)

    @agent.stt.on("metrics_collected")
    def on_stt_metrics(stt_metrics: metrics.STTMetrics):
        usage_collector.collect(stt_metrics)

    @agent.tts.on("metrics_collected")
    def on_tts_metrics(tts_metrics: metrics.TTSMetrics):
        usage_collector.collect(tts_metrics)

    # --- Capture conversation turns (FIXED)
    @session.on("user_message")
    def on_user_message(msg):
        if msg.text.strip():
            conversation_log.append(
                {
                    "role": "user",
                    "text": msg.text,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

    @session.on("assistant_message")
    def on_assistant_message(msg):
        if msg.text.strip():
            conversation_log.append(
                {
                    "role": "assistant",
                    "text": msg.text,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

    # --- Track call lifecycle
    @ctx.room.on("participant_connected")
    def on_connected(remote: rtc.RemoteParticipant):
        print("participant connected")
        ctx.call_start = datetime.utcnow()
        print("-------- Call Started -------", ctx.call_start)

    @ctx.room.on("participant_disconnected")
    def on_finished(remote: rtc.RemoteParticipant):
        call_start = getattr(ctx, "call_start", None)
        call_end = datetime.utcnow()

        if call_start:
            duration_minutes = (call_end - call_start).total_seconds() / 60.0
        else:
            duration_minutes = 0.0

        summary = usage_collector.get_summary()
        summary_dict = summary.__dict__ if hasattr(summary, "__dict__") else summary

        record = {
            "session_id": datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
            "metrics": summary_dict,
            "duration_minutes": duration_minutes,
            "conversation": conversation_log,
        }

        # Append to JSON file (NDJSON style, one session per line)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.write("\n")

        print("✅ Record saved to JSON:", record["session_id"])

    # --- Start the session
    ctx.call_start = datetime.utcnow()
    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=RoomInputOptions(),
    )

    # # --- Background ambience + thinking sounds
    # background_audio = BackgroundAudioPlayer(
    #     ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.6),
    #     thinking_sound=[
    #         AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.7),
    #         AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.6),
    #     ],
    # )
    # await background_audio.start(room=ctx.room, agent_session=session)

    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(random.choice(AMBIENT_AUDIO_FILES), volume=0.6),
        thinking_sound=[
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.7),
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.6),
        ],
    )

    await background_audio.start(room=ctx.room, agent_session=session)

    # Start background rotation loop
    asyncio.create_task(rotate_ambience(background_audio, interval=180))

    # --- Greeting
    await session.say("Hi, I’m your MayfairTech Assistant! How can I help you today?")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )


# okay so this is the code currently, now what i want is that once the form is submitted the user is emailed about this as well, so we can send an email (for now from a set gmail address) to user's email address