# restaurant_agent.py

import os
import smtplib
import logging
import random
from datetime import datetime, date as dt_date, time as dt_time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pydantic import BaseModel, EmailStr, validator
from typing import Optional, List, Dict
from livekit.agents import Agent, RunContext, function_tool
from livekit.plugins import openai, silero

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("restaurant-voice-agent")

from dotenv import load_dotenv
load_dotenv()


# ------------------ SAMPLE RESTAURANT DATA ------------------
RESTAURANT_INFO = {
    "name": "La Piazza Bistro",
    "address": "12 Food Street, Karachi, Pakistan",
    "phone": "+92 21 9876 5432",
    "email": "contact@lapiazza.com",
    "hours": {"open": dt_time(10, 0), "close": dt_time(23, 0)},  # 10 AM – 11 PM
}

MENU = {
    "Starters": {
        "Soups": {"Tomato Soup": 350, "Chicken Corn Soup": 400},
        "Salads": {"Caesar Salad": 550, "Greek Salad": 600},
    },
    "Main Course": {
        "Pasta": {"Fettuccine Alfredo": 950, "Spaghetti Bolognese": 1000},
        "Pizza": {"Margherita": 1200, "Pepperoni": 1400, "BBQ Chicken": 1500},
        "Burgers": {"Classic Burger": 800, "Cheese Burger": 900},
    },
    "Desserts": {"Chocolate Lava Cake": 650, "Cheesecake": 700},
    "Drinks": {"Coke": 150, "Lemonade": 200, "Iced Tea": 250},
}

# Upselling combos
UPSELL_MAP = {
    "Classic Burger": ["Fries", "Coke"],
    "Cheese Burger": ["Fries", "Iced Tea"],
    "Margherita": ["Garlic Bread", "Lemonade"],
    "Pepperoni": ["Garlic Bread", "Coke"],
}

# In-memory stores
RESERVATIONS: Dict[str, dict] = {}
ORDERS: Dict[str, dict] = {}


# ------------------ EMAIL UTILITY ------------------
def send_email(to_email: str, subject: str, body: str):
    sender_email = os.getenv("EMAIL_USER")
    sender_password = os.getenv("EMAIL_APP_PASSWORD")

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
            logger.info(f"✅ Email sent to {to_email}")
            return True
    except Exception as e:
        logger.error(f"❌ Failed to send email: {e}")
        return False


# ------------------ Pydantic Models ------------------
class ReservationRequest(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str]
    people: int
    date: dt_date
    time: dt_time

    @validator("name")
    def validate_name(cls, v):
        if not v.strip().replace(" ", "").isalpha():
            raise ValueError("Name must contain only letters and spaces.")
        return v

    @validator("people")
    def validate_people(cls, v):
        if v <= 0:
            raise ValueError("Number of people must be at least 1.")
        return v

    @validator("date")
    def validate_date(cls, v):
        if v < dt_date.today():
            raise ValueError("Reservation date cannot be in the past.")
        return v

    @validator("time")
    def validate_time(cls, v):
        if v < RESTAURANT_INFO["hours"]["open"] or v > RESTAURANT_INFO["hours"]["close"]:
            raise ValueError("Reservation time must be within restaurant hours.")
        return v


class OrderRequest(BaseModel):
    name: str
    email: EmailStr
    items: Dict[str, int]  # {"Margherita": 2, "Coke": 2}

    @validator("name")
    def validate_name(cls, v):
        if not v.strip().replace(" ", "").isalpha():
            raise ValueError("Name must contain only letters and spaces.")
        return v

    @validator("items")
    def validate_items(cls, v):
        for item in v.keys():
            found = any(item in sub for cat in MENU.values() for sub in (cat.values() if isinstance(cat, dict) else [cat]))
            if not found:
                raise ValueError(f"Menu item '{item}' not found.")
        return v


