# insurance_agent.py

import os
import logging
import random
import json
import asyncio
from datetime import datetime, date as dt_date
from typing import Optional, List, Dict
from pydantic import BaseModel, EmailStr, field_validator
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
from openai import OpenAI
from dotenv import load_dotenv
from livekit.agents import metrics
from livekit.plugins import openai, silero
from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip
from livekit.agents.llm import ChatMessage
from livekit import rtc
import re
from context import INSURANCE_CONTEXT

logger = logging.getLogger("insurance-voice-agent")
load_dotenv(dotenv_path=".env")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
today = datetime.now().date()

if not hasattr(RunContext, "session_data"):
    RunContext.session_data = {}

# ------------------ DUMMY INSURANCE DATA ------------------

# Dummy company contact info
CONTACT_INFO = {
    "phone": "+92-300-1234567",
    "email": "support@securelife.com",
    "address": "123 Insurance Avenue, Karachi, Pakistan",
    "office_hours": "Mon-Fri: 9 AM - 6 PM, Sat: 10 AM - 2 PM"
}

# Dummy general policy info
POLICY_DETAILS = {
    "car insurance": "Covers damages to your vehicle and liability in case of accidents. Includes comprehensive and third-party coverage.",
    "travel insurance": "Provides coverage for trip cancellations, medical emergencies, lost luggage, and other travel-related issues.",
    "health insurance": "Covers medical expenses including hospitalization, surgery, and prescriptions.",
    "life insurance": "Provides financial protection to beneficiaries in the event of the policyholder's death.",
    "home insurance": "Covers damages to property caused by fire, theft, natural disasters, and other perils."
}

USERS = {
    "ali.raza@example.com": {
        "name": "Ali Raza",
        "policies": [
            {"policy_number": "POL123456", "type": "Car Insurance","coverage": "Comprehensive","premium": 25000, "next_due": "2025-12-01", "status": "Active",},
            {"policy_number": "POL789012","type": "Travel Insurance","coverage": "International","premium": 15000, "next_due": "2025-09-15","status": "Active",},
        ],
    },
    "smjafri2002@gmail.com": {
        "name": "Sara Khan",
        "policies": [
            {"policy_number": "POL654321","type": "Health Insurance","coverage": "Individual", "premium": 40000,"next_due": "2026-01-15","status": "Active", },
            {"policy_number": "POL321987","type": "Life Insurance","coverage": "Term Life","premium": 50000,"next_due": "2026-05-01","status": "Active", },
        ],
    },
    "farhan.ali@example.com": {
        "name": "Farhan Ali",
        "policies": [
            { "policy_number": "POL852963", "type": "Home Insurance", "coverage": "Fire & Theft", "premium": 30000,"next_due": "2025-11-20", "status": "Active",}
        ],
    },
    "noor.javed@example.com": {
        "name": "Noor Javed",
        "policies": [
            {"policy_number": "POL741852", "type": "Health Insurance", "coverage": "Family Plan", "premium": 85000,"next_due": "2026-03-12", "status": "Active",
            }
        ],
    },
}

PAYMENT_HISTORY = {
    "ali.raza@example.com": [
        {"policy_number": "POL123456", "date": "2025-06-01", "amount": 25000, "method": "Credit Card", "transaction_id": "TXN1001"},
        {"policy_number": "POL123456", "date": "2024-06-01", "amount": 25000, "method": "Bank Transfer", "transaction_id": "TXN0784"},
        {"policy_number": "POL789012", "date": "2025-09-01", "amount": 15000, "method": "Credit Card", "transaction_id": "TXN1123"},
    ],
    "sara.khan@example.com": [
        {"policy_number": "POL654321", "date": "2025-01-15", "amount": 40000, "method": "Debit Card", "transaction_id": "TXN1025"},
        {"policy_number": "POL321987", "date": "2025-05-01", "amount": 50000, "method": "Credit Card", "transaction_id": "TXN1189"},
    ],
    "farhan.ali@example.com": [
        {"policy_number": "POL852963", "date": "2025-11-01", "amount": 30000, "method": "Bank Transfer", "transaction_id": "TXN1210"},
    ],
    "noor.javed@example.com": [
        {"policy_number": "POL741852", "date": "2026-03-01", "amount": 85000, "method": "Credit Card", "transaction_id": "TXN1345"},
    ],
}

