import re

CORPORATE_SPEAK = re.compile(
    r"\b(synergy|bandwidth|circle back|touch base|pivot|leverage|"
    r"deep dive|move the needle|low hanging fruit|paradigm|deliverable|"
    r"boil the ocean|swim lane|ideate|unpack|onboard)\b",
    re.I,
)

KW = {
    "room4":       re.compile(r"\b(room\s*4|room four|whats in room|what's in room|what is in room|in room 4|about room 4)\b", re.I),
    "dons_secret": re.compile(r"\b(what do you know about the don|don'?s secret|secret about the don|tell me about the don|his secret|the don'?s secret|what do you know about andrejklol|whats the dons|dons secret)\b", re.I),
    "supplies":    re.compile(r"\b(new mop|supply|supplies|requisition|cleaning equipment|broom|get you a mop|gloria|the mop)\b", re.I),
    "demands":     re.compile(r"\b(what do you want|what do you need|what would you ask for|your list|your demands|what do you wish for)\b", re.I),
    "bodycount":   re.compile(r"\b(how long have you|how many years|what have you seen|what did you see|how many incidents|what happened here)\b", re.I),
    "retirement":  re.compile(r"\b(retire|retiring|retirement|ever thought about quitting|ever gonna leave|when are you leaving|how much longer)\b", re.I),
    "quote":       re.compile(r"\b(you said|you told me|earlier you|you mentioned|didn't you say|i heard you say|you once said|you claimed)\b", re.I),
    "grudges":     re.compile(r"\b(who do you hate|who annoys you|who bothers you|who gets on your nerves|who can't you stand|enemies|your enemies|who do you not like|who's on your list)\b", re.I),
    "paranoid":    re.compile(r"\b(fbi|fed|feds|cop|cops|police|wire|bug|snitch|rat|informant|narc|surveillance|undercover|sting)\b", re.I),
    "offended":    re.compile(r"\b(janitor|custodian|cleaning lady|cleaning man|the help|maintenance man|maid)\b", re.I),
    "compliment":  re.compile(r"\b(thank you|thanks|you're great|you're amazing|love you|best janitor|good job sal|appreciate you|wonderful|you rock|you're the best|respect|legend|goat)\b", re.I),
    "lol":         re.compile(r"\b(lol|lmao|lmfao|haha|hahaha)\b", re.I),
    "food":        re.compile(r"\b(hungry|food|eat(ing)?|lunch|dinner|sandwich|cannoli|pizza|pasta|snack|starving|meal)\b", re.I),
    "time":        re.compile(r"\b(what time|what'?s the time|what time is it|got the time|do you have the time)\b", re.I),
    "music":       re.compile(r"\b(music|sinatra|frank sinatra|playlist|song|what are you listening|put on some music)\b", re.I),
    "sleep":       re.compile(r"\b(tired|exhausted|sleepy|sleep|nap|can'?t sleep|insomnia|going to bed|need sleep|fall asleep)\b", re.I),
    "modern":      re.compile(r"\b(these days|nowadays|back in the day|kids today|young people|millennials|gen z|tiktok|instagram|viral|trending)\b", re.I),
    "age":         re.compile(r"\b(how old are you|your age|old are you|how old is sal)\b", re.I),
    "notebook":    re.compile(r"\b(your notebook|sal'?s notebook|the notebook|black notebook)\b", re.I),
    "watch":       re.compile(r"\b(your watch|sal'?s watch|the watch|broken watch|stopped watch)\b", re.I),
    "route":       re.compile(r"\b(your route|sal'?s route|the route|cart route|cleaning route|same route)\b", re.I),
    "nightshift":  re.compile(r"\b(night shift|working late|late night|midnight|3am|2am|after hours|what happens at night)\b", re.I),
    "gloria":      re.compile(r"\bgloria\b", re.I),
    "paulie":      re.compile(r"\b(big paulie|paulie)\b", re.I),
    "frankie":     re.compile(r"\b(frankie two.?shoes|frankie)\b", re.I),
    "2011":        re.compile(r"\b2011\b", re.I),
    "briefcase":   re.compile(r"\b(briefcase|what was inside|what'?s inside|inside the briefcase|did you look inside|did you open it|open the briefcase)\b", re.I),
    "lasagna":     re.compile(r"\b(the lasagna|week of the lasagna)\b", re.I),
    "fed":         re.compile(r"\b(the fed|the agent|toilet bug|bug in the toilet)\b", re.I),
    "80s":         re.compile(r"\b(the 80s|the eighties|back in the 80s|back in the eighties)\b", re.I),
    "mancini":     re.compile(r"\b(sal mancini|mancini)\b", re.I),
    "started":     re.compile(r"\b(what year did you start|when did you start)\b", re.I),
    "carmine":     re.compile(r"\bcarmine\b", re.I),
    "sinatra":     re.compile(r"\b(sinatra|frank sinatra|ol' blue eyes|new york new york)\b", re.I),
}