# ------------------ RESTAURANT AGENT ------------------
class RestaurantAgent(Agent):
    def __init__(self, voice: str = "alloy") -> None:
        stt = openai.STT(model="gpt-4o-transcribe", language="en")
        llm_inst = openai.LLM(model="gpt-4o")
        tts = openai.TTS(model="gpt-4o-mini-tts", voice=voice)
        silero_vad = silero.VAD.load()

        super().__init__(
            instructions="You are a restaurant assistant for La Piazza Bistro. "
                         "You can provide restaurant info, manage reservations, and take food orders.",
            stt=stt,
            llm=llm_inst,
            tts=tts,
            vad=silero_vad,
            allow_interruptions=True,
        )

    # -------- Restaurant Info --------
    @function_tool()
    async def get_restaurant_info(self, context: RunContext, field: Optional[str] = None):
        """Retrieve restaurant info: 'name', 'address', 'phone', 'email', or 'hours'."""
        if field and field in RESTAURANT_INFO:
            return RESTAURANT_INFO[field]
        return RESTAURANT_INFO

    # -------- Browse Menu --------
    @function_tool()
    async def browse_menu(self, context: RunContext) -> dict:
        """Returns full detailed menu with categories, subcategories, items, and prices."""
        return MENU

    # -------- Make Reservation (Preview) --------
    @function_tool()
    async def make_reservation(self, context: RunContext, request: ReservationRequest) -> dict:
        res_id = f"RES{random.randint(1000,9999)}"
        slot_key = f"{request.date}-{request.time}"

        if slot_key in [r["slot"] for r in RESERVATIONS.values() if r["status"] == "confirmed"]:
            return {"error": f"❌ Slot {slot_key} already booked. Please choose another time."}

        RESERVATIONS[res_id] = {
            "id": res_id,
            "slot": slot_key,
            "name": request.name,
            "email": request.email,
            "people": request.people,
            "status": "pending",
        }

        summary = (
            f"Reservation Preview (ID: {res_id})\n"
            f"Name: {request.name}\n"
            f"People: {request.people}\n"
            f"Date: {request.date} at {request.time}\n\n"
            f"Please confirm to finalize."
        )
        context.session_data["pending_reservation"] = RESERVATIONS[res_id]
        return {"reservation_id": res_id, "summary": summary, "requires_confirmation": True}

    # -------- Confirm Reservation --------
    @function_tool()
    async def confirm_reservation(self, context: RunContext) -> str:
        pending = context.session_data.get("pending_reservation")
        if not pending:
            return "❌ No pending reservation found."

        pending["status"] = "confirmed"
        RESERVATIONS[pending["id"]] = pending
        context.session_data.pop("pending_reservation", None)

        msg = (
            f"✅ Reservation Confirmed!\n\n"
            f"ID: {pending['id']}\n"
            f"Name: {pending['name']}\n"
            f"People: {pending['people']}\n"
            f"Date & Time: {pending['slot']}"
        )

        send_email(pending["email"], "Reservation Confirmed", msg)
        send_email(RESTAURANT_INFO["email"], "New Reservation", msg)
        return msg

    # -------- Place Order (Preview) --------
    @function_tool()
    async def place_order(self, context: RunContext, request: OrderRequest) -> dict:
        order_id = f"ORD{random.randint(1000,9999)}"
        subtotal = 0
        upsell_suggestions = []

        for item, qty in request.items.items():
            price = None
            for cat in MENU.values():
                for sub in (cat.values() if isinstance(cat, dict) else [cat]):
                    if isinstance(sub, dict) and item in sub:
                        price = sub[item]
            subtotal += price * qty
            if item in UPSELL_MAP:
                upsell_suggestions.extend(UPSELL_MAP[item])

        total = subtotal
        summary_lines = [f"Order Preview (ID: {order_id})", f"Customer: {request.name} <{request.email}>"]
        for item, qty in request.items.items():
            summary_lines.append(f"{item} x{qty}")
        summary_lines.append(f"Subtotal: {subtotal}")
        if upsell_suggestions:
            summary_lines.append(f"💡 You might also like: {', '.join(set(upsell_suggestions))}")
        summary_lines.append("Please confirm to finalize.")

        ORDERS[order_id] = {"id": order_id, "items": request.items, "total": total, "status": "pending"}
        context.session_data["pending_order"] = ORDERS[order_id]

        return {"order_id": order_id, "summary": "\n".join(summary_lines), "requires_confirmation": True}

    # -------- Confirm Order --------
    @function_tool()
    async def confirm_order(self, context: RunContext) -> str:
        pending = context.session_data.get("pending_order")
        if not pending:
            return "❌ No pending order found."

        pending["status"] = "confirmed"
        ORDERS[pending["id"]] = pending
        context.session_data.pop("pending_order", None)

        msg = (
            f"✅ Order Confirmed!\n\n"
            f"ID: {pending['id']}\n"
            f"Items: {pending['items']}\n"
            f"Total: {pending['total']}"
        )

        send_email(RESTAURANT_INFO["email"], "New Order Received", msg)
        send_email(pending["items"], "Order Confirmation", msg)
        return msg
