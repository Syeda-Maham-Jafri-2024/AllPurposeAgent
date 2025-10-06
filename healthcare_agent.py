
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pydantic import BaseModel, EmailStr, field_validator
from datetime import date as dt_date, datetime
import dateparser
import re
from typing import Optional
import dateparser
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
from livekit import rtc
from livekit.agents import metrics
from livekit.plugins import openai, silero
from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip
from datetime import datetime
import re

logger = logging.getLogger("hospital-voice-agent")
# logging.basicConfig(level=logging.INFO)

from dotenv import load_dotenv
load_dotenv()

LOG_FILE = "healthcare_session_summary.json"

# ------------------ SAMPLE DATA ------------------
FILLER_AUDIO = [
        "audio/filler_1.wav", "audio/filler_2.wav", "audio/filler_3.wav", "audio/filler_4.wav", "audio/filler_5.wav", "audio/filler_6.wav", "audio/filler_7.wav", "audio/filler_8.wav",
        "audio/filler_9.wav","audio/filler_10.wav","audio/filler_11.wav","audio/filler_12.wav","audio/filler_13.wav","audio/filler_14.wav","audio/filler_15.wav","audio/filler_16.wav",
        "audio/filler_17.wav","audio/filler_18.wav","audio/filler_19.wav","audio/filler_20.wav","audio/filler_21.wav","audio/filler_22.wav","audio/filler_23.wav","audio/filler_24.wav",
        "audio/filler_25.wav","audio/filler_26.wav","audio/filler_27.wav","audio/filler_28.wav","audio/filler_29.wav","audio/filler_30.wav","audio/filler_31.wav","audio/filler_32.wav",]


CLOSING_RE = re.compile(
    r"^\s*(bye|goodbye|see you|see ya|later|thanks(?:\s+all)?|thank you|that's it|that is all|no that's all|talk soon|i'm done|done)[\.\!\?]?\s*$",
    flags=re.IGNORECASE | re.UNICODE,
)

HOSPITAL_INFO = {
    "name": "CityCare Hospital",
    "address": "45 Medical Boulevard, Karachi, Pakistan",
    "phone": "+92 21 1234 5678",
    "email": "info@citycarehospital.com",
    "hours": "Mon–Sat: 8:00 AM – 8:00 PM, Sun: Closed",
}

DOCTORS = {
    "Dr. Sara Khan": { "specialization": "Cardiologist","timings": "Mon–Fri: 10 AM – 2 PM","email": "sara.khan@citycarehospital.com",},
    "Dr. Ali Raza": { "specialization": "Dermatologist", "timings": "Tue–Sat: 3 PM – 7 PM","email": "ali.raza@citycarehospital.com",},
    "Dr. Fatima Ahmed": {"specialization": "Pediatrician", "timings": "Mon–Thu: 9 AM – 1 PM", "email": "fatima.ahmed@citycarehospital.com",},
    "Dr. Kamran Siddiqui": { "specialization": "Orthopedic Surgeon", "timings": "Mon–Fri: 11 AM – 4 PM", "email": "kamran.siddiqui@citycarehospital.com",},
    "Dr. Nadia Hussain": { "specialization": "Gynecologist", "timings": "Tue–Sat: 9 AM – 12 PM", "email": "nadia.hussain@citycarehospital.com",},
    "Dr. Imran Qureshi": { "specialization": "Neurologist", "timings": "Mon–Wed: 2 PM – 6 PM", "email": "imran.qureshi@citycarehospital.com",},
    "Dr. Ayesha Malik": { "specialization": "Psychiatrist", "timings": "Thu–Sat: 10 AM – 1 PM","email": "ayesha.malik@citycarehospital.com",},
    "Dr. Bilal Sheikh": { "specialization": "ENT Specialist", "timings": "Mon–Fri: 4 PM – 8 PM", "email": "bilal.sheikh@citycarehospital.com",},
    "Dr. Zainab Akhtar": {"specialization": "Ophthalmologist","timings": "Tue–Fri: 9 AM – 1 PM","email": "zainab.akhtar@citycarehospital.com",},
    "Dr. Hassan Javed": { "specialization": "General Physician", "timings": "Mon–Sat: 9 AM – 5 PM", "email": "hassan.javed@citycarehospital.com", },
}

