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

# from context import CONTEXT
from datetime import datetime
from livekit import rtc
import re
from pydantic import BaseModel, Field, EmailStr, field_validator
from typing import Optional
import re
from openai import OpenAI

logger = logging.getLogger("aisystems-voice-agent")
load_dotenv(dotenv_path=".env")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

if not hasattr(RunContext, "session_data"):
    RunContext.session_data = {}

CONTACT_INFO = {
    "address": "D-38, Block 5 Clifton, Karachi, Pakistan",
    "phone": "+92 326 367057",
    "email": "info@theaisystems.com",
    "office_hours": "Mon–Fri: 9:00 AM – 6:00 PM, Sat, Sun: Closed",
}

#     # --- Filler audio list (short clips, e.g. wav files)
FILLER_AUDIO = [
    "audio/filler_1.wav",
    "audio/filler_2.wav",
    "audio/filler_3.wav",
    "audio/filler_4.wav",
    "audio/filler_5.wav",
    "audio/filler_6.wav",
    "audio/filler_7.wav",
    "audio/filler_8.wav",
    "audio/filler_9.wav",
    "audio/filler_10.wav",
    "audio/filler_11.wav",
    "audio/filler_12.wav",
    "audio/filler_13.wav",
    "audio/filler_14.wav",
    "audio/filler_15.wav",
    "audio/filler_16.wav",
    "audio/filler_17.wav",
    "audio/filler_18.wav",
    "audio/filler_19.wav",
    "audio/filler_20.wav",
    "audio/filler_21.wav",
    "audio/filler_22.wav",
    "audio/filler_23.wav",
    "audio/filler_24.wav",
    "audio/filler_25.wav",
    "audio/filler_26.wav",
    "audio/filler_27.wav",
    "audio/filler_28.wav",
    "audio/filler_29.wav",
    "audio/filler_30.wav",
    "audio/filler_31.wav",
    "audio/filler_32.wav",
]

CLOSING_RE = re.compile(
    r"^\s*(bye|goodbye|see you|see ya|later|thanks(?:\s+all)?|thank you|that's it|that is all|no that's all|talk soon|i'm done|done)[\.\!\?]?\s*$",
    flags=re.IGNORECASE | re.UNICODE,
)

LOG_FILE = "aisystems_session_summary.json"

# ====== Pydantic models for the Agent ======
from pydantic import BaseModel, EmailStr, field_validator
import re


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


def get_random_filler():
    """Pick one random filler audio from the list."""
    return random.choice(FILLER_AUDIO)


# ----------------------------------- AGENT CLASS -----------------------------------


