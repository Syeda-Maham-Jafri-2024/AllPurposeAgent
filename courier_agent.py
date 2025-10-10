# courier_agent.py
import logging
import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone, date
from typing import Optional, List, Dict, Tuple
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

# LiveKit / OpenAI libs (kept as in your other agents)
from openai import OpenAI
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
from livekit.plugins import openai, silero
from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip
from livekit import rtc
from livekit.agents import metrics
from livekit.agents.llm import ChatMessage

logger = logging.getLogger("courier-voice-agent")
load_dotenv(dotenv_path=".env")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
today = datetime.now().date()

# Session data fallback
if not hasattr(RunContext, "session_data"):
    RunContext.session_data = {}



# ---------------------- Dummy Data ----------------------

COURIER_INFO = {
    "name": "SwiftBridge Couriers",
    "address": "Warehouse 7, Logistics Park, Karachi, Pakistan",
    "phone": "+92 300 555 7788",
    "email": "support@swiftbridge.co",
    "pickup_hours": "Mon–Sat: 09:00 – 19:00 (local); International pickups Mon–Fri",
    "website": "https://www.swiftbridge.co",
}

# service areas with zone categories (domestic zones & international supported countries)
SERVICE_AREAS = {
    "domestic": {
        "KHI": {"zone": "A", "same_day": True},
        "LHE": {"zone": "A", "same_day": True},
        "ISB": {"zone": "A", "same_day": True},
        "HYD": {"zone": "B", "same_day": False},
        "GWADAR": {"zone": "C", "same_day": False},  # longer transit
        "RWP": {"zone": "A", "same_day": True},
    },
    "international": {
        "UAE": {"transit_days": 2, "customs_required": True},
        "UK": {"transit_days": 5, "customs_required": True},
        "SAUDI": {"transit_days": 3, "customs_required": True},
        "USA": {"transit_days": 7, "customs_required": True},
        "QATAR": {"transit_days": 2, "customs_required": True},
    },
}

# pricing table (very simplified)
PRICING = {
    "domestic": {
        "base": 150,  # PKR base
        "per_kg": 200,  # PKR per kg
        "zone_multiplier": {"A": 1.0, "B": 1.25, "C": 1.6},
        "cod_fee_pct": 0.02,  # 2% on COD amount
    },
    "international": {
        "base": 1200,
        "per_kg": 1500,
        "country_surcharge": {"UAE": 1.0, "UK": 1.5, "USA": 1.8, "SAUDI": 1.2, "QATAR": 1.0},
    },
}

# Simulated drivers / pickup agents
PICKUP_AGENTS = [
    {"id": "AGT001", "name": "Hamza", "area": "KHI", "available": True},
    {"id": "AGT002", "name": "Ayesha", "area": "LHE", "available": True},
    {"id": "AGT003", "name": "Bilal", "area": "ISB", "available": False},
]

# existing bookings / pickups (in-memory)
PICKUP_BOOKINGS: List[Dict] = [
    {
        "booking_id": "BKP1001",
        "sender_name": "Ali Khan",
        "email": "ali.khan@example.com",
        "pickup_address": "House 12, Clifton, KHI",
        "area_code": "KHI",
        "weight_kg": 2.5,
        "pieces": 1,
        "service": "domestic_standard",
        "date": str(today),
        "time": "11:00",
        "status": "confirmed",
        "assigned_agent": "AGT001",
        "created_at": datetime.utcnow().isoformat(),
    }
]

# shipments (tracking)
SHIPMENTS = {
    # AWB: details
    "CR1000001": {
        "sender": "Ali Khan",
        "recipient": "Sara Ahmed",
        "origin": "KHI",
        "destination": "LHE",
        "weight_kg": 2.5,
        "pieces": 1,
        "service": "domestic_standard",
        "status": "In Transit",
        "last_location": "KHI - Sorting Hub",
        "estimated_delivery": str(today + timedelta(days=1)),
        "events": [
            {"ts": str(datetime.utcnow()), "text": "Shipment received at KHI facility"},
        ],
    }
}

# cancellation policy / terms (simplified)
CANCELLATION_POLICY = (
    "Pickup can be cancelled up to 2 hours before scheduled time without charge. "
    "For international pickups customs docs must be ready—charges may apply for re-routing."
)

