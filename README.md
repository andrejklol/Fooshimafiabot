---

# Fooshi Mafia Staff Bot

Advanced **Discord + VRChat moderation and staff performance tracking bot** designed for structured communities, events, and staff management.

Built specifically for the **Fooshi Mafia VRChat group** to automate moderation tracking, leaderboards, repeat offender detection, and real-time staff syncing.

---

# Features

## Staff Activity Tracking

Automatically tracks moderation actions using VRChat audit logs and system signals.

Tracked actions:

* Warns
* Kicks
* Bans
* Invites
* Invite Accepts
* Activity scoring system

Features:

* Persistent data storage
* Automatic syncing with VRChat group members
* Tracks staff performance over time
* Prevents data loss between restarts

---

## Leaderboard System

Ranks staff performance using an activity-based scoring system.

Includes:

* Global leaderboard
* Monthly leaderboard reset
* Individual staff statistics
* Persistent score storage
* Automatic staff syncing

Commands:

* `/leaderboard`
* `/staffrecord @user`

---

## Repeat Offender Detection

Automatically tracks users who repeatedly receive moderation actions.

Detects repeated:

* warns
* kicks
* bans

Features:

* Configurable thresholds
* Time-based tracking windows
* Automatic alerts for high-risk users
* Persistent offender history
* Integrated with leaderboard scoring

Helps staff identify problematic users quickly and consistently.

---

## VRChat Integration

Direct integration with VRChat group APIs.

Features:

* VRChat group member caching
* Staff role synchronization
* Audit log processing
* Invite tracking
* Presence signal collection
* automatic staff detection
* historical log loading support

Tracks:

* moderation actions
* staff activity
* invite acceptance
* group membership changes

---

## Status Pipeline System

Advanced online detection system combining multiple signals for accurate staff presence tracking.

Signal tiers:

### Tier 1 (highest reliability)

* websocket presence
* VRChat friend presence

### Tier 2

* recent moderation activity
* audit log actor activity

### Tier 3

* VRChat user status
* supported platform signals

Pipeline logic prevents false positives by validating signals before marking users online.

Provides reliable visibility of staff availability.

---

## Staff Sync Protection

Protects leaderboard data from accidental staff removal.

Features:

* Grace period before staff archive
* Discord role override protection
* Preserves staff history when roles change temporarily
* Prevents data loss from VRChat sync changes

---

## Autosave System

Automatically saves data when changes occur.

Prevents:

* leaderboard loss
* repeat offender reset
* unsaved moderation history

Uses async locking to prevent corruption.

---

## Permission System

Role-based permission hierarchy aligned with mafia rank structure.

Rank Levels:

Godfooshi — Owner
Underboss — Admin
Consigliere — High Staff
Capo — Moderator
Soldier — Staff
Associate — Member

Commands are restricted based on permission level.

---

# Commands

## Staff Commands

`/leaderboard`
Displays top performing staff.

`/staffrecord @user`
Shows detailed staff statistics.

`/repeatstats`
Displays repeat offender statistics.

---

## Admin Commands

`/synccommands`
Force refresh slash commands.

`/refreshvrcmembers`
Refresh VRChat group member cache.

`/resetvrcdata`
Reset leaderboard and repeat offender data.

`/loadvrchistory`
Load historical VRChat moderation logs.

---

## Utility Commands

`/ping`
Check bot latency.

`/vrcstatus`
Displays VRChat connection and pipeline status.

---

# Project Structure

```
Discord/

cogs/
│ admin.py
│ commands.py
│ moderation.py
│ tasks_cog.py

core/
│ cache.py
│ config.py
│ embeds.py
│ logger.py
│ utils.py
│ error_embed.py

services/
│ alerts.py
│ leaderboard/
│ offenders/
│ tasks.py
│ vrchat_client.py
│ status_pipeline.py

data/
│ leaderboard.json
│ repeat_offenders.json

main.py
```

---

# Installation

Requirements:

Python 3.11+

Install dependencies:

```
pip install -r requirements.txt
```

---

# Running the Bot

```
python Discord/main.py
```

---

# Data Storage

Runtime data is stored locally:

```
Discord/data/leaderboard.json
Discord/data/repeat_offenders.json
```

These files are automatically created and updated by the bot.

---

# Credits

Built for the **Fooshi Mafia VRChat community**.

Designed for scalable moderation tracking, staff monitoring, and automated performance analytics.

---

# License

Private project.
Not licensed for redistribution.
