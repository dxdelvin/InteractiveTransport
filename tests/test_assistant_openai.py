"""The OpenAI tool loop, with a fake client standing in for the API."""
import asyncio
import json
from types import SimpleNamespace as NS

from app.core.config import Settings
from app.schemas.assistant import ChatRequest
from app.services.assistant import TripAssistant


class FakeCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            tc = NS(id="call_1", type="function", function=NS(name="plan_route", arguments=json.dumps({"origin": "Hauptbahnhof", "destination": "Universität"})))
            tc.model_dump = lambda exclude_none=True: {"id": "call_1", "type": "function", "function": {"name": "plan_route", "arguments": tc.function.arguments}}
            return NS(choices=[NS(message=NS(content=None, tool_calls=[tc]))])
        return NS(choices=[NS(message=NS(content="Take tram 3 towards Haunstetten Südwest.", tool_calls=None))])


def test_openai_loop_runs_tools(augsburg):
    a = TripAssistant(Settings(openai_api_key="sk-test", _env_file=None))
    fake = FakeCompletions()
    a._client = NS(chat=NS(completions=fake))
    res = asyncio.run(a.chat(augsburg, ChatRequest(message="How do I get to the university from the station?")))
    assert res.engine == "openai" and res.journeys and res.tools_used == ["plan_route"]
    second = fake.calls[1]["messages"]
    assert second[-1]["role"] == "tool" and "options" in json.loads(second[-1]["content"])
    assert second[0]["role"] == "system"
