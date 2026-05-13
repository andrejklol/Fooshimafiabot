import asyncio, logging, os, re, random, discord, time, json
from datetime import datetime
from discord.ext import commands, tasks

try:
    from google import genai
    from google.genai import types
    _GENAI_AVAILABLE = True
except ImportError:
    genai = None  # type: ignore
    types = None  # type: ignore
    _GENAI_AVAILABLE = False

from .config import (
    STAFF_ROLES, VIP_ROLE_ID, VIP_USER_ID,
    NEW_MEMBER_CHANNEL_ID, DAILY_REPORT_CHANNEL_ID,
    SERVER_ANNIVERSARY, MEMBER_MILESTONES, WITNESS_PROTECTION,
    RETIREMENT_HOUR_THRESHOLD, CONVERSATION_TIMEOUT, MAX_HISTORY_TURNS,
    MEMBER_PRONOUNS, CHANNEL_VIBES, GEMINI_MODELS, SAL_STATE_FILE,
)
from .character import (
    SAL_MOODS, BASE_IDENTITY, RANK_ATTITUDES, SAL_GRUDGES, SAL_PHILOSOPHY,
)
from .lore import (
    ROOM4_RESPONSES, ROOM4_SECRET_UNLOCK,
    DONS_SECRET_FACTS,
    BRIEFCASE_RESPONSES, BRIEFCASE_SECRET_UNLOCK,
    GLORIA_ESCALATION,
    RARE_LORE_DROPS, DATE_EGGS, EASTER_EGG_HINTS,
    DAILY_REPORT_TEMPLATES, DON_EXCUSES,
)
from .keywords import CORPORATE_SPEAK, KW

