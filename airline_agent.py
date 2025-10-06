# airline_agent.py
import logging
import asyncio
import json
import os
from pathlib import Path
import random
from datetime import datetime
import re
from typing import Optional, List
from dotenv import load_dotenv
from context import AIRLINE_CONTEXT
# OpenAI client(s)
from openai import OpenAI

# LiveKit agent libs (same as your original)
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
from livekit import rtc
from datetime import datetime, timedelta
import random
from pydantic import BaseModel, EmailStr, field_validator, model_validator

logger = logging.getLogger("airline-voice-agent")
load_dotenv(dotenv_path=".env")

# OpenAI client for helper calls (mirrors your usage)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
today = datetime.now().date()
# Session data fallback
if not hasattr(RunContext, "session_data"):
    RunContext.session_data = {}

# Airline contact / meta info
AIRLINE_INFO = {
    "name": "SkyBridge Airlines",
    "address": "Terminal 2, International Airport, Karachi, Pakistan",
    "phone": "+92 300 1234567",
    "email": "support@skybridgeair.com",
    "office_hours": "24/7 — customer service available",
    "website": "https://www.skybridgeair.com",
}

# -------------------- Dummy Data for Airline Simulation --------------------

# Dummy flight schedules (daily)
DUMMY_FLIGHTS = [
    {"flight_number": "SB101","origin": "KHI","destination": "DXB","departure": "08:00","arrival": "10:00","duration": "2h 00m","fare": "PKR 45,000","gate": "A12","terminal": "T1","status": "On Time", "date": str(today),},
    {"flight_number": "SB202","origin": "KHI","destination": "LHR","departure": "14:30","arrival": "19:30","duration": "7h 00m","fare": "PKR 145,000","gate": "B3","terminal": "T2", "status": "Delayed 30 minutes due to weather","date": str(today + timedelta(days=1)),},
    {"flight_number": "SB303","origin": "LHE","destination": "IST","departure": "09:00","arrival": "13:00","duration": "4h 00m","fare": "PKR 85,000","gate": "C5","terminal": "T1","status": "Departed", "date": str(today + timedelta(days=2)), },
    {"flight_number": "SB404","origin": "ISB","destination": "JED","departure": "06:00","arrival": "09:30","duration": "3h 30m","fare": "PKR 70,000","gate": "D9","terminal": "T3","status": "Cancelled due to technical reasons","date": str(today), },
    {"flight_number": "SB505","origin": "DXB","destination": "KHI","departure": "22:00","arrival": "00:30","duration": "2h 30m","fare": "PKR 48,000","gate": "E2","terminal": "T1","status": "Boarding in progress","date": str(today + timedelta(days=1)),},
    {"flight_number": "SB606","origin": "KHI","destination": "DOH","departure": "11:00","arrival": "12:45","duration": "1h 45m","fare": "PKR 55,000","gate": "A5","terminal": "T2","status": "On Time","date": str(today),},
    {"flight_number": "SB707","origin": "LHE","destination": "DXB","departure": "16:00","arrival": "18:00","duration": "2h 00m", "fare": "PKR 49,500","gate": "C7","terminal": "T1","status": "On Time","date": str(today + timedelta(days=3)),},
    {"flight_number": "SB808","origin": "ISB","destination": "KUL","departure": "01:30","arrival": "10:00","duration": "6h 30m","fare": "PKR 120,000","gate": "D2","terminal": "T3","status": "On Time", "date": str(today + timedelta(days=2)),},
    {"flight_number": "SB909","origin": "KHI","destination": "ISB","departure": "12:15","arrival": "13:45","duration": "1h 30m","fare": "PKR 25,000","gate": "A1","terminal": "T1","status": "On Time","date": str(today),},
    {"flight_number": "SB010","origin": "LHE","destination": "KHI","departure": "18:45","arrival": "20:15","duration": "1h 30m","fare": "PKR 24,000","gate": "C4","terminal": "T1","status": "Delayed 15 minutes due to traffic","date": str(today + timedelta(days=4)),},
    {"flight_number": "SB111","origin": "DXB","destination": "LHE","departure": "03:00","arrival": "07:00","duration": "4h 00m","fare": "PKR 50,000","gate": "E8","terminal": "T2","status": "Boarding soon","date": str(today + timedelta(days=1)),},
    {"flight_number": "SB212","origin": "KHI","destination": "DEL","departure": "10:00","arrival": "11:15","duration": "1h 15m","fare": "PKR 40,000","gate": "B6","terminal": "T1","status": "On Time",  "date": str(today + timedelta(days=2)),},
    {"flight_number": "SB313","origin": "ISB", "destination": "PEK","departure": "20:00","arrival": "04:30","duration": "6h 30m", "fare": "PKR 155,000","gate": "D11","terminal": "T3","status": "On Time","date": str(today + timedelta(days=3)),},
    {"flight_number": "SB414","origin": "LHE","destination": "BOM","departure": "07:00","arrival": "08:15","duration": "1h 15m","fare": "PKR 42,000","gate": "C2","terminal": "T2","status": "Departed","date": str(today),},
    {"flight_number": "SB515","origin": "KHI","destination": "MCT","departure": "05:30","arrival": "07:15","duration": "1h 45m","fare": "PKR 52,000","gate": "A9","terminal": "T1", "status": "On Time", "date": str(today + timedelta(days=1)),},
]


