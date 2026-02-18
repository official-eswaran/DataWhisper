from app.nl2sql.llm_client import call_local_llm


# Common chitchat patterns that definitely aren't data queries
CHITCHAT_KEYWORDS = [
    "who are you", "what are you", "your name", "what is your name",
    "how are you", "hello", "hi ", "hey", "good morning", "good evening",
    "thank you", "thanks", "bye", "goodbye", "help me", "what can you do",
    "tell me about yourself", "introduce yourself", "what do you do",
    "are you ai", "are you a bot", "are you human", "who made you",
    "who created you", "what's up", "how's it going", "nice to meet",
    "tell me a joke", "sing a song",
]

# Data query patterns that should go to SQL
DATA_KEYWORDS = [
    "show", "total", "average", "count", "sum", "max", "min", "how many",
    "list", "top", "bottom", "revenue", "sales", "profit", "salary",
    "group by", "by region", "by category", "by department", "by month",
    "filter", "where", "between", "greater", "less than", "sort",
    "highest", "lowest", "most", "least", "trend", "compare",
    "what is the", "how much", "which", "find",
]


def classify_intent(question: str) -> str:
    """
    Classify whether a question is a data query or general conversation.

    Returns:
        "data_query" — should go through NL-to-SQL pipeline
        "chitchat" — should be answered conversationally
    """
    q_lower = question.strip().lower()

    # Quick check: short greetings
    if len(q_lower.split()) <= 3 and any(q_lower.startswith(kw.strip()) for kw in ["hi", "hey", "hello", "bye", "thanks"]):
        return "chitchat"

    # Check for chitchat patterns
    for keyword in CHITCHAT_KEYWORDS:
        if keyword in q_lower:
            return "chitchat"

    # Check for data query patterns
    for keyword in DATA_KEYWORDS:
        if keyword in q_lower:
            return "data_query"

    # If unclear, use LLM to classify
    classify_prompt = f"""Classify this user message as either "data_query" or "chitchat".

- "data_query": The user wants to retrieve, analyze, or explore data from a database (e.g., "show total sales", "how many employees?", "revenue by month")
- "chitchat": The user is having a casual conversation, greeting, asking about the assistant, or asking something unrelated to data (e.g., "hi", "what is your name?", "tell me a joke")

User message: "{question}"

Reply with ONLY one word: data_query or chitchat"""

    response = call_local_llm(classify_prompt).strip().lower()

    if "data_query" in response:
        return "data_query"
    return "chitchat"


def generate_chitchat_response(question: str) -> str:
    """Generate a friendly conversational response."""
    q_lower = question.strip().lower()

    # Quick static responses for common questions
    if any(kw in q_lower for kw in ["your name", "who are you", "what are you"]):
        return "I'm DataWhisper, your private AI data assistant. I help you explore and analyze your uploaded data using natural language. Try asking me something about your data, like 'Show total revenue by category'!"

    if any(kw in q_lower for kw in ["how are you", "how's it going", "what's up"]):
        return "I'm running great and ready to analyze your data! Ask me any question about your uploaded dataset."

    if any(kw in q_lower for kw in ["hello", "hi ", "hey", "good morning", "good evening"]):
        return "Hello! I'm DataWhisper, your data assistant. Ask me anything about your uploaded data — like 'What are the top 5 products?' or 'Show revenue trends'."

    if any(kw in q_lower for kw in ["thank", "thanks"]):
        return "You're welcome! Let me know if you have more questions about your data."

    if any(kw in q_lower for kw in ["what can you do", "help", "what do you do"]):
        return ("I can help you analyze your uploaded data using plain English! Here's what I can do:\n"
                "- Answer questions like 'What is the total revenue?'\n"
                "- Generate charts: 'Show sales by region'\n"
                "- Find patterns: 'Which product sold the most?'\n"
                "- Detect anomalies in your data\n"
                "- Export reports as PDF\n\n"
                "Just ask a question about your data!")

    if any(kw in q_lower for kw in ["bye", "goodbye"]):
        return "Goodbye! Your data stays safe and private. Come back anytime!"

    # For anything else, use LLM
    chat_prompt = f"""You are DataWhisper, a friendly private AI data assistant.
You help users analyze their uploaded data using natural language.
Keep your response short (1-2 sentences) and guide the user to ask data-related questions.

User: {question}
Assistant:"""

    return call_local_llm(chat_prompt).strip()