from datetime import date

APPOINTMENTS = {
    "APT001": {
        "patient": "Ali Khan",
        "email": "ali.khan@example.com",
        "doctor": "Dr. Sara Khan",
        "date": str(date.today()),
        "time": "10:30 AM"
    },
    "APT002": {
        "patient": "Sara Ahmed",
        "email": "sara.ahmed@example.com",
        "doctor": "Dr. Ali Raza",
        "date": str(date.today()),
        "time": "3:45 PM"
    },
}



# ------------------ EMAIL UTILITY ------------------
def send_email_to_patient(to_email: str, subject: str, body: str):
    sender_email = os.getenv("EMAIL_USER")
    sender_password = os.getenv("EMAIL_APP_PASSWORD")  # store in .env

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

# ------------------ Pydantic Model ------------------
class AppointmentRequest(BaseModel):
    name: str
    email: EmailStr
    doctor_name: str
    date: dt_date
    time: str  # e.g. "10:30 AM"

    @field_validator("doctor_name")
    def validate_doctor_exists(cls, value):
        if value not in DOCTORS:
            raise ValueError(f"Doctor '{value}' is not available at this hospital.")
        return value

    @field_validator("date")
    def validate_future_date(cls, value):
        today = dt_date.today()
        if value < today:
            raise ValueError("Appointment date cannot be in the past.")
        return value


class RescheduleRequest(BaseModel):
    """Model for rescheduling an appointment with natural-language date & time"""
    appointment_id: str
    new_date: dt_date
    new_time: Optional[str] = None  # optional; can be "5 pm", "17:00", etc.

    # --- Validate appointment ID ---
    @field_validator("appointment_id")
    def validate_id_format(cls, v):
        if not v.startswith("APT"):
            raise ValueError("Invalid appointment ID format.")
        if v not in APPOINTMENTS:
            raise ValueError(f"Appointment ID '{v}' not found.")
        return v

    # --- Parse natural-language date ---
    @field_validator("new_date", mode="before")
    def parse_natural_language_date(cls, value):
        if isinstance(value, str):
            parsed = dateparser.parse(value)
            if not parsed:
                raise ValueError(f"❌ Could not parse date from '{value}'.")
            return parsed.date()
        return value

    # --- Parse and normalize time (optional) ---
    @field_validator("new_time", mode="before")
    def normalize_time_format(cls, value):
        if value is None:
            return value
        if isinstance(value, str):
            parsed_time = dateparser.parse(value)
            if parsed_time:
                return parsed_time.strftime("%I:%M %p")
        return value

    # --- Validate future date ---
    @field_validator("new_date")
    def validate_future_date(cls, v):
        if v < dt_date.today():
            raise ValueError("New date cannot be in the past.")
        return v



class CancelRequest(BaseModel):
    """Model for cancelling an appointment"""
    appointment_id: str

    @field_validator("appointment_id")
    def validate_id_format(cls, v):
        if not v.startswith("APT"):
            raise ValueError("Invalid appointment ID format.")
        if v not in APPOINTMENTS:
            raise ValueError(f"Appointment ID '{v}' not found.")
        return v

# ------------------ HOSPITAL AGENT ------------------

