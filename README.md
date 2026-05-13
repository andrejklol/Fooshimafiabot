# 🎩 Fooshi Mafia Staff Bot

Advanced **Discord + VRChat moderation, automation, and staff performance tracking bot** built for structured communities, events, and organized staff teams.

Designed specifically for the **Fooshi Mafia VRChat community**, this bot seamlessly bridges Discord, VRChat Group APIs, and your **Emergent Web Dashboard Panel** to automate live presence tracking, logging, security thresholds, and performance analytics.

---

# ✨ Features

## 📊 Staff Activity & Live Syncing
Automatically tracks moderation actions using VRChat audit logs, Discord events, and live web activity signals.
* **Tracked Actions:** Warns, Kicks, Bans, Invites, Invite Accepts, and Staff Activity Scoring.
* **Web Integration:** Features a real-time **Reflection Layer** that securely pushes fresh presence and audit payloads to your web instance every 30–60 seconds.
* **Data Integrity:** Restarts safely without data loss, maintaining sync between Discord roles, VRChat group membership, and database states.

---

## 🏆 Leaderboard System
Ranks staff performance using an activity-based scoring system, visible across both Discord commands and the web panel.
* **Scale:** Built to reliably manage massive active data sets (simultaneously tracking **1,700+ leaderboard metrics**).
* **Includes:** Global leaderboard tracking, automated monthly resets, individual staff analytics, and persistent storage.

**Commands**
```text
/leaderboard
/staffrecord @user
```

---

## 🛡️ Advanced Security & Automation
Equipped with proactive defensive tools to automate day-to-day community safety and server maintenance.
* **Auto-Lockdown Protection:** Dynamically monitors joining patterns. Detects malicious server raids via sudden, calculated spikes in joins + bans, automatically locking invite channels and restricting public text channels. 
* **Smart Overrides:** Easily toggled off via dashboard configurations during official community events to prevent false-alarm lockdowns from legitimate member surges.

---

## 🚨 Repeat Offender Detection & Thresholds
Automatically tracks users who repeatedly violate community rules and triggers automated alerts when thresholds are breached.
* **Configurable Infraction Ceilings:** Automatically flags and moves a user to the "Repeat Offender" profile roster upon hitting their limit (Default: **5 infractions**).
* **Daily Burst Monitoring:** Tracks system-wide action frequencies per day to flag potential issues early:
    * **Warns Burst:** Triggers alert at **3 daily warns**.
    * **Kicks Burst:** Triggers alert at **2 daily kicks**.
    * **Bans Burst:** Triggers immediate alert at **1 daily ban**.

---

## 🌐 VRChat API Integration
Direct, high-fidelity integration with official VRChat group endpoints.
* **Robust Session Management:** Leverages a **Live-Login with a persisted session strategy** featuring custom OTP (One-Time Password) validation to maintain continuous API connectivity.
* **Active Tracking:** Monitors **1,900+ real-time VRChat status entries** to deliver instant member visibility.

---

## 🧠 Status Pipeline System
Advanced online detection system combining multiple infrastructure signals for accurate staff presence tracking.

### Tier 1 — Highest Reliability
* WebSocket presence signals
* VRChat friend presence updates

### Tier 2
* Recent moderation actions
* Audit log actor activity markers

### Tier 3
* VRChat user status configurations
* Supported external platform signals

---

## 🎫 Automated Ticket DM System
Eliminates manual notification overhead for administrative support channels.
* **Instant Routing:** The moment a staff member replies inside a private ticket channel, the bot automatically resolves the ticket owner's details from the web database layer.
* **Direct-to-User Messages:** Instantly fires a direct message to the user notifying them that a staff member has responded, dropping a convenient link back to their ticket.

---

## 🔐 Permission System
Role-based permission hierarchy strictly aligned with the authentic Fooshi Mafia rank structure. Commands are hard-restricted to their respective clearance tiers.

