# Fooshi Mafia Staff Bot

Advanced Discord + VRChat moderation and staff tracking bot built for structured communities and event groups.

Designed for the **Fooshi Mafia** VRChat group to automate moderation tracking, leaderboards, repeat offender detection, and staff activity syncing.

---

# Features

## Staff Activity Tracking
Automatically tracks moderation actions from VRChat audit logs.

Tracks:
- Warns
- Kicks
- Bans
- Invites
- Invite Accepts
- Points system

Leaderboard updates automatically and saves data between restarts.

---

## Leaderboard System
Ranks staff performance based on activity.

Includes:
- Overall leaderboard
- Monthly leaderboard reset
- Points scoring system
- Individual staff stat lookup

Commands:
/leaderboard  
/staffrecord @user  

---

## Repeat Offender Detection
Automatically tracks repeat offenders based on configurable thresholds.

Detects repeated:
- warns
- kicks
- bans

Triggers alerts when thresholds are exceeded.

---

## VRChat Integration
Connects directly to VRChat group APIs.

Features:
- VRChat group member caching
- Staff role syncing
- Live presence status pipeline
- Automatic audit log processing

Tracks:
- staff activity
- invite acceptance
- moderation actions
- user online presence

---

## Status Pipeline
Advanced online detection system combining multiple signals.

Tier 1
- websocket presence
- friend presence

Tier 2
- recent moderation activity
- audit actor activity

Tier 3
- VRChat user status when supported

Provides accurate staff online visibility.

---

## Permission System
Role-based permission hierarchy using mafia structure.

Rank levels:

Godfooshi – Owner  
Underboss – Admin  
Consigliere – High Staff  
Capo – Moderator  
Soldier – Staff  
Associate – Member  

Commands restricted based on rank.

---

## Automation Tasks

Background systems include:

- autosave system
- log polling
- VRChat group cache refresh
- monthly leaderboard reset
- repeat offender tracking
- status pipeline monitoring

---

## Commands

Staff Commands

/staffrecord @user  
Shows individual staff stats

/leaderboard  
Shows top performing staff

/repeatstats  
Shows repeat offender tracking stats

---

Admin Commands

/synccommands  
Force sync slash commands

/refreshvrcmembers  
Refresh VRChat group member cache

/resetvrcdata  
Reset leaderboard and repeat offender data

/loadvrchistory  
Load historical VRChat audit logs

---

Utility Commands

/ping  
Check bot latency

/vrcstatus  
Shows system health status

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

Requirements

Python 3.11 or newer

Install dependencies

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

## Hosting

Recommended platforms

Cybrancee  
Pterodactyl panel  
Docker containers  

Startup command used:

if [[ -d .git ]] && [[ ${AUTO_UPDATE} == "1" ]]; then git pull; fi; if [[ ! -z ${PY_PACKAGES} ]]; then pip install -U --prefix .local ${PY_PACKAGES}; fi; if [[ -f /home/container/${REQUIREMENTS_FILE} ]]; then pip install -U --prefix .local -r ${REQUIREMENTS_FILE}; fi; /usr/local/bin/python /home/container/Discord/main.py

---

## Security Notes

Never upload:

.env  
tokens  
cookies  

Recommended .gitignore entries:

.env
__pycache__/
cookies.json

---

## Planned Improvements

web dashboard  
analytics graphs  
staff performance metrics  
multi-server support  
auto moderation tools  

---

## Credits

Built for the Fooshi Mafia VRChat community.

Designed for scalable moderation tracking and staff performance management.

---

## License

Private project.
Not licensed for redistribution without permission.
