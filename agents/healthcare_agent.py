# # hospital_agent.py (with Pydantic validation for scheduling)

# import os
# import smtplib
# import logging
# from datetime import date as dt_date
# from email.mime.text import MIMEText
# from email.mime.multipart import MIMEMultipart
# from pydantic import BaseModel, EmailStr, validator
# from typing import Optional
# from livekit.agents import (
# Agent,
# RunContext,
# function_tool,
# AgentSession,
# JobContext,
# JobProcess,
# WorkerOptions,
# cli,
# AutoSubscribe,
# RoomInputOptions,
# )
# from livekit.agents import metrics
# from livekit.plugins import openai, silero
# from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip
# logger = logging.getLogger("hospital-voice-agent")
# logging.basicConfig(level=logging.INFO)


# from dotenv import load_dotenv
# load_dotenv()


# # ------------------ SAMPLE DATA ------------------
# HOSPITAL_INFO = {
# "name": "CityCare Hospital",
# "address": "45 Medical Boulevard, Karachi, Pakistan",
# "phone": "+92 21 1234 5678",
# "email": "info@citycarehospital.com",
# "hours": "Mon–Sat: 8:00 AM – 8:00 PM, Sun: Closed",
# }

# DOCTORS = {
# "Dr. Sara Khan": {
# "specialization": "Cardiologist",
# "timings": "Mon–Fri: 10 AM – 2 PM",
# "email": "sara.khan@citycarehospital.com",
# },
# "Dr. Ali Raza": {
# "specialization": "Dermatologist",
# "timings": "Tue–Sat: 3 PM – 7 PM",
# "email": "ali.raza@citycarehospital.com",
# },
# "Dr. Fatima Ahmed": {
# "specialization": "Pediatrician",
# "timings": "Mon–Thu: 9 AM – 1 PM",
# "email": "fatima.ahmed@citycarehospital.com",
# },
# "Dr. Kamran Siddiqui": {
# "specialization": "Orthopedic Surgeon",
# "timings": "Mon–Fri: 11 AM – 4 PM",
# "email": "kamran.siddiqui@citycarehospital.com",
# },
# "Dr. Nadia Hussain": {
# "specialization": "Gynecologist",
# "timings": "Tue–Sat: 9 AM – 12 PM",
# "email": "nadia.hussain@citycarehospital.com",
# },
# "Dr. Imran Qureshi": {
# "specialization": "Neurologist",
# "timings": "Mon–Wed: 2 PM – 6 PM",
# "email": "imran.qureshi@citycarehospital.com",
# },
# "Dr. Ayesha Malik": {
# "specialization": "Psychiatrist",
# "timings": "Thu–Sat: 10 AM – 1 PM",
# "email": "ayesha.malik@citycarehospital.com",
# },
# "Dr. Bilal Sheikh": {
# "specialization": "ENT Specialist",
# "timings": "Mon–Fri: 4 PM – 8 PM",
# "email": "bilal.sheikh@citycarehospital.com",
# },
# "Dr. Zainab Akhtar": {
# "specialization": "Ophthalmologist",
# "timings": "Tue–Fri: 9 AM – 1 PM",
# "email": "zainab.akhtar@citycarehospital.com",
# },
# "Dr. Hassan Javed": {
# "specialization": "General Physician",
# "timings": "Mon–Sat: 9 AM – 5 PM",
# "email": "hassan.javed@citycarehospital.com",
# },
# }

# APPOINTMENTS = {}  # in-memory store


# # ------------------ EMAIL UTILITY ------------------
# def send_email_to_patient(to_email: str, subject: str, body: str):
# sender_email = os.getenv("EMAIL_USER")
# sender_password = os.getenv("EMAIL_APP_PASSWORD")  # store in .env

# msg = MIMEMultipart()
# msg["From"] = sender_email
# msg["To"] = to_email
# msg["Subject"] = subject
# msg.attach(MIMEText(body, "plain"))

# try:
# with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
# server.login(sender_email, sender_password)
# server.send_message(msg)
# logger.info(f"✅ Email sent to {to_email}")
# return True
# except Exception as e:
# logger.error(f"❌ Failed to send email: {e}")
# return False


# # ------------------ Pydantic Model ------------------
# class AppointmentRequest(BaseModel):
# """Model to validate appointment scheduling input."""
# name: str
# email: EmailStr
# doctor_name: str
# date: dt_date

# @validator("doctor_name")
# def validate_doctor_exists(cls, value):
# if value not in DOCTORS:
# raise ValueError(f"Doctor '{value}' is not available at this hospital.")
# return value

