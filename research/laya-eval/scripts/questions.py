"""Our decider's exact questions (backend/pipeline/reasoner/decider.py), in Laya's dict format."""
ACTION_QUESTIONS = {
    "annotate": ("Should this moment get a line in today's running summary?",
                 "Worth one line in today's running summary",
                 "Nothing new happened; the summary already covers it"),
    "log_insight": ("Should this be logged as an insight in today's health report?",
                    "This is a measurable fact worth putting in today's report",
                    "Nothing measurable, or already logged today"),
    "remember": ("Does this reveal something to remember about the wearer long term?",
                 "This reveals a lasting fact about the wearer's habits or preferences",
                 "A one-off moment that says nothing lasting about the wearer"),
    "watch": ("Should the companion check on this again later?",
              "This is worth re-checking after a while",
              "Settled now; nothing to come back to"),
    "speak": ("Should the companion say something in the wearer's ear right now?",
              "A short spoken remark right now would help the wearer make a healthier next choice",
              "Nothing worth saying, or it would interrupt the wearer"),
    "ask": ("Should the companion ask the wearer a short question right now?",
            "Only the wearer can answer what this is, and a quick question now would settle it",
            "The answer is already clear, or a question would interrupt the wearer"),
    "act": ("Should the phone take a concrete action right now?",
            "The phone should do something concrete now, such as adding a walk to the calendar or shielding apps at wind-down",
            "No action on the phone would help right now"),
    "look": ("Would a closer look at the camera frame change the decision?",
             "A closer look at the current frame would settle what this is",
             "The text already makes clear what this is"),
}
TOPICS = ["caffeine", "food", "alcohol", "screen", "people", "outdoors", "medication", "sleep", "movement", "other"]
URGENCY = ["can wait", "soon", "now"]

def laya_questions():
    q = {k: {"type": "noul", "instructions": i, "criteria": {"true": y, "false": n}}
         for k, (i, y, n) in ACTION_QUESTIONS.items()}
    q["topic"] = {"type": "choice", "instructions": "Which of the wearer's health topics is this moment about?",
                  "criteria": {t: None for t in TOPICS}}
    q["urgency"] = {"type": "score", "instructions": "How soon does the wearer need to hear about this?",
                    "criteria": URGENCY}
    return q

def trim(state):
    """Decision-relevant core: drop persona and trends, keep 4 ticks and 8 today lines."""
    s = dict(state)
    s.pop("persona", None); s.pop("trends", None)
    s["recent"] = s["recent"][:4]; s["today"] = s["today"][-8:]
    return s
