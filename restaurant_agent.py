# restaurant_agent.py

import os
import smtplib
import logging
import random
from datetime import datetime, date as dt_date, time as dt_time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pydantic import BaseModel, EmailStr,field_validator
from typing import Optional, List, Dict
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
from dotenv import load_dotenv
from livekit.agents import metrics
from livekit.plugins import openai, silero
from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip
from livekit.agents.llm import ChatMessage
from livekit import rtc
from openai import OpenAI
from context import RESTAURANT_CONTEXT
import re

logger = logging.getLogger("restaurant-voice-agent")
load_dotenv(dotenv_path=".env")

# OpenAI client for helper calls (mirrors your usage)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
today = datetime.now().date()
# Session data fallback
if not hasattr(RunContext, "session_data"):
    RunContext.session_data = {}

# --------------------- Sample Conversation between the User and the Agent ------------------------
"""
🥇 1. Get Restaurant Info
   - User: What are your opening hours?
     Tool Called: get_restaurant_info(field="hours")
     Agent Response: We’re open from 10 AM to 11 PM every day.

   - User: Can you tell me your address and phone number?
     Tool Called: get_restaurant_info()
     Agent Response: We’re located at 12 Food Street, Karachi. You can reach us at +92 21 9876 5432.

🥈 2. Browse Menu
   - User: What’s on your menu?
      Tool Called: browse_menu()
      Agent Response:
      We offer a wide range of dishes — from soups and salads to pizzas, burgers, and desserts.
      Would you like me to tell you about a particular section?

   - User: What desserts do you have?
     Tool Called: browse_menu()
     Agent Response:
     Our desserts include Chocolate Lava Cake and Cheesecake. Both are delicious!

🥉 3. Make Reservation
   - User: I’d like to book a table for 2 people tonight at 8 PM.
     Tool Called: make_reservation( request={ "name": "Sara Khan", "email": "sara.khan@example.com", "people": 2, "date": "2025-10-08", "time": "20:00"})
     Agent Response:
     I’ve found a table for two at 8 PM tonight.
     Would you like me to confirm your reservation?

   - User: Yes, please confirm it.
     Tool Called: confirm_reservation()
     Agent Response:
     Your reservation is confirmed!
     You’ll receive a confirmation email shortly. We look forward to serving you tonight.

🍕 5. Place Order
   - User: I’d like to order one Margherita pizza and a lemonade.
     Tool Called: place_order(request={ "name": "Sara Khan", "email": "sara.khan@example.com", "items": [ {"item_name": "Margherita", "quantity": 1},{"item_name": "Lemonade", "quantity": 1}]})
     Agent Response: That’s one Margherita pizza and a lemonade — your total comes to 1,400 rupees.
     Would you like to confirm your order or add something else, like garlic bread?

🏆 6. Confirm Order
   - User: Yes, please confirm my order.
     Tool Called: confirm_order()
     Agent Response:
     Your order has been confirmed!
     You’ll receive a confirmation email shortly, and we’ll start preparing your food right away.

"""

# ------------------ SAMPLE RESTAURANT DATA ------------------
RESTAURANT_INFO = {
    "name": "La Piazza Bistro",
    "address": "12 Food Street, Karachi, Pakistan",
    "phone": "+92 21 9876 5432",
    "email": "maham@theaisystems.com",
    "hours": {"open": dt_time(10, 0), "close": dt_time(23, 0)},  # 10 AM – 11 PM
}

MENU = {
    "Starters": {
        "Soups": {"Tomato Soup": 350, "Chicken Corn Soup": 400},
        "Salads": {"Caesar Salad": 550, "Greek Salad": 600},
        "Sides": {  # Added sides for upsell items
            "Fries": 300,
            "Garlic Bread": 400,
        },
    },
    "Main Course": {
        "Pasta": {"Fettuccine Alfredo": 950, "Spaghetti Bolognese": 1000},
        "Pizza": {"Margherita": 1200, "Pepperoni": 1400, "BBQ Chicken": 1500},
        "Burgers": {"Classic Burger": 800, "Cheese Burger": 900},
    },
    "Desserts": {"Chocolate Lava Cake": 650, "Cheesecake": 700},
    "Drinks": {"Coke": 150, "Lemonade": 200, "Iced Tea": 250},
}

# Upselling combos (all items now exist in MENU)
UPSELL_MAP = {
    "Classic Burger": ["Fries", "Coke"],
    "Cheese Burger": ["Fries", "Iced Tea"],
    "Margherita": ["Garlic Bread", "Lemonade"],
    "Pepperoni": ["Garlic Bread", "Coke"],
}


# In-memory stores
RESERVATIONS: Dict[str, dict] = {}
ORDERS: Dict[str, dict] = {}