# @validator("date")
# def validate_future_date(cls, value):
# today = dt_date.today()
# if value < today:
# raise ValueError("Appointment date cannot be in the past.")
# return value


# class RescheduleRequest(BaseModel):
# """Model for rescheduling an appointment"""
# appointment_id: str
# new_date: dt_date

# @validator("appointment_id")
# def validate_id_format(cls, v):
# if not v.startswith("APT"):
# raise ValueError("Invalid appointment ID format.")
# if v not in APPOINTMENTS:
# raise ValueError(f"Appointment ID '{v}' not found.")
# return v

# @validator("new_date")
# def validate_future_date(cls, v):
# if v < dt_date.today():
# raise ValueError("New date cannot be in the past.")
# return v


# class CancelRequest(BaseModel):
# """Model for cancelling an appointment"""
# appointment_id: str

# @validator("appointment_id")
# def validate_id_format(cls, v):
# if not v.startswith("APT"):
# raise ValueError("Invalid appointment ID format.")
# if v not in APPOINTMENTS:
# raise ValueError(f"Appointment ID '{v}' not found.")
# return v


# # ------------------ HOSPITAL AGENT ------------------
# class HospitalAgent(Agent):
# def __init__(self, voice: str = "alloy") -> None:
# stt = openai.STT(model="gpt-4o-transcribe", language="en")
# llm_inst = openai.LLM(model="gpt-4o")
# tts = openai.TTS(model="gpt-4o-mini-tts", voice=voice)
# silero_vad = silero.VAD.load()

# super().__init__(
# instructions="You are a hospital assistant for CityCare Hospital. "
# "You can provide hospital info, doctor details, and manage appointments.",
# stt=stt,
# llm=llm_inst,
# tts=tts,
# vad=silero_vad,
# allow_interruptions=True,
# )

# # -------- Get Hospital Details --------
# @function_tool()
# async def get_hospital_info(
# self, context: RunContext, field: Optional[str] = None
# ) -> str:
# """
# Retrieves hospital information.

# Args:
# field (str, optional): 'name', 'address', 'phone', 'email', or 'working_hours'.
# If not provided, return all hospital details.
# """
# logger.info("-------------------------------------")
# logger.info("Tool calling (Get Hospital Info):")
# logger.info("-------------------------------------")

# if field and field in HOSPITAL_INFO:
# logger.info(HOSPITAL_INFO[field])
# return HOSPITAL_INFO[field]
# return HOSPITAL_INFO

# # -------- Get Doctor Details --------
# @function_tool()
# async def get_doctor_details(self, doctor_name: str, context: RunContext) -> str:
# """
# Situation:
# Use this when the patient asks about a specific doctor, their specialization,
# or available timings.
# Arguments:
# doctor_name (str): Full name of the doctor.
# context (RunContext): Conversation context.
# Returns:
# str: A formatted string with the doctor's specialization and timings,
# or an error message if not found.
# """
# logger.info(f"🔍 Looking up details for doctor: {doctor_name}")
# doctor = DOCTORS.get(doctor_name)
# if doctor:
# return f"{doctor_name} ({doctor['specialization']}), Timings: {doctor['timings']}"
# return f"❌ Sorry, no details found for {doctor_name}."

# # -------- Get Appointment Status --------
# @function_tool()
# async def get_appointment_status(self, appointment_id: str, context: RunContext) -> str:
# """
# Situation:
# Use this when the patient asks about the status of their appointment
# (e.g., confirmation, scheduled date, doctor info).
# Arguments:
# appointment_id (str): The unique appointment ID.
# context (RunContext): Conversation context.
# Returns:
# str: Appointment details if found, or error message otherwise.
# """
# logger.info(f"📌 Checking status for appointment {appointment_id}")

# appointment = APPOINTMENTS.get(appointment_id)
# if not appointment:
# logger.warning(f"❌ Appointment ID {appointment_id} not found")
# return f"❌ Appointment ID {appointment_id} not found."

# return (
# f"📋 Appointment Status\n\n"
# f"ID: {appointment_id}\n"
# f"Patient: {appointment['patient']}\n"
# f"Doctor: {appointment['doctor']}\n"
# f"Date: {appointment['date']}\n"
# f"Location: {HOSPITAL_INFO['address']}\n"
# f"Contact: {HOSPITAL_INFO['phone']}"
# )