CLAIMS = {
    "ali.raza@example.com": [
        { "claim_id": "CLM001", "policy_number": "POL123456", "claim_type": "Accident Damage", "incident_date": "2025-07-10", "description": "Minor bumper damage due to collision.",
        "status": "Under Review","attachments": ["photo1.jpg"],
        },
        {"claim_id": "CLM005","policy_number": "POL789012", "claim_type": "Lost Luggage", "incident_date": "2025-09-05","description": "Luggage lost during international travel.",
         "status": "Approved", "attachments": ["luggage_receipt.pdf"],
        }
    ],
    "sara.khan@example.com": [
        {"claim_id": "CLM002", "policy_number": "POL654321", "claim_type": "Medical Reimbursement", "incident_date": "2025-08-03", "description": "Hospitalization due to appendicitis.",
         "status": "Approved", "attachments": ["hospital_bill.pdf"],
        },
        { "claim_id": "CLM006", "policy_number": "POL321987", "claim_type": "Life Insurance Payout", "incident_date": "2025-04-22","description": "Payout due to policy maturity.",
         "status": "Processed","attachments": [],
        }
    ],
    "farhan.ali@example.com": [
        {"claim_id": "CLM003", "policy_number": "POL852963", "claim_type": "Fire Damage", "incident_date": "2025-10-01", "description": "Kitchen fire causing property damage.",
          "status": "Under Review","attachments": ["fire_report.pdf", "photos.zip"],
        }
    ],
    "noor.javed@example.com": [
        {
            "claim_id": "CLM004",
            "policy_number": "POL741852",
            "claim_type": "Hospitalization",
            "incident_date": "2026-02-10",
            "description": "Emergency hospitalization for appendicitis.",
            "status": "Approved",
            "attachments": ["hospital_invoice.pdf"],
        }
    ],
}


# ------------------ FILLER AUDIO ------------------
FILLER_AUDIO = [
        "audio/filler_1.wav", "audio/filler_2.wav", "audio/filler_3.wav", "audio/filler_4.wav", "audio/filler_5.wav", "audio/filler_6.wav", "audio/filler_7.wav", "audio/filler_8.wav",
        "audio/filler_9.wav","audio/filler_10.wav","audio/filler_11.wav","audio/filler_12.wav","audio/filler_13.wav","audio/filler_14.wav","audio/filler_15.wav","audio/filler_16.wav",
        "audio/filler_17.wav","audio/filler_18.wav","audio/filler_19.wav","audio/filler_20.wav","audio/filler_21.wav","audio/filler_22.wav","audio/filler_23.wav","audio/filler_24.wav",
        "audio/filler_25.wav","audio/filler_26.wav","audio/filler_27.wav","audio/filler_28.wav","audio/filler_29.wav","audio/filler_30.wav","audio/filler_31.wav","audio/filler_32.wav",]

CLOSING_RE = re.compile(
    r"^\s*(bye|goodbye|see you|see ya|later|thanks|thank you|that's it|done)[\.\!\?]?\s*$",
    flags=re.IGNORECASE | re.UNICODE,
)

LOG_FILE = "insurance_session_summary.json"

# ------------------ Pydantic Models ------------------
class ClaimRequest(BaseModel):
    policy_number: str
    claim_type: str
    incident_date: datetime
    description: str
    attachments: Optional[List[str]] = []

class PolicyRequest(BaseModel):
    policy_number: str

    @field_validator("policy_number")
    def validate_policy_number(cls, v):
        if not v.startswith("POL") or len(v) < 6:
            raise ValueError("Invalid policy number.")
        return v

# -------------------- Helper utilities --------------------
def send_email(to_email: str, subject: str, body: str) -> bool:
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
    return random.choice(FILLER_AUDIO) if FILLER_AUDIO else None