# filler audio
FILLER_AUDIO = [
    "audio/filler_1.wav", "audio/filler_2.wav", "audio/filler_3.wav"
]

# ---------------------- Utilities ----------------------

def generate_tracking_id() -> str:
    return f"CR{random.randint(1000000, 9999999)}"

def generate_pickup_booking_id() -> str:
    return f"BKP{random.randint(1000, 9999)}"

def send_email(to_email: str, subject: str, body: str) -> bool:
    # simple wrapper like in your other agents
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    sender = os.getenv("EMAIL_USER")
    pwd = os.getenv("EMAIL_APP_PASSWORD")
    if not sender or not pwd:
        logger.warning("Email creds missing; skipping send.")
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
        logger.error(f"Failed to send email: {e}")
        return False

def find_agent_for_area(area_code: str) -> Optional[Dict]:
    for a in PICKUP_AGENTS:
        if a["area"] == area_code and a["available"]:
            return a
    return None

# ---------------------- Pydantic Models ----------------------

class TrackQuery(BaseModel):
    tracking_id: Optional[str] = None
    reference: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one(cls, v):
        if not v.tracking_id and not v.reference:
            raise ValueError("Provide tracking_id or reference.")
        return v

class PricingRequest(BaseModel):
    origin: str
    destination: str
    weight_kg: float = Field(..., gt=0.0)
    service_level: Optional[str] = Field("standard", description="standard | express | overnight")
    cod_amount: Optional[float] = Field(0.0, ge=0.0)

class PickupRequest(BaseModel):
    sender_name: str
    email: EmailStr
    pickup_address: str
    area_code: str  # e.g., KHI, LHE
    pickup_date: date
    pickup_time: str  # "11:00", "14:30" etc.
    weight_kg: float
    pieces: int = Field(..., gt=0)
    service: Optional[str] = Field("domestic_standard")  # domestic_standard, domestic_express, intl_economy, etc.
    cod: Optional[bool] = False
    cod_amount: Optional[float] = 0.0

    @field_validator("pickup_date")
    def validate_future(cls, v):
        if v < date.today():
            raise ValueError("Pickup date cannot be in the past.")
        return v

    @field_validator("pickup_time")
    def validate_time_format(cls, v):
        if not re.match(r"^\d{1,2}:\d{2}$", v):
            raise ValueError("Time must be HH:MM (24h).")
        return v

class PickupLookup(BaseModel):
    booking_id: Optional[str] = None
    email: Optional[EmailStr] = None

    @model_validator(mode="after")
    def at_least_one(cls, v):
        if not v.booking_id and not v.email:
            raise ValueError("Provide booking_id or email.")
        return v

class CancelPickupRequest(BaseModel):
    booking_id: str

    @field_validator("booking_id")
    def exists(cls, v):
        if not any(b["booking_id"] == v for b in PICKUP_BOOKINGS):
            raise ValueError("Booking ID not found.")
        return v

# ---------------------- Core Helper Logic ----------------------

def calculate_domestic_price(origin: str, destination: str, weight_kg: float, service_level: str = "standard", cod_amount: float = 0.0) -> Tuple[int, dict]:
    origin_info = SERVICE_AREAS["domestic"].get(origin.upper())
    dest_info = SERVICE_AREAS["domestic"].get(destination.upper())
    if not origin_info or not dest_info:
        raise ValueError("Origin or destination not in domestic coverage.")

    zone = dest_info["zone"]
    base = PRICING["domestic"]["base"]
    per_kg = PRICING["domestic"]["per_kg"]
    multiplier = PRICING["domestic"]["zone_multiplier"].get(zone, 1.0)

    # service level surcharge
    if service_level == "express":
        svc_mult = 1.5
    elif service_level == "overnight":
        svc_mult = 2.5
    else:
        svc_mult = 1.0

    cost = int((base + (per_kg * weight_kg)) * multiplier * svc_mult)
    cod_fee = int(cost * PRICING["domestic"]["cod_fee_pct"] + (cod_amount * PRICING["domestic"]["cod_fee_pct"] if cod_amount else 0))
    return cost + cod_fee, {"base": base, "per_kg": per_kg, "zone": zone, "svc_mult": svc_mult, "cod_fee": cod_fee}

