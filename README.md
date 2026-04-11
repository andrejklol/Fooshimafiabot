# Fooshi Mafia Staff Bot

Advanced Discord + VRChat moderation and staff tracking bot designed for structured communities, events, and staff performance monitoring.

Built for the Fooshi Mafia VRChat group to automate moderation tracking, leaderboards, repeat offender detection, and real-time staff syncing.

---

## Features

### Staff Activity Tracking
Automatically tracks moderation actions from VRChat audit logs.

Tracked actions:
- Warns
- Kicks
- Bans
- Invites
- Invite Accepts
- Points scoring system

All activity is saved and persists between restarts.

---

### Leaderboard System
Ranks staff performance based on activity levels.

Includes:
- Overall leaderboard
- Monthly leaderboard reset
- Points scoring system
- Individual staff stat lookup

Commands:
- /leaderboard
- /staffrecord @user

---

### Repeat Offender Detection
Automatically tracks repeat offenders using configurable thresholds.

Detects repeated:
- warns
- kicks
- bans

Triggers alerts when thresholds are exceeded to help staff identify problem users quickly.

---

### VRChat Integration
Direct connection to VRChat group APIs.

Features:
- VRChat group member caching
- Staff role syncing
- Audit log processing
- Invite tracking
- Presence tracking signals

Tracks:
- moderation actions
- staff activity
- invite acceptance
- online presence indicators

---

### Status Pipeline
Advanced online detection system combining multiple signals.

Tier 1
- websocket presence
- friend presence

Tier 2
- recent moderation activity
- audit actor activity

Tier 3
- VRChat user status when supported

Provides reliable staff online visibility.

---

### Permission System
Role-based permission hierarchy based on mafia rank structure.

Rank Levels:

Godfooshi — Owner  
Underboss — Admin  
Consigliere — High Staff  
Capo — Moderator  
Soldier — Staff  
Associate — Member  

Commands are restricted based on permission level.

---

## Commands

### Staff Commands

/leaderboard  
Shows top performing staff.

/staffrecord @user  
Shows individual staff statistics.

/repeatstats  
Shows repeat offender tracking stats.

---

### Admin Commands

/synccommands  
Force sync slash commands.

/refreshvrcmembers  
Refresh VRChat group member cache.

/resetvrcdata  
Reset leaderboard and repeat offender data.

/loadvrchistory  
Load historical VRChat audit logs.

---

### Utility Commands

/ping  
Check bot latency.

/vrcstatus  
Displays system health and connection status.

---

## Project Structure

Discord/

cogs/
- admin.py
- commands.py
- moderation.py
- tasks_cog.py

core/
- cache.py
- config.py
- embeds.py
- logger.py
- utils.py

services/
- alerts.py
- leaderboard/
- tasks.py
- vrchat_client.py
- status_pipeline.py

main.py

---

## Installation

Requirements:
Python 3.11 or newer

Install dependencies:

pip install -r requirements.txt

---

## Environment Variables (.env)

DISCORD_TOKEN=

VRC_USERNAME=

VRC_PASSWORD=

GROUP_ID=

LOG_CHANNEL_ID=

---

## Running the Bot

python Discord/main.py

---

## Credits

Built for the Fooshi Mafia VRChat community.

Designed for scalable moderation tracking and staff performance management.

---

## License

Private project.
Not licensed for redistribution.
