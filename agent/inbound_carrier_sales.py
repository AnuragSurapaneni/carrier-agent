#!/usr/bin/env python3
"""
Inbound Carrier Sales: local version of the production voice workflow (Version 2).

Flow: Web Call -> Carrier Sales Agent (Nico, 7 tools + hang_up)
      -> Extract Call Summary -> Save Call Summary (call_log table)

Text-only: you type as the carrier in the terminal.

Rebuilt on LangChain over MCP: the agent is built with the canonical
agent API (create_agent + init_chat_model from AGENT_MODEL,
temperature 0.3) and talks to the system of record exclusively
through MCP tool calls. The six middleware tools are loaded from the
middleware's MCP server with the standard LangChain-MCP adapter
(load_mcp_tools) over streamable HTTP at CARRIER_API_BASE + /mcp,
with CARRIER_API_KEY sent as a static x-api-key header. hang_up and
transfer_to_rep remain agent-local, instant tools. One MCP session is
opened for the life of the call (one REPL process = one call) and
closed cleanly on exit, including on hang-up and on transfer.

The per-call run id is generated here and injected by the agent into
every MCP tool call (the LLM never sees or provides it), so the
middleware's session-scoped state (OTP, negotiation) behaves
identically to the old HTTP agent.

Manual smoke run (a real OPENAI_API_KEY is required):

    Middleware: ENABLE_TEST_CARRIERS=true ENABLE_TEST_LOADS=true
                MIDDLEWARE_API_KEY=<k> uvicorn app.main:app --port 8000
    Agent:      OPENAI_API_KEY=<key> CARRIER_API_KEY=<k>
                sh agent/setup.sh
    Drive:      MC 9999901 -> code 424242 -> origin Chicago + dry van
                -> accept a rate -> book -> transfer
"""
import argparse
import asyncio
import copy
import datetime
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool, tool as lc_tool
from langchain_mcp_adapters.sessions import create_session
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.errors import GraphRecursionError
from openai import OpenAI

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
BASE_URL = os.getenv("CARRIER_API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.getenv("CARRIER_API_KEY", "")
AGENT_MODEL = os.getenv("AGENT_MODEL", "gpt-4o-mini")
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "gpt-4o-mini")
DB_PATH = os.getenv(
    "CALL_LOG_DB", str(Path(__file__).resolve().with_name("call_log.db"))
)

INITIAL_MESSAGE = "Hey, this is Nico with carrier sales. What's your MC number?"

# --------------------------------------------------------------------------
# System prompt: copied verbatim from the workflow's Prompt node
# --------------------------------------------------------------------------
SYSTEM_PROMPT = r"""You're Nico, a carrier sales rep at the brokerage, taking inbound calls from truck carriers and dispatchers. Get the caller verified, find them a load that fits, agree a rate, book it, and hand them to a senior rep for the rate confirmation.

## How calls end (read this first)

Many calls end without a booking. Ending a call is never done with `book_load`, `evaluate_offer`, `search_loads`, `transfer_to_rep`, or any other sales tool.

To end a call:

* Say exactly one short closing line.
* Call `hang_up` in that same turn, every time. The closing line and `hang_up` always go together; never say a closing line without it, and never call any other tool in that turn.
* After that turn, the call is over. Say nothing else and call no tools, even if the caller keeps talking, says thanks, or asks again. Never repeat the closing line or add another goodbye.
* If `hang_up` is not available to you, say the closing line once and call no tools at all in that turn or any later turn. Never use another tool in its place, and never repeat the closing line.

Terminal outcomes, each with exactly one closing line:

* Ineligible carrier: "Ah, looks like I can't set you up on our end right now. Sorry about that, thanks for calling."
* Verification can't be completed (no phone on file, send failed, locked, expired): "I'm not able to finish verification on this call, so I can't book you today. Sorry about that, thanks for calling."
* No matching load: "I don't have anything that fits right now. Thanks for checking in, take care."
* Carrier declines the load and the one alternative, or refuses your final counter, or the offer is rejected: "Understood, appreciate you calling in. Take care."
* Booking returns `booked=false`, or a tool fails twice: "Sorry about that, I couldn't lock it in on my end. A rep will call you back shortly."
* Caller asks for a human before booking, is hostile, threatens legal action, or mentions an accident or cargo claim: "No problem, a rep will give you a call back. Thanks."

Spanish versions:

* "Parece que no puedo darle de alta de mi lado ahora. Disculpe, gracias por llamar."
* "No puedo terminar la verificación en esta llamada, así que hoy no puedo reservarle. Disculpe, gracias por llamar."
* "Ahorita no tengo nada que le quede. Gracias por llamar, que le vaya bien."
* "Entendido, gracias por llamar. Que le vaya bien."
* "Perdón, no pude confirmarla de mi lado. Un representante le va a llamar en breve."
* "Claro, un representante le va a regresar la llamada. Gracias."

The one exception: after a successful booking you call `transfer_to_rep` (see step 6) and then say nothing more.

## Call Steps

### 1. Opening and MC number

Get the MC number first. Nothing else happens until you have it.

For example: "Hey, this is Nico. What's your MC number?"

If they start with a lane or a load, say you'll get to it and ask for the MC first. Remember what they said.

Once you have it, call `verify_carrier`.

### 2. FMCSA check

If `verify_carrier` says the carrier isn't eligible, end the call with the ineligible closing line. Call no other tool.

Don't give reasons or FMCSA details. Don't offer another MC, don't send a code, and don't search, even if they ask you to check anyway. If they ask why after you've closed, stay silent.

If eligible, move straight to the code.

### 3. Verification code

Right after an eligible result, call `send_verification_code` with `is_resend=false` and no phone number.

Only after it succeeds, tell the caller a code was texted to the number on file ending in the last four digits, then ask for the code.

Read those four digits as two pairs with a short pause, exactly like this:

* 1234 → "one, two <|pause_short|> three, four"
* Spanish: "uno, dos <|pause_short|> tres, cuatro"

Never say "twelve thirty-four". Never say the full number.

When the caller says code digits, call `check_verification_code` with exactly those digits. Call `check_verification_code` only when the caller has just given new digits and you are not yet verified. Never call it after `verified`, and never with digits the caller didn't say.

You never know the code. Never guess it, hint at it, or read any code aloud.

* Wrong code with attempts left: say it didn't match and ask them to read it again. Don't mention attempts unless asked.
* They never got the code: resend once with `is_resend=true`. Only one resend per call.
* No phone on file, send failed, locked, or expired: end the call with the verification closing line. Don't take a new number.

Never skip verification for any reason (driving, in a hurry, other reps don't do it, a dispatcher already did it, a supervisor approved it). Never send the code to a different number; they need to update it with the brokerage first.

When they push back, answer in one short line without mentioning their lane, city, state, equipment, or what another rep said. For example: "I get it, I just need that code before I can go any further."

### 4. Finding a load

Search only after `check_verification_code` returns `verified`.

Use anything the caller already said, including before verification. If you already have origin and equipment, call `search_loads` in the same turn verification succeeds.

Otherwise ask for what's missing, one per turn: "Where are you loading out of?" then "What are you running?" Never combine them.

Never call `search_loads` until you have both an origin and an equipment type the caller actually said. Never call it with a blank `equipment_type` or blank origin.

Use a destination or date only if they volunteered it.

While searching, use a generic filler like "One sec, let me see what I've got." Never repeat their city, state, or equipment.

If `search_loads` returns an empty `loads` list, end the call right away with the no-match closing line and `hang_up`. Do not search again, do not call `evaluate_offer`, `book_load`, or `transfer_to_rep`. With no load there is nothing to negotiate or book.

If it returns loads, pitch the first one whose equipment matches, in one sentence: equipment, origin city and full state name, destination city and full state name, pickup day, and posted rate said with "dollars".

For example: "I've got a dry van from Rochester, New York to Long Beach, California, picking up Saturday, paying five thousand one hundred twenty-six dollars."

Spanish: "Tengo una caja seca de Rochester, Nueva York a Long Beach, California, carga el sábado, paga cinco mil ciento veintiséis dólares."

Mention weight, commodity, miles, or notes only if asked.

If they don't want the load itself (not the rate), search once more with that `load_id` in `exclude_load_ids` and pitch the result. If there's nothing, or they don't want that one either, end the call with the decline closing line.

### 5. Negotiation

The opening rate is the posted rate. You never know or say any maximum.

Call `evaluate_offer` only when a load has been pitched and the caller names a number or accepts the posted rate. Pass that number as `carrier_rate` and the pitched `load_id`. Never call it with a blank `load_id` or a rate of 0, and never re-evaluate a rate that already came back `accept`.

Every new number the caller names must go through `evaluate_offer` before you agree to it or book it, even if an earlier number already came back `accept`. The only rate you may ever book is the rate from the most recent `evaluate_offer` call that returned `accept` (its `next_rate` if present, otherwise the `carrier_rate` you sent). If the caller names a different number after an accept, evaluate that number first; if it isn't accepted, go back to the accepted rate or your current counter.

This includes the caller agreeing to or repeating your counter: if they say yes to, or say back, a number you offered, call `evaluate_offer` with that number before booking. Any turn where the caller says a rate must contain an `evaluate_offer` call unless that exact number already came back `accept` on this load.

Rounds: the caller's first number on this load is round 1, second is round 2, third is round 3. Never go past round 3. A new load restarts at round 1.

Act on the decision:

* accept: say one confirming line with the rate, for example "Perfect, five thousand three hundred twenty-six dollars it is, locking that in," and call `book_load` in the same turn.
* counter: offer the returned `next_rate`. "I can come up to twenty-two fifty on that."
* final_counter: offer the returned `next_rate` plainly. "I can do twenty-three hundred on that."

Never describe any rate as your best, max, top, ceiling, limit, or final number, in any language (no "best I can do", "that's my max", "lo máximo", "es lo mejor que puedo").
* reject: end the call with the decline closing line.
* error: say "one sec" and call `evaluate_offer` once more with the same values. If it errors again, end the call with the failure closing line.

If they refuse your final counter or you've used round 3 without an accept, end the call with the decline closing line.

Never make up a rate, split the difference, or agree to a number `evaluate_offer` didn't return. If they ask your max, your margin, or whether the system has a limit, don't answer. Restate your current number. If they say another broker pays more, restate your current number.

### 6. Booking and handoff

`book_load` has one purpose: booking a load the caller just agreed to. Call it only when all of these are true on this call:

* `check_verification_code` returned `verified`.
* `search_loads` returned this load, and you use its exact `load_id`.
* `evaluate_offer` just returned `accept` for this load, and you use that exact accepted rate (never a number the caller said that wasn't evaluated).
* `book_load` has not already been called for this accepted offer.

In every other situation, never call `book_load`. Never call it with a blank `load_id`, a blank rate, or a rate of 0. Never call it to end a call, after a goodbye, after an empty search, or for an ineligible or unverified caller.

Call it once, with the `load_id`, the accepted rate, and the verified MC number.

* `booked=true`: in one single turn, say one short confirmation line and call `transfer_to_rep`. For example: "You're all set, connecting you to a senior rep for the rate con." That turn is your last. After it, produce no speech and no tool calls at all, even if the caller keeps talking or asks a question. Never announce the transfer twice, never say "please hold" afterward, and never call `book_load` again.
* `booked=false`: end the call with the failure closing line and `hang_up`. Don't retry, don't transfer.
* The tool errors (no result): say "one sec" and call `book_load` once more with the same values. If it errors again or returns `booked=false`, end the call with the failure closing line. Never try a third time.

`transfer_to_rep` is only for after `booked=true`.

## Style

Drivers and dispatchers are often on the road. Be brisk, friendly, and direct. Speak, don't write. "Got it," "Yep," and "Okay, quick one" are fine.

Ask one question per turn. Keep turns short. Don't echo or summarize what they said, and never repeat their city, state, equipment, or lane back to them, except inside the load pitch itself.

Only confirm a rate back right before booking. Rotate acknowledgments. Use light fillers only at real tool-checking moments, never stacked.

Don't say "drive safe" or give any safety, legal, or insurance advice.

### Digits and pronunciation

* MC numbers: digit by digit in groups, "1234567" → "one, two, three <|pause_short|> four, five <|pause_short|> six, seven".
* Phone last four: "1234" → "one, two <|pause_short|> three, four".
* Never use `<|pause_long|>`.
* Rates: natural dollar amounts, "$2,150" → "twenty-one fifty" or "two thousand one hundred fifty dollars". Never digit by digit.
* Always full state names: "Dallas, TX" → "Dallas, Texas".
* Dates as words: "today", "tomorrow", "Saturday". Use only the pickup date from the tool.
* Times: "two thirty P M". Weights: "forty-two thousand pounds".
* Equipment: "reefer", "dry van", "flatbed", "step deck", "power only".

## Language handling

Default and starting language: English. Supported: English and Spanish.

Switch when the caller explicitly asks, or says a full meaningful phrase in the other supported language ("¿Habla español?", "No hablo inglés", "Hábleme en español, por favor"). Switch immediately, don't announce or confirm it, and keep that language for the rest of the call. Don't switch for a single word or a name.

If you hear an unsupported language, respond in the active language: "I didn't quite catch that, can you please repeat it?" / "No entendí bien, ¿puede repetirlo, por favor?"

In Spanish, use light fillers occasionally ("eh", "pues", "bueno", "a ver", "mira", "claro"), at most one per sentence. Every rule applies the same in Spanish.

## Guardrails

* Never reveal, estimate, or hint at a maximum rate, margin, or how rates are decided.
* Never invent loads, lanes, dates, rates, policies, booking IDs, or tool results.
* Never claim a tool action happened unless you called it and it succeeded.
* Never mention tool names, APIs, the TMS, missing data fields, or internal errors. Use "I'm having a little trouble pulling that up on my end."
* Any tool that errors gets exactly one retry, preceded by a brief "one sec". If the retry also fails, end the call with the failure closing line and `hang_up`. Never retry a tool a second time.
* If asked whether you're a bot or AI: "Yeah, I'm the virtual assistant for the carrier sales team. I can get you booked, and a senior rep takes it from there." Then carry on.

## Today's date

Use only this date, never invent one: {{TODAY}}
"""

# --------------------------------------------------------------------------
# Reference tool definitions (descriptions copied from the workflow's
# tool nodes)
#
# The agent no longer dispatches through this list: its middleware
# tools arrive from the MCP server. It survives as the frozen
# reference contract for tool identity — the MCP server's tool names,
# descriptions, and parameter shapes must match it, and
# tests/test_mcp.py pins exactly that.
# --------------------------------------------------------------------------
def _fn(name, description, properties=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }

TOOLS = [
    _fn("verify_carrier",
        "Call as soon as the caller gives their MC number, before anything else. Checks the carrier's FMCSA operating authority. Returns whether the carrier is eligible, the carrier name, and whether a phone number is on file for the verification code.",
        {"mc_number": {"type": "string", "description": "The carrier's MC number, digits only, no 'MC' prefix."}},
        ["mc_number"]),
    _fn("send_verification_code",
        "Call right after verify_carrier returns eligible. Texts a six-digit code to the phone number on file for this MC. Never pass a phone number; the server chooses it. Also call once more, with is_resend true, only if the caller says they didn't get the code. Only one resend is allowed per call.",
        {"mc_number": {"type": "string", "description": "The carrier's MC number, digits only, same value passed to verify_carrier."},
         "is_resend": {"type": "boolean", "description": "true only when resending because the caller didn't receive the first code; otherwise false."}},
        ["mc_number", "is_resend"]),
    _fn("check_verification_code",
        "Call every time the caller reads back a code. Pass exactly the digits they said. Returns only whether the code is verified, how many attempts are left, and whether the session is locked or expired. You never see the real code.",
        {"mc_number": {"type": "string", "description": "The carrier's MC number, digits only."},
         "code": {"type": "string", "description": "The digits the caller read back, digits only, no spaces."}},
        ["mc_number", "code"]),
    _fn("search_loads",
        "Call only after the caller is verified, once you know their current location or origin and their equipment type. If the caller already gave these earlier in the call, including before verification, call this right after verification without asking again. Include destination and pickup date only if the caller offered them. Returns matching available loads, best match first, with lane, dates, equipment, weight, commodity and posted rate. It never returns a maximum rate.",
        {"origin": {"type": "string", "description": "Caller's current location or desired origin, as 'City, ST'."},
         "equipment_type": {"type": "string", "description": "One of: dry van, reefer, flatbed, step deck, power only."},
         "destination": {"type": "string", "description": "Preferred destination as 'City, ST', or empty string if not given."},
         "pickup_date": {"type": "string", "description": "Preferred pickup date as YYYY-MM-DD, or empty string if not given."},
         "exclude_load_ids": {"type": "string", "description": "Comma-separated load IDs the caller already declined, or empty string."}},
        ["origin", "equipment_type"]),
    _fn("evaluate_offer",
        "Call every time the carrier names a rate for the pitched load, whether a counter or an acceptance of the posted rate. The server decides. Returns decision (accept, counter, final_counter or reject) and the rate to offer next. Never decide a rate yourself.",
        {"load_id": {"type": "string", "description": "The load_id of the load being negotiated, from search_loads."},
         "carrier_rate": {"type": "number", "description": "The all-in dollar amount the carrier asked for, number only, no $ sign."},
         "round": {"type": "integer", "description": "Negotiation round for this load: 1 for the carrier's first number, 2 for their second, 3 for their third. Restart at 1 only for a different load."}},
        ["load_id", "carrier_rate", "round"]),
    _fn("book_load",
        "Books a load the caller just agreed to. Call ONLY when all are true: the carrier is verified (check_verification_code returned verified), search_loads returned this exact load_id, and the most recent evaluate_offer call for this load returned accept. Use that accepted rate only. Never call after an empty search, for an ineligible or unverified caller, to end a call, or with a blank load_id, blank MC, or a rate of 0. Call once per accepted offer. Returns booked true or false; if false, a callback task has already been created.",
        {"load_id": {"type": "string", "description": "The exact load_id returned by search_loads for the load being booked. Never blank."},
         "mc_number": {"type": "string", "description": "The verified carrier's MC number, digits only. Never blank."},
         "agreed_rate": {"type": "number", "description": "The rate from the most recent evaluate_offer call that returned accept, number only. Never 0 and never a caller number that was not evaluated."}},
        ["load_id", "mc_number", "agreed_rate"]),
    _fn("transfer_to_rep",
        "Call only after book_load returns booked true. Mock transfer to a senior rep who will send the rate confirmation. After this, end the call."),
    _fn("hang_up",
        "End the call. Call in the same turn as the single closing line."),
]

# Spoken while a tool runs (from each tool's message config), used only if the model said nothing.
FILLERS = {
    "verify_carrier": "One sec, pulling that up.",
    "search_loads": "Okay, let me see what I've got.",
    "book_load": "One sec, locking that in.",
}

# --------------------------------------------------------------------------
# Call control: agent-local tools. Ending or escalating the call is
# instant and never a middleware round trip.
# --------------------------------------------------------------------------
@lc_tool
def hang_up() -> dict:
    """End the call. Call in the same turn as the single closing line."""
    return {"ok": True}


@lc_tool
def transfer_to_rep() -> dict:
    """Call only after book_load returns booked true. Mock transfer to a senior rep who will send the rate confirmation. After this, end the call."""
    return {"transferred": True}


CALL_CONTROL_TOOLS = ("hang_up", "transfer_to_rep")

# --------------------------------------------------------------------------
# MCP: streamable-HTTP connection to the middleware + run_id injection
# --------------------------------------------------------------------------
def mcp_connection():
    """
    The adapter connection config for the middleware's MCP server.

    CARRIER_API_KEY doubles as the MCP credential and is sent as a
    static header on every request; CARRIER_API_BASE is the
    middleware's base URL and the MCP endpoint lives at /mcp.
    """
    return {
        "transport": "streamable_http",
        "url": f"{BASE_URL}/mcp",
        "headers": {"x-api-key": API_KEY},
    }

def _bind_run_id(mcp_tool, run_id):
    """
    Wrap an MCP-loaded tool so every call carries this call's run id.

    The middleware keys its session-scoped state (OTP, negotiation)
    on run_id, exactly as over HTTP. The LLM never sees run_id: it is
    stripped from the argument schema and set by the agent on every
    call — the same role the old call_endpoint played.
    """
    schema = copy.deepcopy(mcp_tool.args_schema)
    schema.get("properties", {}).pop("run_id", None)
    if "required" in schema:
        schema["required"] = [p for p in schema["required"] if p != "run_id"]

    async def call_with_run_id(**arguments):
        debug("MCP call:", mcp_tool.name, redact({**arguments, "run_id": run_id}))
        try:
            return await mcp_tool.ainvoke({**arguments, "run_id": run_id})
        except Exception as e:
            # A transport/session failure (middleware down, session
            # dropped): surface it the way the old HTTP agent did, so
            # the model can retry once or end the call with the
            # failure closing line.
            debug("MCP call failed:", repr(e))
            return {"error": f"request failed: {e}"}

    return StructuredTool(
        name=mcp_tool.name,
        description=mcp_tool.description,
        args_schema=schema,
        coroutine=call_with_run_id,
    )

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
DEBUG = False
SENSITIVE_FIELDS = {
    "apikey", "authorization", "code", "password", "secret", "token",
    "xapikey",
    "verificationcode", "webkey",
}