def calculate_international_price(origin: str, country: str, weight_kg: float, service_level: str = "economy") -> Tuple[int, dict]:
    country = country.upper()
    if country not in SERVICE_AREAS["international"]:
        raise ValueError("Country not supported for international shipping.")
    base = PRICING["international"]["base"]
    per_kg = PRICING["international"]["per_kg"]
    surcharge = PRICING["international"]["country_surcharge"].get(country, 1.4)
    # simple service level multiplier
    if service_level == "express":
        svc_mult = 1.6
    else:
        svc_mult = 1.0
    cost = int((base + (per_kg * weight_kg)) * surcharge * svc_mult)
    return cost, {"base": base, "per_kg": per_kg, "surcharge": surcharge, "svc_mult": svc_mult}

# ---------------------- Agent ----------------------

class CourierAgent(Agent):
    def __init__(self, voice: str = "cedar") -> None:
        stt = openai.STT(model="gpt-4o-transcribe", language="en")
        llm_inst = openai.LLM(model="gpt-4o")
        tts = openai.TTS(model="gpt-4o-mini-tts", voice=voice)
        silero_vad = silero.VAD.load()

        super().__init__(
            instructions=(
                "You are an assistant for SwiftBridge Couriers. Help users track shipments, "
                "get quotes, check service areas, and book pickups. When tools return structured data, "
                "paraphrase naturally for the caller and ask clear next steps (confirm, change time, etc)."
            ),
            stt=stt,
            llm=llm_inst,
            tts=tts,
            vad=silero_vad,
            allow_interruptions=True,
        )

    @function_tool()
    async def get_courier_info(self, field: Optional[str] = None, context: RunContext = None) -> dict:
        if field and field in COURIER_INFO:
            return {field: COURIER_INFO[field]}
        return COURIER_INFO

    # ---------------- track shipment ----------------
    @function_tool()
    async def track_shipment(self, query: TrackQuery, context: RunContext = None) -> dict:
        logger.info(f"🔍 Tracking: {query}")
        if query.tracking_id:
            info = SHIPMENTS.get(query.tracking_id.upper())
            if not info:
                return {"error": f"No shipment found with tracking {query.tracking_id}."}
            return {
                "tracking_id": query.tracking_id.upper(),
                "status": info["status"],
                "last_location": info["last_location"],
                "estimated_delivery": info.get("estimated_delivery"),
                "events": info.get("events", []),
            }
        # reference-based lookup (not implemented in dummy)
        return {"error": "Reference lookup not available in demo."}

    # ---------------- pricing quote ----------------
    @function_tool()
    async def get_pricing_quote(self, request: PricingRequest, context: RunContext = None) -> dict:
        logger.info(f"💲 Pricing request: {request}")
        origin = request.origin.upper()
        destination = request.destination.upper()
        weight = request.weight_kg
        service_level = request.service_level.lower()

        # if domestic
        if origin in SERVICE_AREAS["domestic"] and destination in SERVICE_AREAS["domestic"]:
            price, meta = calculate_domestic_price(origin, destination, weight, service_level, request.cod_amount)
            return {
                "type": "domestic",
                "origin": origin,
                "destination": destination,
                "weight_kg": weight,
                "service_level": service_level,
                "price": f"PKR {price:,}",
                "breakdown": meta,
            }

        # try international by destination country
        if destination.upper() in SERVICE_AREAS["international"]:
            price, meta = calculate_international_price(origin, destination, weight, service_level)
            return {
                "type": "international",
                "origin": origin,
                "destination_country": destination,
                "weight_kg": weight,
                "service_level": service_level,
                "price": f"PKR {price:,}",
                "breakdown": meta,
            }

        return {"error": "Unable to price for given origin/destination. Check service area coverage."}

    # ---------------- check service area ----------------
    @function_tool()
    async def check_service_area(self, location: str, context: RunContext = None) -> dict:
        loc = location.strip().upper()
        logger.info(f"🌐 Checking service area for: {loc}")
        if loc in SERVICE_AREAS["domestic"]:
            info = SERVICE_AREAS["domestic"][loc]
            return {"type": "domestic", "area": loc, "zone": info["zone"], "same_day": info["same_day"]}
        if loc in SERVICE_AREAS["international"]:
            info = SERVICE_AREAS["international"][loc]
            return {"type": "international", "country": loc, **info}
        return {"error": f"Location '{location}' not covered."}

    # ---------------- book pickup (preview + confirm) ----------------
    @function_tool()
    async def book_pickup(self, request: PickupRequest, context: RunContext = None) -> dict:
        logger.info(f"📦 Booking pickup request: {request}")

        # 1) service area check
        if request.area_code.upper() not in SERVICE_AREAS["domestic"]:
            return {"error": f"Pickup area {request.area_code} not covered for local pickup."}

        # 2) find agent availability
        agent = find_agent_for_area(request.area_code.upper())
        if agent is None:
            # suggest alternate times or next-day pickup
            return {"error": "No pickup agent currently available in your area. We can schedule next-day pickup between 09:00-14:00. Would you like to schedule that?"}

        # 3) pricing (estimate)
        # split local service choice
        service_level = "standard"
        if "express" in request.service:
            service_level = "express"

        try:
            price, meta = calculate_domestic_price(request.area_code.upper(), request.area_code.upper(), request.weight_kg, service_level, request.cod_amount if hasattr(request, "cod_amount") else 0.0)
        except Exception:
            # fallback basic pricing
            price, meta = calculate_domestic_price(request.area_code.upper(), request.area_code.upper(), request.weight_kg, service_level, 0.0)

        preview_id = generate_pickup_booking_id()
        preview = {
            "preview_id": preview_id,
            "assigned_agent": agent["id"],
            "agent_name": agent["name"],
            "estimated_price": f"PKR {price:,}",
            "pickup_date": str(request.pickup_date),
            "pickup_time": request.pickup_time,
            "pieces": request.pieces,
            "weight_kg": request.weight_kg,
            "service": request.service,
            "requires_confirmation": True,
        }

        # If user hasn't confirmed, return preview only
        # (the LLM should ask user to confirm; if confirm action sent, call book_pickup with same payload + confirm flag)
        # For demo: allow user to pass through preview_id to confirm
        # We'll store preview temporarily in RunContext.session_data to allow confirm flow
        if context:
            context.session_data["pending_pickup_preview"] = {"preview": preview, "request": request.dict()}
        return {"pickup_preview": preview}

    @function_tool()
    async def confirm_pickup(self, preview_id: str, context: RunContext = None) -> dict:
        if not context:
            return {"error": "No context provided."}
        pending = context.session_data.get("pending_pickup_preview")
        if not pending or pending["preview"]["preview_id"] != preview_id:
            return {"error": "No matching pickup preview found. Please request a new pickup."}

        req = pending["request"]
        booking_id = generate_pickup_booking_id()
        assigned_agent = pending["preview"]["assigned_agent"]

        record = {
            "booking_id": booking_id,
            "sender_name": req["sender_name"],
            "email": req["email"],
            "pickup_address": req["pickup_address"],
            "area_code": req["area_code"].upper(),
            "weight_kg": req["weight_kg"],
            "pieces": req["pieces"],
            "service": req["service"],
            "date": req["pickup_date"],
            "time": req["pickup_time"],
            "status": "confirmed",
            "assigned_agent": assigned_agent,
            "created_at": datetime.utcnow().isoformat(),
        }
        PICKUP_BOOKINGS.append(record)

        # mark agent unavailable temporarily (demo)
        for a in PICKUP_AGENTS:
            if a["id"] == assigned_agent:
                a["available"] = False

        # send email
        email_body = (
            f"Dear {req['sender_name']},\n\n"
            f"Your pickup has been scheduled.\nBooking ID: {booking_id}\n"
            f"Pickup: {req['pickup_date']} at {req['pickup_time']}\nAgent: {pending['preview']['agent_name']}\n\n"
            "Thank you — SwiftBridge Couriers"
        )
        send_email(req["email"], f"Pickup Confirmation - {booking_id}", email_body)

        # clear pending preview
        context.session_data.pop("pending_pickup_preview", None)

        return {"booking_id": booking_id, "message": "Pickup confirmed and assigned.", "assigned_agent": assigned_agent}

    # ---------------- view / cancel pickup ----------------
    @function_tool()
    async def view_pickup_status(self, lookup: PickupLookup, context: RunContext = None) -> dict:
        logger.info(f"🔎 Lookup pickup: {lookup}")
        matched = None
        if lookup.booking_id:
            matched = next((b for b in PICKUP_BOOKINGS if b["booking_id"].lower() == lookup.booking_id.lower()), None)
        elif lookup.email:
            matched = next((b for b in PICKUP_BOOKINGS if b["email"].lower() == lookup.email.lower()), None)
        if not matched:
            return {"error": "No pickup booking found."}
        return matched

    @function_tool()
    async def cancel_pickup(self, cancel: CancelPickupRequest, context: RunContext = None) -> dict:
        logger.info(f"🛑 Cancel pickup: {cancel}")
        booking = next((b for b in PICKUP_BOOKINGS if b["booking_id"] == cancel.booking_id), None)
        if not booking:
            return {"error": "Booking not found."}
        # enforce 2-hour rule (demo)
        dt_pickup = datetime.fromisoformat(f"{booking['date']}T{booking['time']}:00")
        if (dt_pickup - datetime.now()) < timedelta(hours=2):
            return {"error": "Cannot cancel within 2 hours of scheduled pickup."}
        booking["status"] = "cancelled"
        # free agent
        for a in PICKUP_AGENTS:
            if a["id"] == booking.get("assigned_agent"):
                a["available"] = True
        send_email(booking["email"], f"Pickup Cancelled - {booking['booking_id']}", f"Your pickup {booking['booking_id']} has been cancelled.")
        return {"message": f"Booking {booking['booking_id']} cancelled."}

    # ---------------- simulate shipment update (for testing) ----------------
    @function_tool()
    async def simulate_shipment_update(self, tracking_id: str, new_status: str, location: Optional[str] = None, context: RunContext = None) -> dict:
        logger.info(f"🔧 Simulate update {tracking_id} -> {new_status}")
        key = tracking_id.upper()
        if key not in SHIPMENTS:
            return {"error": "Tracking ID not found."}
        info = SHIPMENTS[key]
        event = {"ts": str(datetime.utcnow()), "text": new_status + (f" at {location}" if location else "")}
        info["events"].append(event)
        info["status"] = new_status
        if location:
            info["last_location"] = location
        return {"message": "Updated", "tracking_id": key, "status": new_status}

    # ---------------- cancellation policy ----------------
    @function_tool()
    async def cancellation_policy(self, context: RunContext = None) -> dict:
        return {"policy": CANCELLATION_POLICY}