# Dummy loyalty members
DUMMY_LOYALTY_MEMBERS = {
    "SB12345": {"tier": "Silver", "miles_balance": 12450, "valid_until": "2026-03-01", "benefits": ["Priority check-in", "Extra baggage allowance"]},
    "SB67890": {"tier": "Gold", "miles_balance": 30200, "valid_until": "2026-09-15", "benefits": ["Lounge access", "Free seat upgrades", "Priority boarding"]},
    "SB99999": {"tier": "Platinum", "miles_balance": 78000, "valid_until": "2027-01-10", "benefits": ["First-class upgrades", "Personal travel assistant", "Unlimited lounge access"]},
}

# Dummy baggage rules
DUMMY_BAGGAGE = {
    "economy": "1 checked bag up to 23kg + 1 carry-on bag (7kg)",
    "premium economy": "2 checked bags up to 23kg each + 1 carry-on bag (7kg)",
    "business": "2 checked bags up to 32kg each + 1 carry-on bag (10kg)",
    "first": "3 checked bags up to 32kg each + 2 carry-on bags (12kg total)"
}

# Dummy cancellation policies
DUMMY_CANCELLATION_POLICY = (
    "Refundable fares can be cancelled up to 24 hours before departure with a 10% fee. "
    "Non-refundable fares can only be rebooked for a change fee of PKR 10,000 plus fare difference."
)

# Dummy booking storage (for simulation only)
# ---------------- Dummy Booking Records ----------------
from datetime import datetime, timedelta, timezone

DUMMY_BOOKINGS = [
    {"booking_id": "BK10001", "passenger": "Ali Raza","email": "ali.raza@example.com","flight_number": "SB101","route": "KHI → DXB","seat_class": "Economy","num_passengers": 1,"total_fare": "PKR 45,000","date": str(datetime.now().date()),"timestamp": datetime.now(timezone.utc).isoformat(),},
    {"booking_id": "BK10002","passenger": "Sara Khan","email": "sara.khan@example.com","flight_number": "SB202","route": "KHI → LHR","seat_class": "Business","num_passengers": 2,"total_fare": "PKR 290,000","date": str(datetime.now().date() + timedelta(days=1)),"timestamp": datetime.now(timezone.utc).isoformat(),},
    {"booking_id": "BK10003","passenger": "Zain Ahmed","email": "zain.ahmed@example.com","flight_number": "SB909","route": "KHI → ISB", "seat_class": "Economy","num_passengers": 1,"total_fare": "PKR 25,000","date": str(datetime.now().date()),"timestamp": datetime.now(timezone.utc).isoformat(),},
]

 #     # --- Filler audio list (short clips, e.g. wav files)