class AISystemsAgent(Agent):
    def __init__(self, voice: str = "cedar") -> None:
        stt = openai.STT(
            model="gpt-4o-transcribe",
            language="en",
            prompt="ALways transcribe in English or Urdu",
        )
        llm_inst = openai.LLM(model="gpt-4.1")
        tts = openai.TTS(model="gpt-4o-mini-tts", voice=voice)
        silero_vad = silero.VAD.load()

        super().__init__(
            # instructions=(f""" {CONTEXT}"""),
            instructions=f"You are an agent that responds to queries related to the company The AI Systems",
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
        by letting the LLM select the best-matching heading, then returning the full section.

        Args:
            query (str): The user's query related to the company.
            context (RunContext): The current run context for the agent.

        Returns:
            str: The most relevant section (heading + content) from the company information markdown file.
        """

        fileloc = Path("info/")
        filenam = "about_company.md"

        with open(fileloc / filenam, "r", encoding="utf-8") as f:
            markdown_text = f.read()

        # --- Split into sections by headings starting with ##
        sections = re.split(r"(^## .*)", markdown_text, flags=re.MULTILINE)
        section_map = {}

        for i in range(1, len(sections), 2):
            heading = sections[i].strip()
            content = sections[i + 1].strip() if i + 1 < len(sections) else ""
            section_map[heading] = content

        # --- Just give headings to the LLM ---
        headings_only = "\n".join(list(section_map.keys()))
        logger.info(f"Headings list: {headings_only}")

        llm_prompt = f"""
        A user asked: "{query}"

        Here are the possible sections from the company info:

        {headings_only}

        Please return ONLY the most relevant heading from the list above. 
        Do not return the content, just the heading exactly as written.
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a selector system."},
                {"role": "user", "content": llm_prompt},
            ],
            max_tokens=100,
        )

        selected_heading = response.choices[0].message.content.strip()

        # --- Get the matching content ---
        content = section_map.get(
            selected_heading, "Sorry, I couldn’t find relevant company info."
        )

        result = f"{selected_heading}\n{content}"
        logger.info(f"Selected Section: {result}")

        return result

    # ------------------ FLOW 2: Get Company Solutions ------------------
    @function_tool()
    async def get_company_solution(self, query: str, context: RunContext) -> str:
        """
        Retrieves company solution information from solutions.md.
        - If the query is general (about all solutions), return the full file.
        - If the query is specific, select the most relevant solution section.
        """

        fileloc = Path("info/")
        filenam = "solutions.md"

        with open(fileloc / filenam, "r", encoding="utf-8") as f:
            markdown_text = f.read()

        # --- Split into sections by headings starting with ##
        sections = re.split(r"(^## .*)", markdown_text, flags=re.MULTILINE)
        section_map = {}

        for i in range(1, len(sections), 2):
            heading = sections[i].strip()
            content = sections[i + 1].strip() if i + 1 < len(sections) else ""
            section_map[heading] = content

        headings_only = "\n".join(list(section_map.keys()))

        # --- Step 1: Ask LLM if query is general or specific ---
        classification_prompt = f"""
        A user asked: "{query}"

        The company has a list of solutions with these headings:
        {headings_only}

        Your task:
        - If the query is asking generally about all solutions, respond with: GENERAL
        - If the query is asking about one specific solution, respond with: SPECIFIC
        Do not include anything else in your answer.
        """

        classification = (
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a classification system."},
                    {"role": "user", "content": classification_prompt},
                ],
                max_tokens=10,
            )
            .choices[0]
            .message.content.strip()
        )

        logger.info(f"Solution query classified as: {classification}")

        # --- Step 2: If GENERAL → return entire document ---
        if classification.upper() == "GENERAL":
            return markdown_text

        # --- Step 3: If SPECIFIC → pick the most relevant heading ---
        llm_prompt = f"""
        A user asked: "{query}"

        Here are the possible solution categories offered by the company:

        {headings_only}

        Please return ONLY the most relevant heading from the list above. 
        Do not return the content, just the heading exactly as written.
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a selector system."},
                {"role": "user", "content": llm_prompt},
            ],
            max_tokens=100,
        )

        selected_heading = response.choices[0].message.content.strip()

        # --- Get the matching content ---
        content = section_map.get(
            selected_heading, "Sorry, I couldn’t find relevant solution info."
        )

        result = f"{selected_heading}\n{content}"
        logger.info(f"Selected Solution Section: {result}")

        return result

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
    async def contact_company(
        self, context: RunContext, contact: ContactRequest
    ) -> dict:
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
        logger.info(
            f"Creating contact request preview for {contact.name} <{contact.email}>"
        )

        contact_id = f"CTC{random.randint(10000, 99999)}"

        summary_lines = [
            f"Contact Request Preview (ID: {contact_id})",
            f"Name: {contact.name}",
            f"Email: {contact.email}",
            f"Phone: {contact.phone or 'Not provided'}",
            f"Subject: {contact.subject}",
            f"Message: {contact.message}",
            "",
            "Please confirm to finalize submitting this contact request.",
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

    # ------------------ FLOW 5: Confirm or Cancel Contact Request ------------------
    @function_tool()
    async def confirm_contact_request(self, action: str, context: RunContext) -> dict:
        """
        Confirm or cancel the pending contact request, then handle emails if confirmed.

        Args:
            action (str): Either "confirm" or "cancel".
            context (RunContext): The current run context for the agent.

        Returns:
            dict: Result of the operation with status and message.
        """
        pending = context.session_data.get("pending_contact")
        if not pending:
            return {"error": "No pending contact request to process."}

        if action.lower() == "confirm":
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
                "Best regards,\nThe AI Systems"
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
                "message": "Your contact request has been submitted and a confirmation email has been sent.",
            }

        elif action.lower() == "cancel":
            context.session_data.pop("pending_contact", None)
            logger.info("Pending contact request cancelled by user.")
            return {
                "status": "cancelled",
                "message": "Your contact request has been cancelled.",
            }

        else:
            return {"error": "Invalid action. Use 'confirm' or 'cancel'."}

    # ------------------ FLOW 6: Products ------------------
    @function_tool()
    async def get_company_product(self, query: str, context: RunContext) -> str:
        """
        Retrieves company product information from products.md.
        - If the query is general (about all products), return the full file.
        - If the query is specific, select the most relevant product section.
        """

        fileloc = Path("info/")
        filenam = "products.md"

        with open(fileloc / filenam, "r", encoding="utf-8") as f:
            markdown_text = f.read()

        # --- Split into sections by headings starting with ##
        sections = re.split(r"(^## .*)", markdown_text, flags=re.MULTILINE)
        section_map = {}

        for i in range(1, len(sections), 2):
            heading = sections[i].strip()
            content = sections[i + 1].strip() if i + 1 < len(sections) else ""
            section_map[heading] = content

        headings_only = "\n".join(list(section_map.keys()))

        # --- Step 1: Ask LLM if query is general or specific ---
        classification_prompt = f"""
        A user asked: "{query}"

        The company has a list of products with these headings:
        {headings_only}

        Your task:
        - If the query is asking generally about all products, respond with: GENERAL
        - If the query is asking about one specific product, respond with: SPECIFIC
        Keep it concise while responding.
        """

        classification = (
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a classification system."},
                    {"role": "user", "content": classification_prompt},
                ],
                max_tokens=10,
            )
            .choices[0]
            .message.content.strip()
        )

        logger.info(f"Product query classified as: {classification}")

        # --- Step 2: If GENERAL → return entire document ---
        if classification.upper() == "GENERAL":
            return markdown_text

        # --- Step 3: If SPECIFIC → pick the most relevant heading ---
        llm_prompt = f"""
        A user asked: "{query}"

        Here are the possible product categories offered by the company:

        {headings_only}

        Please return ONLY the most relevant heading from the list above. 
        Do not return the content, just the heading exactly as written.
        Keep it concise while responding.
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a selector system."},
                {"role": "user", "content": llm_prompt},
            ],
            max_tokens=100,
        )

        selected_heading = response.choices[0].message.content.strip()

        # --- Get the matching content ---
        content = section_map.get(
            selected_heading, "Sorry, I couldn’t find relevant product info."
        )

        result = f"{selected_heading}\n{content}"
        logger.info(f"Selected Product Section: {result}")

        return result
