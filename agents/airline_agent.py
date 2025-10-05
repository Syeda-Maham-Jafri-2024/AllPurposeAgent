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

# Pydantic for validation
from pydantic import BaseModel, EmailStr, field_validator

logger = logging.getLogger("airline-voice-agent")
load_dotenv(dotenv_path=".env")

# OpenAI client for helper calls (mirrors your usage)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
    {"flight_number": "SB101", "origin": "KHI", "destination": "DXB", "departure": "08:00", "arrival": "10:00", "duration": "2h 00m", "fare": "PKR 45,000", "gate": "A12", "terminal": "T1"},
    {"flight_number": "SB202", "origin": "KHI", "destination": "LHR", "departure": "14:30", "arrival": "19:30", "duration": "7h 00m", "fare": "PKR 145,000", "gate": "B3", "terminal": "T2"},
    {"flight_number": "SB303", "origin": "LHE", "destination": "IST", "departure": "09:00", "arrival": "13:00", "duration": "4h 00m", "fare": "PKR 85,000", "gate": "C5", "terminal": "T1"},
    {"flight_number": "SB404", "origin": "ISB", "destination": "JED", "departure": "06:00", "arrival": "09:30", "duration": "3h 30m", "fare": "PKR 70,000", "gate": "D9", "terminal": "T3"},
    {"flight_number": "SB505", "origin": "DXB", "destination": "KHI", "departure": "22:00", "arrival": "00:30", "duration": "2h 30m", "fare": "PKR 48,000", "gate": "E2", "terminal": "T1"},
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
DUMMY_BOOKINGS = []


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
    r"^\s*(bye|goodbye|see you|see ya|later|thanks|thank you|that's it|done)[\.\!\?]?\s*$",
    flags=re.IGNORECASE | re.UNICODE,
)

LOG_FILE = "airline_session_summary.json"

# -------------------- Pydantic models --------------------
class Passenger(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    phone: Optional[str] = None
    passport_number: Optional[str] = None

    @field_validator("first_name", "last_name")
    def name_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) < 1:
            raise ValueError("Name must not be empty.")
        return v

    @field_validator("phone")
    def valid_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        pattern = re.compile(r"^\+?[0-9\s\-]{7,15}$")
        if not pattern.match(v):
            raise ValueError("Invalid phone number format.")
        return v.strip()

class BookingRequest(BaseModel):
    origin: str
    destination: str
    departure_date: str  # ISO date or natural language — LLM will help normalize
    return_date: Optional[str] = None
    passengers: List[Passenger]
    cabin_class: Optional[str] = "economy"
    direct_only: Optional[bool] = False

    @field_validator("origin", "destination", "departure_date")
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty.")
        return v.strip()

class CheckInRequest(BaseModel):
    booking_reference: str
    passenger_last_name: str
    seat_preference: Optional[str] = None

    @field_validator("booking_reference")
    def ref_valid(cls, v: str) -> str:
        if not re.match(r"^[A-Z0-9]{4,8}$", v.strip(), flags=re.IGNORECASE):
            # many airlines use 6-char PNR; relax this pattern if needed
            return v.strip()
        return v.strip()

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



# ---- Stubbed external integration functions (replace with real APIs) ----
def lookup_flight_status(flight_number: str, date: Optional[str] = None) -> dict:
    """
    Returns dummy flight status information from DUMMY_FLIGHTS.
    """
    logger.info(f"Looking up flight status for {flight_number} on {date}")
    for flight in DUMMY_FLIGHTS:
        if flight["flight_number"].lower() == flight_number.lower():
            # Randomly vary status for realism
            status = random.choice(["On Time", "Delayed", "Boarding", "Departed"])
            delay_minutes = random.choice([0, 15, 30, 45]) if status == "Delayed" else 0
            return {
                "flight_number": flight["flight_number"],
                "status": status,
                "scheduled_departure": f"{date or datetime.now().date()}T{flight['departure']}:00",
                "estimated_departure": f"{date or datetime.now().date()}T{flight['departure'] if delay_minutes == 0 else '0'+str(int(flight['departure'].split(':')[0]) + 1)+':'+flight['departure'].split(':')[1]}:00",
                "gate": flight["gate"],
                "terminal": flight["terminal"],
            }
    return {"error": f"No flight found for number {flight_number}"}


