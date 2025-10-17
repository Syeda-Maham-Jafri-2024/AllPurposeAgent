# insurance_agent.py

import os
import logging
import random
import json
import asyncio
from datetime import datetime, date as dt_date
from typing import Optional, List, Dict
from pydantic import BaseModel, EmailStr, field_validator, Field
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

# --------------------- Sample Conversation between the User and the Agent ------------------------
"""
💼 1. Get Contact Info
   - User: What are your office hours?
     Tool Called: get_contact_info(field="office_hours")
     Agent Response: Our office is open Monday to Friday from 9 AM to 6 PM, and on Saturday from 10 AM to 2 PM.

   - User: Can you give me your contact details?
     Tool Called: get_contact_info()
     Agent Response:
     You can reach us at +92-300-1234567 or email support@securelife.com.
     Our office is located at 123 Insurance Avenue, Karachi.

🏥 2. Get Policy Details
   - User: Tell me about your health insurance policy.
     Tool Called: get_policy_details(policy_type="health insurance")
     Agent Response:
     Our health insurance plan covers hospitalization, surgeries, and medical prescriptions.
     It ensures that your medical expenses are well taken care of.

   - User: What about travel insurance?
     Tool Called: get_policy_details(policy_type="travel insurance")
     Agent Response:
     Our travel insurance covers trip cancellations, lost luggage, and medical emergencies during your travel.

🧾 3. View My Policies
   - User: Can you show my active policies? My email is smjafri2002@gmail.com.
     Tool Called: get_policy_info(user_email="smjafri2002@gmail.com")
     Agent Response:
     Sara Khan, you currently have two active policies:
     1️⃣ Health Insurance — Individual coverage, Rs. 40,000 premium, due on January 15, 2026.
     2️⃣ Life Insurance — Term Life coverage, Rs. 50,000 premium, due on May 1, 2026.

💳 4. Check Payment History
   - User: Can you show my payment history?
     Tool Called: get_payment_history(user_email="smjafri2002@gmail.com")
     Agent Response:
     Here’s your payment history:
     • Rs. 40,000 for Health Insurance on Jan 15, 2025 (Debit Card)
     • Rs. 50,000 for Life Insurance on May 1, 2025 (Credit Card)

📄 5. Check Claim Status
   - User: What’s the status of my recent claim?
     Tool Called: get_claim_status(user_email="smjafri2002@gmail.com")
     Agent Response:
     You have two claims:
     ✅ Claim CLM002 for Health Insurance — Approved.
     💼 Claim CLM006 for Life Insurance — Processed and completed.

🩺 6. File a New Claim
   - User: I want to file a claim for my health policy POL654321 for a surgery last week.
     Tool Called: file_claim(request={
        "policy_number": "POL654321",
        "claim_type": "Surgery Reimbursement",
        "incident_date": "2025-10-01",
        "description": "Surgery for appendix removal.",
        "attachments": ["surgery_report.pdf"]
     })
     Agent Response:
     Your claim has been successfully filed under ID CLM2437 and is now under review.
     You’ll receive a confirmation email shortly.

"""


# ------------------ DUMMY INSURANCE DATA ------------------

# Dummy company contact info
CONTACT_INFO = {
    "phone": "+92-300-1234567",
    "email": "support@securelife.com",
    "address": "123 Insurance Avenue, Karachi, Pakistan",
    "office_hours": "Mon-Fri: 9 AM - 6 PM, Sat: 10 AM - 2 PM"
}

# Dummy general policy info
# POLICY_DETAILS = {
#     "car insurance": "Covers damages to your vehicle and liability in case of accidents. Includes comprehensive and third-party coverage.",
#     "travel insurance": "Provides coverage for trip cancellations, medical emergencies, lost luggage, and other travel-related issues.",
#     "health insurance": "Covers medical expenses including hospitalization, surgery, and prescriptions.",
#     "life insurance": "Provides financial protection to beneficiaries in the event of the policyholder's death.",
#     "home insurance": "Covers damages to property caused by fire, theft, natural disasters, and other perils."
# }

