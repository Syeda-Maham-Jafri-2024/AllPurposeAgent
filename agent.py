from livekit.agents import Agent, RunContext, function_tool
from dataclasses import dataclass
from typing import Optional
import logging
import re
import asyncio
from livekit.plugins import openai, silero
from livekit.agents import (
    AgentSession,
    JobContext,
    JobProcess,
    RoomInputOptions,
    RoomOutputOptions,
    WorkerOptions,
    cli,
    metrics,
)
from livekit.agents import metrics
from livekit.agents import MetricsCollectedEvent

from context import ALL_PURPOSE_CONTEXT
from healthcare_agent import HospitalAgent
from restaurant_agent import RestaurantAgent
from airline_agent import AirlineAgent
from aisystems_agent import AISystemsAgent
from insurance_agent import InsuranceAgent

logger = logging.getLogger("all-purpose-agent")

# --- Goodbye keywords to detect ---
GOODBYE_PATTERN = re.compile(
    r"\b(bye|goodbye|see you|talk to you later|farewell|end the call|end session|disconnect)\b",
    re.I,
)


@dataclass
class UserContext:
    last_domain: Optional[str] = None
    user_name: Optional[str] = None
    current_task: Optional[str] = None


class AllPurposeAgent(Agent):
    def __init__(self):
        super().__init__(instructions=ALL_PURPOSE_CONTEXT)

    # async def on_enter(self):
    #     await self.session.generate_reply(
    #         instructions="Hi, I’m your All Purpose Agent Assistant! How can I help you today?"
    #     )

    # ------------------------ HANDOFF FUNCTIONS ------------------------

    @function_tool()
    async def handoff_to_insurance(self, context: RunContext[UserContext]):
        """Transfer the user to the insurance assistant."""
        logger.info("Handing off to InsuranceAgent.")
        insurance_agent = InsuranceAgent()
        return insurance_agent

    @function_tool()
    async def handoff_to_healthcare(self, context: RunContext[UserContext]):
        """Transfer the user to the healthcare assistant."""
        logger.info("Handing off to HealthcareAgent.")
        healthcare_agent = HospitalAgent()
        return healthcare_agent

    @function_tool()
    async def handoff_to_airline(self, context: RunContext[UserContext]):
        """Transfer the user to the airline assistant."""
        logger.info("Handing off to AirlineAgent.")
        airline_agent = AirlineAgent()
        return airline_agent
    @function_tool()
    async def handoff_to_restaurant(self, context: RunContext[UserContext]):
        """Transfer the user to the restaurant assistant."""
        logger.info("Handing off to RestaurantAgent.")
        restaurant_agent = RestaurantAgent()
        return restaurant_agent

    @function_tool()
    async def handoff_to_aisystems(self, context: RunContext[UserContext]):
        """Transfer the user to the AI Systems assistant."""
        logger.info("Handing off to AISystemsAgent.")
        aisystems_agent = AISystemsAgent()
        return aisystems_agent

# ------------------------ ENTRYPOINT + PREWARM ------------------------

def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    await ctx.connect()

    session = AgentSession[UserContext](
        vad=silero.VAD.load(),
        llm=openai.LLM(model="gpt-4o"),
        stt=openai.STT(
            model="gpt-4o-transcribe",
            language="en",
            prompt="Always transcribe in English or Urdu",
        ),
        tts=openai.TTS(model="gpt-4o-mini-tts", voice="cedar"),
        userdata=UserContext(),
    )

    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info(f"Usage summary: {summary}")

    ctx.add_shutdown_callback(log_usage)

    await session.start(
        agent=AllPurposeAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(),
        room_output_options=RoomOutputOptions(transcription_enabled=True),
    )

    await session.say("Hi, I’m your AI Assistant ! How can I help you today?")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