FILLER_AUDIO = [
        "audio/filler_1.wav", "audio/filler_2.wav", "audio/filler_3.wav", "audio/filler_4.wav", "audio/filler_5.wav", "audio/filler_6.wav", "audio/filler_7.wav", "audio/filler_8.wav",
        "audio/filler_9.wav","audio/filler_10.wav","audio/filler_11.wav","audio/filler_12.wav","audio/filler_13.wav","audio/filler_14.wav","audio/filler_15.wav","audio/filler_16.wav",
        "audio/filler_17.wav","audio/filler_18.wav","audio/filler_19.wav","audio/filler_20.wav","audio/filler_21.wav","audio/filler_22.wav","audio/filler_23.wav","audio/filler_24.wav",
        "audio/filler_25.wav","audio/filler_26.wav","audio/filler_27.wav","audio/filler_28.wav","audio/filler_29.wav","audio/filler_30.wav","audio/filler_31.wav","audio/filler_32.wav",]

CLOSING_RE = re.compile(
    r"^\s*(bye|goodbye|see you|see ya|later|thanks|thank you|that's it|done)[\.\!\?]?\s*$",
    flags=re.IGNORECASE | re.UNICODE,
)

LOG_FILE = "airline_session_summary.json"

# -------------------- Pydantic models --------------------
from pydantic import BaseModel, Field
from typing import Optional

class FlightStatusInput(BaseModel):
    flight_number: Optional[str] = Field(None, description="Flight number like SB101")
    origin: Optional[str] = Field(None, description="Origin airport code like KHI")
    destination: Optional[str] = Field(None, description="Destination airport code like DXB")
    date: Optional[str] = Field(None, description="Date of flight in YYYY-MM-DD format")

class FlightSearchInput(BaseModel):
    location: Optional[str] = Field(None, description="City or airport to search from")
    origin: Optional[str] = Field(None, description="Origin airport code like KHI")
    destination: Optional[str] = Field(None, description="Destination airport code like DXB")
    date: Optional[str] = Field(None, description="Flight date in YYYY-MM-DD format")


class BookingLookupInput(BaseModel):
    booking_id: Optional[str] = Field(None, description="Booking ID assigned during flight reservation (e.g., BK12345)")
    email: Optional[EmailStr] = Field(None, description="Email address used at the time of booking")

    @model_validator(mode="after")
    def at_least_one(cls, values):
        if not values.booking_id and not values.email:
            raise ValueError("Either booking_id or email must be provided to look up a booking.")
        return values


class FlightBookingInput(BaseModel):
    full_name: str = Field(..., description="Passenger full name as per passport")
    email: EmailStr = Field(..., description="Valid email address to send booking confirmation")
    flight_number: str = Field(..., description="Selected flight number (e.g., SB101)")
    num_passengers: int = Field(..., gt=0, le=9, description="Number of passengers to book, 1–9")
    seat_class: str = Field(..., description="Travel class such as economy, business, or first")
    confirm: Optional[bool] = Field(False, description="True to confirm booking after preview")



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