class HospitalAgent(Agent):
    def __init__(self, voice: str = "alloy") -> None:
        stt = openai.STT(model="gpt-4o-transcribe", language="en")
        llm_inst = openai.LLM(model="gpt-4o")
        tts = openai.TTS(model="gpt-4o-mini-tts", voice=voice)
        silero_vad = silero.VAD.load()

        super().__init__(
            instructions="You are a hospital assistant for CityCare Hospital. "
            "You can provide hospital info, doctor details, and manage appointments.",
            stt=stt,
            llm=llm_inst,
            tts=tts,
            vad=silero_vad,
            allow_interruptions=True,
        )

    # -------- Get Hospital Details --------
    @function_tool()
    async def get_hospital_info(
        self, context: RunContext, field: Optional[str] = None
    ) -> str:
        """
        Retrieves hospital information.
        """
        logger.info("-------------------------------------")
        logger.info("Tool calling (Get Hospital Info):")
        logger.info("-------------------------------------")

        if field and field in HOSPITAL_INFO:
            logger.info(HOSPITAL_INFO[field])
            return HOSPITAL_INFO[field]
        return HOSPITAL_INFO

    # -------- Get Doctor Details --------
    @function_tool()
    async def get_doctor_details(self, doctor_name: str, context: RunContext) -> str:
        logger.info(f"🔍 Looking up details for doctor: {doctor_name}")

        # Find all matching doctors (case-insensitive partial match)
        matches = [
            name for name in DOCTORS.keys()
            if doctor_name.lower() in name.lower()
        ]

        if not matches:
            return f"❌ Sorry, no doctor found matching '{doctor_name}'."

        if len(matches) == 1:
            # Only one match → return full details
            doc = DOCTORS[matches[0]]
            return f"{matches[0]} ({doc['specialization']}), Timings: {doc['timings']}"

        # Multiple matches → list them
        match_details = "\n".join(
            f"{name} ({DOCTORS[name]['specialization']}), Timings: {DOCTORS[name]['timings']}"
            for name in matches
        )
        return (
            f"Multiple doctors found matching '{doctor_name}':\n{match_details}\n"
            "Please specify the full name."
        )


    # -------- Get Appointment Status --------
    @function_tool()
    async def get_appointment_status(self, appointment_id: str, context: RunContext) -> str:
        logger.info(f"📌 Checking status for appointment {appointment_id}")

        appointment = APPOINTMENTS.get(appointment_id)
        if not appointment:
            logger.warning(f"❌ Appointment ID {appointment_id} not found")
            return f"❌ Appointment ID {appointment_id} not found."

        return (
            f"📋 Appointment Status\n\n"
            f"ID: {appointment_id}\n"
            f"Patient: {appointment['patient']}\n"
            f"Doctor: {appointment['doctor']}\n"
            f"Date: {appointment['date']}\n"
            f"Time: {appointment.get('time', 'Not specified')}\n"
            f"Location: {HOSPITAL_INFO['address']}\n"
            f"Contact: {HOSPITAL_INFO['phone']}"
        )


    # -------- Schedule Appointment (with Pydantic) --------
    @function_tool()
    async def schedule_appointment(self, request: AppointmentRequest, context: RunContext) -> str:
        logger.info(f"📝 Scheduling validated appointment for {request.name} with {request.doctor_name} on {request.date}")

        appointment_id = f"APT{len(APPOINTMENTS)+1:03d}"
        APPOINTMENTS[appointment_id] = {
            "patient": request.name,
            "email": request.email,
            "doctor": request.doctor_name,
            "date": str(request.date),
            "time": request.time,
        }

        confirmation_msg = (
            f"✅ Appointment confirmed!\n\n"
            f"ID: {appointment_id}\n"
            f"Patient: {request.name}\n"
            f"Doctor: {request.doctor_name}\n"
            f"Date: {request.date}\n\n"
            f"Location: {HOSPITAL_INFO['address']}\n"
            f"Contact: {HOSPITAL_INFO['phone']}"
        )

        email_body = (
            f"Dear {request.name},\n\n"
            f"Your appointment has been scheduled.\n\n"
            f"{confirmation_msg}\n\n"
            f"- CityCare Hospital"
        )
        send_email_to_patient(request.email, "Appointment Confirmation", email_body)

        return confirmation_msg

    # # -------- Reschedule Appointment --------
    # @function_tool()
    # async def reschedule_appointment(self, request: RescheduleRequest, context: RunContext) -> str:
    #     logger.info(f"🔄 Rescheduling appointment {request.appointment_id} to {request.new_date} {request.new_time or ''}")

    #     appointment = APPOINTMENTS.get(request.appointment_id)
    #     if not appointment:
    #         logger.error(f"Appointment {request.appointment_id} vanished from store")
    #         return f"❌ Appointment ID {request.appointment_id} not found."

    #     old_date = appointment["date"]
    #     old_time = appointment.get("time", "Not specified")

    #     # --- Update fields ---
    #     appointment["date"] = str(request.new_date)
    #     if request.new_time:
    #         appointment["time"] = request.new_time

    #     msg = (
    #         f"✅ Appointment rescheduled!\n\n"
    #         f"ID: {request.appointment_id}\n"
    #         f"Patient: {appointment['patient']}\n"
    #         f"Doctor: {appointment['doctor']}\n"
    #         f"Old Date & Time: {old_date}, {old_time}\n"
    #         f"New Date & Time: {request.new_date}, {appointment.get('time', old_time)}"
    #     )

    #     email_body = (
    #         f"Dear {appointment['patient']},\n\n"
    #         f"Your appointment has been rescheduled.\n\n"
    #         f"{msg}\n\n"
    #         f"- CityCare Hospital"
    #     )
    #     send_email_to_patient(appointment["email"], "Appointment Rescheduled", email_body)
    #     return msg


    # # -------- Cancel Appointment --------
    # @function_tool()
    # async def cancel_appointment(self, request: CancelRequest, context: RunContext) -> str:
    #     logger.info(f"❌ Cancelling appointment {request.appointment_id}")

    #     appointment = APPOINTMENTS.pop(request.appointment_id, None)
    #     if not appointment:
    #         logger.error(f"Appointment {request.appointment_id} vanished from store")
    #         return f"❌ Appointment ID {request.appointment_id} not found."

    #     msg = (
    #         f"✅ Appointment cancelled.\n\n"
    #         f"ID: {request.appointment_id}\n"
    #         f"Patient: {appointment['patient']}\n"
    #         f"Doctor: {appointment['doctor']}\n"
    #         f"Date: {appointment['date']}"
    #     )

    #     email_body = f"Dear {appointment['patient']},\n\nYour appointment has been cancelled.\n\n{msg}\n\n- CityCare Hospital"
    #     send_email_to_patient(appointment["email"], "Appointment Cancelled", email_body)

    #     return msg


