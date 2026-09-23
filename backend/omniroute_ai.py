from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any, Optional
import httpx
import pytz
from dateutil import parser as date_parser
from backend.config import settings

logger = logging.getLogger(__name__)


class ParsedReminder:
    def __init__(
        self,
        title: str,
        start_time_iso: str,
        interval_seconds: Optional[int] = None,
        interval_label: Optional[str] = None,
        end_time_iso: Optional[str] = None,
        explanation: Optional[str] = None,
    ):
        self.title = title
        self.start_time_iso = start_time_iso
        self.interval_seconds = interval_seconds
        self.interval_label = interval_label
        self.end_time_iso = end_time_iso
        self.explanation = explanation

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start_time": self.start_time_iso,
            "interval_seconds": self.interval_seconds,
            "interval_label": self.interval_label,
            "end_time": self.end_time_iso,
            "explanation": self.explanation,
        }


def parse_interval_string(text: str) -> tuple[Optional[int], Optional[str]]:
    """Helper to detect common interval patterns like 'every 8 hours', 'every 6 hours', 'every 7 days'."""
    text_lower = text.lower()
    
    # 8 hours
    if "8 hour" in text_lower or "8h" in text_lower:
        return 8 * 3600, "Every 8 hours"
    # 6 hours
    if "6 hour" in text_lower or "6h" in text_lower:
        return 6 * 3600, "Every 6 hours"
    # 12 hours
    if "12 hour" in text_lower or "12h" in text_lower:
        return 12 * 3600, "Every 12 hours"
    # Daily / every day / 24 hours
    if "daily" in text_lower or "every day" in text_lower or "24 hour" in text_lower:
        return 24 * 3600, "Daily"
    # 7 days / weekly
    if "weekly" in text_lower or "7 day" in text_lower or "every week" in text_lower:
        return 7 * 86400, "Every 7 days"

    # Regex for "every X hours/days/minutes"
    match = re.search(r"every\s+(\d+)\s+(minute|min|hour|hr|day|week)s?", text_lower)
    if match:
        count = int(match.group(1))
        unit = match.group(2)
        if "min" in unit:
            return count * 60, f"Every {count} minutes"
        elif "hour" in unit or "hr" in unit:
            return count * 3600, f"Every {count} hours"
        elif "day" in unit:
            return count * 86400, f"Every {count} days"
        elif "week" in unit:
            return count * 7 * 86400, f"Every {count} weeks"

    return None, None


async def parse_reminder_with_omniroute(
    user_prompt: str,
    user_timezone: str = "UTC",
) -> ParsedReminder:
    """
    Calls the local OmniRoute instance using model 'combo' to parse natural language
    into a structured reminder with start_time, interval, and optional end_time.
    """
    try:
        tz = pytz.timezone(user_timezone)
    except Exception:
        tz = pytz.UTC
        user_timezone = "UTC"

    now_local = datetime.datetime.now(tz)
    current_time_str = now_local.strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")

    system_instruction = f"""
You are an expert reminder assistant. Convert the user's natural language request into a structured reminder JSON.
Current local time: {current_time_str}
User timezone: {user_timezone}

CRITICAL RULES:
1. `title`: Clear, concise summary of the action/task (do not include "remind me to").
2. `start_time`: ISO 8601 string (with UTC offset or local timezone) of when the reminder should trigger for the FIRST time. If the user doesn't specify a time, default to 1 hour from now or the next morning at 09:00 if it's late.
3. `interval_seconds`: Integer in seconds between recurrences (e.g. 6 hours = 21600, 8 hours = 28800, 24 hours = 86400, 7 days = 604800). If one-off or not recurring, set to null.
4. `interval_label`: Friendly string like "Every 8 hours", "Every 6 hours", "Daily", "Every 7 days", or null if not recurring.
5. `end_time`: ISO 8601 string of the cut-off date/time when recurrence must STOP (e.g. "until next Friday"). If no end time specified, set to null.
6. `explanation`: Brief 1-sentence confirmation of what was scheduled.

Output ONLY valid JSON matching this schema:
{{
  "title": "string",
  "start_time": "YYYY-MM-DDTHH:MM:SS+00:00",
  "interval_seconds": null or integer,
  "interval_label": null or "string",
  "end_time": null or "YYYY-MM-DDTHH:MM:SS+00:00",
  "explanation": "string"
}}
"""

    headers = {
        "Content-Type": "application/json",
    }
    if settings.omniroute_api_key:
        headers["Authorization"] = f"Bearer {settings.omniroute_api_key}"

    payload = {
        "model": settings.omniroute_model,
        "messages": [
            {"role": "system", "content": system_instruction.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }

    url = f"{settings.omniroute_base_url.rstrip('/')}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                raw_text = resp.text.strip()
                content = ""

                if raw_text.startswith("data:"):
                    # OmniRoute returned Server-Sent Events (SSE) stream
                    content_parts = []
                    for line in raw_text.splitlines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        chunk_data = line[5:].strip()
                        if not chunk_data or chunk_data == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(chunk_data)
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                c = delta.get("content") or choices[0].get("text")
                                if c:
                                    content_parts.append(c)
                        except Exception:
                            continue
                    content = "".join(content_parts)
                else:
                    # Standard non-streaming JSON response
                    data = json.loads(raw_text)
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")

                # Strip markdown code blocks if present
                clean_content = content.strip()
                if "```" in clean_content:
                    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean_content, re.DOTALL)
                    if match:
                        clean_content = match.group(1)
                    else:
                        clean_content = re.sub(r"^```(?:json)?\s*", "", clean_content)
                        clean_content = re.sub(r"\s*```$", "", clean_content).strip()

                parsed = json.loads(clean_content)
                
                # Normalize start_time to UTC ISO
                start_dt = date_parser.parse(parsed["start_time"])
                if start_dt.tzinfo is None:
                    start_dt = tz.localize(start_dt)
                start_utc_iso = start_dt.astimezone(datetime.timezone.utc).isoformat()

                end_utc_iso = None
                if parsed.get("end_time"):
                    end_dt = date_parser.parse(parsed["end_time"])
                    if end_dt.tzinfo is None:
                        end_dt = tz.localize(end_dt)
                    end_utc_iso = end_dt.astimezone(datetime.timezone.utc).isoformat()

                return ParsedReminder(
                    title=parsed.get("title", user_prompt),
                    start_time_iso=start_utc_iso,
                    interval_seconds=parsed.get("interval_seconds"),
                    interval_label=parsed.get("interval_label"),
                    end_time_iso=end_utc_iso,
                    explanation=parsed.get("explanation"),
                )
            else:
                logger.warning("OmniRoute error HTTP %s: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.error("OmniRoute AI parse exception: %s. Falling back to local parser.", e)

    # Local Fallback Parser
    return fallback_local_parse(user_prompt, tz)


def fallback_local_parse(text: str, tz: pytz.BaseTzInfo) -> ParsedReminder:
    """Local fallback parser when OmniRoute AI is not reachable."""
    now_local = datetime.datetime.now(tz)
    interval_seconds, interval_label = parse_interval_string(text)
    
    # Default start time: 1 hour from now
    start_dt = now_local + datetime.timedelta(hours=1)
    
    # Clean title
    cleaned_title = re.sub(
        r"^(remind me to|remind me|alert me to|please remind me to)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    start_utc_iso = start_dt.astimezone(datetime.timezone.utc).isoformat()

    return ParsedReminder(
        title=cleaned_title or text,
        start_time_iso=start_utc_iso,
        interval_seconds=interval_seconds,
        interval_label=interval_label,
        end_time_iso=None,
        explanation=f"Scheduled for {start_dt.strftime('%b %d at %H:%M')}" + (f" ({interval_label})" if interval_label else ""),
    )