# -------------------- Agent Definition --------------------
class AirlineAgent(Agent):
    def __init__(self, voice: str = "cedar") -> None:
        stt = openai.STT(
            model="gpt-4o-transcribe",
            language="en",
            prompt="Always transcribe in English or Urdu"
        )
        llm_inst = openai.LLM(model="gpt-4o")
        tts = openai.TTS(model="gpt-4o-mini-tts", voice=voice)
        silero_vad = silero.VAD.load()

        super().__init__(
            instructions=(f""" {AIRLINE_CONTEXT}"""),
            stt=stt,
            llm=llm_inst,
            tts=tts,
            vad=silero_vad,
            allow_interruptions=True,
        )

    # ---------------- Flow: Flight Status ----------------
    @function_tool()
    async def check_flight_status(
        self,
        flight_info: FlightStatusInput,
        context: RunContext = None,
    ) -> dict:
        """
        Situation:
            Called when the user asks for the status of a specific flight.
            The user may provide a flight number (preferred) or a combination
            of origin, destination, and date. Uses stored dummy data to
            simulate real-time flight information retrieval.

        Args:
            context (RunContext): Conversation context provided by LiveKit.
            flight_info (FlightStatusInput): Validated input containing one or
                more of the following — flight_number, origin, destination, date.

        Returns:
            dict: {
                "flight_number": str,
                "route": str,              # e.g., "KHI → DXB"
                "departure": str,          # e.g., "08:00"
                "arrival": str,            # e.g., "10:00"
                "terminal": str,           # e.g., "T1"
                "gate": str,               # e.g., "A12"
                "status": str,             # e.g., "On Time", "Delayed", "Cancelled"
                "date": str                # e.g., "2025-10-06"
            }
            or
            {"error": "No matching flight found."}
        """
        logger.info(f"🔍 Checking flight status: {flight_info}")

        matched_flights = []

        if flight_info.flight_number:
            matched_flights = [
                f for f in DUMMY_FLIGHTS
                if f["flight_number"].lower() == flight_info.flight_number.lower()
            ]
        elif flight_info.origin and flight_info.destination:
            matched_flights = [
                f for f in DUMMY_FLIGHTS
                if f["origin"].lower() == flight_info.origin.lower()
                and f["destination"].lower() == flight_info.destination.lower()
                and (not flight_info.date or f["date"] == flight_info.date)
            ]

        if not matched_flights:
            return {"error": "No matching flight found. Please check the details."}

        flight = matched_flights[0]
        return {
            "flight_number": flight["flight_number"],
            "route": f"{flight['origin']} → {flight['destination']}",
            "departure": flight["departure"],
            "arrival": flight["arrival"],
            "terminal": flight["terminal"],
            "gate": flight["gate"],
            "status": flight["status"],
            "date": flight["date"],
        }


    # ------------------ Flight Search ------------------
    @function_tool()
    async def search_flights(
        self,
        search_info: FlightSearchInput,
        context: RunContext = None,
    ) -> dict:
        """
        Situation:
            Called when the user wants to explore or browse available flights.
            The query may include location, origin, destination, and/or date.
            Searches dummy flight data to return a list of matching results.

        Args:
            context (RunContext): Conversation context provided by LiveKit.
            search_info (FlightSearchInput): Validated input containing
                search parameters such as location, origin, destination, or date.

        Returns:
            dict: {
                "message": str,           # Summary of results
                "flights": [              # List of matching flights
                    {
                        "flight_number": str,
                        "route": str,      # e.g., "KHI → DXB"
                        "departure": str,
                        "arrival": str,
                        "fare": str,       # e.g., "PKR 45,000"
                        "date": str,
                        "status": str
                    },
                    ...
                ]
            }
            or
            {"message": "No flights found for your search."}
        """
        logger.info(f"🔍 Searching flights: {search_info}")

        location = search_info.location.lower() if search_info.location else None
        origin = search_info.origin.lower() if search_info.origin else None
        destination = search_info.destination.lower() if search_info.destination else None
        date = search_info.date

        matched_flights = []
        for flight in DUMMY_FLIGHTS:
            if date and flight["date"] != date:
                continue
            if location:
                if location in (flight["origin"].lower(), flight["destination"].lower()):
                    matched_flights.append(flight)
            elif origin and destination:
                if flight["origin"].lower() == origin and flight["destination"].lower() == destination:
                    matched_flights.append(flight)
            elif origin:
                if flight["origin"].lower() == origin:
                    matched_flights.append(flight)
            elif destination:
                if flight["destination"].lower() == destination:
                    matched_flights.append(flight)

        if not matched_flights:
            return {"message": f"No flights found for your search."}

        return {
            "message": f"Found {len(matched_flights)} flights.",
            "flights": [
                {
                    "flight_number": f["flight_number"],
                    "route": f"{f['origin']} → {f['destination']}",
                    "departure": f["departure"],
                    "arrival": f["arrival"],
                    "fare": f["fare"],
                    "date": f["date"],
                    "status": f["status"],
                }
                for f in matched_flights
            ],
        }

    # ---------------- Flow: Flight Booking --------------------
    @function_tool()
    async def book_flight(
        self,
        booking_info: FlightBookingInput,
        context: RunContext = None,
    ) -> dict:
        """
        Situation:
            Called when the user provides details to book a flight.
            The assistant first generates a booking preview for confirmation.
            Once the user confirms (confirm=True), the booking is finalized and
            a confirmation email is sent to the user.

        Args:
            context (RunContext): Conversation context provided by LiveKit.
            booking_info (FlightBookingInput): Validated input containing
                passenger and flight details along with confirmation status.

        Returns:
            dict: 
                When confirm=False (preview stage):
                    {
                        "booking_preview": {
                            "passenger": str,
                            "flight_number": str,
                            "route": str,
                            "departure": str,
                            "arrival": str,
                            "seat_class": str,
                            "fare": str,
                            "num_passengers": int,
                            "total_fare": str,
                            "requires_confirmation": True
                        }
                    }

                When confirm=True (finalized booking):
                    {
                        "booking_id": str,
                        "message": str,
                        "email_sent": bool
                    }

                or 
                    {"error": "Invalid flight number or booking data."}
        """
        logger.info(f"🧾 Booking flight: {booking_info}")

        # Find the flight
        flight = next(
            (f for f in DUMMY_FLIGHTS if f["flight_number"].lower() == booking_info.flight_number.lower()),
            None,
        )
        if not flight:
            return {"error": "Invalid flight number. Please check and try again."}

        # Extract fare amount numerically
        try:
            fare_amount = int(re.sub(r"[^\d]", "", flight["fare"]))
        except ValueError:
            fare_amount = 0

        total_cost = fare_amount * booking_info.num_passengers
        total_fare_str = f"PKR {total_cost:,.0f}"

        # If user has not confirmed yet, return preview
        if not booking_info.confirm:
            preview = {
                "passenger": booking_info.full_name,
                "flight_number": flight["flight_number"],
                "route": f"{flight['origin']} → {flight['destination']}",
                "departure": flight["departure"],
                "arrival": flight["arrival"],
                "seat_class": booking_info.seat_class.title(),
                "fare": flight["fare"],
                "num_passengers": booking_info.num_passengers,
                "total_fare": total_fare_str,
                "requires_confirmation": True,
            }
            return {"booking_preview": preview}

        # If confirmed, finalize booking
        booking_id = f"BK{random.randint(10000, 99999)}"
        record = {
            "booking_id": booking_id,
            "passenger": booking_info.full_name,
            "email": booking_info.email,
            "flight_number": flight["flight_number"],
            "route": f"{flight['origin']} → {flight['destination']}",
            "seat_class": booking_info.seat_class,
            "num_passengers": booking_info.num_passengers,
            "total_fare": total_fare_str,
            "date": flight["date"],
            "timestamp": datetime.utcnow().isoformat(),
        }

        DUMMY_BOOKINGS.append(record)

        # Prepare email body
        email_body = (
            f"Dear {booking_info.full_name},\n\n"
            f"Your booking with {AIRLINE_INFO['name']} has been confirmed.\n\n"
            f"Booking ID: {booking_id}\n"
            f"Flight: {flight['flight_number']} ({flight['origin']} → {flight['destination']})\n"
            f"Departure: {flight['departure']} | Arrival: {flight['arrival']}\n"
            f"Class: {booking_info.seat_class.title()}\n"
            f"Passengers: {booking_info.num_passengers}\n"
            f"Total Fare: {total_fare_str}\n"
            f"Date: {flight['date']}\n\n"
            "Thank you for choosing SkyBridge Airlines!\n"
            f"{AIRLINE_INFO['website']}"
        )

        # Send confirmation email
        email_status = send_email(booking_info.email, f"Booking Confirmation - {booking_id}", email_body)

        return {
            "booking_id": booking_id,
            "message": f"Booking confirmed for {booking_info.full_name}. Confirmation sent via email.",
            "email_sent": email_status,
        }
    
    # ---------------- Flow: View Booking Status --------------------
    @function_tool()
    async def view_booking_status(
        self,
        lookup: BookingLookupInput,
        context: RunContext = None,
    ) -> dict:
        """
        Situation:
            Called when the user asks to check their existing flight booking status.
            The assistant retrieves the booking from stored data using either
            the booking ID or the email address.

        Args:
            context (RunContext): Conversation context provided by LiveKit.
            lookup (BookingLookupInput): Booking lookup parameters, can include
                either a booking_id or email.

        Returns:
            dict:
                If found:
                    {
                        "booking_id": str,
                        "passenger": str,
                        "email": str,
                        "flight_number": str,
                        "route": str,
                        "seat_class": str,
                        "num_passengers": int,
                        "total_fare": str,
                        "date": str,
                        "status": str
                    }
                If not found:
                    {"error": "No booking found for the provided details."}
        """
        logger.info(f"🔎 Checking booking status for: {lookup}")

        matched_booking = None

        if lookup.booking_id:
            matched_booking = next(
                (b for b in DUMMY_BOOKINGS if b["booking_id"].lower() == lookup.booking_id.lower()),
                None,
            )
        elif lookup.email:
            matched_booking = next(
                (b for b in DUMMY_BOOKINGS if b["email"].lower() == lookup.email.lower()),
                None,
            )

        if not matched_booking:
            return {"error": "No booking found for the provided details."}

        # Get flight status if available
        flight = next(
            (f for f in DUMMY_FLIGHTS if f["flight_number"].lower() == matched_booking["flight_number"].lower()),
            None,
        )

        current_status = flight["status"] if flight else "Flight not found in schedule"

        booking_status = {
            "booking_id": matched_booking["booking_id"],
            "passenger": matched_booking["passenger"],
            "email": matched_booking["email"],
            "flight_number": matched_booking["flight_number"],
            "route": matched_booking["route"],
            "seat_class": matched_booking["seat_class"],
            "num_passengers": matched_booking["num_passengers"],
            "total_fare": matched_booking["total_fare"],
            "date": matched_booking["date"],
            "status": current_status,
        }

        logger.info(f"✅ Booking found: {booking_status}")
        return booking_status


    # ---------------- Flow: Baggage allowance policies ----------------
    @function_tool()
    async def baggage_allowance(
        self,
        seat_class: Optional[str] = None,
        context: RunContext = None,
    ) -> dict:
        """
        Situation:
            Called when the user asks about baggage allowance or luggage policy.
            Can handle both general inquiries (all classes) or specific ones
            (e.g., "What's the baggage limit for business class?").

        Args:
            context (RunContext): Conversation context provided by LiveKit.
            seat_class (Optional[str]): Cabin class name like "economy", "business", etc.

        Returns:
            dict:
                If seat_class provided and found:
                    {
                        "seat_class": str,
                        "allowance": str
                    }
                If seat_class not provided:
                    {
                        "message": str,
                        "allowances": {class: rule, ...}
                    }
                If invalid class:
                    {"error": "Invalid seat class. Please specify economy, premium economy, business, or first."}
        """
        logger.info(f"🎒 Checking baggage allowance for: {seat_class}")

        # Normalize and search
        if seat_class:
            key = seat_class.strip().lower()
            if key in DUMMY_BAGGAGE:
                return {"seat_class": key.title(), "allowance": DUMMY_BAGGAGE[key]}
            else:
                return {"error": "Invalid seat class. Please specify economy, premium economy, business, or first."}

        # General overview
        return {
            "message": "Here is the baggage allowance by cabin class:",
            "allowances": {k.title(): v for k, v in DUMMY_BAGGAGE.items()},
        }


    # ---------------- Flow: Policies & Contact Info ----------------
    @function_tool()
    async def get_airline_info(self, field: Optional[str] = None, context: RunContext = None) -> dict:
        """
        Returns contact or policy information. If field provided, return that key.
        """
        if field and field in AIRLINE_INFO:
            return {field: AIRLINE_INFO[field]}
        return AIRLINE_INFO

    @function_tool()
    async def cancellation_policy(self, context: RunContext = None) -> str:
        """
        Describe cancellation and refund policy (concise).
        """
        # default summary — replace with true policy text
        return (
            "Our standard cancellation policy: Refunds depend on fare rules. "
            "Non-refundable fares cannot be refunded but may be rebooked for a change fee. "
            "Refundable fares are subject to processing fees. For exact terms check your fare conditions or provide your PNR."
        )