def search_available_flights(request: BookingRequest) -> List[dict]:
    """
    Search DUMMY_FLIGHTS that match origin/destination.
    """
    matches = [
        f for f in DUMMY_FLIGHTS
        if f["origin"].lower() == request.origin.lower()
        and f["destination"].lower() == request.destination.lower()
    ]
    if not matches:
        return [{"message": f"No direct flights found from {request.origin} to {request.destination}. Please try another date."}]
    return [
        {
            "option_id": f"OPT{random.randint(1000,9999)}",
            "itinerary": f"{f['origin']} → {f['destination']}",
            "departure": f"{request.departure_date}T{f['departure']}:00",
            "arrival": f"{request.departure_date}T{f['arrival']}:00",
            "duration": f["duration"],
            "fare": f["fare"],
            "stops": 0,
            "cabin": request.cabin_class,
        }
        for f in matches
    ]


def create_booking(booking_req: BookingRequest) -> dict:
    """
    Creates a dummy booking and stores it locally.
    """
    pnr = f"SB{random.randint(100000,999999)}"
    booking = {
        "pnr": pnr,
        "status": "BOOKED",
        "booking_reference": pnr,
        "details": booking_req.model_dump(),
    }
    DUMMY_BOOKINGS.append(booking)
    logger.info(f"Created dummy booking: {pnr}")
    return booking


