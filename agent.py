# dispatcher_agent.py

import os
import logging
import random
import json
from datetime import datetime

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    RunContext,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    AutoSubscribe,
    RoomInputOptions,
    function_tool,
)
from livekit.agents import metrics
from livekit.plugins import openai, silero
from livekit import rtc

from healthcare_agent import HospitalAgent
from airline_agent import AirlineAgent
from restaurant_agent import RestaurantAgent
from insurance_agent import InsuranceAgent
from aisystems_agent import AISystemsAgent
from context import ALL_PURPOSE_CONTEXT

logger = logging.getLogger("dispatcher-agent")
load_dotenv(dotenv_path=".env")

# ------------------ FILLER AUDIO ------------------
FILLER_AUDIO = [
    f"audio/filler_{i}.wav" for i in range(1, 33)
]


def get_random_filler():
    return random.choice(FILLER_AUDIO) if FILLER_AUDIO else None


# ------------------ DISPATCHER AGENT ------------------
class DispatcherAgent(Agent):
    def __init__(self, voice: str = "alloy") -> None:
        stt = openai.STT(model="gpt-4o-transcribe", language="en")
        llm_inst = openai.LLM(model="gpt-4o")
        tts = openai.TTS(model="gpt-4o-mini-tts", voice=voice)
        silero_vad = silero.VAD.load()

        super().__init__(
            instructions=f"{ALL_PURPOSE_CONTEXT}",
            stt=stt,
            llm=llm_inst,
            tts=tts,
            vad=silero_vad,
            allow_interruptions=True,
        )

    @function_tool()
    async def classify_domain(self, user_question: str, context: RunContext) -> dict:
        logger.info("Classifying domain for user request...")
        response = await self.llm.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a domain classification model."},
                {"role": "user", "content": ALL_PURPOSE_CONTEXT.replace("{user_question}", user_question)},
            ],
            max_tokens=150,
        )
        try:
            classification = json.loads(response.choices[0].message.content)
            logger.info(f"Classification result: {classification}")
            return classification
        except Exception as e:
            logger.error(f"Domain classification failed: {e}")
            return {"domain": None, "tool": None}

    @function_tool()
    async def handle_user_query(self, user_question: str, context: RunContext):
        filler_audio = get_random_filler()
        if filler_audio:
            await context.agent_session.background_audio.set_thinking([filler_audio])

        classification = await self.classify_domain(user_question, context)
        domain = classification.get("domain")
        tool = classification.get("tool")

        logger.info(f"Routing request: domain={domain}, tool={tool}")

        if domain == "healthcare":
            logger.info("HANDOFF: Dispatcher → HealthcareAgent")
            return HospitalAgent(), f"Switching to Healthcare Agent for {tool}"
        elif domain == "airline":
            logger.info("HANDOFF: Dispatcher → AirlineAgent")
            return AirlineAgent(), f"Switching to Airline Agent for {tool}"
        elif domain == "restaurant":
            logger.info("HANDOFF: Dispatcher → RestaurantAgent")
            return RestaurantAgent(), f"Switching to Restaurant Agent for {tool}"
        elif domain == "insurance":
            logger.info("HANDOFF: Dispatcher → InsuranceAgent")
            return InsuranceAgent(), f"Switching to Insurance Agent for {tool}"
        elif domain == "aisystems":
            logger.info("HANDOFF: Dispatcher → AISystemsAgent")
            return AISystemsAgent(), f"Switching to AISystems Agent for {tool}"
        else:
            logger.warning("HANDOFF FAILED: Domain not recognized")
            return None, "❌ Sorry, I couldn't identify which service your question is related to."


# ------------------ AGENT LIFECYCLE ------------------
def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting dispatcher agent to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        userdata={},
    )

    agent = DispatcherAgent()
    usage_collector = metrics.UsageCollector()
    conversation_log = []

    @session.on("user_message")
    def on_user_message(msg):
        if msg.text.strip():
            conversation_log.append({
                "role": "user",
                "text": msg.text,
                "timestamp": datetime.utcnow().isoformat()
            })

    @session.on("assistant_message")
    def on_assistant_message(msg):
        if msg.text.strip():
            conversation_log.append({
                "role": "assistant",
                "text": msg.text,
                "timestamp": datetime.utcnow().isoformat()
            })

    @ctx.room.on("participant_connected")
    def on_connected(remote: rtc.RemoteParticipant):
        logger.info("Participant connected.")

    @ctx.room.on("participant_disconnected")
    def on_finished(remote: rtc.RemoteParticipant):
        record = {
            "conversation": conversation_log,
            "metrics": usage_collector.get_summary().__dict__
        }
        with open("dispatcher_agent_log.json", "a", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.write("\n")
        logger.info("Session record saved.")

    @session.on("agent_handoff")
    def on_agent_handoff(new_agent, message):
        logger.info(f"HANDOFF: {type(new_agent).__name__} - {message}")
        asyncio.create_task(session.set_agent(new_agent))

    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(),
    )

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