# ------------------ AGENT LIFECYCLE ------------------
def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

    # Add this at the top of entrypoint

async def entrypoint(ctx: JobContext):
    filler_task = None  
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

    agent = HospitalAgent()
    usage_collector = metrics.UsageCollector()

    # Store conversation in memory
    conversation_log = []
    
    # ----------------------------
    # Metrics collection
    # ----------------------------
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
        nonlocal filler_task  # so we can modify the outer variable
        if msg.text.strip():
            conversation_log.append(
                {"role": "user", "text": msg.text, "timestamp": datetime.utcnow().isoformat()}
            )

            text = msg.text.lower().strip().replace("’", "'")
            if not hasattr(session, "ending"):
                session.ending = False

            closing_keywords = [
                "bye", "goodbye", "see you", "later", "thanks", "thank you",
                "that's it", "no that's all", "talk soon", "done"
            ]

            # 🔑 If closing phrase detected → mark ending + cancel fillers
            if any(kw in text for kw in closing_keywords):
                session.ending = True
                if filler_task and not filler_task.done():
                    filler_task.cancel()
                    asyncio.create_task(background_audio.clear_thinking())
                logger.info(f"Closing detected, skipping filler: {msg.text}")
                return

            if session.ending:
                logger.info(f"Session is ending, suppressing filler for: {msg.text}")
                return

            # Otherwise schedule filler as usual
            async def delayed_filler():
                await asyncio.sleep(1.0)
                filler = get_random_filler()
                logger.info(f"Playing filler after: {msg.text} → {filler}")
                await background_audio.set_thinking([AudioConfig(filler, volume=0.9)])

            filler_task = asyncio.create_task(delayed_filler())

    @session.on("assistant_message")
    def on_assistant_message(msg):
        if msg.text.strip():
            conversation_log.append(
                {"role": "assistant", "text": msg.text, "timestamp": datetime.utcnow().isoformat()}
            )
        # Always stop filler when assistant responds
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

    # --- Start the session
    ctx.call_start = datetime.utcnow()
    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=RoomInputOptions(),
    )

    # --- Background ambience + fillers
    global background_audio
    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.6),
        thinking_sound=[AudioConfig(f, volume=0.9) for f in FILLER_AUDIO],
    )
    await background_audio.start(room=ctx.room, agent_session=session)
  

    # --- Greeting
    await session.say("Hi, I’m your Healthcare Assistant! How can I help you today?")

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )

