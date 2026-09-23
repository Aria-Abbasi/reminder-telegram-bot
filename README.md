# ⏰ Telegram Reminder Bot + Mini App (AI-Powered)

An intelligent Telegram Reminder Bot featuring an embedded **Telegram Mini App (Web App)** dashboard and **OmniRoute AI (`combo`)** natural language parsing.

---

## ✨ Features

- **Embedded Telegram Mini App**:
  - Open a sleek, responsive dashboard right inside Telegram.
  - Visual timeline and cards of active, recurring, and completed reminders.
  - Quick action buttons (Snooze, Mark as Done, Delete).
  - Native Telegram dark/light theme integration.
- **Flexible Scheduling Model**:
  - **Start Time**: Set the exact date & time for the first execution.
  - **Interval (Recurrence)**: Optional recurrence presets (`6 Hours`, `8 Hours`, `12 Hours`, `1 Day`, `7 Days`) or custom duration (minutes/hours/days).
  - **End Time**: Optional cut-off date & time after which recurrence terminates automatically.
- **✨ AI Natural Language Parsing (OmniRoute `combo`)**:
  - Type naturally in chat or in the Mini App (e.g. *"Remind me to take medication every 8 hours starting tomorrow at 9am until Friday"*).
  - Automatically parsed via OmniRoute's `combo` model into structured task parameters.
- **Interactive Chat Alerts**:
  - Notifications delivered with interactive inline keyboard buttons: `[ ✅ Done ]`, `[ 💤 +15m ]`, `[ 💤 +1h ]`, `[ 🛑 Stop Recurrence ]`.
  - Timezone detection to guarantee alarms trigger at the user's exact local time.
- **Resilient Background Scheduler**:
  - SQLite WAL-mode persistence ensures no reminders are lost across service restarts.

---

## 🏗 Architecture

```
Telegram Client (Chat & Mini App)
        │                 │
        ▼                 ▼
   FastAPI Server + aiogram 3.x
        │                 │
        ├─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
 Background Scheduler  SQLite Database  OmniRoute AI (combo)
```

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.11+ or Docker & Docker Compose
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- Access to an OmniRoute endpoint (or OpenAI-compatible API)

### 2. Environment Variables
Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

```ini
TELEGRAM_BOT_TOKEN="your-bot-token"
WEBAPP_URL="https://your-domain.com"
OMNIROUTE_BASE_URL="http://127.0.0.1:20128/v1"
OMNIROUTE_API_KEY="your-omniroute-key"
OMNIROUTE_MODEL="combo"
DATABASE_URL="sqlite+aiosqlite:///data/reminders.db"
PORT=8080
```

### 3. Run with Docker Compose
```bash
docker compose up -d --build
```

---

## 📜 License
MIT License. Created by [Aria Abbasi](https://github.com/Aria-Abbasi).