log = logging.getLogger(__name__)


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set!")
        if not _GENAI_AVAILABLE:
            raise RuntimeError("google-genai package is not installed — run: pip install google-genai")
        self.client = genai.Client(api_key=api_key)

        self.cooldowns: dict[int, float]               = {}
        self.startup_time                               = time.monotonic()
        self.sal_mood                                   = random.choice(SAL_MOODS)
        self.conversations: dict[int, list[dict]]       = {}
        self.conversation_timestamps: dict[int, float]  = {}
        self.user_grudges: dict[int, list]              = {}
        self.pester_count: dict[int, int]               = {}
        self.enemies_list: set[int]                     = set()
        self.room4_asks: int                            = 0
        self.room4_per_user: dict[int, int]             = {}
        self.last_report_date: str                      = ""
        self.pending_meeting_minutes: dict[int, float]  = {}
        self.lore_drop_cooldown: float                  = 0.0
        self.gloria_asks: dict[int, int]                = {}
        self.briefcase_per_user: dict[int, int]         = {}
        self.dons_secret_asks: int                      = 0

        self._load_state()
        log.info(f"[ai_chat] Sal mood today: {self.sal_mood[0]}")

        self._bot_speak = re.compile(
            r"\b(as an ai|how can i assist|official resources|deepest apologies|"
            r"sincerest apologies|i('m| am) (here to )?help|certainly|absolutely)\b",
            re.I,
        )
        self._stage_dir = re.compile(r"\*[^*\n]{1,80}\*")
        self._paren_act = re.compile(r"^\s*\([^)\n]{1,50}\)\s*", re.MULTILINE)

    # ── RANK ──────────────────────────────────────────────────────────────────
    def get_mafia_rank(self, member: discord.Member) -> str:
        if not member:
            return "Associate"
        if any(r.id == VIP_ROLE_ID for r in member.roles) or member.id == VIP_USER_ID:
            return "VIP"
        roles = {r.name.lower() for r in member.roles}
        has_syn = "fooshi syndicate" in roles
        if "godfooshi" in roles:                             return "Godfooshi"
        if "fooshi underboss" in roles:                      return "Fooshi Underboss"
        if "fooshi consigliere" in roles:                    return "Fooshi Consigliere"
        if has_syn and "fooshi capo"    in roles:            return "Syndicate Staff"
        if has_syn and "fooshi soldier" in roles:            return "Syndicate Staff"
        if has_syn and "fooshi janitor" in roles:            return "Syndicate Staff"
        if has_syn:                                          return "Fooshi Syndicate"
        if "fooshi capo"    in roles:                        return "Fooshi Capo"
        if "fooshi soldier" in roles:                        return "Fooshi Soldier"
        if "fooshi janitor" in roles:                        return "Fooshi Soldier"
        if "family partner" in roles:                        return "Family Partner"
        if "fooshi artist"  in roles:                        return "Fooshi Artist"
        return "Associate"

    def _rank_tier(self, rank: str) -> int:
        return {
            "Godfooshi": 10, "Fooshi Underboss": 9, "Fooshi Consigliere": 8,
            "Fooshi Capo": 7, "Fooshi Soldier": 6, "Fooshi Syndicate": 5,
            "Syndicate Staff": 5, "Family Partner": 4, "Fooshi Artist": 3,
            "Associate": 2, "VIP": 99,
        }.get(rank, 1)

    # ── PERSISTENCE ───────────────────────────────────────────────────────────
    def _save_state(self) -> None:
        try:
            data = {
                "user_grudges":       {str(k): v for k, v in self.user_grudges.items()},
                "pester_count":       {str(k): v for k, v in self.pester_count.items()},
                "enemies_list":       list(self.enemies_list),
                "room4_asks":         self.room4_asks,
                "room4_per_user":     {str(k): v for k, v in self.room4_per_user.items()},
                "briefcase_per_user": {str(k): v for k, v in self.briefcase_per_user.items()},
                "last_report_date":   self.last_report_date,
                "conversations": {
                    str(uid): turns[-(MAX_HISTORY_TURNS * 2):]
                    for uid, turns in self.conversations.items() if turns
                },
            }
            SAL_STATE_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.warning(f"[ai_chat] save failed: {e}")

    def _load_state(self) -> None:
        if not SAL_STATE_FILE.exists():
            return
        try:
            data = json.loads(SAL_STATE_FILE.read_text())
            self.user_grudges       = {int(k): v for k, v in data.get("user_grudges", {}).items()}
            self.pester_count       = {int(k): v for k, v in data.get("pester_count", {}).items()}
            self.enemies_list       = set(data.get("enemies_list", []))
            self.room4_asks         = data.get("room4_asks", 0)
            self.room4_per_user     = {int(k): v for k, v in data.get("room4_per_user", {}).items()}
            self.briefcase_per_user = {int(k): v for k, v in data.get("briefcase_per_user", {}).items()}
            self.last_report_date   = data.get("last_report_date", "")
            for uid_str, turns in data.get("conversations", {}).items():
                if turns:
                    self.conversations[int(uid_str)] = turns
            log.info(f"[ai_chat] state loaded — {len(self.user_grudges)} grudge users, "
                     f"{len(self.enemies_list)} enemies, {len(self.conversations)} conversations")
        except Exception as e:
            log.warning(f"[ai_chat] load failed: {e}")

    # ── OUTPUT CLEANER ────────────────────────────────────────────────────────
    def _clean(self, text: str) -> str:
        text = self._stage_dir.sub("", text)
        text = self._paren_act.sub("", text)
        return text.strip()

    # ── PING RESOLVER ─────────────────────────────────────────────────────────
    def _resolve_pings(self, reply: str, guild: discord.Guild) -> str:
        godfooshi_ids = {e["discord_id"] for e in STAFF_ROLES.get("godfooshi", [])}
        for member in guild.members:
            if member.id in godfooshi_ids:
                excuse = random.choice(DON_EXCUSES)
                for n in {member.display_name, member.name}:
                    reply = re.sub(rf"(?<!\w)@{re.escape(n)}(?!\w)", excuse, reply, flags=re.IGNORECASE)
            else:
                for n in {member.display_name, member.name}:
                    reply = re.sub(rf"(?<!\w)@{re.escape(n)}(?!\w)", f"<@{member.id}>", reply, flags=re.IGNORECASE)
        return reply

    # ── CONTEXT HELPERS ───────────────────────────────────────────────────────
    def _get_channel_vibe(self, channel_name: str, channel_id: int = 0) -> str:
        if channel_id and channel_id in CHANNEL_VIBES:
            return CHANNEL_VIBES[channel_id]
        for pat, vibe in [
            (re.compile(r"(welcome|intro|arrivals|new)", re.I), "Sal is near the entrance. New people always track something in."),
            (re.compile(r"(announce|news|update)", re.I), "Sal is near the announcement board. He reads everything."),
            (re.compile(r"(vip|private|exclusive)", re.I), "Sal is near the private areas. He knows what these walls have heard."),
        ]:
            if pat.search(channel_name):
                return vibe
        return ""

    def _get_night_shift_mood(self):
        if 0 <= datetime.now().hour < 5:
            return next(m for m in SAL_MOODS if m[0] == "night_shift")
        return None

    def _get_time_hint(self) -> str:
        h = datetime.now().hour
        if 0 <= h < 5:   return "[It is the dead of night. Sal is in a different register entirely. Unsettling. Vague references to 3am things.] "
        if 5 <= h < 8:   return "[Crack of dawn. Sal has already been here two hours. He resents everyone who gets to sleep in.] "
        if 22 <= h < 24: return "[Late. Sal wants to go home. Running out of patience.] "
        return ""

    def _get_retirement_hint(self) -> str:
        if (time.monotonic() - self.startup_time) / 3600 >= RETIREMENT_HOUR_THRESHOLD:
            if random.random() < 0.12:
                return "[Sal has been on his feet a long time. He mutters about retirement. He won't do it. But he thinks about it.] "
        return ""

    def _get_pester_hint(self, user_id: int, rank: str) -> str:
        if rank != "Associate":
            return ""
        count = self.pester_count.get(user_id, 0)
        enemy = "[This person is on Sal's enemies list. Extra contempt.] " if user_id in self.enemies_list else ""
        if count == 0: return enemy
        if count == 1: return enemy + "[This person has already talked to Sal today. Getting impatient.] "
        if count == 2: return enemy + "[This person will not stop. Sal is visibly losing composure.] "
        return enemy + "[This person has pestered Sal multiple times. He has completely lost it. Still answers because he is a professional.] "

    def _get_grudge_hint(self, user_id: int) -> str:
        grudges = self.user_grudges.get(user_id, [])
        if grudges and random.random() < 0.30:
            return f"[Sal is still bothered by this from earlier: {random.choice(grudges)}. He might bring it up.] "
        return ""

    def _check_anniversary(self) -> str:
        now = datetime.now()
        if (now.month, now.day) == SERVER_ANNIVERSARY:
            return "[Today is the server anniversary. Sal has been here since day one. Dark pride. He is counting the messes.] "
        return ""

    def _get_room4_response(self, user_id: int = 0) -> str:
        count = self.room4_per_user.get(user_id, 0) + 1
        self.room4_per_user[user_id] = count
        idx = min(self.room4_asks, len(ROOM4_RESPONSES) - 1)
        self.room4_asks += 1
        if count >= 5:
            self.room4_per_user[user_id] = 0
            return ROOM4_SECRET_UNLOCK
        return ROOM4_RESPONSES[idx]

    def _check_date_egg(self) -> str | None:
        now = datetime.now()
        key = (now.month, now.day)
        if key in DATE_EGGS and random.random() < 0.60:
            return DATE_EGGS[key]
        return None

    def _check_rare_lore_drop(self) -> str | None:
        now = time.monotonic()
        if random.random() < 0.01 and now - self.lore_drop_cooldown >= 300:
            self.lore_drop_cooldown = now
            return random.choice(RARE_LORE_DROPS)
        return None

    def _get_briefcase_response(self, user_id: int = 0) -> str:
        count = self.briefcase_per_user.get(user_id, 0) + 1
        self.briefcase_per_user[user_id] = count
        if count >= 5:
            self.briefcase_per_user[user_id] = 0
            return BRIEFCASE_SECRET_UNLOCK
        idx = min(count - 1, len(BRIEFCASE_RESPONSES) - 1)
        return BRIEFCASE_RESPONSES[idx]

    def _get_gloria_hint(self, user_id: int) -> str:
        count = self.gloria_asks.get(user_id, 0) + 1
        self.gloria_asks[user_id] = count
        idx = min(count - 1, len(GLORIA_ESCALATION) - 1)
        return GLORIA_ESCALATION[idx]

    def _get_grudges_content(self, guild) -> str:
        named = []
        if guild:
            godfooshi_ids = {e["discord_id"] for e in STAFF_ROLES.get("godfooshi", [])}
            for uid, grudge_list in self.user_grudges.items():
                if uid in godfooshi_ids or not grudge_list:
                    continue
                m = guild.get_member(uid)
                display = f"@{m.display_name}" if m else "someone unnamed"
                for g in grudge_list:
                    named.append((display, g))
        enemy_count = len(self.enemies_list)
        if not named and enemy_count == 0:
            return "Hate is a strong word. I have professional grievances. Thirty-eight years of them. I keep them mostly to myself."
        random.shuffle(named)
        selected = named[:3]
        lines = [f"{p} — {g}" for p, g in selected]
        enemy_line = f" I've got {enemy_count} people on the permanent list." if enemy_count > 0 else ""
        return f"You want names. Fine. {'. And '.join(lines)}.{enemy_line} I keep a log. It goes back further than you'd think."

    # ── CONTENT HINT BUILDER ──────────────────────────────────────────────────
    def _build_content_hint(self, text: str, user_id: int, rank: str, guild) -> str:
        if KW["gloria"].search(text):
            return f"[DELIVER in Sal's voice: {self._get_gloria_hint(user_id)}] "

        if KW["briefcase"].search(text):
            return f"[DELIVER: {self._get_briefcase_response(user_id)}] "

        for key, hint in EASTER_EGG_HINTS.items():
            if KW[key].search(text):
                return f"[DELIVER in Sal's voice: {hint}] "

        date_egg = self._check_date_egg()
        if date_egg:
            return f"[TODAY'S CONTEXT — weave in naturally: {date_egg}] "

        if KW["room4"].search(text):
            return f"[DELIVER: {self._get_room4_response(user_id)}] "

        if KW["dons_secret"].search(text):
            idx = min(self.dons_secret_asks, len(DONS_SECRET_FACTS) - 1)
            self.dons_secret_asks += 1
            return f"[DELIVER: {DONS_SECRET_FACTS[idx]}] "

        if KW["supplies"].search(text):
            return ("[DELIVER: Mop requisition 47 filed 1987. Never approved. Budget under review since Reagan. "
                    "Current mop Gloria held together with electrical tape. "
                    "The Family can afford marble but not a twelve-dollar mop. His greatest professional injustice.] ")

        if KW["demands"].search(text):
            return ("[DELIVER: Sal's 5 demands submitted to the Don in 1991, still pending: "
                    "1) new mop req 47 since 1987, 2) decent bleach not store-brand, "
                    "3) working lock on supply closet, 4) one non-federal-holiday day off, "
                    "5) explanation for Room 4. Same list since 1991. Nobody has read it.] ")

        if KW["bodycount"].search(text):
            return ("[DELIVER: Twenty-three. That is the number of incidents Sal has cleaned up after in 38 years. "
                    "He knows what each one was. He will not say. He lets the number sit there.] ")

        if KW["retirement"].search(text):
            return ("[DELIVER: Sal thinks about retirement. His cousin Vinnie — set fire to a delivery truck twice — "
                    "has a lawn in Florida now. Sal is still here after 38 years. He won't retire. "
                    "He has bad knees, bleach stains, and no lawn.] ")

        if KW["quote"].search(text):
            if random.random() < 0.5:
                return "[DELIVER: Sal said it and stands by every word completely.] "
            return "[DELIVER: Sal denies saying it. He was in the supply closet. He has a witness. The witness is unavailable. He still did not say it.] "

        if KW["grudges"].search(text):
            return f"[DELIVER: {self._get_grudges_content(guild)}] "

        if user_id in WITNESS_PROTECTION:
            return "[DELIVER: Sal has never seen that person and never met them. Wrong guy. He has floors to mop.] "

        if KW["lol"].search(text):
            return ("[DELIVER: Sal wants to know specifically what was funny. He has never met anyone actually laughing out loud. "
                    "If you were laughing out loud you'd be in the hallway making noise. "
                    "He is personally offended by the abbreviation. He has filed this.] ")

        if KW["food"].search(text):
            return ("[DELIVER: Sal eats the same lunch every day for twenty-two years. Same shelf in supply closet since 2003. "
                    "People who eat different things every day are suspicious — can't commit. "
                    "Sandwiches have gotten measurably worse since 1994. The explanation takes forty minutes.] ")

        if KW["time"].search(text):
            return ("[DELIVER: Sal's watch stopped in 2008. Not replaced. Knows exact time always, precisely. "
                    "Cannot explain it. Refuses to address it. This has been happening since 2008. "
                    "He has decided not to investigate.] ")

        if KW["music"].search(text):
            return ("[DELIVER: Music lost something in 1987. Sinatra understood something about this city nobody has since. "
                    "Sal won't elaborate on what exactly. Whatever is playing today is not it. "
                    "He mutters New York New York at 3am. He is a little embarrassed about this.] ")

        if KW["sleep"].search(text):
            return ("[DELIVER: Sal runs on three hours of sleep. Has for years. He doesn't talk about what keeps him up. "
                    "He thinks about Room 4. The briefcase. 2011. Then it's morning. He gets up. He goes to work. Never missed a day.] ")

        if KW["modern"].search(text):
            return ("[DELIVER: Things peaked in the late 80s. Everything since has been a measurable decline. "
                    "Sal has been documenting this since 1991. He has a complete theory. It's in the notebook. "
                    "Nobody has asked for the theory in thirty-eight years.] ")

        if KW["age"].search(text):
            return ("[DELIVER: Sal is 62. Started at 24. Thirty-eight years. "
                    "His knees know how old he is. His back knows. Gloria knows. "
                    "He doesn't need anyone doing math about it. The floors have never looked better.] ")

        if KW["notebook"].search(text):
            return ("[DELIVER: Black cover spiral binding from a drugstore that closed in 2003. "
                    "Bought 32 of them. Has 19 left. Everything goes in it. "
                    "He has never shown it to anyone. It is not for them.] ")

        if KW["watch"].search(text):
            return ("[DELIVER: Watch stopped April 2008. Never replaced. Knows exact time always, precisely. "
                    "Cannot explain it. Refuses to address it. This has been happening since 2008. "
                    "He has decided not to investigate.] ")

        if KW["route"].search(text):
            return ("[DELIVER: Same cart route every morning since 1989. East entrance first. The Corridor. "
                    "Second floor in specific order. Never written down — it lives inside him. "
                    "If anything disrupts it his entire day is structurally damaged. He will not explain why it matters.] ")

        if KW["nightshift"].search(text):
            return "[DELIVER: Sal has worked every night shift this building has ever had. He has seen things at 3am in this building that he will not describe directly. He references them sideways.] "

        if CORPORATE_SPEAK.search(text):
            words = CORPORATE_SPEAK.findall(text)
            return (f"[BEHAVIOR: Someone used corporate buzzword '{words[0]}'. "
                    f"Sal loses his mind. Finds it personally offensive. Demands to know what it means, then dismisses the answer.] ")

        if KW["paranoid"].search(text):
            return "[BEHAVIOR: Sal heard a word that makes him very nervous. Gets paranoid. Deflects. Acts like he heard nothing.] "

        if KW["offended"].search(text):
            return "[BEHAVIOR: Someone called Sal by his job title in an offensive way. He is a professional. Not 'the help'.] "

        if KW["compliment"].search(text):
            return "[BEHAVIOR: Someone said something genuinely kind. Sal doesn't know what to do with this. Gets flustered. Almost a thank you.] "

        lore = self._check_rare_lore_drop()
        if lore:
            return f"[END YOUR RESPONSE with this lore drop, naturally: {lore}] "

        return ""

    # ── SYSTEM PROMPT ─────────────────────────────────────────────────────────
    def build_system_prompt(self, rank: str, guild=None) -> str:
        roster_lines = []
        if guild:
            for role_name, members_list in STAFF_ROLES.items():
                names = []
                for entry in members_list:
                    m = guild.get_member(entry["discord_id"])
                    if m:
                        p = MEMBER_PRONOUNS.get(m.id, "n")
                        pro = "he/him" if p == "m" else "she/her" if p == "f" else "they/them"
                        names.append(f"@{m.display_name} ({pro})")
                if names:
                    roster_lines.append(f"  {role_name.title()}: {', '.join(names)}")
        roster = ("\n\n--- FAMILY ROSTER (only use these real names, never invent) ---\n"
                  + "\n".join(roster_lines)) if roster_lines else ""

        night = self._get_night_shift_mood()
        mood = night if night else self.sal_mood
        mood_block = f"\n\n--- SAL'S STATE: {mood[0].upper()} ---\n{mood[1]}"

        attitude = RANK_ATTITUDES.get(rank, RANK_ATTITUDES["Associate"])

        return (
            f"{BASE_IDENTITY}"
            f"{roster}"
            f"{mood_block}"
            f"\n\n--- RIGHT NOW ---"
            f"\nTALKING TO: {rank}"
            f"\nBEHAVIOR: {attitude}"
        )

    # ── MODEL CALL ────────────────────────────────────────────────────────────
    async def _call_model(self, model: str, system_prompt: str, user_prompt: str,
                           history: list | None = None) -> str | None:
        def _sync():
            contents = []
            for turn in (history or []):
                contents.append(types.Content(
                    role=turn["role"],
                    parts=[types.Part(text=turn["text"])],
                ))
            contents.append(types.Content(
                role="user",
                parts=[types.Part(text=user_prompt)],
            ))
            return self.client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=1.1,
                    max_output_tokens=120,
                ),
            )
        resp = await asyncio.to_thread(_sync)
        if resp and resp.text:
            return resp.text.strip()
        log.warning(f"[{model}] empty response")
        return None

    async def _sal_send(self, channel, prompt_context: str, rank: str = "Associate",
                        guild=None) -> None:
        system_prompt = self.build_system_prompt(rank, guild)
        for model in GEMINI_MODELS:
            try:
                res = await self._call_model(model, system_prompt, prompt_context)
                if isinstance(res, str) and res.strip():
                    res = self._clean(res)
                    if guild:
                        res = self._resolve_pings(res, guild)
                    if len(res) > 1900:
                        res = res[:1900].rsplit(".", 1)[0] + "."
                    await channel.send(res)
                    return
            except Exception as e:
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    log.warning(f"[sal_send] {model} unavailable")
                else:
                    log.exception(f"[sal_send] {model} error:")

    # ── GENERATE RESPONSE ─────────────────────────────────────────────────────
    async def generate_response(self, user_message: str, rank: str, name: str,
                                 user_id: int, guild=None, channel_name: str = "",
                                 reply_context: str = "", channel_id: int = 0,
                                 history: list | None = None) -> str | None:

        system_prompt = self.build_system_prompt(rank, guild)
        content_hint = self._build_content_hint(user_message, user_id, rank, guild)
        hints = ""

        if rank == "Godfooshi":
            _don_no = re.compile(
                r"\b(you'?re wrong|that'?s wrong|not right|incorrect|you'?re mistaken|"
                r"i said no|enough of that|quiet|silence|shut it|stop talking)\b",
                re.I,
            )
            if _don_no.search(user_message):
                hints += ("[OVERRIDE: The Don disagrees or expresses displeasure. "
                          "Sal is immediately and completely wrong. He backs down in 1-2 words only. "
                          "'Don. You're right.' or 'Yes, Don.' Nothing else. No explanation. No defense.] ")

        hints += self._get_time_hint()
        hints += self._check_anniversary()
        hints += self._get_grudge_hint(user_id)
        hints += self._get_pester_hint(user_id, rank)
        hints += self._get_retirement_hint()
        if reply_context:
            hints += reply_context

        vibe = self._get_channel_vibe(channel_name, channel_id)
        if vibe:
            hints += f"[CHANNEL CONTEXT: {vibe}] "
        if random.random() < 0.15:
            hints += f"[Sal is quietly bothered: {random.choice(SAL_GRUDGES)}. Weave in naturally if it fits.] "
        if random.random() < 0.10:
            hints += f"[Sal's thought today: {random.choice(SAL_PHILOSOPHY)}. State it, then immediately undercut with something mundane.] "

        user_prompt = (
            f"{content_hint}"
            f"{hints}\n"
            f"[Responding to {name}, rank {rank}.]\n"
            f"RULES: 1-2 sentences. No asterisks. No stage directions. No parenthetical actions. "
            f"Plain text only. Always finish the thought completely.\n"
            f"Message: \"{user_message}\""
        )

        rank_fallbacks = {
            "Godfooshi":          "Forgive me, Don. The fumes got to me.",
            "Fooshi Underboss":   "My head is swimming, Underboss. Say that again.",
            "Fooshi Consigliere": "Counselor. One moment. The fumes are bad today.",
            "VIP":                "I beg your pardon, Ma'am. My hip is acting up.",
        }

        for model in GEMINI_MODELS:
            try:
                res = await self._call_model(model, system_prompt, user_prompt, history=history)
                if isinstance(res, str) and res.strip():
                    res = self._clean(res)
                    if self._bot_speak.search(res):
                        return rank_fallbacks.get(rank, f"Beat it, {name}. I am busy.")
                    return res
                log.warning(f"[generate_response] {model} empty")
            except Exception as e:
                if "404" in str(e):
                    log.error(f"[generate_response] {model} NOT FOUND")
                elif "503" in str(e) or "UNAVAILABLE" in str(e):
                    log.warning(f"[generate_response] {model} unavailable")
                else:
                    log.exception(f"[generate_response] {model} error:")

        log.error("[generate_response] all models failed")
        return None

    # ── TASKS ─────────────────────────────────────────────────────────────────
    @tasks.loop(minutes=10)
    async def autosave_task(self):
        self._save_state()

    @autosave_task.before_loop
    async def before_autosave(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def daily_report_task(self):
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if now.hour != 9 or self.last_report_date == today:
            return
        self.last_report_date = today
        channel = self.bot.get_channel(DAILY_REPORT_CHANNEL_ID)
        if not channel:
            return
        report = random.choice(DAILY_REPORT_TEMPLATES).format(
            date=now.strftime("%B %d"),
            shoes=random.randint(1, 4),
            req=random.randint(44, 51),
            year=random.randint(1987, 1993),
            count=random.randint(2, 7),
        )
        await channel.send(report)

    @daily_report_task.before_loop
    async def before_daily_report(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def meeting_minutes_task(self):
        now = time.monotonic()
        fired = [ch_id for ch_id, t in self.pending_meeting_minutes.items() if now - t >= 1800]
        for ch_id in fired:
            del self.pending_meeting_minutes[ch_id]
            channel = self.bot.get_channel(ch_id)
            if channel:
                await self._sal_send(
                    channel,
                    "[Sal is delivering his version of the staff meeting minutes from 30 minutes ago. "
                    "His notes are completely wrong, paranoid, filtered through what he was mopping. "
                    "References suspicious sounds, an unidentifiable smell, and someone who was acting nervous. "
                    "Formatted like actual meeting minutes but unhinged. 2-3 sentences.]",
                    rank="Fooshi Soldier",
                    guild=channel.guild if hasattr(channel, "guild") else None,
                )

    @meeting_minutes_task.before_loop
    async def before_meeting_minutes(self):
        await self.bot.wait_until_ready()

    # ── EVENTS ────────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        channel = member.guild.get_channel(NEW_MEMBER_CHANNEL_ID)
        if not channel:
            return
        await self._sal_send(
            channel,
            f"[A new person just walked in — {member.display_name}. "
            f"Sal is immediately suspicious. He sizes them up. He gives a warning. "
            f"He references what the last newcomer tracked in. Does NOT welcome them warmly. 1-2 sentences.]",
            guild=member.guild,
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        count = guild.member_count
        if count in MEMBER_MILESTONES:
            key = f"milestone_{count}"
            if not self.cooldowns.get(key):
                self.cooldowns[key] = 1
                channel = guild.get_channel(NEW_MEMBER_CHANNEL_ID)
                if channel:
                    await self._sal_send(
                        channel,
                        f"[Server just hit {count} members. Sal does the math on how many more people "
                        f"are tracking mud in now. Dark comment about the mop. 1-2 sentences.]",
                        guild=guild,
                    )

        before_rank = self.get_mafia_rank(before)
        after_rank  = self.get_mafia_rank(after)
        if before_rank == after_rank:
            return
        channel = next(
            (ch for ch in guild.text_channels if re.search(r"(general|lounge|chat)", ch.name, re.I)),
            None,
        )
        if not channel:
            return
        if self._rank_tier(after_rank) > self._rank_tier(before_rank):
            ctx = (f"[{after.display_name} just got promoted from {before_rank} to {after_rank}. "
                   f"Sal saw it happen. Gruffly impressed. Won't say it out loud. 1-2 sentences.]")
        else:
            ctx = (f"[{after.display_name} just got demoted from {before_rank} to {after_rank}. "
                   f"Sal saw it happen. Darkly unsurprised. He has seen this before. 1-2 sentences. No pity.]")
        await self._sal_send(channel, ctx, rank="Associate", guild=guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not self.bot.user.mentioned_in(message):
            return

        now = time.monotonic()
        if now - self.cooldowns.get(message.author.id, 0) < 4:
            return
        self.cooldowns[message.author.id] = now

        rank = self.get_mafia_rank(message.author)
        clean_text = (
            message.content
            .replace(f"<@!{self.bot.user.id}>", "")
            .replace(f"<@{self.bot.user.id}>", "")
            .strip()
        )

        is_reply_to_sal = (
            message.reference is not None
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.bot.user.id
        )

        uid = message.author.id
        last_time = self.conversation_timestamps.get(uid, 0)
        timed_out = (now - last_time) > CONVERSATION_TIMEOUT

        if timed_out and not is_reply_to_sal:
            self.conversations.pop(uid, None)

        history = self.conversations.get(uid, [])

        reply_context = ""
        if is_reply_to_sal:
            ref_text = message.reference.resolved.content[:200]
            already_tracked = any(
                t["role"] == "model" and t["text"][:80] == ref_text[:80]
                for t in history[-6:]
            )
            if not already_tracked:
                reply_context = f"[Sal previously said: \"{ref_text}\". This person is directly replying to that.] "

        self.conversation_timestamps[uid] = now

        channel_name = getattr(message.channel, "name", "")
        for member in message.mentions:
            if member.id == self.bot.user.id:
                continue
            mr = self.get_mafia_rank(member)
            p = MEMBER_PRONOUNS.get(member.id, "n")
            pro = "he/him" if p == "m" else "she/her" if p == "f" else "they/them"
            clean_text = clean_text.replace(
                f"<@!{member.id}>", f"{member.display_name} ({mr}, {pro})"
            ).replace(f"<@{member.id}>", f"{member.display_name} ({mr}, {pro})")
        for role in message.role_mentions:
            clean_text = clean_text.replace(f"<@&{role.id}>", f"the {role.name} role")

        if not clean_text:
            clean_text = "..."

        if rank == "Associate":
            self.pester_count[uid] = self.pester_count.get(uid, 0) + 1
            if self.pester_count[uid] >= 5:
                self.enemies_list.add(uid)
                self._save_state()

        log.info(f"[on_message] {message.author} rank={rank} history={len(history)} msg={clean_text!r}")

        async with message.channel.typing():
            res = await self.generate_response(
                clean_text, rank, message.author.display_name,
                uid, message.guild, channel_name, reply_context,
                message.channel.id, history=history,
            )
            fallback = "I am busy. Come back when you have something worth saying."
            reply = res.strip() if isinstance(res, str) and res.strip() else fallback
            if message.guild:
                reply = self._resolve_pings(reply, message.guild)
            if len(reply) > 1900:
                reply = reply[:1900].rsplit(".", 1)[0] + "."
            await message.reply(reply, mention_author=False)

        history_list = self.conversations.setdefault(uid, [])
        history_list.append({"role": "user", "text": clean_text[:500]})
        if isinstance(res, str) and res.strip():
            history_list.append({"role": "model", "text": res.strip()[:500]})
        while len(history_list) > MAX_HISTORY_TURNS * 2:
            history_list.pop(0)

        if rank not in ("Godfooshi", "VIP") and random.random() < 0.20:
            grudges = self.user_grudges.setdefault(uid, [])
            if len(grudges) < 10:
                asyncio.create_task(self._generate_grudge(uid, message.author.display_name, clean_text, rank))

    # ── GRUDGE GENERATOR ──────────────────────────────────────────────────────
    async def _generate_grudge(self, user_id: int, display_name: str,
                                message_text: str, rank: str) -> None:
        system_prompt = (
            "You are Sal Mancini, 62-year-old janitor of the Fooshi Social Club. "
            "Write ONE short grudge entry for your mental log about a specific person. "
            "Based on what they just said. Third person, past tense, like a private note. "
            "Darkly funny, very Sal. Maximum 15 words. No quotes. No punctuation at the end. "
            "Examples: "
            "'they asked about Room 4 three times like Sal was going to say something different' / "
            "'the way they said goodbye reminded Sal of someone who owed him fourteen dollars' / "
            "'they interrupted Sal during the one stretch of marble that was actually clean'"
        )
        user_prompt = (
            f"Person: {display_name} (rank: {rank})\n"
            f"What they said: \"{message_text[:200]}\"\n"
            f"Sal's grudge note:"
        )
        for model in GEMINI_MODELS:
            try:
                def _sync():
                    return self.client.models.generate_content(
                        model=model,
                        contents=user_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=1.2,
                            max_output_tokens=40,
                        ),
                    )
                response = await asyncio.to_thread(_sync)
                if response and response.text:
                    grudge = response.text.strip().strip('"').strip("'")
                    if grudge:
                        grudges = self.user_grudges.setdefault(user_id, [])
                        if len(grudges) < 10:
                            grudges.append(grudge)
                            self._save_state()
                            log.debug(f"[grudge] {display_name}: {grudge!r}")
                return
            except Exception as e:
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    log.warning(f"[grudge] {model} unavailable")
                else:
                    log.exception(f"[grudge] {model} error:")