# -------------------- Agent lifecycle & entrypoint --------------------
def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()
    # prewarm other resources if needed

async def entrypoint(ctx: JobContext):
    filler_task = None
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    participant = await ctx.wait_for_participant()
    logger.info(f"starting airline voice assistant for participant {participant.identity}")

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        min_endpointing_delay=0.9,
        max_endpointing_delay=5.0,
    )

    agent = AirlineAgent()
    usage_collector = metrics.UsageCollector()
    conversation_log = []

    @session.on("metrics_collected")
    def on_agent_metrics(agent_metrics: metrics.AgentMetrics):
        usage_collector.collect(agent_metrics)

    @agent.llm.on("metrics_collected")
    def on_llm_metrics(llm_metrics: metrics.LLMMetrics):
        usage_collector.collect(llm_metrics)

    @agent.stt.on("metrics_collected")
    def on_stt_metrics(stt_metrics: metrics.STTMetrics):
        usage_collector.collect(stt_metrics)

    @agent.tts.on("metrics_collected")
    def on_tts_metrics(tts_metrics: metrics.TTSMetrics):
        usage_collector.collect(tts_metrics)

    # user message capture + filler scheduling
    @session.on("user_message")
    def on_user_message(msg):
        nonlocal filler_task
        if msg.text.strip():
            conversation_log.append({"role": "user", "text": msg.text, "timestamp": datetime.utcnow().isoformat()})
            text = msg.text.lower().strip()

            if not hasattr(session, "ending"):
                session.ending = False

            if CLOSING_RE.match(text):
                session.ending = True
                if filler_task and not filler_task.done():
                    filler_task.cancel()
                    asyncio.create_task(background_audio.clear_thinking())
                logger.info("Detected closing phrase; cancelling filler.")
                return

            if session.ending:
                logger.info("Session ending; suppressing filler.")
                return

            async def delayed_filler():
                await asyncio.sleep(1.0)
                filler = get_random_filler()
                if filler:
                    await background_audio.set_thinking([AudioConfig(filler, volume=0.9)])

            filler_task = asyncio.create_task(delayed_filler())

    @session.on("assistant_message")
    def on_assistant_message(msg):
        if msg.text.strip():
            conversation_log.append({"role": "assistant", "text": msg.text, "timestamp": datetime.utcnow().isoformat()})
        asyncio.create_task(background_audio.clear_thinking())

    @ctx.room.on("participant_connected")
    def on_connected(remote: rtc.RemoteParticipant):
        ctx.call_start = datetime.utcnow()
        logger.info("-------- Call Started -------")

    @ctx.room.on("participant_disconnected")
    def on_finished(remote: rtc.RemoteParticipant):
        call_start = getattr(ctx, "call_start", None)
        call_end = datetime.utcnow()
        duration_minutes = (call_end - call_start).total_seconds() / 60.0 if call_start else 0.0
        summary = usage_collector.get_summary()
        summary_dict = summary.__dict__ if hasattr(summary, "__dict__") else summary
        record = {
            "session_id": datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
            "metrics": summary_dict,
            "duration_minutes": duration_minutes,
            "conversation": conversation_log,
        }
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.write("\n")
        logger.info(f"✅ Record saved to JSON: {record['session_id']}")

    # start session
    ctx.call_start = datetime.utcnow()
    await session.start(room=ctx.room, agent=agent, room_input_options=RoomInputOptions())

    # background audio and filler
    global background_audio
    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.AIRPORT_AMBIENCE if hasattr(BuiltinAudioClip, "AIRPORT_AMBIENCE") else BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.5),
        thinking_sound=[AudioConfig(f, volume=0.9) for f in FILLER_AUDIO] if FILLER_AUDIO else [],
    )
    await background_audio.start(room=ctx.room, agent_session=session)

    # greeting
    await session.say(f"Welcome to {AIRLINE_INFO['name']} — I’m your virtual assistant. How can I help you with your travel today?")

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