# ------------------ INSURANCE AGENT ------------------
class InsuranceAgent(Agent):
    def __init__(self, voice: str = "alloy") -> None:
        stt = openai.STT(model="gpt-4o-transcribe", language="en")
        llm_inst = openai.LLM(model="gpt-4o")
        tts = openai.TTS(model="gpt-4o-mini-tts", voice=voice)
        silero_vad = silero.VAD.load()

        super().__init__(
            instructions=(f""" {INSURANCE_CONTEXT}"""),
            stt=stt,
            llm=llm_inst,
            tts=tts,
            vad=silero_vad,
            allow_interruptions=True,
        )

    # -------- Get Contact Info --------
    @function_tool()
    async def get_contact_info(
        self, context: RunContext, field: Optional[str] = None
    ) -> str:
        """
        Retrieve the contact information of SecureLife Insurance.

        This tool can return the full set of contact details, or a specific field such as:
        - phone
        - email
        - address
        - office_hours

        Args:
            context (RunContext): The context of the current conversation.
            field (Optional[str]): Specific field requested. If None, all contact info is returned.

        Returns:
            str: Requested contact detail(s) or an error message if field not found.
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (Get Contact Info):")
        logger.info("-------------------------------------")

        if field:
            field_lower = field.strip().lower()
            if field_lower in CONTACT_INFO:
                logger.info(CONTACT_INFO[field_lower])
                return f"{field_lower.title()}: {CONTACT_INFO[field_lower]}"
            return f"Sorry, no contact info found for '{field}'. Available fields: phone, email, address, office_hours."
        
        # Return full contact info if no field is specified
        return "\n".join([f"{k.title()}: {v}" for k, v in CONTACT_INFO.items()])


    # -------- Get Policy Details --------
    @function_tool()
    async def get_policy_details(
        self, context: RunContext, policy_type: str
    ) -> str:
        """
        Retrieve details of a specific insurance policy type offered by SecureLife Insurance.

        Args:
            context (RunContext): The context of the current conversation.
            policy_type (str): Name of the policy type (e.g., "car insurance", "travel insurance").

        Returns:
            str: Policy description if found, or an error message listing available policy types.
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (Get Policy Details):")
        logger.info("-------------------------------------")

        key = policy_type.strip().lower()
        if key in POLICY_DETAILS:
            logger.info(POLICY_DETAILS[key])
            return f"{policy_type.title()} Policy Details:\n{POLICY_DETAILS[key]}"
        return f"Sorry, we don't have information on '{policy_type}'. Available policies: {', '.join(POLICY_DETAILS.keys())}."

    # -------- Get Policy Info --------
    @function_tool()
    async def get_policy_info(self, context: RunContext, user_email: str) -> str:
        """
        Retrieve all active policy information for a given customer email.

        Args:
            context (RunContext): The context of the current conversation.
            user_email (str): Email of the user requesting policy details.

        Returns:
            str: List of policies with number, type, coverage, premium, next due date, and status.
                 Returns a not-found message if user or policies are missing.
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (Get Policy Info):")
        logger.info("-------------------------------------")

        if user_email not in USERS:
            return "User not found."
        user = USERS[user_email]
        policies = user["policies"]
        if not policies:
            return "No active policies found."
        response = [f"Policies for {user['name']}:"]
        for p in policies:
            response.append(
                f"- Policy Number: {p['policy_number']}\n"
                f"  Type: {p['type']}\n"
                f"  Coverage: {p['coverage']}\n"
                f"  Premium: Rs.{p['premium']}\n"
                f"  Next Due: {p['next_due']}\n"
                f"  Status: {p['status']}"
            )
        return "\n\n".join(response)


    # -------- Get Payment History --------
    @function_tool()
    async def get_payment_history(self, context: RunContext, user_email: str) -> str:
        """
        Retrieve payment history for a specific customer email.

        Args:
            context (RunContext): The context of the current conversation.
            user_email (str): Email of the user requesting payment history.

        Returns:
            str: A chronological list of payments with policy number, date, amount, payment method, and transaction ID.
                 Returns a not-found message if no payment history exists.
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (Get Payment History):")
        logger.info("-------------------------------------")
        if user_email not in PAYMENT_HISTORY:
            return "No payment history found for your account."
        history = "\n".join(
            [f"{p['date']} (Policy {p['policy_number']}): Rs.{p['amount']} via {p['method']} (Txn: {p['transaction_id']})"
            for p in PAYMENT_HISTORY[user_email]]
        )
        return f"Payment history for {user_email}:\n{history}"


    # -------- File Claim --------
    @function_tool()
    async def file_claim(self, context: RunContext, user_email: str, request: ClaimRequest) -> str:
        """
        File a new insurance claim if the user owns the policy.

        Args:
            context (RunContext): Conversation context.
            user_email (str): Email of the user filing the claim.
            request (ClaimRequest): Claim details.

        Returns:
            str: Confirmation or error message.
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (File Claim):")
        logger.info("-------------------------------------")

        # Check if user exists
        if user_email not in USERS:
            return "User not found."

        # Check if user owns the given policy number
        owns_policy = any(p["policy_number"] == request.policy_number for p in USERS[user_email]["policies"])
        if not owns_policy:
            return "❌ You do not have this policy number. Please check your policy details."

        # Create claim
        claim_id = f"CLM{random.randint(1000, 9999)}"
        claim_data = {
            "claim_id": claim_id,
            "policy_number": request.policy_number,
            "claim_type": request.claim_type,
            "incident_date": request.incident_date.strftime("%Y-%m-%d"),
            "description": request.description,
            "status": "Under Review",
            "attachments": request.attachments or [],
        }

        if user_email not in CLAIMS:
            CLAIMS[user_email] = []
        CLAIMS[user_email].append(claim_data)

        # Send confirmation email
        send_email(
            to_email=user_email,
            subject="Claim Filed Successfully",
            body=f"Dear {USERS[user_email]['name']},\n\n"
                f"Your claim ({claim_id}) has been filed successfully and is under review.\n\n"
                f"Details:\n{claim_data}"
        )

        return f"✅ Claim filed successfully! Claim ID: {claim_id}. " \
            f"A confirmation email has been sent to {user_email}."



    # -------- Get Claim Status --------
    @function_tool()
    async def get_claim_status(self, context: RunContext, user_email: str, claim_id: Optional[str] = None) -> str:
        """
        Retrieve the status of claims filed by the user.

        Args:
            context (RunContext): The context of the current conversation.
            user_email (str): Email of the user requesting claim status.
            claim_id (Optional[str]): Specific claim ID to query. If None, returns all claims for the user.

        Returns:
            str: Claim details including claim ID, policy number, type, date, status, and description.
                 Returns an error message if no claims are found or claim ID does not match.
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (Get Claim Status):")
        logger.info("-------------------------------------")

        if user_email not in CLAIMS:
            return "No claims found for your account."
        claims = CLAIMS[user_email]
        results = []
        for claim in claims:
            if not claim_id or claim["claim_id"] == claim_id:
                results.append(
                    f"Claim ID: {claim['claim_id']}\n"
                    f"Policy Number: {claim['policy_number']}\n"
                    f"Type: {claim['claim_type']}\n"
                    f"Date: {claim['incident_date']}\n"
                    f"Status: {claim['status']}\n"
                    f"Description: {claim['description']}"
                )
        return "\n\n".join(results) if results else "No matching claims found."