| Rank | Permission Level | System Access Tags | Description |
| :--- | :--- | :--- | :--- |
| 👑 **Godfooshi** | Owner | `Owner only` | Full system control, data resets, and core configuration access. |
| ⚔️ **Underboss** | Co-Owner | `Underboss+` | High-level management, custom alert overrides, and threshold tuning. |
| ⚖️ **Consigliere** | High Staff | `Consigliere+` | Elevated moderation, data management tools, and event coordination. |
| 💼 **Capo** | Admin | `Capo+` | Access to core automation panels, lockdown controls, and advanced commands. |
| 🛡️ **Soldier** | Moderator | `Soldier+` | Standard chat, group moderation tools, and basic logs. |
| 🤝 **Associate** | Member | *None* | General community member features. No access to administrative tooling. |

---

# 💬 Commands

## Staff Commands
```text
/leaderboard        - Displays top-performing staff members.
/staffrecord @user  - Shows highly detailed, granular staff performance statistics.
/repeatstats        - Displays active repeat offender metrics and flagged profiles.
```

## Owner Commands
```text
/synccommands       - Force refreshes and flushes application slash commands with Discord.
/refreshvrcmembers  - Re-indexes and rebuilds the VRChat group member cache.
/resetvrcdata       - Complete factory wipe of local leaderboard and offender tracking records.
/loadvrchistory     - Linearly parses and loads historical VRChat moderation logs into database.
```

## Utility Commands
```text
/ping               - Measures bot API latency and gateway heartbeat.
/vrcstatus          - Displays status of the VRChat API connection and active ingestion pipelines.
```

---

# 📁 Project Structure

```text
Discord/
├── cogs/
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── capo_commands.py
│   │   ├── consigliere_commands.py
│   │   ├── general_commands.py
│   │   ├── owner_commands.py
│   │   ├── permissions.py
│   │   └── underboss_commands.py
│   ├── __init__.py
│   ├── error_handler.py
│   ├── general.py
│   └── tasks.py
├── core/
│   ├── __init__.py
│   ├── cache.py
│   ├── config.py
│   ├── embeds.py
│   ├── error_embed.py
│   ├── logger.py
│   └── utils.py
├── data/
│   ├── leaderboard.template.json
│   └── repeat_offenders.template.json
├── services/
│   ├── leaderboard/
│   │   ├── __init__.py
│   │   ├── constants.py
│   │   ├── history_loader.py
│   │   ├── processors.py
│   │   ├── queries.py
│   │   ├── scoring.py
│   │   ├── service.py
│   │   ├── staff_sync.py
│   │   └── storage.py
│   ├── offenders/
│   │   ├── __init__.py
│   │   ├── queries.py
│   │   ├── storage.py
│   │   └── tracking.py
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── autosave.py
│   │   ├── group_cache.py
│   │   ├── log_polling.py
│   │   └── monthly_reset.py
│   ├── vrchat/
│   │   ├── __init__.py
│   │   ├── status_pipeline.py
│   │   ├── vrchat_auth.py
│   │   ├── vrchat_client.py
│   │   ├── vrchat_group.py
│   │   └── vrchat_presence.py
│   ├── alerts.py
│   └── high_staff.py
├── main.py
├── .gitignore
├── README.md
└── requirements.txt
```

---

# ⚙️ Deployment & Hosting

### System Environment
* **Runtime:** Python **3.11+**
* **Infrastructure Platform:** Fully optimized for Pterodactyl-based architectures (**Cybrancee Hosting** environment).
* **Footprint Resource Profile:** Extremely lightweight architecture running at an ultra-lean idle memory profile of roughly **~61.57 MiB / 1 GiB RAM**.

### Installation
Install your required environment dependencies via your server console terminal:
```bash
pip install -r requirements.txt
```

### Execution
Run the system initialization script from the root project folder:
```bash
python Discord/main.py
```

### Backup Protocols
* **Automated Scheduling:** Configured for daily system-wide automated cold backups executing precisely at **3:00 AM**.
* **Manual Locks:** Critical data snapshots can be locked via the dashboard interface to shield them against rolling rotation purges.

---

# 👥 Credits
Proudly built and engineered for the exclusive use of the **Fooshi Mafia VRChat community**.

---

# 📜 License
**Private Configuration.** All rights reserved. 
Unauthorized redistribution, compilation, or extraction of code blocks without explicit owner consent is strictly prohibited.