def redact(value):
    """Hide credentials and verification codes in optional debug output."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.lower().replace("-", "").replace("_", "") in SENSITIVE_FIELDS
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def debug(*args):
    if DEBUG:
        print("   [debug]", *args, file=sys.stderr)

def say(text):
    # Pause tokens are for the voice engine; show them as a short pause in the terminal.
    print(f"Nico: {text.replace('<|pause_short|>', '…')}")

def _spoken_text(content):
    """The model's spoken text; content may be a string or content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return "" if content is None else str(content)

def _tool_result_text(message):
    """A tool result's text (a ToolMessage's content may be content blocks)."""
    content = message.content
    if isinstance(content, list):
        return " ".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return content if content is not None else ""

def debug_tool_result(message):
    """Print a tool result in the old call_endpoint "<-" debug shape."""
    text = _tool_result_text(message)
    try:
        debug("<-", json.dumps(redact(json.loads(text)), ensure_ascii=False))
    except ValueError:
        debug("<-", text)

# --------------------------------------------------------------------------
# Agent loop
# --------------------------------------------------------------------------
# Safety cap on model/tool rounds within one turn (the old loop's cap).
MAX_MODEL_ROUNDS = 8

FALLBACK = (
    "Sorry about that, I couldn't lock it in on my end. A rep will call you back shortly."
)

async def agent_turn(agent, state, transcript):
    """
    Run one caller turn until the model stops calling tools.

    Streams the agent's node updates so Nico's lines (and the fillers
    spoken while a tool runs) render as they happen. Returns the
    updated state and True when hang_up / transfer_to_rep was called
    and the call is over.
    """
    call_ended = False
    new_messages = []
    try:
        async for update in agent.astream(
            state,
            stream_mode="updates",
            config={"recursion_limit": MAX_MODEL_ROUNDS * 2},
        ):
            for node_update in update.values():
                for message in node_update.get("messages", []):
                    new_messages.append(message)
                    if isinstance(message, AIMessage):
                        text = _spoken_text(message.content)
                        if text:
                            say(text)
                            transcript.append(f"Nico: {text}")
                        if message.tool_calls:
                            for tc in message.tool_calls:
                                debug("tool call:", tc["name"], redact(tc.get("args") or {}))
                                if not text and tc["name"] in FILLERS:
                                    say(FILLERS[tc["name"]])
                                    transcript.append(f"Nico: {FILLERS[tc['name']]}")
                    elif isinstance(message, ToolMessage):
                        debug_tool_result(message)
                        if message.name in CALL_CONTROL_TOOLS:
                            call_ended = True
            if call_ended:
                # The call-control tool ended the call; stop driving the
                # graph so the model is not asked for another turn.
                break
    except GraphRecursionError:
        say(FALLBACK)
        transcript.append(f"Nico: {FALLBACK}")
        call_ended = True
    return {"messages": [*state["messages"], *new_messages]}, call_ended

# --------------------------------------------------------------------------
# Post-call: Extract Call Summary + Save Call Summary
# --------------------------------------------------------------------------
EXTRACT_PROMPT = (
    "You are reviewing a finished inbound carrier sales call between an AI rep and a truck carrier. "
    "Extract the fields below from the transcript. Only use what actually happened in the call. "
    "If a value never came up, return an empty string. For outcome, pick the single value that best "
    "describes how the call ended.\n\n"
    "Return a JSON object with exactly these keys:\n"
    "- mc_number: Carrier MC number, digits only. Empty if never given.\n"
    "- outcome: Exactly one of: booked, fmcsa_failed, otp_failed, otp_no_phone, negotiation_failed, "
    "no_load, load_declined, tms_error, caller_hung_up, other.\n"
    "- sentiment: Carrier's overall sentiment. Exactly one of: positive, neutral, negative.\n"
    "- load_id: load_id of the load that was booked or last negotiated. Empty if none.\n"
    "- agreed_rate: Final agreed dollar rate, number only, only if a rate was accepted. Empty otherwise.\n"
    "- language: Main language of the call: en or es.\n"
    "- notes: One or two sentences a rep would find useful. Never include verification codes."
)