# -------------------- Agent lifecycle & entrypoint --------------------
def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

async def entrypoint(ctx: JobContext):
    filler_task = None
    logger.info(f"Connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    participant = await ctx.wait_for_participant()
    logger.info(f"Starting insurance voice assistant for participant {participant.identity}")

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        min_endpointing_delay=0.9,
        max_endpointing_delay=5.0,
    )

    agent = InsuranceAgent()
    usage_collector = metrics.UsageCollector()
    conversation_log = []

    @session.on("user_message")
    def on_user_message(msg):
        nonlocal filler_task
        if msg.text.strip():
            conversation_log.append({"role": "user", "text": msg.text, "timestamp": datetime.utcnow().isoformat()})
            text = msg.text.lower().strip()
            if CLOSING_RE.match(text):
                if filler_task and not filler_task.done():
                    filler_task.cancel()
                return
            async def delayed_filler():
                await asyncio.sleep(1.0)
                filler = random.choice(FILLER_AUDIO)
                await background_audio.set_thinking([AudioConfig(filler, volume=0.9)])
            filler_task = asyncio.create_task(delayed_filler())

    @session.on("assistant_message")
    def on_assistant_message(msg):
        if msg.text.strip():
            conversation_log.append({"role": "assistant", "text": msg.text, "timestamp": datetime.utcnow().isoformat()})

    @ctx.room.on("participant_connected")
    def on_connected(remote: rtc.RemoteParticipant):
        logger.info("-------- Call Started -------")

    @ctx.room.on("participant_disconnected")
    def on_finished(remote: rtc.RemoteParticipant):
        summary = usage_collector.get_summary()
        record = {"conversation": conversation_log, "metrics": summary.__dict__}
        with open("insurance_agent_log.json", "a", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.write("\n")
        logger.info("Session record saved.")

    ctx.call_start = datetime.utcnow()
    await session.start(room=ctx.room, agent=agent, room_input_options=RoomInputOptions())

    global background_audio
    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.5),
        thinking_sound=[AudioConfig(f, volume=0.9) for f in FILLER_AUDIO] if FILLER_AUDIO else [],
    )
    await background_audio.start(room=ctx.room, agent_session=session)

    await session.say("Welcome to SecureLife Insurance — your virtual assistant. How can I help you today?")

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