POLICY_DETAILS = {
    "car insurance": (
        "Car insurance provides financial protection against losses resulting from accidents, theft, or damage to your vehicle. "
        "It typically includes:\n"
        "• **Comprehensive Coverage:** Protects against theft, fire, vandalism, natural disasters, and non-collision-related damage.\n"
        "• **Third-Party Liability:** Covers injury or property damage you cause to others.\n"
        "• **Collision Coverage:** Pays for damages to your own vehicle due to a collision.\n"
        "• **Personal Accident Cover:** Provides compensation for medical expenses or death benefits for the driver and passengers.\n"
        "• **Optional Add-ons:** Such as zero depreciation, roadside assistance, engine protection, and more."
    ),

    "travel insurance": (
        "Travel insurance protects you against unexpected events while traveling domestically or internationally. "
        "Key benefits include:\n"
        "• **Trip Cancellation or Interruption:** Reimbursement for non-refundable expenses if your trip is canceled or cut short.\n"
        "• **Medical Emergencies Abroad:** Coverage for hospitalization, doctor visits, and medical evacuation.\n"
        "• **Lost or Delayed Baggage:** Compensation for personal belongings that are lost, stolen, or delayed.\n"
        "• **Flight Delays and Missed Connections:** Covers extra expenses due to delays or missed flights.\n"
        "• **24/7 Assistance:** Access to travel and medical support services anywhere in the world."
    ),

    "health insurance": (
        "Health insurance covers the cost of medical care and ensures access to quality healthcare without financial strain. "
        "It generally includes:\n"
        "• **Hospitalization Coverage:** Room charges, doctor fees, nursing, and surgery costs.\n"
        "• **Outpatient Benefits:** Doctor consultations, diagnostic tests, and prescription medicines.\n"
        "• **Maternity Benefits:** Coverage for childbirth and related medical expenses (in select plans).\n"
        "• **Pre- and Post-Hospitalization:** Medical expenses before and after hospitalization.\n"
        "• **Preventive Care:** Annual checkups and vaccinations in some plans.\n"
        "• **Cashless Treatment:** Direct settlement at network hospitals without upfront payment."
    ),

    "life insurance": (
        "Life insurance provides financial security to your loved ones in the event of your death. "
        "It serves as a long-term investment and protection tool. Common types include:\n"
        "• **Term Life Insurance:** Offers pure protection with a fixed payout to beneficiaries upon the policyholder’s death.\n"
        "• **Whole Life Insurance:** Provides lifetime coverage and may include cash value accumulation.\n"
        "• **Endowment Plans:** Combine life coverage with savings benefits payable at maturity.\n"
        "• **Unit-Linked Insurance Plans (ULIPs):** Offer both investment opportunities and life coverage.\n"
        "• **Riders:** Optional add-ons like critical illness, accidental death, or disability coverage."
    ),

    "home insurance": (
        "Home insurance protects your home and its contents against a wide range of risks. "
        "Typical coverage includes:\n"
        "• **Structure Coverage:** Protects the physical structure (walls, roof, foundation) against fire, natural disasters, or vandalism.\n"
        "• **Contents Coverage:** Covers household belongings such as furniture, electronics, and appliances.\n"
        "• **Theft and Burglary Protection:** Compensation for loss of valuables due to theft or break-ins.\n"
        "• **Natural Disaster Protection:** Includes coverage for floods, earthquakes, and storms.\n"
        "• **Liability Coverage:** Protects you from legal liability if someone is injured on your property.\n"
        "• **Additional Living Expenses:** Covers temporary housing costs if your home becomes uninhabitable after a covered event."
    )
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

class LatePaymentRequest(BaseModel):
    """Information required to calculate late payment penalty."""
    premium_amount: float = Field(..., description="Premium amount in rupees.")
    due_date: str = Field(..., description="The due date in YYYY-MM-DD format.")
    paid_date: Optional[str] = Field(None, description="The date when payment was made (YYYY-MM-DD). If not provided, assumes today.")

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
        
        
    async def on_enter(self):
     await self.session.say("Welcome to SecureLife Insurance! How can I assist you today?")

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
    
    from datetime import datetime

    @function_tool()
    async def calculate_late_payment_penalty(self, context: RunContext, request: LatePaymentRequest) -> str:
        """
        Calculates late payment penalty if the premium is overdue.
        Penalty = 1% of premium per week after due date.
        """
        due = datetime.strptime(request.due_date, "%Y-%m-%d")
        paid = datetime.strptime(request.paid_date, "%Y-%m-%d") if request.paid_date else datetime.now()

        if paid <= due:
            return f"No penalty. Payment is on time ✅"

        days_late = (paid - due).days
        weeks_late = days_late // 7 or 1  # round up to 1 week minimum
        penalty = round(request.premium_amount * 0.01 * weeks_late, 2)

        total_due = request.premium_amount + penalty

        return (
            f"📅 Payment was {days_late} days late.\n"
            f"💸 Penalty (1% per week): Rs. {penalty}\n"
            f"Total Amount Due (with penalty): Rs. {total_due}"
        )