# # -------- Schedule Appointment (with Pydantic) --------
# @function_tool()
# async def schedule_appointment(self, request: AppointmentRequest, context: RunContext) -> str:
# """
# Situation:
# Use this when the patient wants to book a new appointment with a doctor.
# Input must be a valid AppointmentRequest object (all fields validated).
# Arguments:
# request (AppointmentRequest): Patient details (validated by Pydantic).
# context (RunContext): Conversation context.
# Returns:
# str: Appointment confirmation message with ID, doctor, and date.
# """
# logger.info(f"📝 Scheduling validated appointment for {request.name} with {request.doctor_name} on {request.date}")

# appointment_id = f"APT{len(APPOINTMENTS)+1:03d}"
# APPOINTMENTS[appointment_id] = {
# "patient": request.name,
# "email": request.email,
# "doctor": request.doctor_name,
# "date": str(request.date),
# }

# confirmation_msg = (
# f"✅ Appointment confirmed!\n\n"
# f"ID: {appointment_id}\n"
# f"Patient: {request.name}\n"
# f"Doctor: {request.doctor_name}\n"
# f"Date: {request.date}\n\n"
# f"Location: {HOSPITAL_INFO['address']}\n"
# f"Contact: {HOSPITAL_INFO['phone']}"
# )

# email_body = (
# f"Dear {request.name},\n\n"
# f"Your appointment has been scheduled.\n\n"
# f"{confirmation_msg}\n\n"
# f"- CityCare Hospital"
# )
# send_email_to_patient(request.email, "Appointment Confirmation", email_body)

# return confirmation_msg

# # -------- Reschedule Appointment --------
# @function_tool()
# async def reschedule_appointment(self, request: RescheduleRequest, context: RunContext) -> str:
# logger.info(f"🔄 Rescheduling appointment {request.appointment_id} to {request.new_date}")

# appointment = APPOINTMENTS.get(request.appointment_id)
# if not appointment:
# logger.error(f"Appointment {request.appointment_id} vanished from store")
# return f"❌ Appointment ID {request.appointment_id} not found."

# old_date = appointment["date"]
# appointment["date"] = str(request.new_date)

# msg = (
# f"✅ Appointment rescheduled!\n\n"
# f"ID: {request.appointment_id}\n"
# f"Patient: {appointment['patient']}\n"
# f"Doctor: {appointment['doctor']}\n"
# f"Old Date: {old_date}\n"
# f"New Date: {request.new_date}"
# )

# email_body = f"Dear {appointment['patient']},\n\nYour appointment has been rescheduled.\n\n{msg}\n\n- CityCare Hospital"
# send_email_to_patient(appointment["email"], "Appointment Rescheduled", email_body)

# return msg


# # -------- Cancel Appointment --------
# @function_tool()
# async def cancel_appointment(self, request: CancelRequest, context: RunContext) -> str:
# logger.info(f"❌ Cancelling appointment {request.appointment_id}")

# appointment = APPOINTMENTS.pop(request.appointment_id, None)
# if not appointment:
# logger.error(f"Appointment {request.appointment_id} vanished from store")
# return f"❌ Appointment ID {request.appointment_id} not found."

# msg = (
# f"✅ Appointment cancelled.\n\n"
# f"ID: {request.appointment_id}\n"
# f"Patient: {appointment['patient']}\n"
# f"Doctor: {appointment['doctor']}\n"
# f"Date: {appointment['date']}"
# )

# email_body = f"Dear {appointment['patient']},\n\nYour appointment has been cancelled.\n\n{msg}\n\n- CityCare Hospital"
# send_email_to_patient(appointment["email"], "Appointment Cancelled", email_body)

# return msg

# if __name__ == "__main__":
# success = send_email_to_patient(
# "syeda.maham.jafri.2024@gmail.com",
# "Test Appointment Email",
# "This is a test from CityCare Hospital Agent."
# )
# print("Email sent:", success)



# hospital_agent.py (with Pydantic validation for scheduling)

import os
import smtplib
import logging
from datetime import date as dt_date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
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

logger = logging.getLogger("hospital-voice-agent")
logging.basicConfig(level=logging.INFO)

from dotenv import load_dotenv
load_dotenv()

# ------------------ SAMPLE DATA ------------------
HOSPITAL_INFO = {
    "name": "CityCare Hospital",
    "address": "45 Medical Boulevard, Karachi, Pakistan",
    "phone": "+92 21 1234 5678",
    "email": "info@citycarehospital.com",
    "hours": "Mon–Sat: 8:00 AM – 8:00 PM, Sun: Closed",
}

