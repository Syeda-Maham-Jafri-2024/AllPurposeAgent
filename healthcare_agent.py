
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

# Dummy reports data
REPORTS = {
    "RPT001": {
        "patient_name": "Ali Khan", "test_name": "Blood Test", "status": "Completed", "date": "2025-10-04", "remarks": "Normal blood count levels.", },
    "RPT002": {
        "patient_name": "Sara Ahmed", "test_name": "X-Ray (Chest)", "status": "In Progress", "date": "2025-10-06", "remarks": "Awaiting radiologist review.",},
    "RPT003": {
        "patient_name": "Bilal Hussain", "test_name": "MRI (Brain)", "status": "Pending", "date": "2025-10-07", "remarks": "Test scheduled tomorrow.", },
    "RPT004": {
        "patient_name": "Fatima Noor", "test_name": "Urine Analysis", "status": "Completed", "date": "2025-10-03", "remarks": "No signs of infection detected.", },
    "RPT005": {
        "patient_name": "Hassan Raza", "test_name": "ECG", "status": "Completed", "date": "2025-10-02", "remarks": "Minor irregularities, follow-up recommended.",},
    "RPT006": {
        "patient_name": "Ayesha Siddiqui", "test_name": "Thyroid Function Test", "status": "In Progress", "date": "2025-10-05", "remarks": "Lab processing underway.",},
    "RPT007": {
        "patient_name": "Imran Sheikh", "test_name": "COVID-19 PCR", "status": "Completed","date": "2025-10-01", "remarks": "Negative result.", },
    "RPT008": {
        "patient_name": "Zara Malik", "test_name": "Ultrasound (Abdomen)", "status": "Pending", "date": "2025-10-08", "remarks": "Test scheduled for today at 3 PM.",},
    "RPT009": {
        "patient_name": "Usman Tariq", "test_name": "Liver Function Test", "status": "Completed", "date": "2025-09-30", "remarks": "Slightly elevated enzyme levels, doctor review advised.",},
    "RPT010": {
        "patient_name": "Maryam Shah", "test_name": "CT Scan (Abdomen)","status": "In Progress", "date": "2025-10-06", "remarks": "Scans uploaded, awaiting radiologist’s summary.",},
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

class ReportQuery(BaseModel):
    report_id: str
    patient_name: Optional[str] = None

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

# ----------------- Helper Functions ---------------

import calendar

def is_doctor_available(doctor_name: str, appointment_date: dt_date, appointment_time_str: str) -> tuple[bool, str]:
    """
    Checks if the doctor is available on the requested day and time.
    Returns (True, message) if valid, else (False, reason).
    """
    doc_info = DOCTORS.get(doctor_name)
    if not doc_info:
        return False, f"Doctor '{doctor_name}' not found."

    timing_str = doc_info["timings"]  # e.g., "Mon–Fri: 10 AM – 2 PM"
    match = re.match(r"([A-Za-z]{3})[–-]([A-Za-z]{3}):\s*(\d{1,2}\s*[AP]M)\s*[–-]\s*(\d{1,2}\s*[AP]M)", timing_str)
    if not match:
        return False, f"Could not parse timings for {doctor_name}."

    start_day, end_day, start_time_str, end_time_str = match.groups()

    # Convert to comparable objects
    weekdays = list(calendar.day_abbr)  # ['Mon', 'Tue', ...]
    start_index = weekdays.index(start_day)
    end_index = weekdays.index(end_day)
    appointment_day = appointment_date.weekday()  # Monday = 0

    # Handle week wrap (e.g., Fri–Mon)
    if start_index <= end_index:
        day_in_range = start_index <= appointment_day <= end_index
    else:
        day_in_range = appointment_day >= start_index or appointment_day <= end_index

    if not day_in_range:
        return False, f"{doctor_name} is only available {timing_str}."

    # Parse times to compare
    start_time = datetime.strptime(start_time_str, "%I %p").time()
    end_time = datetime.strptime(end_time_str, "%I %p").time()
    requested_time = dateparser.parse(appointment_time_str).time()

    if not (start_time <= requested_time <= end_time):
        return False, f"{doctor_name} is available only between {start_time_str} and {end_time_str}."

    return True, f"{doctor_name} is available at {appointment_time_str} on that day."


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

        # --- Check doctor's availability ---
        available, msg = is_doctor_available(request.doctor_name, request.date, request.time)
        if not available:
            logger.warning(f"❌ Appointment rejected: {msg}")
            return f"❌ Sorry, {msg} Please choose another time within their working hours."

        # --- Proceed with appointment booking ---
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
            f"Date: {request.date} at {request.time}\n\n"
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

    @function_tool
    def check_report_status(query: ReportQuery) -> str:
        """Check the status of a patient's medical report using their report ID."""
        report = REPORTS.get(query.report_id)
        if not report:
            return f"Sorry, I couldn’t find any report with ID {query.report_id}. Please make sure it’s correct."
        
        return (
            f"Report ID: {query.report_id}\n"
            f"Patient Name: {report['patient_name']}\n"
            f"Test Name: {report['test_name']}\n"
            f"Status: {report['status']}\n"
            f"Date: {report['date']}\n"
            f"Remarks: {report['remarks']}"
    )

    # async def schedule_appointment(self, request: AppointmentRequest, context: RunContext) -> str:
    #     logger.info(f"📝 Scheduling validated appointment for {request.name} with {request.doctor_name} on {request.date}")

    #     appointment_id = f"APT{len(APPOINTMENTS)+1:03d}"
    #     APPOINTMENTS[appointment_id] = {
    #         "patient": request.name,
    #         "email": request.email,
    #         "doctor": request.doctor_name,
    #         "date": str(request.date),
    #         "time": request.time,
    #     }

    #     confirmation_msg = (
    #         f"✅ Appointment confirmed!\n\n"
    #         f"ID: {appointment_id}\n"
    #         f"Patient: {request.name}\n"
    #         f"Doctor: {request.doctor_name}\n"
    #         f"Date: {request.date}\n\n"
    #         f"Location: {HOSPITAL_INFO['address']}\n"
    #         f"Contact: {HOSPITAL_INFO['phone']}"
    #     )

    #     email_body = (
    #         f"Dear {request.name},\n\n"
    #         f"Your appointment has been scheduled.\n\n"
    #         f"{confirmation_msg}\n\n"
    #         f"- CityCare Hospital"
    #     )
    #     send_email_to_patient(request.email, "Appointment Confirmation", email_body)

    #     return confirmation_msg


    # -------- Cancel Appointment --------
    @function_tool()
    async def cancel_appointment(self, request: CancelRequest, context: RunContext) -> str:
        logger.info(f"❌ Cancelling appointment {request.appointment_id}")

        appointment = APPOINTMENTS.pop(request.appointment_id, None)
        if not appointment:
            logger.error(f"Appointment {request.appointment_id} vanished from store")
            return f"❌ Appointment ID {request.appointment_id} not found."

        msg = (
            f"✅ Appointment cancelled.\n\n"
            f"ID: {request.appointment_id}\n"
            f"Patient: {appointment['patient']}\n"
            f"Doctor: {appointment['doctor']}\n"
            f"Date: {appointment['date']}"
        )

        email_body = f"Dear {appointment['patient']},\n\nYour appointment has been cancelled.\n\n{msg}\n\n- CityCare Hospital"
        send_email_to_patient(appointment["email"], "Appointment Cancelled", email_body)

        return msg
    
    # --------------- Handoff Functions ---------------------------
    @function_tool()
    async def handoff_to_insurance(self, context: RunContext[UserContext]):
        """Transfer the user to the insurance assistant."""
        logger.info("Handing off to InsuranceAgent.")
        insurance_agent = InsuranceAgent()
        return insurance_agent, "Switching you to our insurance assistant."

    @function_tool()
    async def handoff_to_airline(self, context: RunContext[UserContext]):
        """Transfer the user to the airline assistant."""
        logger.info("Handing off to AirlineAgent.")
        airline_agent = AirlineAgent()
        return airline_agent, "Switching you to our airline assistant."

    @function_tool()
    async def handoff_to_restaurant(self, context: RunContext[UserContext]):
        """Transfer the user to the restaurant assistant."""
        logger.info("Handing off to RestaurantAgent.")
        restaurant_agent = RestaurantAgent()
        return restaurant_agent, "Switching you to our restaurant assistant."

    @function_tool()
    async def handoff_to_aisystems(self, context: RunContext[UserContext]):
        """Transfer the user to the AI Systems assistant."""
        logger.info("Handing off to AISystemsAgent.")
        aisystems_agent = AISystemsAgent()
        return aisystems_agent, "Switching you to our AI Systems support assistant."