FILLER_AUDIO = [
        "audio/filler_1.wav", "audio/filler_2.wav", "audio/filler_3.wav", "audio/filler_4.wav", "audio/filler_5.wav", "audio/filler_6.wav", "audio/filler_7.wav", "audio/filler_8.wav",
        "audio/filler_9.wav","audio/filler_10.wav","audio/filler_11.wav","audio/filler_12.wav","audio/filler_13.wav","audio/filler_14.wav","audio/filler_15.wav","audio/filler_16.wav",
        "audio/filler_17.wav","audio/filler_18.wav","audio/filler_19.wav","audio/filler_20.wav","audio/filler_21.wav","audio/filler_22.wav","audio/filler_23.wav","audio/filler_24.wav",
        "audio/filler_25.wav","audio/filler_26.wav","audio/filler_27.wav","audio/filler_28.wav","audio/filler_29.wav","audio/filler_30.wav","audio/filler_31.wav","audio/filler_32.wav",]

CLOSING_RE = re.compile(
    r"^\s*(bye|goodbye|see you|see ya|later|thanks|thank you|that's it|done)[\.\!\?]?\s*$",
    flags=re.IGNORECASE | re.UNICODE,
)


# ------------------ TABLE AVAILABILITY (Dummy Data) ------------------
TABLE_AVAILABILITY = {
    2: {  # tables for 2 people
        "Table 1": {
            "13:00": True, "14:00": False, "18:00": True, "19:00": False, "20:00": True, "21:00": False,},
        "Table 2": {
           "13:00": True, "14:00": True,"18:00": False, "19:00": False, "20:00": False, "21:00": True,},
    },
    4: {  # tables for 4 people
        "Table 3": {
            "13:00": True, "14:00": False, "18:00": True, "19:00": False, "20:00": True,},
        "Table 4": {
            "13:00": True, "14:00": True, "18:00": False, "19:00": True, "20:00": False, },
    },
    6: {  # tables for 6 people
        "Table 5": {
            "13:00": True, "14:00": True, "18:00": False, "19:00": True,}
    },
    10: {  # tables for 10 people
        "Table 6": {
            "13:00": True,"14:00": False,"18:00": True, "20:00": False, }
    },
}

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

    @field_validator("name")
    def validate_name(cls, v):
        if not v.strip().replace(" ", "").isalpha():
            raise ValueError("Name must contain only letters and spaces.")
        return v

    @field_validator("people")
    def validate_people(cls, v):
        if v <= 0:
            raise ValueError("Number of people must be at least 1.")
        return v

    @field_validator("date")
    def validate_date(cls, v):
        today = dt_date.today()
        # If date is in the past but less than 30 days old, assume typo and shift to next month/year
        if v < today:
            # If it's more than a year behind, it's likely a wrong parsing (e.g., 2023 instead of 2025)
            if (today.year - v.year) >= 1:
                corrected = dt_date(today.year, v.month, v.day)
                if corrected < today:
                    corrected = dt_date(today.year + 1, v.month, v.day)
                v = corrected
            else:
                raise ValueError("Reservation date cannot be in the past.")
        return v
   

    @field_validator("time")
    def validate_time(cls, v):
        # Ensure both times are naive for safe comparison
        if v.tzinfo is not None:
            v = v.replace(tzinfo=None)

        open_time = RESTAURANT_INFO["hours"]["open"]
        close_time = RESTAURANT_INFO["hours"]["close"]

        if v < open_time or v > close_time:
            raise ValueError(
                f"Reservation time must be within restaurant hours ({open_time.strftime('%I:%M %p')} – {close_time.strftime('%I:%M %p')})."
            )
        return v

class OrderItem(BaseModel):
    item_name: str
    quantity: int

class OrderRequest(BaseModel):
    name: str
    email: EmailStr
    items: List[OrderItem]  # List of objects instead of a dict

    @field_validator("name")
    def validate_name(cls, v):
        if not v.strip().replace(" ", "").isalpha():
            raise ValueError("Name must contain only letters and spaces.")
        return v

# ------------------ Helper Functions ----------------------------

def find_available_table(people: int, requested_time: dt_time) -> dict:
    """Check if any table for the given group size is available at the requested time."""
    time_str = requested_time.strftime("%H:%M")

    if people not in TABLE_AVAILABILITY:
        return {"error": f"No tables configured for {people} people."}

    tables = TABLE_AVAILABILITY[people]
    available_tables = [t for t, slots in tables.items() if slots.get(time_str)]
    if available_tables:
        return {"available": True, "table": random.choice(available_tables)}

    # If no exact time is available, suggest next available times
    alternate_times = set()
    for t, slots in tables.items():
        for alt_time, available in slots.items():
            if available:
                alternate_times.add(alt_time)

    return {
        "available": False,
        "alternatives": sorted(alternate_times),
    }