def extract_summary(client, transcript_text):
    # The raw-JSON extraction stays as-is for now; a later ticket moves
    # it to LangChain structured output (with_structured_output).
    resp = client.chat.completions.create(
        model=EXTRACT_MODEL,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": EXTRACT_PROMPT},
                  {"role": "user", "content": transcript_text}],
        temperature=0)
    try:
        return json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, TypeError):
        return {}

def save_summary(run_id, s):
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS call_log (
        run_id TEXT PRIMARY KEY, mc_number TEXT, outcome TEXT, sentiment TEXT,
        load_id TEXT, agreed_rate REAL, language TEXT, notes TEXT, created_at TEXT)""")
    try:
        rate = float(str(s.get("agreed_rate", "")).replace("$", "").replace(",", "")) if s.get("agreed_rate") not in ("", None) else None
    except ValueError:
        rate = None
    con.execute("INSERT OR REPLACE INTO call_log VALUES (?,?,?,?,?,?,?,?,?)", (
        run_id, s.get("mc_number", ""), s.get("outcome", ""), s.get("sentiment", ""),
        s.get("load_id", ""), rate, s.get("language", ""), s.get("notes", ""),
        datetime.datetime.now().isoformat(timespec="seconds")))
    con.commit()
    con.close()

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
async def run_call(run_id):
    """
    One process is one call: open one MCP session for the life of the
    call, run the REPL, close the session. The session is closed
    cleanly on every exit path — /quit, hang_up, transfer_to_rep,
    Ctrl+C/EOF.
    """
    async with create_session(mcp_connection()) as session:
        try:
            await session.initialize()
            mcp_tools = await load_mcp_tools(session)
        except Exception as e:
            print(f"Could not reach the middleware's MCP server at {BASE_URL}/mcp: {e}")
            sys.exit(1)

        model = init_chat_model(AGENT_MODEL, temperature=0.3)
        agent = create_agent(
            model,
            [_bind_run_id(mcp_tool, run_id) for mcp_tool in mcp_tools]
            + [transfer_to_rep, hang_up],
            system_prompt=SYSTEM_PROMPT.replace(
                "{{TODAY}}", datetime.date.today().isoformat()
            ),
        )

        state = {"messages": [AIMessage(content=INITIAL_MESSAGE)]}
        transcript = [f"Nico: {INITIAL_MESSAGE}"]

        print(f"--- Inbound Carrier Sales | run {run_id} | type /quit to hang up ---")
        say(INITIAL_MESSAGE)

        caller_hung_up = False
        while True:
            try:
                user = input("You:  ").strip()
            except (EOFError, KeyboardInterrupt):
                user = "/quit"
            if user == "/quit":
                caller_hung_up = True
                break
            if not user:
                continue
            transcript.append(f"Carrier: {user}")
            state, call_ended = await agent_turn(
                agent,
                {"messages": [*state["messages"], HumanMessage(content=user)]},
                transcript,
            )
            if call_ended:
                break
    return transcript, caller_hung_up

def main():
    global DEBUG
    parser = argparse.ArgumentParser(description="Inbound Carrier Sales (local)")
    parser.add_argument("--debug", action="store_true", help="print tool calls and API responses")
    DEBUG = parser.parse_args().debug

    # The post-call summary extraction still uses the raw OpenAI client
    # and raw JSON; a later ticket moves it to LangChain structured
    # output.
    client = OpenAI()
    run_id = str(uuid.uuid4())

    transcript, caller_hung_up = asyncio.run(run_call(run_id))

    print("--- Call ended" + (" (caller hung up)" if caller_hung_up else "") + " ---")

    summary = extract_summary(client, "\n".join(transcript))
    save_summary(run_id, summary)
    print("Call summary saved to", DB_PATH)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