def checkin_passenger(checkin_req: CheckInRequest) -> dict:
    """
    Dummy check-in using PNR.
    """
    boarding_pass_url = f"https://dummy.skybridgeair.com/boarding/{checkin_req.booking_reference}"
    seat = checkin_req.seat_preference or random.choice(["12A", "14C", "23B", "16F"])
    return {
        "booking_reference": checkin_req.booking_reference,
        "status": "CHECKED_IN",
        "seat_assigned": seat,
        "boarding_pass_url": boarding_pass_url,
    }


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
            instructions=f"You are an assistant for {AIRLINE_INFO['name']}. Help users with flight info, bookings, check-in, baggage, loyalty and contact queries.",
            stt=stt,
            llm=llm_inst,
            tts=tts,
            vad=silero_vad,
            allow_interruptions=True,
        )

    # ---------------- Flow: Flight Status ----------------
    @function_tool()
    async def flight_status(self, flight_number: str, date: Optional[str], context: RunContext) -> dict:
        """
        Return flight status for a given flight number and optional date.
        """
        # validate / normalize flight number quickly
        flight_number = flight_number.strip().upper()
        if not re.match(r"^[A-Z]{2,3}\s?\d{1,4}$", flight_number):
            # Let LLM or caller still try; but warn
            logger.info(f"Received non-standard flight number format: {flight_number}")

        status = lookup_flight_status(flight_number, date)
        return status

    # ---------------- Flow: Flight Search & Booking Preview ----------------
    @function_tool()
    async def search_flights(self, request: BookingRequest, context: RunContext) -> dict:
        """
        Search for flights matching the booking request.
        Returns a short list of options.
        """
        options = search_available_flights(request)
        # save options in session for later confirm step
        context.session_data["last_search_options"] = options
        return {"options": options}

    @function_tool()
    async def preview_booking(self, option_id: str, context: RunContext) -> dict:
        """
        Provide a booking preview for a previously searched option_id.
        """
        options = context.session_data.get("last_search_options", [])
        chosen = next((o for o in options if o.get("option_id") == option_id), None)
        if not chosen:
            return {"error": "Selected option not found. Please search flights first."}
        # create a preview summary
        summary = {
            "option_id": chosen["option_id"],
            "itinerary": chosen["itinerary"],
            "departure": chosen["departure"],
            "arrival": chosen["arrival"],
            "fare": chosen["fare"],
            "passengers_required": "Please provide passenger details to confirm booking.",
            "requires_confirmation": True,
        }
        # temporarily cache preview
        context.session_data["pending_booking_preview"] = {"option": chosen, "timestamp": datetime.utcnow().isoformat()}
        return summary

    @function_tool()
    async def confirm_booking(self, booking_req: BookingRequest, context: RunContext) -> dict:
        """
        Create a booking from the booking request and return PNR and details.
        (This requires payment integration in a real system; here it's a stub.)
        """
        # create booking in (stub) system
        created = create_booking(booking_req)
        context.session_data["last_booking"] = created
        # send confirmation email to lead passenger (if available)
        lead_email = booking_req.passengers[0].email if booking_req.passengers else None
        if lead_email:
            subject = f"Booking Confirmation - {created['pnr']}"
            body = f"Your booking is confirmed. PNR: {created['pnr']}\nDetails: {json.dumps(created['details'], indent=2)}"
            send_email(lead_email, subject, body)
        return created

    # ---------------- Flow: Check-in ----------------
    @function_tool()
    async def check_in(self, checkin: CheckInRequest, context: RunContext) -> dict:
        """
        Check in a passenger and return boarding pass info.
        """
        result = checkin_passenger(checkin)
        # save check-in result in session
        context.session_data.setdefault("checkins", []).append(result)
        # optionally email boarding pass link
        last_booking = context.session_data.get("last_booking")
        if last_booking:
            # try to get email from booking details
            try:
                lead_email = last_booking["details"]["passengers"][0]["email"]
                send_email(lead_email, f"Boarding Pass {result['booking_reference']}", f"Get your boarding pass: {result['boarding_pass_url']}")
            except Exception:
                pass
        return result

    # ---------------- Flow: Baggage allowance & policies ----------------
    @function_tool()
    async def baggage_allowance(self, route_or_class: Optional[str] = None, context: RunContext = None) -> str:
        """
        Return baggage allowance information. If route_or_class is provided, attempt to give tailored rules.
        """
        if route_or_class:
            # a quick LLM-based normalization to detect cabin class from phrase
            prompt = f"User asked about baggage allowance for: {route_or_class}. Answer concisely with typical allowance for economy, premium economy, business and first class."
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user", "content": prompt}],
                max_tokens=200,
            )
            return resp.choices[0].message.content.strip()
        # default policy (example)
        return (
            "Standard allowance: Economy — 1 checked bag up to 23kg + 1 carry-on; "
            "Business — 2 checked bags up to 32kg each + 1 carry-on. "
            "Excess baggage fees apply for overweight/extra items. For accurate allowance please provide your ticket class and route."
        )

    # ---------------- Flow: Seat selection ----------------
    @function_tool()
    async def select_seat(self, booking_reference: str, passenger_last_name: str, seat: Optional[str] = None, context: RunContext = None) -> dict:
        """
        Reserve or change seat. (Stubbed — integrate with seat map service.)
        """
        assigned = seat or "23A"
        logger.info(f"Assigning seat {assigned} for {passenger_last_name} PNR {booking_reference}")
        result = {"booking_reference": booking_reference, "passenger_last_name": passenger_last_name, "seat_assigned": assigned, "status": "SEAT_ASSIGNED"}
        # save
        context.session_data.setdefault("seats", []).append(result)
        return result

    # ---------------- Flow: Loyalty program ----------------
    @function_tool()
    async def loyalty_lookup(self, member_id: str, context: RunContext = None) -> dict:
        """
        Lookup loyalty account details (stub).
        """
        logger.info(f"Looking up loyalty member {member_id}")
        # dummy response
        return {
            "member_id": member_id,
            "tier": "Silver",
            "miles_balance": 12450,
            "valid_until": "2026-03-01",
            "benefits": ["Priority check-in", "Extra baggage allowance"]
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

    # ---------------- Flow: Alerts (user subscription) ----------------
    @function_tool()
    async def subscribe_flight_alerts(self, booking_reference: str, contact_email: Optional[EmailStr] = None, context: RunContext = None) -> dict:
        """
        Subscribe user to flight alerts for a booking_reference or flight.
        In a real system, connect to push/notify service (SMS/email).
        """
        sub_id = f"ALRT{random.randint(10000,99999)}"
        logger.info(f"Subscribing alerts for {booking_reference} → {contact_email}")
        # store subscription in session
        context.session_data.setdefault("alerts", []).append({"sub_id": sub_id, "booking_reference": booking_reference, "email": contact_email, "created": datetime.utcnow().isoformat()})
        return {"sub_id": sub_id, "status": "subscribed"}

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