# ---------------------- Entrypoint & lifecycle (boilerplate similar to airline agent) ----------------------

def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

async def entrypoint(ctx: JobContext):
    filler_task = None
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    participant = await ctx.wait_for_participant()
    logger.info(f"starting courier voice assistant for participant {participant.identity}")

    session = AgentSession(vad=ctx.proc.userdata["vad"], min_endpointing_delay=0.9, max_endpointing_delay=5.0)
    agent = CourierAgent()
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

    @session.on("user_message")
    def on_user_message(msg):
        nonlocal filler_task
        if msg.text.strip():
            conversation_log.append({"role": "user", "text": msg.text, "timestamp": datetime.utcnow().isoformat()})
            async def delayed_filler():
                await asyncio.sleep(1.0)
                filler = random.choice(FILLER_AUDIO) if FILLER_AUDIO else None
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
        with open("courier_session_summary.json", "a", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.write("\n")
        logger.info(f"✅ Record saved to JSON: {record['session_id']}")

    # start session
    ctx.call_start = datetime.utcnow()
    await session.start(room=ctx.room, agent=agent, room_input_options=RoomInputOptions())

    # background audio and filler
    global background_audio
    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.AIRPORT_AMBIENCE if hasattr(BuiltinAudioClip, "AIRPORT_AMBIENCE") else BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.4),
        thinking_sound=[AudioConfig(f, volume=0.9) for f in FILLER_AUDIO] if FILLER_AUDIO else [],
    )
    await background_audio.start(room=ctx.room, agent_session=session)

    # greeting
    await session.say(f"Hi — this is SwiftBridge Couriers. How can I help you with shipping or pickup today?")

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))