DOCTORS = {
    "Dr. Sara Khan": {
        "specialization": "Cardiologist",
        "timings": "Mon–Fri: 10 AM – 2 PM",
        "email": "sara.khan@citycarehospital.com",
    },
    "Dr. Ali Raza": {
        "specialization": "Dermatologist",
        "timings": "Tue–Sat: 3 PM – 7 PM",
        "email": "ali.raza@citycarehospital.com",
    },
    "Dr. Fatima Ahmed": {
        "specialization": "Pediatrician",
        "timings": "Mon–Thu: 9 AM – 1 PM",
        "email": "fatima.ahmed@citycarehospital.com",
    },
    "Dr. Kamran Siddiqui": {
        "specialization": "Orthopedic Surgeon",
        "timings": "Mon–Fri: 11 AM – 4 PM",
        "email": "kamran.siddiqui@citycarehospital.com",
    },
    "Dr. Nadia Hussain": {
        "specialization": "Gynecologist",
        "timings": "Tue–Sat: 9 AM – 12 PM",
        "email": "nadia.hussain@citycarehospital.com",
    },
    "Dr. Imran Qureshi": {
        "specialization": "Neurologist",
        "timings": "Mon–Wed: 2 PM – 6 PM",
        "email": "imran.qureshi@citycarehospital.com",
    },
    "Dr. Ayesha Malik": {
        "specialization": "Psychiatrist",
        "timings": "Thu–Sat: 10 AM – 1 PM",
        "email": "ayesha.malik@citycarehospital.com",
    },
    "Dr. Bilal Sheikh": {
        "specialization": "ENT Specialist",
        "timings": "Mon–Fri: 4 PM – 8 PM",
        "email": "bilal.sheikh@citycarehospital.com",
    },
    "Dr. Zainab Akhtar": {
        "specialization": "Ophthalmologist",
        "timings": "Tue–Fri: 9 AM – 1 PM",
        "email": "zainab.akhtar@citycarehospital.com",
    },
    "Dr. Hassan Javed": {
        "specialization": "General Physician",
        "timings": "Mon–Sat: 9 AM – 5 PM",
        "email": "hassan.javed@citycarehospital.com",
    },
}

APPOINTMENTS = {}  # in-memory store

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
    """Model to validate appointment scheduling input."""
    name: str
    email: EmailStr
    doctor_name: str
    date: dt_date

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
    """Model for rescheduling an appointment"""
    appointment_id: str
    new_date: dt_date

    @field_validator("appointment_id")
    def validate_id_format(cls, v):
        if not v.startswith("APT"):
            raise ValueError("Invalid appointment ID format.")
        if v not in APPOINTMENTS:
            raise ValueError(f"Appointment ID '{v}' not found.")
        return v

    @validator("new_date")
    def validate_future_date(cls, v):
        if v < dt_date.today():
            raise ValueError("New date cannot be in the past.")
        return v


class CancelRequest(BaseModel):
    """Model for cancelling an appointment"""
    appointment_id: str

    @validator("appointment_id")
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
        doctor = DOCTORS.get(doctor_name)
        if doctor:
            return f"{doctor_name} ({doctor['specialization']}), Timings: {doctor['timings']}"
        return f"❌ Sorry, no details found for {doctor_name}."

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

    # -------- Reschedule Appointment --------
    @function_tool()
    async def reschedule_appointment(self, request: RescheduleRequest, context: RunContext) -> str:
        logger.info(f"🔄 Rescheduling appointment {request.appointment_id} to {request.new_date}")

        appointment = APPOINTMENTS.get(request.appointment_id)
        if not appointment:
            logger.error(f"Appointment {request.appointment_id} vanished from store")
            return f"❌ Appointment ID {request.appointment_id} not found."

        old_date = appointment["date"]
        appointment["date"] = str(request.new_date)

        msg = (
            f"✅ Appointment rescheduled!\n\n"
            f"ID: {request.appointment_id}\n"
            f"Patient: {appointment['patient']}\n"
            f"Doctor: {appointment['doctor']}\n"
            f"Old Date: {old_date}\n"
            f"New Date: {request.new_date}"
        )

        email_body = f"Dear {appointment['patient']},\n\nYour appointment has been rescheduled.\n\n{msg}\n\n- CityCare Hospital"
        send_email_to_patient(appointment["email"], "Appointment Rescheduled", email_body)

        return msg

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


if __name__ == "__main__":
    success = send_email_to_patient(
        "syeda.maham.jafri.2024@gmail.com",
        "Test Appointment Email",
        "This is a test from CityCare Hospital Agent."
    )
    print("Email sent:", success)