# ------------------ RESTAURANT AGENT ------------------
class RestaurantAgent(Agent):
    def __init__(self, voice: str = "alloy") -> None:
        stt = openai.STT(model="gpt-4o-transcribe", language="en")
        llm_inst = openai.LLM(model="gpt-4o")
        tts = openai.TTS(model="gpt-4o-mini-tts", voice=voice)
        silero_vad = silero.VAD.load()

        super().__init__(
            instructions=(f""" {RESTAURANT_CONTEXT}"""),
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
            value = RESTAURANT_INFO[field]
            if field == "hours":
                # Convert time objects to human-readable strings
                return {
                    "open": RESTAURANT_INFO["hours"]["open"].strftime("%I:%M %p"),
                    "close": RESTAURANT_INFO["hours"]["close"].strftime("%I:%M %p"),
                }
            return value

        # If no field provided, return full info with formatted hours
        info = RESTAURANT_INFO.copy()
        info["hours"] = {
            "open": RESTAURANT_INFO["hours"]["open"].strftime("%I:%M %p"),
            "close": RESTAURANT_INFO["hours"]["close"].strftime("%I:%M %p"),
        }
        return info


    # -------- Browse Menu --------
    @function_tool()
    async def browse_menu(self, context: RunContext) -> dict:
        """Returns full detailed menu with categories, subcategories, items, and prices."""
        return MENU

    # -------- Make Reservation (Preview) --------
    @function_tool
    async def make_reservation(self, context: RunContext, request: ReservationRequest) -> dict:
        """Handles reservation requests, checking table availability."""
        res_id = f"RES{random.randint(1000,9999)}"
        slot_key = f"{request.date}-{request.time.strftime('%H:%M')}"

        # --- Check if table is available ---
        availability = find_available_table(request.people, request.time)

        if "error" in availability:
            return {"error": availability["error"]}

        if not availability["available"]:
            alternatives = availability["alternatives"]
            formatted_times = ", ".join(alternatives)
            return {
                "error": (
                    f"Sorry, no tables for {request.people} people are available at {request.time.strftime('%I:%M %p')}.\n"
                    f"However, we do have availability at these times: {formatted_times}.\n"
                    "Would you like to choose one of these instead?"
                )
            }

        # --- Proceed with booking preview if available ---
        chosen_table = availability["table"]
        RESERVATIONS[res_id] = {
            "id": res_id,
            "table": chosen_table,
            "slot": slot_key,
            "name": request.name,
            "email": request.email,
            "people": request.people,
            "status": "pending",
        }

        summary = (
            f"Here's your reservation preview:\n\n"
            f"✅ **Table:** {chosen_table}\n"
            f"**Reservation ID:** {res_id}\n"
            f"**Name:** {request.name}\n"
            f"**Number of People:** {request.people}\n"
            f"**Date & Time:** {request.date.strftime('%B %d, %Y')} at {request.time.strftime('%I:%M %p')}\n\n"
            f"Please confirm to finalize your reservation."
        )

        context.session_data["pending_reservation"] = RESERVATIONS[res_id]
        return {
            "reservation_id": res_id,
            "summary": summary,
            "requires_confirmation": True,
        }

    # @function_tool()
    # async def make_reservation(self, context: RunContext, request: ReservationRequest) -> dict:
    #     res_id = f"RES{random.randint(1000,9999)}"
    #     slot_key = f"{request.date}-{request.time}"

    #     if slot_key in [r["slot"] for r in RESERVATIONS.values() if r["status"] == "confirmed"]:
    #         return {"error": f"❌ Slot {slot_key} already booked. Please choose another time."}

    #     RESERVATIONS[res_id] = {
    #         "id": res_id,
    #         "slot": slot_key,
    #         "name": request.name,
    #         "email": request.email,
    #         "people": request.people,
    #         "status": "pending",
    #     }

    #     summary = (
    #         f"Reservation Preview (ID: {res_id})\n"
    #         f"Name: {request.name}\n"
    #         f"People: {request.people}\n"
    #         f"Date: {request.date} at {request.time}\n\n"
    #         f"Please confirm to finalize."
    #     )
    #     context.session_data["pending_reservation"] = RESERVATIONS[res_id]
    #     return {"reservation_id": res_id, "summary": summary, "requires_confirmation": True}

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
        """
        Places an order (preview mode). Calculates total, suggests upsells,
        and prepares a summary for confirmation.
        """
        order_id = f"ORD{random.randint(1000,9999)}"
        subtotal = 0
        upsell_suggestions = []

        # --- Helper function to check if an item exists anywhere in MENU
        def item_exists_in_menu(search_item: str) -> bool:
            search_item = search_item.lower()
            for cat, subcats in MENU.items():
                if all(isinstance(v, dict) for v in subcats.values()):
                    for sub, items in subcats.items():
                        for menu_item in items.keys():
                            if menu_item.lower() == search_item:
                                return True
                else:
                    for menu_item in subcats.keys():
                        if menu_item.lower() == search_item:
                            return True
            return False

        # --- Process each order item
        for item in request.items:
            item_name = item.item_name.strip().lower()
            qty = item.quantity
            price = None

            # Search for item in nested or flat menus
            for cat, subcats in MENU.items():
                # Case 1: Nested menu (e.g., Main Course → Pizza)
                if all(isinstance(v, dict) for v in subcats.values()):
                    for sub, items in subcats.items():
                        for menu_item, menu_price in items.items():
                            normalized_menu_item = menu_item.lower()
                            if (
                                item_name == normalized_menu_item
                                or item_name == f"{normalized_menu_item} {sub.lower()}"
                                or item_name == f"{normalized_menu_item} {cat.lower()}"
                            ):
                                price = menu_price
                                item.item_name = menu_item  # Standardize
                                break

                # Case 2: Flat menu (e.g., Desserts or Drinks)
                else:
                    for menu_item, menu_price in subcats.items():
                        normalized_menu_item = menu_item.lower()
                        if (
                            item_name == normalized_menu_item
                            or item_name == f"{normalized_menu_item} {cat.lower()}"
                        ):
                            price = menu_price
                            item.item_name = menu_item
                            break

                if price is not None:
                    break

            if price is None:
                raise ValueError(f"Item '{item.item_name}' not found in menu.")

            subtotal += price * qty

            # --- Handle upsell suggestions
            if item.item_name in UPSELL_MAP:
                for upsell_item in UPSELL_MAP[item.item_name]:
                    if item_exists_in_menu(upsell_item):
                        upsell_suggestions.append(upsell_item)
                    else:
                        logging.warning(
                            f"Upsell item '{upsell_item}' not found in MENU — skipped."
                        )

        # --- Order summary
        total = subtotal
        summary_lines = [
            f"🧾 Order Preview (ID: {order_id})",
            f"👤 Customer: {request.name} <{request.email}>",
            "",
            "Items:",
        ]

        for item in request.items:
            summary_lines.append(f"  • {item.item_name} x{item.quantity}")

        summary_lines.append(f"\n💰 Subtotal: Rs. {subtotal}")
        if upsell_suggestions:
            summary_lines.append(
                f"💡 You might also like: {', '.join(set(upsell_suggestions))}"
            )
        summary_lines.append("\nPlease confirm to finalize your order.")

        # --- Save pending order
        ORDERS[order_id] = {
            "id": order_id,
            "name": request.name,
            "email": request.email,
            "items": {item.item_name: item.quantity for item in request.items},
            "total": total,
            "status": "pending",
        }
        context.session_data["pending_order"] = ORDERS[order_id]

        # --- Return response
        return {
            "order_id": order_id,
            "summary": "\n".join(summary_lines),
            "requires_confirmation": True,
        }


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
            f"Customer: {pending['name']} <{pending['email']}>\n"
            f"Items: {pending['items']}\n"  
            f"Total: Rs. {pending['total']}"
        )

        # Send confirmation to both customer and restaurant
        send_email(pending["email"], "Your Order Confirmation - La Piazza Bistro", msg)
        send_email(RESTAURANT_INFO["email"], "New Customer Order Received", msg)

        return msg
   
   #------------------------- Handoff Functions for Agents ----------------------------
    @function_tool()
    async def handoff_to_insurance(self, context: RunContext[UserContext]):
        """Transfer the user to the insurance assistant."""
        logger.info("Handing off to InsuranceAgent.")
        insurance_agent = InsuranceAgent()
        return insurance_agent, "Switching you to our insurance assistant."

    @function_tool()
    async def handoff_to_healthcare(self, context: RunContext[UserContext]):
        """Transfer the user to the healthcare assistant."""
        logger.info("Handing off to HealthcareAgent.")
        healthcare_agent = HospitalAgent()
        return healthcare_agent, "Switching you to our healthcare assistant."

    @function_tool()
    async def handoff_to_airline(self, context: RunContext[UserContext]):
        """Transfer the user to the airline assistant."""
        logger.info("Handing off to AirlineAgent.")
        airline_agent = AirlineAgent()
        return airline_agent, "Switching you to our airline assistant."

    @function_tool()
    async def handoff_to_aisystems(self, context: RunContext[UserContext]):
        """Transfer the user to the AI Systems assistant."""
        logger.info("Handing off to AISystemsAgent.")
        aisystems_agent = AISystemsAgent()
        return aisystems_agent, "Switching you to our AI Systems support assistant."

